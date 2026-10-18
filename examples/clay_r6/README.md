# Clay R6：真实原生 DAT 实验输入

输入来自 [ah-cog/Clay 的 R6 硬件](https://github.com/ah-cog/Clay/tree/31a84237d09eb93affb9b2587ff1d5b9a2b8c6a6/Circuit/R6)。`clay-r6/` 中的三份 DAT 与固定上游逐字节相同；附带原仓库 MIT 许可证。没有经过 KiCad 或其他格式转换，也没有改动原设计。

资料来源与接线来源分开记录：

- [source.json](source.json)：固定设计版本、原文件 URL、原理图和板文件出处。
- [datasheets.json](datasheets.json)：11 份对应资料的来源、型号适用范围、相关页码及下载验证。6 份来自厂家站点，5 份是板卡仓库随附的厂家原版 PDF 镜像。
- `clay-r6/LICENSE` 仅对应开源板卡输入；厂家 PDF 的权利属于各自权利人，不随本仓库重新分发。

## 从仓库根目录运行

Python 3.12，PDF 工具需要 PyMuPDF；资料下载检查还需要 `curl` 和 Poppler 的 `pdfinfo`。本次验证的 PyMuPDF 为 1.26.6。

```bash
python -m pip install -r requirements.txt
python examples/clay_r6/fetch_real_board.py
python -m unittest discover -s tests -v
```

下载默认保存在本目录下被忽略的 `.cache/`，会拒绝 HTML、无法打开的 PDF 和已变化的文档内容。资料下载失败不会抹掉已获取文件；具体调查可继续使用可用的资料。厂家替换文件后，应重新核对相关页与适用条件，再明确更新资料绑定。校验摘要仅在获取清单内部使用，不进入 Agent 查询或电气错误报告。

启动一次长期工具会话，让实际 Agent 多次查询，复用同一个图与 PDF 句柄：

```bash
python -m pureagent \
  --dat examples/clay_r6/clay-r6 \
  --pdf-dir examples/clay_r6/.cache/datasheets \
  --manifest examples/clay_r6/datasheets.json \
  --trace .cache/my-investigation.jsonl \
  serve
```

逐行输入 JSON；每行返回一个 JSON 结果。以下是查询示例，不预设电气结论：

```json
{"tool":"overview"}
{"tool":"inventory","args":{"offset":0,"limit":40}}
{"tool":"component","args":{"ref":"U6"}}
{"tool":"pin","args":{"ref":"U6","number":"7","detail":true}}
{"tool":"net","args":{"query":"UVLO","limit":40}}
{"tool":"boundary","args":{"refs":["U6","L5","C29","C30","C31","C32"]}}
{"tool":"datasheets","args":{"ref":"U6"}}
{"tool":"pdf_search","args":{"file":"TPS61201.pdf","query":"UVLO"}}
{"tool":"pdf_page","args":{"file":"TPS61201.pdf","page_number":4}}
{"tool":"pdf_render","args":{"file":"TPS61201.pdf","page_number":4,"output_path":".cache/tps-page4.png"}}
{"tool":"stats"}
```

`query '<JSON>'` 可代替 `serve` 做单次查询。单次 CLI 会新建进程并重新加载；需要复用时使用 `serve` 或 Python 的 `ToolSession`/`DatGraph`。

## 图工具的含义

真实统计为 133 个器件、548 个物理脚、164 个普通网络及 537 个普通端点；另有 9 个 `NC` 汇总端点和 2 个 `NC_PINS` 声明，合计 11 个未连接脚。原始文件中的 165 个 `NET_NAME` 包含专用 `NC` 分组，不能直接等同于 165 个电气网络。

Cadence《OrCAD Capture User Guide》16.6（October 2012，[公开镜像](https://www.i-t.com/wp-content/uploads/2019/05/cap_ug.pdf)）第 415 页说明 `NC` 汇总语义，第 474 页定义 `NC_PINS`。这些格式依据与本次三 DAT 对应。

| 工具 | 数据与边界 |
|---|---|
| `inventory` | 完整实例清单，含普通无源和未知对象。按 `next_offset` 翻到末页。 |
| `component` / `pin` | 物理脚号与库脚名分开；未声明脚号与明确未连接分开。默认不带封装、文件摘要、层次路径等内部字段。 |
| `net` | 完整网络成员可分页恢复；长名/全路径作为定位入口。单成员网仍是实际命名网，不能因为有网名就认定脚已得到偏置。 |
| `boundary` | 提议局部电路的完整外部端点。它帮助识别遗漏支路；不会自动把这些端点解释成零负载或理想边界。 |
| `source` | 按 `filename/start_line/end_line` 回读原始 DAT。 |
| `pdf_search/page/render` | 字面文本搜索、完整原页与页面图像；页码从 1 开始。表格/脚注应配合渲染检查；没有 OCR。 |
| `calculate` | 带来源和适用条件的标量算术；不自动确定器件模型、单位正确性或工作模式。条件结果不得作为已知板上电压向下传播。 |

`connected` 表示该脚属于一个普通网。是否存在有效驱动、回路或偏置，仍要查看网络全体成员与资料。没有节点记录又没有明确 NC 声明的已知脚返回 `connection_unspecified`，不伪装成未连接。

本适配器目前验证了这份单 section 方言。多 section、复合 `PIN_NUMBER`、`POWER_PINS` 等未验证格式会明确停止接入，避免给出错误物理映射；不能据此声称已支持所有 OrCAD 导出文件。

## 受控变体与复跑边界

```bash
python examples/clay_r6/make_variants.py --output .cache/variants
```

这只产生测试副本，原板不变。变体 B 植入一个接线改动，变体 C 在同样接线基础上移除无关封装文本。实际调查先只读取相应输入，协调方在调查后对照真值。原板发现和植入问题在实验记录中分别标明。

本仓库记录的调查和复核由本次会话的真实 Agent 使用这些工具完成。重算已保存表达式、重建已保存确认报告属于确定性回放；它不会自动重跑 LLM，也不是独立 Deep Agents 应用已经部署。

复跑已记录的标量算术和报告呈现：

```bash
python scripts/replay_clay_r6.py
```

输出位于 `.cache/replay/`。该命令不会调用模型，不重新证明手册语义，也不改写已提交的调查/复核基线。
