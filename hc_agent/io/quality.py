"""quality.py — 数据质量治理产物的标准落盘与读取。

## 为什么要单独一层

本工程早就有校验逻辑（`io/schema.py` 的 `IssueCollector`，十列字段
`issue_id/file/sheet/row/field/object_id/severity/code/message/blocking`
与开发文档 9.5 完全一致），但产物**落点不固定**：
有的运行有 `quality_issues.csv`，有的没有（FD002 就没有）。

结果是"这个结论的数据质量到底过不过关"没法从产物里直接回答。

本模块做两件事：
1. **统一落点**：固定写到 `<run>/quality_issues.csv`；
2. **统一读取**：给出按严重度/阻断的统计，供问答、看板、报告使用。

## 不重写校验逻辑

校验规则仍在 `io/schema.py` / `io/validation.py`。这里只做搬运与统计，
避免两套规则各写一遍、慢慢分叉。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 与开发文档 9.5 一致的列顺序
COLUMNS = ["issue_id", "file", "sheet", "row", "field", "object_id",
           "severity", "code", "message", "blocking"]

#: 严重度排序（越大越严重）
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "fatal": 3}

FILENAME = "quality_issues.csv"


def path_for(run_dir) -> Path:
    return Path(str(run_dir)) / FILENAME


def write(run_dir, collector) -> str:
    """把 `IssueCollector` 写到标准位置，返回文件路径。

    collector 为空也要写表头 —— "没有质量问题"本身是一个结论，
    文件不存在和有文件但零行，含义不同（前者是"没检查过"）。
    """
    root = Path(str(run_dir))
    root.mkdir(parents=True, exist_ok=True)
    out = path_for(root)
    issues = list(getattr(collector, "issues", []) or [])
    rows = [i.to_row(n + 1) for n, i in enumerate(issues)]
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLUMNS})
    return str(out)


def read(run_dir) -> Dict[str, Any]:
    """读取质量问题清单并统计。

    返回 `{"exists", "rows", "counts", "blocking", "by_severity",
           "top", "ok"}`。
    `exists=False` 表示**没有做过质量检查**，与"检查通过"不是一回事。
    """
    p = path_for(run_dir)
    out: Dict[str, Any] = {
        "exists": False, "rows": [], "count": 0, "blocking": 0,
        "by_severity": {}, "by_code": {}, "top": [],
        "ok": True, "note": "",
    }
    if not p.is_file():
        out["ok"] = False
        out["note"] = "这次运行没有质量检查产物（不是\"通过\"，是没查过）"
        return out
    out["exists"] = True
    try:
        with open(p, "r", encoding="utf-8-sig", newline="") as fh:
            rows = [dict(r) for r in csv.DictReader(fh)]
    except Exception as exc:
        out["ok"] = False
        out["note"] = f"读不出来：{exc}"
        return out
    out["rows"] = rows
    out["count"] = len(rows)
    by_sev: Dict[str, int] = {}
    by_code: Dict[str, int] = {}
    blocking = 0
    for r in rows:
        sev = str(r.get("severity") or "").lower()
        by_sev[sev] = by_sev.get(sev, 0) + 1
        code = str(r.get("code") or "")
        if code:
            by_code[code] = by_code.get(code, 0) + 1
        if str(r.get("blocking") or "").lower() in ("true", "1", "yes"):
            blocking += 1
    out["by_severity"] = by_sev
    out["by_code"] = by_code
    out["blocking"] = blocking
    # 最严重的若干条（先按严重度，再按阻断）
    rows_sorted = sorted(
        rows,
        key=lambda r: (-SEVERITY_ORDER.get(str(r.get("severity") or "").lower(), -1),
                       str(r.get("blocking") or "").lower() != "true"))
    out["top"] = rows_sorted[:10]
    out["ok"] = blocking == 0
    if blocking:
        out["note"] = f"存在 {blocking} 项**阻断**缺陷，输入未通过质量门禁"
    elif rows:
        out["note"] = (f"{len(rows)} 项非阻断提示"
                       f"（{'、'.join(f'{k} {v}' for k, v in by_sev.items())}）")
    else:
        out["note"] = "没有质量问题"
    return out


def format_report(res: Dict[str, Any]) -> str:
    """排成可读文本（问答直接用）。"""
    if not res.get("exists"):
        return res.get("note") or "没有质量检查产物"
    lines = []
    if res.get("blocking"):
        lines.append(f"❌ **数据质量未通过**：{res['blocking']} 项阻断缺陷")
    elif res.get("rows"):
        lines.append(f"⚠️ 数据质量检查有 {res['count']} 项提示，无阻断")
    else:
        lines.append("✅ 数据质量检查通过：没有发现问题")
    lines.append("")
    if res.get("by_severity"):
        lines.append("· 按严重度：" + "、".join(
            f"{k} {v}" for k, v in sorted(
                res["by_severity"].items(),
                key=lambda x: -SEVERITY_ORDER.get(x[0], -1))))
    if res.get("by_code"):
        top = sorted(res["by_code"].items(), key=lambda x: -x[1])[:5]
        lines.append("· 主要问题类型：" + "、".join(f"{k}×{v}" for k, v in top))
    rows = res.get("top") or []
    if rows:
        lines.append("")
        lines.append("**最需要注意的几条**")
        for r in rows[:6]:
            flag = "🚫" if str(r.get("blocking")).lower() == "true" else "·"
            loc = f"{r.get('file') or '—'}"
            if r.get("sheet"):
                loc += f"/{r['sheet']}"
            if r.get("row"):
                loc += f" 第{r['row']}行"
            lines.append(f"{flag} {r.get('severity')} `{r.get('code')}` "
                         f"{loc}：{r.get('message')}")
    return "\n".join(lines)
