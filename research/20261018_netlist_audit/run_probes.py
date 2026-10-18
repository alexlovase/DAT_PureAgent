#!/usr/bin/env python3
"""Run the small archived probes without installing or calling model services."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def run(name, command):
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    stdout = completed.stdout.replace(str(ROOT), "<audit_root>")
    stderr = completed.stderr.replace(str(ROOT), "<audit_root>")
    records = []
    try:
        records.append(json.loads(stdout))
    except json.JSONDecodeError:
        for line in stdout.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return {"name": name, "command": [Path(command[0]).name, *command[1:]],
            "exit_code": completed.returncode, "records": records,
            "stdout": stdout, "stderr": stderr}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node", help="Node executable (tested: 24.19.0)")
    parser.add_argument("--output", default="_work/latest-results.json")
    args = parser.parse_args()
    try:
        pydantic_version = version("pydantic")
    except PackageNotFoundError:
        pydantic_version = None
    runs = []
    for name, command in [
        ("dsh", [args.node, "probes/dsh_probe.mjs"]),
        ("compile_unet", [args.node, "probes/compile_unet.mjs"]),
        ("unet", [args.node, "probes/unet_probe.mjs"]),
        ("pinscope", [sys.executable, "probes/pinscope_probe.py"]),
    ]:
        result = run(name, command)
        runs.append(result)
        print(f"{name}: exit {result['exit_code']}")
        if result["exit_code"]:
            print(result["stderr"], file=sys.stderr)
    manifest = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    result = {
        "schema_version": 1,
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "固定上游函数与真实工具入口的合成案例探针；3 个 DSH、10 个 UN、4 个 Pinscope 案例组。通过表示符合探针预期，包括复现出的缺陷，并非产品检查通过。",
        "runtime": {"python": platform.python_version(),
                    "node": subprocess.check_output([args.node, "--version"], text=True).strip(),
                    "pydantic": pydantic_version},
        "sources": [{"id": p["id"], "repository": p["repository"], "commit": p["commit"]}
                    for p in manifest["projects"]],
        "limitations": [
            "没有付费或免费 LLM 调用，没有完整数据手册语义审查，没有真实整板端到端准确率、漏报率或性能测量。",
            "DSH 的 EN 悬空输入仅产生待查手册候选，不能将 45 个候选算作 45 个真实电气错误；该项目未提供在本探针中可运行的最终 LLM 复核流程。",
            "UN 测试直接调用生产模块，不运行完整 Vitest 套件、MCP 服务或完整 DSN 导入；跨单元合并案例从已解析的物理连接开始。",
            "Pinscope 采用真实 Pydantic 模型及原查询模块；通过 AST 提取 build_component_context 与原摘要阈值，避免导入模型客户端。未测试主调度器如何选择缺 MPN 的器件，也未执行最终报告过滤。",
            "输出只将运行目录替换为 <audit_root>、将 DSH 示例路径变为相对路径；未改写上游诊断或测量值。"
        ],
        "runs": runs,
        "all_probe_processes_exit_zero": all(r["exit_code"] == 0 for r in runs)
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Results: {args.output}")
    return 0 if result["all_probe_processes_exit_zero"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
