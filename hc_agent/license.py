"""license.py — 本地授权：机器指纹绑定 + Ed25519 签名校验 + 到期与演示模式。

设计要点（与商业方案的取舍）：
  · 只做**防随手拷贝 + 可溯源**，不做“防住所有人”。纯软件 + 内网 + 无外联
    的组合下，后者做不到；能证明的是“这份授权只授权给了哪台机器”。
  · 公钥内置、私钥只在发布方手里（tools/make_license.py 使用，不进仓库）。
  · 不写隐藏文件、不改注册表、不外联——电网客户的等保审查会直接拒绝那些做法。
  · 到期后不闪退：进入演示模式，锁定容量搜索，基础功能保留。

授权文件 lic.json 结构：
    {"payload": {...}, "sig": "<base64>"}
payload 里含 machine_id / customer / expire / features，签名覆盖 payload 的规范化 JSON。
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: 发布方公钥（PEM, Ed25519）。私钥只在签发端，绝不入库。
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAvzEfJkjUKrIxegPBW+oe8nZgM5H/zUS2lpqiua0GgmI=
-----END PUBLIC KEY-----
"""

LICENSE_FILENAME = "lic.json"
TRIAL_DAYS = 60
TRIAL_FILE = Path.home() / ".hc_agent" / "first_run.txt"


def _run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=4,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (out.stdout or "").strip()
    except Exception:
        return ""


_MID_CACHE: str | None = None
_MID_FILE = Path.home() / ".hc_agent" / "machine_id.txt"


def machine_id() -> str:
    """机器指纹：CPU + 主板 UUID + 系统盘序列号，SHA256 取前 32 位。

    性能注意：采集要调子进程（wmic / powershell），单次可能数百毫秒到数秒。
    界面每次渲染配置页都会读它，所以必须缓存——否则会出现“打开配置页卡几秒”。
    缓存顺序：内存 → 磁盘缓存文件 → 实际采集后写盘。
    """
    global _MID_CACHE
    if _MID_CACHE:
        return _MID_CACHE
    try:
        if _MID_FILE.is_file():
            cached = _MID_FILE.read_text(encoding="utf-8").strip()
            if len(cached) == 32:
                _MID_CACHE = cached
                return cached
    except Exception:
        pass

    raw: list[str] = []
    raw.append(_run(["wmic", "cpu", "get", "ProcessorId"]).replace("ProcessorId", "").strip())
    raw.append(_run(["wmic", "csproduct", "get", "UUID"]).replace("UUID", "").strip())
    disk = _run(["cmd", "/c", "vol", "C:"])
    raw.append(disk.split("Volume Serial Number is")[-1].strip() if "Serial" in disk else "")
    parts = [p for p in raw if p]
    # 注意：指纹算法一旦发布就不能改，否则历史授权全部失效。
    # 这里保持与首发版本完全一致的取数逻辑，只加缓存。
    if len(parts) < 2:
        parts.append(f"node-{uuid.getnode()}")
    mid = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    _MID_CACHE = mid
    try:
        _MID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MID_FILE.write_text(mid, encoding="utf-8")
    except Exception:
        pass
    return mid


@dataclass
class Status:
    ok: bool               # 授权有效且未过期
    reason: str            # 无效原因，ok=True 时为空
    customer: str = ""
    expire: str = ""
    days_left: int = -1
    demo: bool = False     # 演示模式（未授权且试用已过）
    trial: bool = False    # 是否处于试用期
    trial_days_left: int = -1
    features: dict | None = None

    @property
    def locked(self) -> list[str]:
        return [] if self.ok else ["capacity_search"]


def _payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def verify(lic_path: Path, *, today: date | None = None,
           mid: str | None = None) -> Status:
    """校验授权文件。任何异常都返回 demo 状态，不抛错（界面不能因授权问题崩）。"""
    path = Path(lic_path)
    if not path.is_file():
        return Status(False, "未找到授权文件", demo=True)

    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        payload = obj["payload"]
        sig = base64.b64decode(obj["sig"])
    except Exception as exc:
        return Status(False, f"授权文件格式错误：{type(exc).__name__}", demo=True)

    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        pub = load_pem_public_key(PUBLIC_KEY_PEM)
        pub.verify(sig, _payload_bytes(payload))
    except Exception:
        return Status(False, "签名校验失败（文件被改动或非本机签发）", demo=True)

    expect = mid or machine_id()
    if payload.get("machine_id") != expect:
        return Status(False, "机器指纹不匹配（换机或克隆）", demo=True)

    expire_s = str(payload.get("expire") or "")
    try:
        expire_d = datetime.strptime(expire_s, "%Y-%m-%d").date()
    except ValueError:
        return Status(False, "授权到期日格式错误", demo=True)

    now = today or datetime.now(timezone.utc).astimezone().date()
    left = (expire_d - now).days
    if left < 0:
        return Status(False, f"授权已于 {expire_s} 到期", customer=str(payload.get("customer", "")),
                      expire=expire_s, days_left=left, demo=True,
                      features=payload.get("features") or {})

    return Status(True, "", customer=str(payload.get("customer", "")), expire=expire_s,
                  days_left=left, demo=False, features=payload.get("features") or {})


def locate(project_root: Path | None = None) -> Path:
    """授权文件位置。

    查找顺序：
      1) 程序目录（打包版＝exe 同级；源码版＝工程根）下的 lic.json
      2) 用户目录 ~/.hc_agent/lic.json
      3) 上述两处的任意 lic*.json（容忍客户改过文件名，取最近修改的一份）
    """
    if project_root:
        roots = [Path(project_root)]
    else:
        import sys
        roots = [Path(sys.executable).parent if getattr(sys, "frozen", False)
                 else Path(__file__).resolve().parents[1]]
    roots.append(Path.home() / ".hc_agent")

    for d in roots:
        exact = d / LICENSE_FILENAME
        if exact.is_file():
            return exact

    loose: list[Path] = []
    for d in roots:
        if d.is_dir():
            loose.extend(p for p in d.glob("lic*.json") if p.is_file())
    if loose:
        return max(loose, key=lambda p: p.stat().st_mtime)

    return roots[0] / LICENSE_FILENAME


def _trial_status(today: date | None = None) -> Status:
    """无有效授权时的试用期判定：首次运行起 TRIAL_DAYS 天。"""
    now = today or datetime.now(timezone.utc).astimezone().date()
    try:
        if TRIAL_FILE.is_file():
            started = datetime.strptime(TRIAL_FILE.read_text(encoding="utf-8").strip(),
                                        "%Y-%m-%d").date()
        else:
            started = now
            TRIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            TRIAL_FILE.write_text(now.isoformat(), encoding="utf-8")
    except Exception:
        started = now
    left = TRIAL_DAYS - (now - started).days
    if left > 0:
        return Status(True, "", customer="试用版", expire=(started + timedelta(days=TRIAL_DAYS)).isoformat(),
                      days_left=left, demo=False, trial=True, trial_days_left=left)
    return Status(False, f"试用期已于 {started + timedelta(days=TRIAL_DAYS)} 结束",
                  customer="试用版", demo=True, trial=False, trial_days_left=0)


def status(project_root: Path | None = None) -> Status:
    st = verify(locate(project_root))
    return st if st.ok else _trial_status()


def trial_info() -> dict:
    st = _trial_status()
    return {"trial": st.trial, "days_left": st.trial_days_left,
            "expire": st.expire, "reason": st.reason}


def reset_trial(day: str = "2026-01-01") -> None:
    """仅测试用：把首启时间改成指定日期，用来验证到期行为。"""
    TRIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRIAL_FILE.write_text(day, encoding="utf-8")


def banner(st: Status) -> str:
    """状态栏文案（界面用）。"""
    if st.ok and st.trial:
        return f"试用版：剩 {st.trial_days_left} 天（到期后容量搜索锁定）"
    if st.ok:
        days = f"剩 {st.days_left} 天" if 0 <= st.days_left <= 30 else f"至 {st.expire}"
        return f"授权：{st.customer or '已授权'}（{days}）"
    return f"演示模式：{st.reason}（容量搜索已锁定）"


if __name__ == "__main__":
    s = status()
    print("机器码：", machine_id())
    print(banner(s))
