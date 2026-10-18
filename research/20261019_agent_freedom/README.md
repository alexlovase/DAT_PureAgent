# 程序与模型边界：具体反例

研究基线为 `7a743e0cab5dedf31230a0c2bc316aa191a0e23d`。本目录使用已有 Clay 调查/复核记录和合成算术核实工具行为；没有重新调查原板，也没有新增电气错误结论。

## 本轮观察

| 反例 | 修改前 | 修改后 |
|---|---|---|
| 仅删除原 Clay 确认候选的整改建议，证据不变 | 确认错误从 1 条变为 0 条 | 保留 1 条确认错误，缺少的建议不编造 |
| 同一明确假设下先算电流，再算功耗 | 两步数值可算，但返回“trial result must not be propagated” | 数值和条件结果分组不变，说明允许携带同一条件继续分析 |
| 调用方仅声明 1/1 和 full_board | 返回 completed_review | 行为未改；研究明确其为调用方声明，不能作为电气覆盖证明 |

条件算术使用合成的 5V/1kΩ 情形，结果 5mA、25mW。调用者明确携带同一条件；这不证明工具自动追踪条件。报告探针只修改内存副本，不改写历史研究。

保存结果为 [before.json](before.json)、[after.json](after.json)；最终验证摘要见 [verification.json](verification.json)。

## 复现

从仓库根目录运行：

```bash
python research/20261019_agent_freedom/probe_boundaries.py
python -m unittest discover -s tests -p test_calculations_report.py -v
python scripts/replay_clay_r6.py
```

探针默认写入忽略的缓存。重现修改前行为需要使用上述基线的生产模块；不能用修改后的模块重新生成 before.json 并称其为修改前证据。

本次执行 12 项计算与报告针对性测试；原有 9 项算术回放及 3 份报告保持一致。未改变 DAT 解析器，未声称重跑了全部板卡实验或完成端到端模型评估。

当前开发方向见 [PureAgent_Design_Principles.md](../../docs/PureAgent_Design_Principles.md)，独立批判见 [20261019_agent_freedom_independent.md](../../docs/research/20261019_agent_freedom_independent.md)。
