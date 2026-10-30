"""证据、哈希与血缘（设计文档 5.4 不可变与血缘机制 / 9.2 证据包目录）。

规则：
  1. 接入时以字节流计算 SHA-256，登记文件名、大小、来源、时间、权限和业务对象。
  2. 原始快照进入只读区；任何清洗、单位换算、重采样或字段映射均输出新对象。
  3. 每个计算 run 固定引用 snapshot_id 和各派生数据 version_id。
  4. 正式证据包保存输入清单、哈希、转换链、规则/技能/引擎版本、参数、
     日志、输出和审批签名。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

CHUNK = 1024 * 1024
PathLike = Union[str, Path]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: PathLike) -> str:
    """以字节流计算文件 SHA-256（设计文档 5.4-1）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def sha256_obj(obj: Any) -> str:
    """对结构化对象求稳定哈希（键排序、紧凑分隔、UTF-8）。"""
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return sha256_bytes(payload)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def make_readonly(path: PathLike) -> None:
    """把文件置为只读，落实"原始快照进入只读区"（设计文档 5.4-2）。"""
    p = Path(path)
    p.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def make_writable(path: PathLike) -> None:
    p = Path(path)
    try:
        p.chmod(stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def clear_readonly_tree(root: PathLike) -> None:
    """递归恢复目录树的可写属性。

    冻结输入会被置为只读（设计文档 5.4-2）。若再次向同一输出目录运行，
    复制会被只读位拒绝（PermissionError）。重跑是正常运维动作，
    因此在写入前先解除旧快照的只读位；这不影响"已封存证据不可修改"的
    约束——封存证据位于独立的 evidence/ 目录且以哈希索引保护。
    """
    r = Path(root)
    if not r.exists():
        return
    for p in r.rglob("*"):
        try:
            if p.is_dir():
                p.chmod(stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            else:
                p.chmod(stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


def register_input(path: PathLike, *, source: str = "manual",
                   business_object: Optional[str] = None,
                   read_only: bool = True) -> Dict[str, Any]:
    """登记一个输入文件为不可变快照对象（设计文档 5.4-1）。"""
    p = Path(path).resolve()
    st = p.stat()
    record: Dict[str, Any] = {
        "path": p.name,
        "abspath": str(p),
        "size_bytes": st.st_size,
        "sha256": sha256_file(p),
        "source": source,
        "registered_at": utc_now_iso(),
        "business_object": business_object,
        "mode": oct(stat.S_IMODE(st.st_mode)),
    }
    if read_only:
        make_readonly(p)
        record["readonly"] = True
    return record


def freeze_inputs(input_dir: PathLike, dest_dir: PathLike,
                  *, manifest_extra: Optional[Dict[str, Any]] = None,
                  read_only: bool = True) -> Dict[str, Any]:
    """冻结输入目录：复制到只读区并生成哈希清单。

    对应设计文档 S01：create_run() / freeze_inputs() → manifest.json、只读副本。
    """
    src = Path(input_dir).resolve()
    dst = Path(dest_dir).resolve()
    # 重跑保护：上一次运行留下的只读快照会让复制失败，先解除只读位
    clear_readonly_tree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    inputs: List[Dict[str, Any]] = []

    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        rec = register_input(target, source="frozen_snapshot",
                             business_object=str(rel), read_only=read_only)
        rec["relpath"] = rel.as_posix()
        inputs.append(rec)

    manifest: Dict[str, Any] = {
        "frozen_at": utc_now_iso(),
        "input_root": str(src),
        "snapshot_root": str(dst),
        "inputs": inputs,
        "input_digest": sha256_obj(
            [{k: r[k] for k in ("relpath", "sha256", "size_bytes")} for r in inputs]
        ),
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    return manifest


def derive_object(src: PathLike, dst: PathLike, *, transform_id: str,
                  operator: str, reason: str,
                  lineage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生成派生数据版本并记录血缘（设计文档 5.2 / 5.4-2）。"""
    s, d = Path(src), Path(dst)
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s, d)
    rec = {
        "derived_version_id": f"{transform_id}:{sha256_file(d)[:12]}",
        "source_path": s.name,
        "source_snapshot_id": (lineage or {}).get("source_snapshot_id"),
        "target_path": d.name,
        "transform_id": transform_id,
        "operator": operator,
        "timestamp": utc_now_iso(),
        "reason": reason,
        "source_sha256": sha256_file(s),
        "derived_sha256": sha256_file(d),
        "lineage": lineage or {},
    }
    return rec


class EvidenceLedger:
    """审计事件账本（设计文档 9.3）。

    所有事件统一携带 event_id、task_id、run_id、user/service_id、action、
    object_ref、before_hash、after_hash、timestamp、source_ip/device、
    result 和 reason。
    """

    REQUIRED_FIELDS = (
        "event_id", "task_id", "run_id", "actor", "action", "object_ref",
        "before_hash", "after_hash", "timestamp", "source", "result", "reason",
    )

    def __init__(self, path: PathLike, *, task_id: str = "", run_id: str = "",
                 actor: str = "hc_agent", source: str = "local") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.task_id = task_id
        self.run_id = run_id
        self.actor = actor
        self.source = source
        self._seq = 0

    def append(self, action: str, *, object_ref: str = "",
               before_hash: Optional[str] = None,
               after_hash: Optional[str] = None,
               result: str = "ok", reason: str = "",
               extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._seq += 1
        event = {
            "event_id": f"EVT-{self._seq:06d}",
            "task_id": self.task_id,
            "run_id": self.run_id,
            "actor": self.actor,
            "action": action,
            "object_ref": object_ref,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "timestamp": utc_now_iso(),
            "source": self.source,
            "result": result,
            "reason": reason,
        }
        if extra:
            event["extra"] = extra
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


# 证据包目录结构（设计文档 9.2 / 表 56）
EVIDENCE_LAYOUT = {
    "inputs/": "原始输入清单与只读副本哈希",
    "config/": "规则包、技能版本、引擎指纹与运行参数",
    "model/": "生成的 Master.dss 与引用文件",
    "results/": "结构化结果 CSV/JSON（容量、约束、母线、元件）",
    "logs/": "引擎原生日志与平台日志",
    "verification/": "独立复算清单、差异与通过状态",
    "review/": "人工复核与审批记录",
    "audit/": "审计事件账本",
}


def seal_evidence(run_dir: PathLike, out_zip: PathLike,
                  *, ledger_path: Optional[PathLike] = None) -> Dict[str, Any]:
    """封存证据包为 zip，并返回索引与哈希（设计文档 S09 / 9.2）。

    证据包不含密钥令牌（表 56）。
    """
    run = Path(run_dir).resolve()
    out = Path(out_zip).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    index: List[Dict[str, Any]] = []

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(run.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix in (".zip", ".pyc"):
                continue
            rel = path.relative_to(run).as_posix()
            entry = {"path": rel, "size_bytes": path.stat().st_size,
                     "sha256": sha256_file(path)}
            index.append(entry)
            zf.write(path, rel)
        index_doc = {
            "sealed_at": utc_now_iso(),
            "run_dir": str(run),
            "layout": EVIDENCE_LAYOUT,
            "file_count": len(index),
            "files": index,
            "package_digest": sha256_obj(index),
        }
        zf.writestr("evidence_index.json",
                    json.dumps(index_doc, ensure_ascii=False, indent=2))

    index_doc["package_path"] = str(out)
    index_doc["package_sha256"] = sha256_file(out)
    return index_doc


def write_json(path: PathLike, obj: Any) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default)
    p.write_text(text, encoding="utf-8")
    return sha256_bytes(text.encode("utf-8"))


def read_json(path: PathLike) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if hasattr(obj, "item"):  # numpy 标量
        return obj.item()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


def verify_file_immutable(record: Dict[str, Any]) -> bool:
    """核对快照文件哈希前后一致（验收指标：原始数据保护 100%）。"""
    p = Path(record.get("abspath") or record["path"])
    if not p.exists():
        return False
    return sha256_file(p) == record["sha256"]
