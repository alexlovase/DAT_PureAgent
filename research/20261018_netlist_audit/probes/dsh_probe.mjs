import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseEnet } from '../_vendor/dsh/lib/netlist.js';
import { audit } from '../_vendor/dsh/lib/rules.js';
import { TOOLS } from '../_vendor/dsh/lib/tools.js';

const here = dirname(fileURLToPath(import.meta.url));
const caseDir = join(here, '..', '_work', 'dsh', 'cases');
mkdirSync(caseDir, { recursive: true });
function component(des, pinRows, props = {}) {
  return { props: { Designator: des, ...props }, pinInfoMap: Object.fromEntries(pinRows.map(([number,name,net]) => [number, {number,name,net}])) };
}
function fixture(name, raw) {
  const file = join(caseDir, `${name}.enet`);
  writeFileSync(file, JSON.stringify(raw));
  return { file, nl: parseEnet(JSON.stringify(raw), file) };
}
async function tool(name, file, args = {}) {
  const found = TOOLS.find(t => t.name === name);
  assert.ok(found, name);
  const result = await found.execute({file, ...args}, {cwd: here});
  assert.equal(result.ok, true, result.message);
  return result.message;
}
function wiring(nl) {
  return nl.components.flatMap(c => c.pins.map(p => `${c.des}.${p.number}:${p.name}->${p.net}`)).sort();
}
function fda(nl) {
  return audit(nl).findings.filter(f => f.id === 'fda-feedback-same-side').map(f => ({id:f.id, severity:f.severity, title:f.title}));
}

// T1: Synthetic wiring is unchanged when unrelated identity metadata is removed.
// These are detector candidates, not validated vendor-specific electrical errors.
const fullRaw = { components: {
  c1: component('U1', [['1','VIN+','IP'],['2','VIN-','IM'],['3','VOUT+','OP'],['4','VOUT-','OM']], {'Manufacturer Part':'DEMO_FDA','FootprintName':'SOIC8'}),
  c2: component('R1', [['1','1','OP'],['2','2','IP']], {Value:'10k','Manufacturer Part':'DEMO_R','FootprintName':'0603'}),
  c3: component('R2', [['1','1','OM'],['2','2','IM']], {Value:'10k','Manufacturer Part':'DEMO_R','FootprintName':'0603'}),
} };
const thinRaw = structuredClone(fullRaw);
for (const c of Object.values(thinRaw.components)) {
  delete c.props['Manufacturer Part'];
  delete c.props.FootprintName;
}
const full = fixture('t1-full', fullRaw);
const thin = fixture('t1-no-mpn-package', thinRaw);
assert.deepEqual(wiring(full.nl), wiring(thin.nl));
assert.deepEqual(fda(full.nl), fda(thin.nl));
assert.equal(fda(thin.nl).length, 2);
console.log(JSON.stringify({test:'T1_missing_nonessential_identity',components:thin.nl.components.length,pins:wiring(thin.nl).length,wiring_equal:true,fda_candidates_before:fda(full.nl).length,fda_candidates_after:fda(thin.nl).length,missing_mpn:thin.nl.components.every(c=>c.mpn===''),missing_footprint:thin.nl.components.every(c=>c.footprint==='') }));

// T2: A large shared net and an additional branch must stay queryable.
const fanRaw = {components:{}};
for (let i=1;i<=85;i++) fanRaw.components[`p${i}`]=component(`R${i}`, [['1','1','BUS'],['2','2',`N_${i}`]], {Value:'1k'});
fanRaw.components.extra = component('R999', [['1','1','BUS'],['2','2','EXTRA_N']], {Value:'10k'});
const fan = fixture('t2-86-endpoints', fanRaw);
const netText = await tool('enet_net', fan.file, {net:'BUS'});
const listText = await tool('enet_netlist', fan.file, {components:true,limit:60});
const partText = await tool('enet_part', fan.file, {des:'R999'});
const printedEndpoints = netText.split('\n').filter(l=>/^\s+R\d+\s+pin\s/.test(l)).length;
assert.equal(fan.nl.net('BUS').length, 86);
assert.equal(printedEndpoints, 86);
assert.match(netText, /R999\s+pin\s+1/);
assert.match(partText, /EXTRA_N/);
console.log(JSON.stringify({test:'T2_full_index_and_local_views',raw_endpoints:86,index_endpoints:fan.nl.net('BUS').length,net_tool_endpoints:printedEndpoints,extra_branch_in_net_tool:/R999\s+pin\s+1/.test(netText),extra_branch_other_end_in_part_tool:/EXTRA_N/.test(partText),component_overview_discloses_truncation:listText.includes('86')&&listText.includes('仅列前 60'),net_tool_header:netText.split('\n')[0]}));

// T3: Synthetic EN-open candidates expose what gets hidden by presentation/filtering.
// No datasheet has been supplied. No candidate is labeled a confirmed real defect.
const pendingRaw = {components:{}};
for (let i=1;i<=45;i++) pendingRaw.components[`u${i}`]=component(`U${i}`, [['1','EN','']], {'Manufacturer Part':'DEMO_ENABLE'});
const pending = fixture('t3-45-pending', pendingRaw);
const standard = audit(pending.nl).findings.find(f=>f.id==='pin-net-conflict');
const expanded = audit(pending.nl,{limit:100}).findings.find(f=>f.id==='pin-net-conflict');
const fullAuditText = await tool('enet_audit',pending.file,{detail:true});
const p1AuditText = await tool('enet_audit',pending.file,{severity:'P1',detail:true});
const tailPartText = await tool('enet_part',pending.file,{des:'U45'});
function shownRefs(text) {return [...new Set([...text.matchAll(/\bU\d+\.1\(EN\)/g)].map(m=>m[0]))].length;}
assert.equal(shownRefs(standard.evidence),40);
assert.equal(shownRefs(expanded.evidence),45);
assert.equal(shownRefs(fullAuditText),40);
assert.match(tailPartText,/^U45\s/m);
console.log(JSON.stringify({test:'T3_pending_candidate_visibility',synthetic_open_EN_pins:45,source_candidate_title:standard.title,source_candidate_severity:standard.severity,source_needs_datasheet:standard.needsDatasheet,default_core_evidence_refs:shownRefs(standard.evidence),core_limit100_evidence_refs:shownRefs(expanded.evidence),audit_tool_detail_true_refs:shownRefs(fullAuditText),audit_tool_detail_true_includes_U45:/\bU45\.1\(EN\)/.test(fullAuditText),direct_part_query_recovers_U45:/^U45\s/m.test(tailPartText),P1_filter_lists_any_P2:/\[P2\]/.test(p1AuditText),P1_filter_still_discloses_pending_count:/P2 1/.test(p1AuditText),P1_filter_says_no_findings_under_filter:p1AuditText.includes('该筛选条件下没有发现项')}));
console.log('T3_P1_RAW_BEGIN');
console.log(p1AuditText.replace(pending.file, 'cases/t3-45-pending.enet'));
console.log('T3_P1_RAW_END');
console.log('No LLM calls, no datasheet semantic review, no end-to-end accuracy measurement.');
