# -*- coding: utf-8 -*-
"""make_license.py — 签发端：生成客户授权文件（私钥只在发布方，不入库）。

用法：
    python tools\\make_license.py --keygen            # 首次：生成密钥对
    python tools\\make_license.py --machine <机器码> \\
        --customer "XX供电公司" --expire 2027-12-31  # 签发授权
    python tools\\make_license.py --machine <机器码> --customer XX --days 90

私钥文件默认放 %USERPROFILE%\\.hc_agent\\issuer_key.pem，**永远不进仓库**。
"""
from __future__ import annotations

import argparse
import base64
import json
from datetime import date, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEY_DIR = Path.home() / ".hc_agent"
KEY_PATH = KEY_DIR / "issuer_key.pem"


def keygen(force: bool = False) -> Path:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.is_file() and not force:
        print("私钥已存在：", KEY_PATH)
    else:
        key = Ed25519PrivateKey.generate()
        KEY_PATH.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()))
        print("已生成私钥：", KEY_PATH)
    key = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    print("\n把下面这段替换到 hc_agent/license.py 的 PUBLIC_KEY_PEM：\n")
    print(pub_pem)
    return KEY_PATH


def _payload_bytes(p: dict) -> bytes:
    return json.dumps(p, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def issue(machine: str, customer: str, expire: str = "", days: int = 0,
          out: Path | None = None, features: dict | None = None) -> Path:
    if not KEY_PATH.is_file():
        raise SystemExit("没有私钥，先运行 --keygen")
    if not expire:
        if days <= 0:
            days = 4 * 365          # 默认四年（用户 2026-11-10 定的规则）
        expire = (date.today() + timedelta(days=days)).isoformat()

    key = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    payload = {
        "machine_id": machine.strip(),
        "customer": customer,
        "expire": expire,
        "features": features or {"capacity_search": True, "report_export": True},
        "issued": date.today().isoformat(),
    }
    sig = key.sign(_payload_bytes(payload))
    obj = {"payload": payload, "sig": base64.b64encode(sig).decode()}
    target = Path(out) if out else (Path.cwd() / "lic.json")
    target.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已签发：", target)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return target


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--keygen", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--machine", default="")
    ap.add_argument("--customer", default="")
    ap.add_argument("--expire", default="")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.keygen:
        keygen(a.force)
    else:
        issue(a.machine, a.customer, a.expire, a.days,
              Path(a.out) if a.out else None)
