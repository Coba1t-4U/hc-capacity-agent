# -*- coding: utf-8 -*-
"""自检：业务流八段闭环模块（business_flow.py）与它的三个出口。

被检查的三个出口：
  1. 直接调用 ``hc_agent.business_flow``；
  2. 问答通道 ``hc_agent.qa``（问"业务流"要能命中）；
  3. JSON 桥 ``hc_agent.bridge business_flow``（DSH 插件走这条）。

不测"业务是否真能做到"，只测"状态标注是否自洽、出口是否通"。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hc_agent import business_flow as BF          # noqa: E402
from hc_agent import qa                            # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def main() -> int:
    # ---- 1) 结构
    check("八段齐全", len(BF.STAGES) == 8, f"实际 {len(BF.STAGES)} 段")
    check("序号 1..8 连续", [s["no"] for s in BF.STAGES] == list(range(1, 9)))
    check("每段有 key/name/status", all(
        s.get("key") and s.get("name") and s.get("status") in BF.STATUS_LABELS
        for s in BF.STAGES))
    check("状态取值合法", all(s["status"] in ("implemented", "partial", "absent")
                              for s in BF.STAGES))
    check("每段都写了缺口", all(s.get("gaps") for s in BF.STAGES))
    check("未实现的段落 modules 为空",
          all(not s["modules"] for s in BF.STAGES if s["status"] == "absent"))

    # ---- 2) 声称"有代码"的模块必须真的存在
    missing = []
    for s in BF.STAGES:
        for m in s["modules"]:
            if not (ROOT / m).is_file():
                missing.append(f"{s['name']} -> {m}")
    check("模块路径都真实存在", not missing, "；".join(missing[:3]))

    # ---- 3) 汇总自洽
    st = BF.summary()
    total = sum(st["counts"].values())
    check("汇总计数等于 8", total == 8, f"合计 {total}")
    check("未实现清单与状态一致",
          len(st["absent"]) == st["counts"]["absent"])

    # ---- 4) 文本输出
    flow = BF.format_flow()
    check("format_flow 含全部段名", all(s["name"] in flow for s in BF.STAGES))
    check("format_flow 标注口径提醒", "不等于" in flow or "不等同" in flow)
    one = BF.format_stage(5)
    check("format_stage(5) 指向发布公示", "结果发布与公示" in one)
    check("format_stage 未知段有兜底", "没有这一段" in BF.format_stage(99))

    # ---- 5) 问答路由
    r = qa.Responder(qa.Facts())
    cases = [
        ("业务流是怎样的", "业务流"),
        ("第5步是什么", "结果发布与公示"),
        ("哪些环节还没做", "业务流"),
    ]
    for q, must in cases:
        a = r.answer(q)
        text = a.text if hasattr(a, "text") else str(a)
        check(f"问答命中：{q}", must in text, text.splitlines()[0][:40] if text else "(空)")

    # ---- 6) JSON 桥
    try:
        p = subprocess.run(
            [sys.executable, "-m", "hc_agent.bridge", "business_flow"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180)
        line = [ln for ln in p.stdout.splitlines() if ln.strip()][-1]
        payload = json.loads(line)
        check("桥命令 ok", payload.get("ok") is True, str(payload)[:120])
        check("桥返回 summary.total=8",
              (payload.get("summary") or {}).get("total") == 8)
        check("桥返回 text 非空", bool((payload.get("text") or "").strip()))
    except Exception as exc:                       # noqa: BLE001
        check("桥命令 ok", False, f"{type(exc).__name__}: {exc}")

    # ---- 报告
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        mark = "OK  " if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    print(f"\n结果：{passed} 通过 / {len(RESULTS) - passed} 失败 / {len(RESULTS)} 总计")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
