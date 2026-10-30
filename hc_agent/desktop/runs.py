"""runs.py — 运行产物访问（桌面端用，**不经过任何 Web 服务**）。

从 `hc_agent/web.py` 抽出来的同一套判据，避免"两个界面各写一份目录扫描逻辑"
而结论不一致。两处都遵守：

  * 运行目录必须**至少有一个产物**才算一次运行（空目录、只有日志的不算）；
  * 支持**两种布局**：扁平 ``runs/ACCEPT/`` 与分组 ``runs/ACCEPT/FD001/``；
  * 所有按名字取路径的操作**必须限定在 runs/ 之内**（防目录穿越）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from hc_agent import paths

_PROJECT_ROOT = paths.bundle_root()

#: 非运行目录（基础设施），出现在列表里会误导用户
NON_RUN_DIRS = {"_logs", "_archive", "__pycache__"}

#: 判定"这是一次运行"的产物文件
RUN_MARKERS = ("run_outcome.json", "state.json",
               "results/run_summary.json", "checkpoints.json")

#: 运行明细里可读取的文件（相对运行目录）
DETAIL_FILES = (
    ("summary", "results/run_summary.json"),
    ("outcome", "run_outcome.json"),
    ("gate", "review/release_gate.json"),
    ("publication", "review/publication.json"),
    ("state", "state.json"),
    ("conversion", "results/conversion.json"),
    ("verification", "verification/verification.json"),
    ("manifest", "manifest.json"),
    # 报告清单：含报告文件路径、章节、哈希，供「打开计算报告」按钮定位
    ("report_manifest", "report/report_manifest.json"),
)

#: 「打开计算报告」时可选的报告文件（按优先级）。
#: docx 用系统默认程序打开；html 有就用 html（免装 Word）。
REPORT_CANDIDATES = (
    "report/report.docx",
    "report/report.html",
    "report/report.md",
    "报告.docx",
)


def runs_root() -> Path:
    # 用**可写**的 runs 根：安装路径可能只读（Program Files），那里建不出
    # 目录，任务会以 PermissionError 失败。paths 会在必要时退到临时目录。
    return paths.writable_runs_root()


def is_run_dir(p: Path) -> bool:
    if not p.is_dir() or p.name in NON_RUN_DIRS or p.name.startswith("."):
        return False
    return any((p / f).exists() for f in RUN_MARKERS)


def list_run_dirs() -> List[Path]:
    """列出运行目录，含一层分组嵌套。"""
    root = runs_root()
    if not root.exists():
        return []
    found: List[Path] = []
    for d in root.glob("*"):
        if d.name in NON_RUN_DIRS or d.name.startswith("."):
            continue
        if is_run_dir(d):
            found.append(d)
        elif d.is_dir():
            for sub in d.glob("*"):
                if is_run_dir(sub):
                    found.append(sub)
    return sorted(found, key=lambda d: d.stat().st_mtime, reverse=True)


def _read_json(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_state(run_dir: Path) -> Optional[dict]:
    return _read_json(Path(run_dir) / "state.json")


def read_outcome(run_dir: Path) -> Optional[dict]:
    return _read_json(Path(run_dir) / "run_outcome.json")


def list_runs() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in list_run_dirs()[:60]:
        item: Dict[str, Any] = {"run_id": d.name, "path": str(d)}
        oc = read_outcome(d)
        if oc:
            for k in ("exit_code", "finished_at", "capacity_physical_kw",
                      "capacity_managed_kw", "level", "binding_constraint"):
                if oc.get(k) is not None:
                    item[k] = oc[k]
        sm = _read_json(d / "results" / "run_summary.json")
        if sm:
            item.setdefault("task_id", sm.get("task_id"))
            for k in ("capacity_physical_kw", "capacity_managed_kw", "level",
                      "binding_constraint", "business_scene"):
                if item.get(k) is None and sm.get(k) is not None:
                    item[k] = sm.get(k)
        st = read_state(d)
        if st:
            item["last_stage"] = st.get("_persisted_stage", "(无痕迹)")
            item["status"] = st.get("status", "")
        out.append(item)
    return out


def resolve_run_dir(run_id: str) -> Optional[Path]:
    """按名字解析运行目录，支持裸名与嵌套名，且**限定在 runs/ 之内**。"""
    root = runs_root().resolve()
    try:
        direct = (root / run_id).resolve()
        direct.relative_to(root)              # 越界抛 ValueError
        if is_run_dir(direct):
            return direct
    except (ValueError, OSError):
        return None
    for d in list_run_dirs():
        if d.name == run_id:
            return d
    return None


def run_detail(run_id: str) -> Dict[str, Any]:
    d = resolve_run_dir(run_id)
    if d is None:
        return {"error": f"运行 {run_id} 不存在或不是一次运行"}
    out: Dict[str, Any] = {"run_id": run_id, "path": str(d)}
    for key, rel in DETAIL_FILES:
        p = d / rel
        if p.exists():
            val = _read_json(p)
            out[key] = val if val is not None else {"error": f"{rel} 解析失败"}
    return out


def find_report(run_id: str) -> Optional[Path]:
    """定位该次运行的计算报告文件；没有则返回 None。"""
    d = resolve_run_dir(run_id)
    if d is None:
        return None
    for rel in REPORT_CANDIDATES:
        p = d / rel
        if p.is_file():
            return p
    # 兜底：report/ 下任意一个 docx/html
    rp = d / "report"
    if rp.is_dir():
        for pat in ("*.docx", "*.html", "*.md"):
            hits = sorted(rp.glob(pat))
            if hits:
                return hits[0]
    return None


def find_report_in(directory) -> Optional[Path]:
    """在**指定目录**里找计算报告（不受 runs 根目录限制）。

    为什么要这个：`容量分析` 页跑完后用户可能把输出放到任意目录，
    `resolve_run_dir()` 只在 runs 根下找，会找不到。
    这里直接按同一套候选文件名扫给定目录。
    """
    d = Path(str(directory or ""))
    if not d.is_dir():
        return None
    for rel in REPORT_CANDIDATES:
        f = d / rel
        if f.is_file():
            return f
    rp = d / "report"
    if rp.is_dir():
        for pat in ("*.docx", "*.html", "*.md"):
            hits = sorted(rp.glob(pat))
            if hits:
                return hits[0]
    return None


def open_report(run_id: str) -> Dict[str, Any]:
    """用系统默认程序打开计算报告。

    只允许打开**运行目录内**的文件，避免被前端指示打开任意路径。
    """
    d = resolve_run_dir(run_id)
    if d is None:
        return {"ok": False, "error": f"运行 {run_id} 不存在"}
    p = find_report(run_id)
    if p is None:
        return {"ok": False, "error": f"运行 {run_id} 还没有生成报告"}
    try:
        p.resolve().relative_to(d.resolve())
    except ValueError:
        return {"ok": False, "error": "拒绝打开运行目录之外的路径"}
    try:
        os.startfile(str(p))          # Windows：等同双击
        return {"ok": True, "path": str(p), "name": p.name}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "path": str(p)}


def read_artifact(run_id: str, rel: str) -> str:
    """读运行目录内的文本文件（报告等）。**防目录穿越**。"""
    d = resolve_run_dir(run_id)
    if d is None:
        raise ValueError(f"运行 {run_id} 不存在")
    base = d.resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError("拒绝读取运行目录之外的路径")
    if not target.is_file():
        raise FileNotFoundError(f"产物不存在：{rel}")
    if target.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("产物过大，请直接打开文件查看")
    return target.read_text(encoding="utf-8", errors="replace")


def execute_task(input_dir: str, output_dir: str,
                 on_progress: Optional[Callable[[str, str], None]] = None
                 ) -> Dict[str, Any]:
    """在**本进程**内执行一次评估。

    由 engine_host 调用（因此跑在子进程里，界面不会被阻塞）。
    """
    from hc_agent.pipeline import Pipeline
    inp, out = Path(input_dir), Path(output_dir)
    if not inp.is_dir():
        return {"ok": False, "error": f"输入目录不存在：{inp}"}
    pipe = Pipeline(str(inp), str(inp), str(out))
    if on_progress:
        # 包一层 checkpoint，把阶段推进实时报给界面
        original = pipe.checkpoint

        def tapped(step, status, **extra):
            original(step, status, **extra)
            try:
                on_progress(step, status)
            except Exception:
                pass

        pipe.checkpoint = tapped        # type: ignore[assignment]
    outcome = pipe.run()
    return {"ok": True, "outcome": outcome, "output_dir": str(out),
            "run_id": out.name}


def summary_line(item: Dict[str, Any]) -> str:
    """列表里一行摘要文案（供界面直接显示）。"""
    cap = item.get("capacity_physical_kw")
    lvl = item.get("level") or ""
    code = item.get("exit_code")
    bits = []
    if cap is not None:
        bits.append(f"{cap:.4g} kW")
    if lvl:
        bits.append(str(lvl))
    if code is not None:
        bits.append(f"exit {code}")
    return " · ".join(bits) or "—"
