# 网表事实与模型视图：现成项目探针

本包保存 2026-10-18 对 DAT_PureAgent 设计讨论的可复现研究证据。重点验证：身份信息缺失是否破坏连接、紧凑视图能否展开完整端点、未确认候选在输出时是否被隐藏。

**这不是整板电气检查成绩。** 这里实际调用固定版本的生产函数、查询工具及真实数据模型，输入为小型合成案例。没有调用 LLM、没有完整数据手册语义审查，也没有测量真实电路的准确率、漏报率或运行效率。“通过”表示探针成功验证预期行为，其中包括成功复现的问题。

## 已验证的结果

| 项目 | 案例组 | 实际结果与边界 |
| --- | ---: | --- |
| dsh-enet-review | 3 | 移除 MPN、封装字段后，3 个器件、8 个引脚连接不变，两项 FDA 拓扑候选仍产生。86 端点网络可全部查询，新增支路及其另一端保留。45 个 EN 悬空待查候选默认仅输出 40 个证据端点；`detail=true` 仍只有 40 个，直接查 U45 可找回。只筛 P1 时仍显示 P2 计数，并限定为“该筛选条件下没有发现项”。这些 EN 候选未查手册，不能视作 45 个真实错误。 |
| universal-netlist | 10 | 缺 MPN 的器件、缺组件元数据的连接占位对象及已声明未连接脚可保留。局部查询可以只呈现 IC 的部分引脚，41 端点共享网络会限制默认遍历；完整图不变，扩大阈值或直接查网络可展开。跨单元合并保留预先解析的连接。物理脚映射缺失时回退到符号记录序号，不能据此证明真实封装脚号。文件投影会丢掉 `footprint`，保留连接；文件元数据要求 `netlistHash`，解析后的对象只含 `components` 与 `nets`。 |
| Pinscope | 4 | 删除可选封装只减少封装文本；缺 MPN 的局部上下文保留连接。8 个去耦电容可在默认上下文中汇总，再用查询完整展开两端；信号上的额外负载和另一端仍可见。反例也成立：不完整 datasheet pintable 会使一个已声明未连接脚从上下文消失；fallback pintable 把空网络脚显示为 `[connected]`；不存在的脚号查询与未连接状态混淆。仅验证函数边界，未执行主任务调度或最终报告过滤。 |

原始 stdout、结构化观察、退出码、运行版本及限制见 [results.json](results.json)。不以进程耗时推算电路检查性能。

## 对 PureAgent 的直接启发

1. 完整可查询的器件—物理脚—网络事实应独立保存；供模型阅读的摘要和局部视图允许更小。视图裁剪不能修改事实，额外支路应有可展开的查询入口。
2. 缺少与当前断言无关的 MPN、封装信息，可以局部继续。这个结论不等于所有缺身份器件都会被现成项目的主调度器审查，也不意味着可以猜物理脚号或额定值。
3. 查询接口须区分“已声明未连接”“未声明的脚号”“只因局部视图未显示”。不能直接把便于阅读的字符串当作精确物理脚事实。
4. 最终只向工程师报告已经确认的电气错误，同时应在内部保留待查对象和可恢复证据。DSH 的 40/45 案例证明证据呈现可以隐藏尾项；这里没有证明任何项目最终漏报了真实电气错误。
5. 这些观察支持采用紧凑视图与按需展开，但未证明去掉哈希能带来某个比例的提速，也不能把查询框架当作完整 checker。

## 重跑

在本目录执行。下载器只使用 Python 标准库，下载清单中的固定提交文件到 `_vendor/`，不会安装依赖或执行下载内容。

```bash
python fetch_sources.py
python run_probes.py
```

运行结果默认写入 `_work/latest-results.json`。如需明确更新归档基线：

```bash
python run_probes.py --output results.json
```

本次实际环境为 Node **24.19.0**、Python **3.12.14**、Pydantic **2.14.0**。UN 的 9 个 TypeScript 模块由 Node 内置 `stripTypeScriptTypes` 去除类型，不做源码逻辑改写，不安装 npm 包。Pinscope 探针需要 Pydantic；可在独立 Python 环境中安装本次使用的 `pydantic==2.14.0`。没有安装模型 SDK、启动应用服务器或调用模型 API。

也可分别执行：

```bash
node probes/dsh_probe.mjs
node probes/compile_unet.mjs
node probes/unet_probe.mjs
python probes/pinscope_probe.py
```

Pinscope 探针从原始 `validate.py` 的 AST 提取 `build_component_context` 及原摘要阈值，调用原查询模块与 Pydantic 模型，以避开模型客户端初始化。它不是完整应用执行器。DSH 探针生成临时 `.enet` 输入，并直接调用 `parseEnet`、`audit` 和 `TOOLS.execute`。UN 探针调用解析、遍历、聚合、跨单元合并及脚号解析函数；跨单元案例从已解析的物理连接开始，没有测试整份 DSN 导出与导入。

本次归档时，`fetch_sources.py` 已成功下载清单中的 27 个文件；以上 4 个执行步骤均退出 0。这里存储的结果来自归档脚本的实际复跑。stdout 中只规范化本地路径，避免包含个人工作目录。

## 固定来源与许可

完整版本、最小下载文件集与入口见 [sources.json](sources.json)。本包提交研究说明、观察和探针；第三方源码与生成文件均留在被忽略的 `_vendor/`、`_work/`，不随包提交。

| 项目与版本 | 许可来源 | 关键实现 |
| --- | --- | --- |
| [lishuo4096/dsh-enet-review @ 8668406](https://github.com/lishuo4096/dsh-enet-review/tree/8668406fd8a91635778561607a5367be77e58b6b) | 未确认：固定提交的完整树没有 LICENSE/COPYING/NOTICE，`package.json` 未声明 license。这里不重新分发其源码。 | [解析与完整索引](https://github.com/lishuo4096/dsh-enet-review/blob/8668406fd8a91635778561607a5367be77e58b6b/lib/netlist.js)、[候选规则及默认证据限制](https://github.com/lishuo4096/dsh-enet-review/blob/8668406fd8a91635778561607a5367be77e58b6b/lib/rules.js#L191-L214)、[工具呈现](https://github.com/lishuo4096/dsh-enet-review/blob/8668406fd8a91635778561607a5367be77e58b6b/lib/tools.js#L295-L325) |
| [IntelligentElectron/universal-netlist @ 8c2c851](https://github.com/IntelligentElectron/universal-netlist/tree/8c2c851fa81685fb43be4edfa8645765f4ca4628) | [Apache-2.0 LICENSE](https://github.com/IntelligentElectron/universal-netlist/blob/8c2c851fa81685fb43be4edfa8645765f4ca4628/LICENSE)；[NOTICE](https://github.com/IntelligentElectron/universal-netlist/blob/8c2c851fa81685fb43be4edfa8645765f4ca4628/NOTICE) 另列 OpenOrCadParser 的 MIT 来源。下载保留两份声明。 | [文件读写](https://github.com/IntelligentElectron/universal-netlist/blob/8c2c851fa81685fb43be4edfa8645765f4ca4628/src/parsers/universal/reader.ts)、[局部遍历](https://github.com/IntelligentElectron/universal-netlist/blob/8c2c851fa81685fb43be4edfa8645765f4ca4628/src/circuit-traversal.ts)、[脚号映射](https://github.com/IntelligentElectron/universal-netlist/blob/8c2c851fa81685fb43be4edfa8645765f4ca4628/src/parsers/cadence/dsn/pin-resolver.ts) |
| [Faradworks/Pinscope @ b26ad35](https://github.com/Faradworks/Pinscope/tree/b26ad3509f4a19878fb7050aad3da9eacba8d914) | [GNU AGPL version 3 LICENSE](https://github.com/Faradworks/Pinscope/blob/b26ad3509f4a19878fb7050aad3da9eacba8d914/LICENSE)。本记录不推断 only/or-later。 | [上下文生成](https://github.com/Faradworks/Pinscope/blob/b26ad3509f4a19878fb7050aad3da9eacba8d914/backend/pinscopex/validate.py#L424)、[完整查询与状态呈现](https://github.com/Faradworks/Pinscope/blob/b26ad3509f4a19878fb7050aad3da9eacba8d914/backend/pinscopex/validation_tools.py) |

这些是指定公开版本上的有限工程证据，不代表全部市场方案或行业共识，也不引入 DAT_check_2 的完整器件模型库。
