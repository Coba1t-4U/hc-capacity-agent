"""cli.py — 命令行入口（含引擎通道诊断）。

用法：
  python -m hc_agent.cli channels          # 查看引擎通道状态
  python -m hc_agent.cli run --input-dir X --output-dir Y
  python -m hc_agent.cli convert --source case.xlsx --out inputs/
  python -m hc_agent.cli diagnostics --run-dir runs/RUN-001
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional, Sequence

from hc_agent.errors import EXIT_SUCCESS, EXIT_INPUT_BLOCKED


def cmd_channels(args: argparse.Namespace) -> int:
    """列出引擎通道状态（可用性、隔离性、精度、复算容差下限）。"""
    from hc_agent.engines import engine_channels
    print(engine_channels.describe_channels())
    ch, info = engine_channels.select_channel(
        preferred=args.prefer, fallback=not args.no_fallback)
    print()
    if ch is None:
        print(f"未选出可用通道：{info.get('reason')}")
        for r in info.get("rejected") or []:
            print(f"  - {r['channel']}: {r['why']} {r.get('detail','')}")
        return EXIT_INPUT_BLOCKED
    print(f"当前将使用通道：{ch}")
    if info.get("fell_back"):
        print(f"  （首选 {info.get('requested')} 不可用，已降级）")
    cap = info.get("capability") or {}
    print(f"  隔离={cap.get('killable_on_hang')} "
          f"有效位={cap.get('significant_digits') or '双精度'} "
          f"复算容差下限={cap.get('min_reliable_tolerance')}")
    return EXIT_SUCCESS


def cmd_convert(args: argparse.Namespace) -> int:
    """把外部算例（xlsx/csv/json/MATPOWER）转换为本工程输入包。"""
    from hc_agent.io.converters import convert_case
    from hc_agent.io.schema import IssueCollector, write_csv

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    col = IssueCollector()
    bundle, report = convert_case(args.source, collector=col)

    # 落盘：转换说明 + 规范化后的清单
    (out / "conversion.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8")
    write_csv(out / "converted_buses.csv", bundle.buses)
    write_csv(out / "converted_lines.csv", bundle.lines)
    write_csv(out / "converted_transformers.csv", bundle.transformers)
    write_csv(out / "converted_loads.csv", bundle.loads)
    write_csv(out / "converted_sources.csv", bundle.sources)
    if col.issues:
        col.write(out / "conversion_issues.csv")

    print(json.dumps({
        "source": str(args.source),
        "source_format": report.source_format,
        "fidelity_status": report.to_dict()["fidelity_status"],
        "buses": len(bundle.buses),
        "lines": len(bundle.lines),
        "transformers": len(bundle.transformers),
        "loads": len(bundle.loads),
        "sources": len(bundle.sources),
        "blocking_issues": len(col.blocking),
        "outputs": str(out),
        "notes": report.notes,
        "warnings": report.warnings,
    }, ensure_ascii=False, indent=2))
    return EXIT_SUCCESS if not col.blocking else EXIT_INPUT_BLOCKED


def cmd_run(args: argparse.Namespace) -> int:
    """执行承载力评估主流程。"""
    from hc_agent.pipeline import main as pipeline_main
    argv = ["--input-dir", args.input_dir, "--output-dir", args.output_dir]
    if args.skip_verification:
        argv.append("--skip-verification")
    if args.max_slices:
        argv += ["--max-slices", str(args.max_slices)]
    return pipeline_main(argv)


def cmd_diagnostics(args: argparse.Namespace) -> int:
    """汇总一次运行的可诊断信息（看门狗、崩溃日志、最后遗言、引擎通道）。"""
    from hc_agent.runtime import crashlog, watchdog
    from hc_agent.runtime.state_store import StateStore

    run = Path(args.run_dir)
    out: dict = {"run_dir": str(run)}
    for key, fn in (("crashlog", lambda: crashlog.diagnostics()),
                    ("watchdog", lambda: watchdog.diagnostics())):
        try:
            out[key] = fn()
        except Exception as exc:
            # 诊断接口**自身报错不能掩盖被诊断的问题**，取不到就如实说
            out[key] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        out["last_stage"] = StateStore.last_stage(run / "state.json")
        out["state"] = StateStore.read(run / "state.json")
    except Exception as exc:
        out["last_stage"] = f"(读取失败: {exc})"
    for name in ("run_outcome.json", "checkpoints.json",
                 "config/engine_channels.json"):
        p = run / name
        if p.exists():
            try:
                out[name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                out[name] = "(解析失败)"
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return EXIT_SUCCESS


def cmd_channels_json(args: argparse.Namespace) -> int:
    from hc_agent.engines import engine_channels
    print(json.dumps({k: v.to_dict()
                      for k, v in engine_channels.probe_all().items()},
                     ensure_ascii=False, indent=2))
    return EXIT_SUCCESS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hc_agent", description="配网承载力智能体Agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("channels", help="查看引擎通道状态")
    c.add_argument("--prefer", default=None, help="首选通道")
    c.add_argument("--no-fallback", action="store_true", help="禁用自动降级")
    c.set_defaults(func=cmd_channels)

    cj = sub.add_parser("channels-json", help="引擎通道状态（JSON）")
    cj.set_defaults(func=cmd_channels_json)

    cv = sub.add_parser("convert", help="转换外部算例格式")
    cv.add_argument("--source", required=True)
    cv.add_argument("--out", required=True)
    cv.set_defaults(func=cmd_convert)

    r = sub.add_parser("run", help="执行承载力评估")
    r.add_argument("--input-dir", required=True)
    r.add_argument("--output-dir", required=True)
    r.add_argument("--skip-verification", action="store_true")
    r.add_argument("--max-slices", type=int, default=None)
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("diagnostics", help="汇总运行诊断信息")
    d.add_argument("--run-dir", required=True)
    d.set_defaults(func=cmd_diagnostics)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        from hc_agent.errors import EXIT_INTERRUPTED
        print("\n已中断", file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
