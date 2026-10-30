"""bridge.py — 给外部宿主（如 DeepSeek Harness 插件）用的 JSON 桥。

**为什么需要它**：DSH 插件是 TypeScript 进程，本智能体的计算核心是 Python。
两者之间用「子进程 + 单次 JSON」通信，比让 TS 直接理解业务逻辑稳得多：

    python -m hc_agent.bridge <command> [--run DIR] ...  →  stdout 一行 JSON

约定：
  * **成功/失败都只输出一行 JSON**，形状固定 ``{"ok": bool, ...}``；
  * 失败不抛栈到 stdout（会污染 JSON），统一放进 ``error`` 字段；
  * 退出码 0=成功、1=业务失败、2=参数错误。

命令一览：:

    engine_status                        引擎注册表摘要（含是否可用）
    acceptance    --run DIR              9 条验收指标（含四态判定）
    crosscheck    --run DIR              pandapower 与 OpenDSS 交叉校核
    case_analyze  --case PATH            OpenDSS 算例单断面分析（含报告路径）
    capacity_run  --input DIR --output DIR   完整承载力评估（含容量结论）
    qa            --question TEXT        规则问答（不依赖大模型）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _emit(payload: Dict[str, Any], code: int = 0) -> int:
    """输出一行 JSON。**只允许这里往 stdout 写**。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
    return code


# ---------------------------------------------------------------- 命令实现
def _engine_status(_: argparse.Namespace) -> Dict[str, Any]:
    from hc_agent.engines import registry

    summary = registry.summary()
    return {
        "count": summary["count"],
        "names": summary["names"],
        "primary": summary["primary"],
        "crosscheck": summary["crosscheck"],
        "engines": [
            {
                "id": e.get("engine_id"),
                "label": e.get("engine_label"),
                "version": e.get("version"),
                "available": bool(e.get("available")),
                "scope": e.get("scope_label"),
                "role": e.get("role_label"),
            }
            for e in summary["entries"]
        ],
    }


def _acceptance(args: argparse.Namespace) -> Dict[str, Any]:
    from hc_agent import acceptance

    if not args.run:
        raise ValueError("acceptance 需要 --run <运行目录>")
    res = acceptance.single_run(Path(args.run))
    return {
        "summary": res.get("summary"),
        "items": [
            {"key": i.get("key"), "name": i.get("name"),
             "target": i.get("target"), "value": i.get("value"),
             "status": i.get("status"), "status_label": i.get("status_label"),
             "detail": i.get("detail")}
            for i in res.get("items", [])
        ],
        "text": acceptance.format_matrix(res),
    }


def _crosscheck(args: argparse.Namespace) -> Dict[str, Any]:
    from hc_agent.engines import pandapower_adapter

    if not args.run:
        raise ValueError("crosscheck 需要 --run <运行目录>")
    rep = pandapower_adapter.crosscheck(Path(args.run))
    return {
        "verdict": rep.get("verdict"),
        "summary": rep.get("summary"),
        "note": rep.get("note"),
        "points": rep.get("points"),
        "text": pandapower_adapter.format_crosscheck(rep),
    }


def _case_analyze(args: argparse.Namespace) -> Dict[str, Any]:
    from hc_agent.io import dss_report

    if not args.case:
        raise ValueError("case_analyze 需要 --case <算例路径或名称>")
    r = dss_report.analyze_and_report(args.case)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or "算例分析失败",
                "hint": r.get("hint")}
    s = r.get("summary") or {}
    case = s.get("case") or {}
    system = s.get("system") or {}
    return {
        "case": case.get("name"),
        "converged": (s.get("solve") or {}).get("converged"),
        "vmin_pu": system.get("vmin_pu"),
        "vmax_pu": system.get("vmax_pu"),
        "losses_kw": system.get("losses_kw"),
        "report_path": r.get("report_path"),
        "dir": r.get("dir"),
    }


def _capacity_run(args: argparse.Namespace) -> Dict[str, Any]:
    from hc_agent.pipeline import Pipeline

    if not args.input or not args.output:
        raise ValueError("capacity_run 需要 --input <输入包目录> --output <输出目录>")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    res = Pipeline(args.input, args.input, str(out)).run() or {}
    return {
        "exit_code": res.get("exit_code"),
        "capacity_physical_kw": res.get("capacity_physical_kw"),
        "capacity_managed_kw": res.get("capacity_managed_kw"),
        "level": res.get("level"),
        "binding_constraint": res.get("binding_constraint"),
        "run_dir": str(out),
    }


def _qa(args: argparse.Namespace) -> Dict[str, Any]:
    from hc_agent import qa

    if not args.question:
        raise ValueError("qa 需要 --question <问题>")
    facts = qa.Facts()
    ans = qa.Responder(facts).answer(args.question)
    d = ans.to_dict() if hasattr(ans, "to_dict") else {"text": str(ans)}
    return {"text": d.get("text", ""), "source": d.get("source", ""),
            "engine": "rule"}


COMMANDS = {
    "engine_status": _engine_status,
    "acceptance": _acceptance,
    "crosscheck": _crosscheck,
    "case_analyze": _case_analyze,
    "capacity_run": _capacity_run,
    "qa": _qa,
}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m hc_agent.bridge",
        description="配网承载力智能体的 JSON 桥（供 DSH 等外部宿主调用）")
    ap.add_argument("command", choices=sorted(COMMANDS))
    ap.add_argument("--run", default="", help="运行目录（acceptance/crosscheck）")
    ap.add_argument("--case", default="", help="算例路径或名称（case_analyze）")
    ap.add_argument("--input", default="", help="输入包目录（capacity_run）")
    ap.add_argument("--output", default="", help="输出目录（capacity_run）")
    ap.add_argument("--question", default="", help="问题文本（qa）")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    ap = build_parser()
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:                     # argparse 自己的退出码
        return int(exc.code or 2)

    fn = COMMANDS.get(args.command)
    if fn is None:
        return _emit({"ok": False, "error": f"未知命令：{args.command}"}, 2)
    try:
        payload = fn(args) or {}
        if payload.get("ok") is False:
            return _emit(payload, 1)
        payload.pop("ok", None)
        return _emit({"ok": True, "command": args.command, **payload})
    except Exception as exc:
        return _emit({"ok": False, "command": args.command,
                      "error": f"{type(exc).__name__}: {exc}"}, 1)


if __name__ == "__main__":
    raise SystemExit(main())
