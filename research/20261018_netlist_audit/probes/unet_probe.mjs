import assert from "node:assert/strict";
import {createPinEntry,getPinNet} from "../_vendor/unet/src/types.js";
import {parseUniversalNetlist,toUniversalNetlistDocument,serializeUniversalNetlist} from "../_vendor/unet/src/parsers/universal/reader.js";
import {parseKicadNetlist} from "../_vendor/unet/src/parsers/kicad/netlist-parser.js";
import {traverseCircuitFromNet} from "../_vendor/unet/src/circuit-traversal.js";
import {groupComponentsByMpn,aggregateCircuitByMpn} from "../_vendor/unet/src/service/component-grouping.js";
import {buildComponents} from "../_vendor/unet/src/parsers/cadence/dsn/component-builder.js";
import {resolvePinNumber,buildDeviceIndexMap} from "../_vendor/unet/src/parsers/cadence/dsn/pin-resolver.js";
let passed=0;
function check(name,fn){const result=fn();passed++;console.log(JSON.stringify({name,status:"PASS",...result}));}
const meta={generatedAt:"2026-09-01T12:34:56.789Z"};
const fixture=()=>({nets:{VCC:{U1:["1"],C1:["1"]},GND:{U1:["2","3"],C1:["2"]}},components:{U1:{mpn:"PART-1",description:"IC",pins:{"1":{name:"VIN",net:"VCC"},"2":"GND","3":{name:"EP",net:"GND"},"4":""}},C1:{value:"1uF",dns:true,pins:{"1":"VCC","2":"GND"}}}});
check("official_reader_fixture_missing_MPN_round_trip",()=>{
 const input=fixture(),doc=toUniversalNetlistDocument(input,meta),parsed=parseUniversalNetlist(JSON.stringify(doc));
 assert.deepEqual(parsed,input);assert.equal(parsed.components.C1.mpn,undefined);assert.equal(parsed.components.U1.pins["4"],"");assert.deepEqual(Object.keys(parsed).sort(),["components","nets"]);
 return {components:Object.keys(parsed.components).length,pins:Object.values(parsed.components).reduce((n,c)=>n+Object.keys(c.pins).length,0),parsed_top_keys:Object.keys(parsed),file_metadata_keys:Object.keys(doc.metadata)};
});
check("footprint_dropped_only_by_file_projection",()=>{
 const input=fixture();input.components.C1.footprint="Capacitor_SMD:C_0402";
 const parsed=parseUniversalNetlist(serializeUniversalNetlist(input,meta));
 assert.equal(parsed.components.C1.footprint,undefined);assert.deepEqual(parsed.nets,input.nets);assert.deepEqual(parsed.components.C1.pins,input.components.C1.pins);
 return {footprint_preserved:false,connectivity_preserved:true};
});
check("hash_required_in_file_and_graph_mismatch_rejected",()=>{
 const missing=toUniversalNetlistDocument(fixture(),meta);delete missing.metadata.netlistHash;
 assert.throws(()=>parseUniversalNetlist(JSON.stringify(missing)),/netlistHash/);
 const broken=toUniversalNetlistDocument(fixture(),meta);delete broken.components.C1;
 assert.throws(()=>parseUniversalNetlist(JSON.stringify(broken)),/C1/);
 return {missing_hash:"rejected",net_membership_without_component:"rejected"};
});
const imported=parseKicadNetlist('(export (version "E") (components (comp (ref "R1") (value "10k"))) (nets (net (code "1") (name "SIGNAL_A") (node (ref "R1") (pin "1")) (node (ref "U404") (pin "7") (pinfunction "SENSE"))) (net (code "2") (name "SIGNAL_B") (node (ref "U404") (pin "9"))) (net (code "3") (name "GND") (node (ref "R1") (pin "2")))))');
check("kicad_missing_component_metadata_creates_connection_stub",()=>{
 assert.deepEqual(Object.keys(imported.components).sort(),["R1","U404"]);assert.equal(imported.components.R1.mpn,undefined);assert.deepEqual(imported.components.U404.pins,{"7":{name:"SENSE",net:"SIGNAL_A"},"9":"SIGNAL_B"});
 assert.deepEqual(parseUniversalNetlist(serializeUniversalNetlist(imported,meta)),imported);
 return {refdes:Object.keys(imported.components),unknown_pins:imported.components.U404.pins,persisted_roundtrip:"equal"};
});
check("unknown_MPN_component_remains_terminal_in_local_view",()=>{
 const before=JSON.stringify(imported);const r=traverseCircuitFromNet("SIGNAL_A",imported.nets,imported.components);
 const u=r.components.find(c=>c.refdes==="U404");assert.ok(u);assert.deepEqual(u.connections.flatMap(c=>c.pins),["7"]);assert.ok(!r.visited_nets.includes("SIGNAL_B"));assert.equal(JSON.stringify(imported),before);
 return {local_components:r.components.map(c=>c.refdes),unknown_local_pins:u.connections,unknown_full_pin_count:Object.keys(imported.components.U404.pins).length,full_graph_unchanged:true};
});
check("official_active_pin_fixture_is_a_view_not_full_IC",()=>{
 const input={nets:{MY_SIGNAL:{U1:["5"],R1:["1"]},GND:{U1:["1","10","20"],R1:["2"]},VCC:{U1:["2","11"]},OTHER_SIGNAL:{U1:["3"]}},components:{U1:{pins:{"1":"GND","2":"VCC","3":"OTHER_SIGNAL","5":"MY_SIGNAL","10":"GND","11":"VCC","20":"GND"},mpn:"STM32F411"},R1:{pins:{"1":"MY_SIGNAL","2":"GND"},mpn:"10k"}}};
 const before=JSON.stringify(input),r=traverseCircuitFromNet("MY_SIGNAL",input.nets,input.components),u=r.components.find(c=>c.refdes==="U1");
 assert.deepEqual(u.connections.flatMap(c=>c.pins),["5"]);assert.equal(JSON.stringify(input),before);
 return {queried_IC_pins:u.connections.flatMap(c=>c.pins),stored_IC_pin_count:Object.keys(input.components.U1.pins).length,full_graph_unchanged:true};
});
function railFixture(rail,pinCount){
 const nets={ENTRY_SIGNAL:{R0:["1"],U1:["1"]},[rail]:{R0:["2"]}},components={U1:{pins:{"1":"ENTRY_SIGNAL"},mpn:"IC"},R0:{pins:{"1":"ENTRY_SIGNAL","2":rail},mpn:"10k"}};
 for(let i=1;i<=3;i++){const far="FAR_SIGNAL_"+i;nets[rail]["R"+i]=["1"];nets[far]={["R"+i]:["2"],["U"+(i+1)]:["1"]};components["R"+i]={pins:{"1":rail,"2":far},mpn:"10k"};components["U"+(i+1)]={pins:{"1":far},mpn:"FAR_IC"};}
 const filler=pinCount-Object.values(nets[rail]).flat().length;nets.GND={};
 for(let i=1;i<=filler;i++){nets[rail]["C"+i]=["1"];nets.GND["C"+i]=["2"];components["C"+i]={pins:{"1":rail,"2":"GND"},mpn:"100nF"};}
 return {nets,components};
}
check("official_41_pin_rail_fixture_bounds_expand_only_local_view",()=>{
 const rail="E610_MYSTERY_RAIL",input=railFixture(rail,41),before=JSON.stringify(input);
 const bounded=traverseCircuitFromNet("ENTRY_SIGNAL",input.nets,input.components);
 const expanded=traverseCircuitFromNet("ENTRY_SIGNAL",input.nets,input.components,{stopNetPinThreshold:1000});
 const direct=traverseCircuitFromNet(rail,input.nets,input.components);
 assert.deepEqual(bounded.components.map(c=>c.refdes).sort(),["R0","U1"]);assert.ok(!bounded.visited_nets.includes("FAR_SIGNAL_1"));assert.ok(expanded.visited_nets.includes("FAR_SIGNAL_1"));assert.ok(direct.visited_nets.includes("FAR_SIGNAL_1"));assert.equal(JSON.stringify(input),before);
 return {stored_components:Object.keys(input.components).length,default_components:bounded.components.length,raised_threshold_components:expanded.components.length,direct_rail_components:direct.components.length,full_graph_unchanged:true};
});
check("missing_identity_survives_list_and_circuit_aggregation",()=>{
 const components={U1:{pins:{"1":"A"}},U2:{pins:{"1":"A"}}};
 const groups=groupComponentsByMpn(Object.entries(components),true);
 assert.deepEqual(groups.flatMap(g=>g.refdes).sort(),["U1","U2"]);assert.equal(groups.length,2);
 const connection={refdes:"X1",connections:[{net:"NET1",pins:["1"]},{net:"NET2",pins:["2"]}]};
 const r=aggregateCircuitByMpn([connection]);assert.deepEqual(r[0].refdes,["X1"]);assert.deepEqual(r[0].connections,connection.connections);
 return {list_groups:groups.length,retained_refdes:groups.flatMap(g=>g.refdes),unidentified_circuit_connections:r[0].connections,adds_internal_MPN_note:!!r[0].notes};
});
const pin=index=>({pinIndex:index,pointX:0,pointY:0,netId:0,symbolDisplayProps:[]});
const inst=(dbId,sectionIndex)=>({pkgName:"DUAL_OPAMP.Normal",dbId,reference:"U1",sourcePackage:"DUAL_OPAMP",partValueIdx:0,prefixProperties:[],locX:0,locY:0,symbolDisplayProps:[],t0x10s:[pin(1),pin(2)],sectionIndex});
const page=placedInstances=>({name:"page",netTable:new Map(),wires:[],placedInstances,drawnInstances:[],ports:[],globals:[],offPageConnectors:[]});
const pmd=(map={})=>({pinMaps:new Map(Object.entries(map)),cachePinMaps:new Map(),deviceUnitRefs:new Map(),pinIgnores:new Map(),cachePinIgnores:new Map()});
check("multiunit_across_pages_merge_one_refdes_preserve_all_resolved_pins",()=>{
 const pages=[page([inst(300,0)]),page([inst(301,1)])],pinNets=new Map([["1","IN_A"],["2","OUT_A"],["3","IN_B"],["4","OUT_B"]]),componentPins=new Map([["U1",pinNets]]);
 const indices=buildDeviceIndexMap(pages),r=buildComponents(pages,componentPins,[],new Map(),pmd(),indices);
 assert.equal(indices.get(300),0);assert.equal(indices.get(301),1);assert.deepEqual(Object.keys(r),["U1"]);assert.deepEqual(r.U1.pins,Object.fromEntries(pinNets));assert.equal(r.U1.mpn,undefined);
 return {stored_refdes:Object.keys(r),stored_pins:r.U1.pins,section_indices:[indices.get(300),indices.get(301)],test_boundary:"starts after physical pin connectivity is already resolved"};
});
check("physical_mapping_fallback_is_symbol_order_not_proven_package_number",()=>{
 const i=inst(1,0),mapped=resolvePinNumber(pin(2),i,pmd({DUAL_OPAMP:["7","9"]})),fallback=resolvePinNumber(pin(2),i,pmd());
 assert.equal(mapped,"9");assert.equal(fallback,"2");
 return {with_physical_mapping:mapped,without_mapping:fallback,limitation:"fallback is logical symbol record order, physical correctness not established"};
});
console.log(JSON.stringify({passed,failed:0,scope:"production-module probes with official fixtures plus custom edge cases; no full Vitest suite or whole DSN import"}));

