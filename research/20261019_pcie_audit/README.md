# PCIe 真实输入与灵活性审计复现

结论见[主研究记录](../../docs/research/20261019_pcie_flexibility.md)。本目录只保存来源、脚本、简要验证和局部审计证据；第三方 DAT/PDF/DSN 放在忽略的缓存中，避免重复提交。

## 输入与用途

- `opencellular-breakout`：原设计仓库固定版本的原生三 DAT，CC-BY-4.0。
- `j401-fixture`：fixture 提供的真实 J401 Cadence 导出，匹配原仓 DSN/PDF，Apache-2.0；未独立重导，不称为原仓原生 DAT。
- 原图用于本次研究验证 DNP 等导出信息丢失，不是 PureAgent 新增的强制产品输入。

固定路径、版本、许可和资产校验见 [sources.json](sources.json)。厂家资料及实际读取页见 [electrical_audit.sources.json](electrical_audit.sources.json)。哈希仅在研究获取记录中使用，不追加到默认 Agent 查询视图。

## 从仓库根目录运行

首次获取固定输入（需要 Python 3 和 curl，以及访问公开来源的网络）：

```bash
python research/20261019_pcie_audit/fetch_sources.py
```

默认缓存为 `.cache/pcie-inputs`。`--offline` 只校验已有缓存；`--cache-dir` 可指定已有目录。`--with-reference-docs` 额外获取本轮使用的 NVIDIA 文档，检查版本对应的文件校验值，不以最新文档替换研究输入。

独立扫描先从原始文本构造期望集合，再对照生产解析器及所有公开查询分页：

```bash
python research/20261019_pcie_audit/verify_raw.py --input-root .cache/pcie-inputs --compare
```

重放已经人工/Agent 核对的 28 张网络与 4 条跨电容路径，再通过公开工具会话复现这些事实：

```bash
python research/20261019_pcie_audit/electrical_audit.py --sample-root .cache/pcie-inputs --output .cache/pcie-electrical-replay.json
python research/20261019_pcie_audit/replay_tool_checks.py
```

重放独立灵活性探针、生产工具回归，以及以前 Clay 保存的核算和报告：

```bash
python research/20261019_pcie_audit/audit_findings.probes.py --output .cache/pcie-flexibility-replay.json
python -m unittest discover -s tests -v
python scripts/replay_clay_r6.py
```

## 保存的证据

| 文件 | 验证什么 |
|---|---|
| `raw_inventory.json` | 两板独立事实统计、各 22 项全量比较和完整分页验证 |
| `audit_findings.before.json` / `audit_findings.after.json` | 17 个受控探针的修改前后行为；合成修改不是原板缺陷 |
| `audit_findings.nc_regression.json` | 宽容化过程中发现的 NC 隔离回归及修复 |
| `audit_findings.pcie_facts.json` | 灵活性审计方独立核对两板全部端点 |
| `audit_findings.md` | 过严条件、已改行为、仍未支持的范围 |
| `electrical_audit.json` / `electrical_audit.md` | 原始连接、资料页、人工判断和仍未知条件 |
| `tool_replay.json` | 公开 ToolSession 对独立期望的实际查询结果 |
| `verification.json` | 本轮最终验证摘要与执行边界 |

完整原始扫描清单可由 `verify_raw.py --full-output` 生成，默认保存在 `.cache`，无需在报告中重复全部层次路径。

上述脚本为确定性重放，不执行 LLM 调查，也不重新证明资料适用性。两板解析成功、选定范围零确认电气错误，都不等于全板 PCIe 检查通过。没有新增完整模型库、自动信号路径求解器或独立模型 API 运行底座。
