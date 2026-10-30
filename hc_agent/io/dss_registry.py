"""dss_registry.py — 用户自己添加的 OpenDSS 算例登记表。

## 为什么需要登记表

用户可能把算例放在任意目录（桌面、U 盘、项目里）。问答里说
"算一下我刚才加的那个算例"时，系统得知道"那个"是哪个。

登记表就是一个本地 JSON：记录算例名、主文件路径、文件数、添加时间。
**不复制文件** —— 原地引用。理由：

* OpenDSS 算例是**多文件**结构（Master + Redirect 若干子文件），
  复制容易漏文件、也容易和用户后续的修改脱节；
* 用户改完参数想重算时，原地引用能立刻生效，不用重新上传。

代价是：用户移动/删除目录后会失效。所以每次使用前都校验主文件是否还在，
不存在就如实报"算例已失效"，不静默失败。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from hc_agent import paths

REGISTRY_NAME = "dss_cases.json"


def store_path() -> Path:
    return paths.data_root_safe() / REGISTRY_NAME


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load() -> List[Dict[str, Any]]:
    p = store_path()
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            cases = data.get("cases") if isinstance(data, dict) else data
            if isinstance(cases, list):
                return [c for c in cases if isinstance(c, dict)]
    except Exception:
        pass
    return []


def _save(cases: List[Dict[str, Any]]) -> bool:
    p = store_path()
    try:
        paths.ensure_writable(p.parent)
        p.write_text(json.dumps({"cases": cases}, ensure_ascii=False,
                                indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _master_of(path: Path) -> Optional[Path]:
    """定位主文件（与 dss_case.resolve_master 同规则，但不抛异常）。"""
    try:
        from hc_agent.io.dss_case import resolve_master
        return resolve_master(path)
    except Exception:
        return None


def _scan(directory: Path) -> Dict[str, Any]:
    """统计算例目录里的 .dss 文件数，便于界面上确认"加对了没有"。

    Windows 上 glob 大小写不敏感，`*.dss` 与 `*.DSS` 会命中同一批文件，
    直接相加会把数量翻倍（实测 6 个文件报成 12）。所以按真实路径去重。
    """
    try:
        seen: Dict[Path, None] = {}
        for pat in ("*.dss", "*.DSS"):
            for f in directory.rglob(pat):
                try:
                    seen[f.resolve()] = None
                except Exception:
                    continue
        files = list(seen)
        return {"files": len(files),
                "has_master": any(f.name.lower() == "master.dss"
                                  for f in files)}
    except Exception:
        return {"files": 0, "has_master": False}


def list_cases(*, alive_only: bool = False) -> List[Dict[str, Any]]:
    """列出已添加的算例；`alive_only` 只返回主文件仍存在的。"""
    out: List[Dict[str, Any]] = []
    for c in _load():
        master = Path(str(c.get("master") or ""))
        ok = master.is_file()
        item = dict(c)
        item["alive"] = ok
        if ok:
            item["master"] = str(master)
            item["dir"] = str(master.parent)
        if alive_only and not ok:
            continue
        out.append(item)
    out.sort(key=lambda x: str(x.get("added_at") or ""), reverse=True)
    return out


def add_case(path) -> Dict[str, Any]:
    """登记一个算例（目录或 .dss 文件）。不复制文件。"""
    raw = str(path or "").strip().strip('"').strip("'")
    if not raw:
        return {"ok": False, "error": "没有指定算例路径"}
    src = Path(raw).expanduser()
    if not src.exists():
        return {"ok": False, "error": f"路径不存在：{src}"}

    master = _master_of(src)
    if master is None:
        return {"ok": False,
                "error": ("没找到算例主文件：目录里应有 Master.dss / *.dss。"
                          if src.is_dir() else f"不是可用的 .dss 算例：{src.name}")}

    master = master.resolve()
    cases = _load()
    # 去重：同一个主文件只登记一次
    for c in cases:
        try:
            if Path(str(c.get("master"))).resolve() == master:
                c["added_at"] = _now()      # 刷新到最前
                _save(cases)
                return {"ok": True, "already": True, "case": c}
        except Exception:
            continue

    info = _scan(master.parent)
    # 名字：主文件叫 Master.dss 就用目录名，否则用文件名
    name = (master.parent.name if master.name.lower() == "master.dss"
            else master.stem)
    item = {
        "id": "c_" + uuid.uuid4().hex[:10],
        "name": name,
        "master": str(master),
        "dir": str(master.parent),
        "files": info["files"],
        "has_master": info["has_master"],
        "added_at": _now(),
    }
    cases.append(item)
    if not _save(cases):
        return {"ok": False, "error": "登记表写入失败（数据目录不可写）"}
    return {"ok": True, "already": False, "case": item}


def remove_case(key: str) -> Dict[str, Any]:
    """按 id / 名称 / 主文件路径移除登记。"""
    key = str(key or "").strip()
    if not key:
        return {"ok": False, "error": "没有指定要移除的算例"}
    cases = _load()
    keep: List[Dict[str, Any]] = []
    removed = None
    for c in cases:
        ident = {str(c.get("id")), str(c.get("name")),
                 str(c.get("master")), str(c.get("dir"))}
        if removed is None and (key in ident
                                or key.lower() in {i.lower() for i in ident}):
            removed = c
            continue
        keep.append(c)
    if removed is None:
        return {"ok": False, "error": f"没有登记过这个算例：{key}"}
    _save(keep)
    return {"ok": True, "removed": removed}


def find_case(text: str = "") -> Optional[Dict[str, Any]]:
    """在已登记算例里找最匹配的一个。

    匹配优先级：id 精确 → 名称精确 → 名称包含 → 路径包含。
    空字符串表示"最近添加的那个"。
    """
    cases = list_cases(alive_only=True)
    if not cases:
        return None
    q = str(text or "").strip()
    if not q:
        return cases[0]
    ql = q.lower()
    for c in cases:
        if q == str(c.get("id")):
            return c
    for c in cases:
        if ql == str(c.get("name") or "").lower():
            return c
    for c in cases:
        n = str(c.get("name") or "").lower()
        if n and (n in ql or ql in n):
            return c
    for c in cases:
        if ql in str(c.get("master") or "").lower():
            return c
    return None


#: 指代性说法：不含算例名，但显然在指"我加过的那个"
_DEIXIS = ("我加的", "我加的算例", "刚才加的", "刚才那个", "刚加的",
           "添加的算例", "那个算例", "这个算例", "加的算例", "我添加的",
           "刚添加的", "原来那个", "之前加的")


def resolve_in_registry(text: str = "") -> str:
    """把用户说法解析成登记过的算例主文件路径；找不到返回空串。

    特别处理指代说法（"我加的算例""刚才那个"）：
    这类说法里没有算例名，按"最近添加的那个"解释，
    否则用户点完「添加算例」再问一句会得到"没找到"。
    """
    q = str(text or "").strip()
    if q and any(d in q for d in _DEIXIS):
        # 先尝试按名字精确匹配（万一用户同时说了名字）
        c = find_case(q)
        if c is not None:
            return str(c.get("master"))
        c = find_case("")          # 退化为最近添加
        return str(c.get("master")) if c else ""
    c = find_case(q)
    return str(c.get("master")) if c else ""
