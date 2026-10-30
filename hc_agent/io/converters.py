"""converters.py — 外部算例格式转换（移植天枢一号的**数值裁定**）。

本模块只搬"物理规律"，不搬它的管道：天枢一号把四种格式转成一个扁平
``Master.dss``；本工程保持多文件 + ``Redirect`` 的既有结构，因此转换器只负责
**把外部表格/矩阵规范化成本工程的 InputBundle 形状**，DSS 生成仍由
``model/build_model.py`` 统一负责（口径单点定义）。

## 从外部算例接入时容易搞错的数值裁定（均有实测依据）

1. **变压器 kVA 基准取系统基准，不用 `rateA`**
   OpenDSS 的 ``%Rs`` / ``XHL`` 是**变压器自身 kVA 基准**下的百分值，而输入的
   ``r_pu``/``x_pu`` 是**系统基准**下的标幺值。令变压器 kVA 基准 = 系统基准，
   则 ``%R = r_pu×100`` 恰好还原原算例阻抗。
   ⚠️ 用 MATPOWER 的 ``rateA`` 当基准是错的——``rateA`` 是**热稳限额**不是阻抗
   基准。天枢一号实测：以 rateA 为基准会把变压器等值阻抗放大 **1.8~5.6 倍**，
   最低电压从参考 0.900 崩到 **0.787**。

2. **串联 ``%Rs`` 对半拆，``XHL`` 不拆**
   双绕组变压器，OpenDSS 串联 R 是两绕组 ``%R`` 之和，故总 r_pct 需 /2；
   ``XHL`` 已是**总**漏抗百分值，不再拆分（拆了电阻翻倍）。

3. **单相负荷的 ``kV`` = 线电压 / √3**
   输入的 ``base_kv`` 恒为线电压；单相负荷的 OpenDSS ``kV`` 是**相电压**。
   不除 √3 会把单相负荷高估 √3 倍。（本工程原先正是这么写的，已修。）

4. **``b_pu`` → ``C1(nF)``**：``C = (b_pu/Z_base)/(2πf)·1e9``，``Units=none``。

5. **``Zbase = Vbase²/Sbase``，Vbase 取 from 侧母线**，``R = rpu·Zbase``。

6. **零阻抗判定 ``1e-12`` 且 r/x 双双小于**——**只拦严格为零**。
   ``1e-9 pu`` 是合法数据，交给求解器按容差处理。

7. **``Vg`` 覆盖 ``Vm``**（发电机母线）；PV → ``Model=3``+``Vpu``，
   PQ → ``Model=1``。

8. **频率从算例读，不写死**（影响充电电纳→电容换算）。

> 本工程**不照抄**的地方：天枢一号从不写负荷 ``Model``/``Vminpu``，其自身测试
> 把"沿用 OpenDSS 默认 0.95 会隐性转恒阻抗"标为**已知未决项**。本工程显式写
> ``model=1`` 并显式写 ``vminpu``。另外它的 ``_to_float(默认值)`` 会把畸形数值
> 吞成默认值，本工程 ``as_float`` 返回 None 并阻断——保持本工程的严格性。
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent.errors import InputBlockedError
from hc_agent.io.ingest import InputBundle
from hc_agent.io.schema import (
    IssueCollector,
    as_bool,
    as_float,
    as_str,
    is_nullish,
)

SQRT3 = math.sqrt(3.0)

#: 零阻抗判定阈值（单点定义）。
#: **只拦严格为零**：|r| 与 |x| 双双小于本值才拒绝；1e-9 这类"很小但非零"是
#: 合法数据，由求解器按容差处理。判据与对外解释共用这一个常量，避免写两遍漂移。
ZERO_IMPEDANCE_TOL = 1e-12

#: 转换器版本。**改了转换逻辑必须升版本号**，否则老产物不会被重建，
#: 等于修复白做（天枢一号的经验，已实测过）。
CONVERTER_VERSION = "1.0.0"

SUPPORTED_SUFFIXES = (".xlsx", ".csv", ".json", ".m")

BUS_TYPES_SLACK = {"slack", "3", "ref", "reference", "swing"}
BUS_TYPES_PV = {"pv", "2"}
BUS_TYPES_PQ = {"pq", "1", "load", ""}


@dataclass
class ConversionReport:
    """转换说明（对应天枢一号的 header-comment 保真度协议，但用结构化字段）。

    本工程不靠解析 DSS 注释取证，而是把它写进结构化结果 —— 更可靠，且能被
    manifest 与报告直接引用。
    """

    source_format: str = ""
    source_path: str = ""
    approximation: bool = True
    phase_modeled: bool = False
    assumed_three_phase: int = 0
    elements_with_phase: int = 0
    unbalanced_input: bool = False
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    converter_version: str = CONVERTER_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_format": self.source_format,
            "source_path": self.source_path,
            "approximation": self.approximation,
            "fidelity_status": "warning" if self.approximation else "native",
            "phase_modeled": self.phase_modeled,
            "assumed_three_phase": self.assumed_three_phase,
            "elements_with_phase": self.elements_with_phase,
            "unbalanced_input": self.unbalanced_input,
            "warnings": list(self.warnings),
            "notes": list(self.notes),
            "converter_version": self.converter_version,
        }


# ---------------------------------------------------------------- 相别工具
_PHASE_ALIASES = {
    "a": (1,), "b": (2,), "c": (3,), "n": (0,),
    "甲": (1,), "乙": (2,), "丙": (3,),
    "中性": (0,), "中线": (0,), "地": (0,),
    "ground": (0,), "gnd": (0,), "earth": (0,),
}


def parse_phase_identity(value: Any) -> Optional[Tuple[int, ...]]:
    """解析相别**身份**（不是相数）。返回节点号元组，无法解析返回 None。

    **相数可默认，相别身份不可猜**：``phases=1`` 只说明"单相"，没说"哪一相"；
    猜错相会静默算出完全错误的电压分布，比直接失败危险得多。
    """
    if is_nullish(value):
        return None
    text = as_str(value).strip().lower()
    if not text:
        return None
    # 支持 "A" / "AB" / "ABC" / "1.2" / "1-2" / "a,b" 等写法
    parts = re.split(r"[.,\-、;|/+&\s]+", text)
    nodes: set = set()
    for part in parts:
        if not part:
            continue
        if part in _PHASE_ALIASES:
            nodes.update(_PHASE_ALIASES[part])
            continue
        if part.rstrip("相") in _PHASE_ALIASES:
            nodes.update(_PHASE_ALIASES[part.rstrip("相")])
            continue
        # 纯数字节点号："1" / "2" / "3" / "0"
        if part.isdigit():
            n = int(part)
            if n in (0, 1, 2, 3):
                nodes.add(n)
                continue
            return None
        # 连写形式 "ab" / "abc" / "12" / "a0"
        if len(part) > 1 and all(ch in "abcn0123" for ch in part):
            ok = True
            for ch in part:
                if ch in "abc":
                    nodes.update(_PHASE_ALIASES[ch])
                elif ch in "123":
                    nodes.add(int(ch))
                elif ch == "0":
                    nodes.add(0)
                else:
                    ok = False
                    break
            if ok:
                continue
        return None
    if not nodes:
        return None
    phases = sorted(n for n in nodes if n != 0)
    if not phases:
        return None
    if 0 in nodes and len(phases) < 3:
        phases = phases + [0]
    return tuple(phases)


def is_three_phase_nodes(nodes: Optional[Sequence[int]]) -> bool:
    if not nodes:
        return True
    return tuple(sorted(n for n in nodes if n != 0)) == (1, 2, 3)


def node_suffix(nodes: Optional[Sequence[int]]) -> str:
    if not nodes or is_three_phase_nodes(nodes):
        return ""
    return "".join(f".{int(n)}" for n in nodes)


def load_kv_for_phase(line_kv: float, phase: str) -> float:
    """负荷接入电压：**3φ/2φ 用线电压，1φ 用相电压**（= 线电压/√3）。

    输入的额定电压恒为线电压；OpenDSS 单相负荷的 ``kV`` 参数是相电压。
    不做这个换算会把单相负荷**高估 √3 倍**。
    """
    n = _phase_count(phase)
    if n >= 2:
        return line_kv
    return line_kv / SQRT3


def _phase_count(phase: str) -> int:
    p = as_str(phase).upper()
    if p in ("ABC", "ALL", ""):
        return 3
    if p in ("AB", "BC", "CA"):
        return 2
    return 1


# ------------------------------------------------------------ 阻抗换算
def ohm_to_pu(r_ohm: Optional[float], x_ohm: Optional[float],
              v_base_kv: float, s_base_mva: float
              ) -> Tuple[Optional[float], Optional[float]]:
    """欧姆 → 标幺：``Zbase = Vbase²/Sbase``（Vbase 取 from 侧母线）。"""
    if not v_base_kv or v_base_kv <= 0 or not s_base_mva or s_base_mva <= 0:
        return r_ohm, x_ohm
    z_base = (v_base_kv ** 2) / s_base_mva
    if z_base <= 0:
        return r_ohm, x_ohm
    r = None if r_ohm is None else r_ohm / z_base
    x = None if x_ohm is None else x_ohm / z_base
    return r, x


def b_pu_to_c1_nf(b_pu: float, z_base_ohm: float, freq_hz: float) -> float:
    """并联电纳(pu) → 电容(nF)：``C = (b/Zbase)/(2πf)·1e9``。

    频率必须从算例读，不能写死 —— 50/60 Hz 会给出不同的电容值。
    """
    if abs(b_pu) <= 1e-12 or z_base_ohm <= 0 or freq_hz <= 0:
        return 0.0
    b_siemens = b_pu / z_base_ohm
    return b_siemens / (2.0 * math.pi * freq_hz) * 1e9


def transformer_percent_impedance(r_pu: Optional[float],
                                  x_pu: Optional[float]
                                  ) -> Tuple[Optional[float], Optional[float]]:
    """标幺阻抗 → OpenDSS 百分值（**移植自天枢一号的核心裁定**）。

    返回 ``(r_pct_per_winding, xhl_pct)``：

      * ``%Rs`` **对半拆**：OpenDSS 双绕组串联 R 是两绕组之和，
        故每绕组 = ``r_pu×100/2``（不拆则电阻翻倍）；
      * ``XHL`` **不拆**：它已是总漏抗百分值。

    ⚠️ 本函数假定 ``r_pu``/``x_pu`` 是**系统基准**下的标幺值，且变压器 kVA
    基准取系统基准。若用 ``rateA`` 之类热稳限额当基准，等值阻抗会被放大
    1.8~5.6 倍（天枢一号实测：最低电压 0.900 → 0.787）。
    """
    r_pct = None if r_pu is None else r_pu * 100.0 / 2.0
    x_pct = None if x_pu is None else x_pu * 100.0
    return r_pct, x_pct


def check_zero_impedance(r: Optional[float], x: Optional[float],
                         element_id: str, *, unit: str = "pu") -> None:
    """零阻抗门禁：**只拦严格为零**（r 与 x 双双小于阈值）。

    1e-9 这类"很小但非零"是合法数据，不拒绝——交给求解器按容差处理。
    报错信息按实际单位措辞（天枢一号此处恒写 "pu"，即便该行给的是欧姆，
    会误导审计）。
    """
    rr = 0.0 if r is None else float(r)
    xx = 0.0 if x is None else float(x)
    if abs(rr) < ZERO_IMPEDANCE_TOL and abs(xx) < ZERO_IMPEDANCE_TOL:
        raise InputBlockedError(
            f"检测到零阻抗支路：{element_id}（r=x=0）。"
            f"判据是 |r| 与 |x| 双双小于 {ZERO_IMPEDANCE_TOL:g} {unit}（严格为零）；"
            f"若阻抗只是非常小但非零（如 1e-9 {unit}），本转换器不会拒绝 —— "
            f"那是合法数据，由求解器按容差处理")


# ---------------------------------------------------------------- 表格读取
def _read_csv_rows(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _read_xlsx_sheets(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover
        raise InputBlockedError("openpyxl 不可用，无法读取 xlsx")
    wb = load_workbook(path, read_only=True, data_only=True)
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for name in wb.sheetnames:
            ws = wb[name]
            rows = ws.iter_rows(values_only=True)
            try:
                header = next(rows)
            except StopIteration:
                continue
            cols = [as_str(h) for h in header]
            recs = []
            for r in rows:
                if r is None or all(c is None for c in r):
                    continue
                recs.append({cols[i]: r[i] for i in range(min(len(cols), len(r)))})
            out[name.strip().lower()] = recs
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return out


# ------------------------------------------------- MATPOWER (.m) 解析
_MAT_RE = r"mpc\.{name}\s*=\s*\[([\s\S]*?)\]\s*;"


def _matpower_matrix(text: str, name: str) -> List[List[float]]:
    m = re.search(_MAT_RE.format(name=name), text)
    if not m:
        return []
    body = m.group(1)
    rows: List[List[float]] = []
    for line in body.splitlines():
        line = line.split("%")[0].strip().rstrip(";")
        if not line:
            continue
        parts = [p for p in re.split(r"[\s,]+", line) if p]
        try:
            rows.append([float(p) for p in parts])
        except ValueError:
            continue
    return rows


def convert_matpower(path: Path, bundle: InputBundle,
                     report: ConversionReport,
                     collector: IssueCollector) -> None:
    """MATPOWER ``.m`` → InputBundle。

    列序（MATPOWER 标准）：bus = [bus_i, type, Pd, Qd, Gs, Bs, area, Vm,
    baseKV, zone, Vmax, Vmin]；branch = [fbus, tbus, r, x, b, rateA, ...,
    ratio, ..., status]。
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    base_mva = 100.0
    mb = re.search(r"mpc\.baseMVA\s*=\s*([0-9.eE+-]+)", text)
    if mb:
        base_mva = as_float(mb.group(1)) or 100.0
    if base_mva <= 0:
        raise InputBlockedError(f"MATPOWER baseMVA 必须大于 0，实际 {base_mva:g}")

    bus_rows = _matpower_matrix(text, "bus")
    br_rows = _matpower_matrix(text, "branch")
    gen_rows = _matpower_matrix(text, "gen")
    if not bus_rows or not br_rows:
        raise InputBlockedError("MATPOWER 文件缺少 mpc.bus 或 mpc.branch 矩阵")

    report.notes.append(f"MATPOWER baseMVA={base_mva:g}")
    report.notes.append(
        "变压器 kVA 基准取系统基准（非 rateA）：rateA 是热稳限额，"
        "用作阻抗基准会把等值阻抗放大 1.8~5.6 倍")

    # 母线
    vg_by_bus: Dict[int, float] = {}
    for row in gen_rows:
        if len(row) >= 6:
            vg_by_bus[int(row[0])] = row[5]   # Vg 覆盖 Vm

    for row in bus_rows:
        if len(row) < 10:
            collector.add(file=path.name, code="MATPOWER_ROW_SHORT",
                          message="MATPOWER bus 行列数不足（至少 10 列）",
                          severity="error", blocking=True)
            continue
        bid = as_str(int(row[0]))
        btype = row[1]
        # MATPOWER bus 列序（1 基）：
        #   1 bus_i | 2 type | 3 Pd | 4 Qd | 5 Gs | 6 Bs | 7 area | 8 Vm
        #   9 baseKV | 10 zone | 11 Vmax | 12 Vmin
        # 即 0 基下标：baseKV=row[8]、Vm=row[7]、zone=row[9]。
        # ⚠️ 实测踩过：把 baseKV 读成 row[9]（zone 列）会让所有母线电压变成
        #    1.0 kV，变压器判定随之全部失效（两端电压相同 → 全被当成线路）。
        vn = float(row[8]) if len(row) > 8 and row[8] > 0 else 0.4
        vm = vg_by_bus.get(int(row[0]), row[7])
        bundle.buses.append({
            "node_id": bid, "name": bid, "feeder_id": "FD-MP",
            "dt_id": "", "vn_kv": vn, "phase": "ABC", "connection": "wye",
            "neutral_model": "",
            "_matpower_type": btype, "_vm_pu": vm,
            "_pd_mw": float(row[2]), "_qd_mvar": float(row[3]),
        })

    bus_vn = {as_str(int(b["node_id"])): float(b["vn_kv"])
              for b in bundle.buses}
    slack_buses = [as_str(int(r[0])) for r in bus_rows
                   if len(r) >= 10 and as_str(int(r[1])) in BUS_TYPES_SLACK]
    if not slack_buses:
        raise InputBlockedError("MATPOWER 算例中没有平衡母线（type=3）")
    if len(slack_buses) > 1:
        collector.add(file=path.name, code="MULTI_SLACK",
                      message=f"检测到多个平衡母线：{slack_buses}；"
                              f"请明确多岛运行策略后再转换",
                      severity="error", blocking=True)
    src_bus = slack_buses[0]

    # 电源：电压取**平衡母线自身**的设定值。
    # ⚠️ 不能写成 bundle.buses[0]（"列表里第一条"）——那只是碰巧相等；
    #    平衡母线不一定排在首位。Vg（若该母线上有发电机）已覆盖 Vm。
    src_vm = 1.0
    for b in bundle.buses:
        if as_str(b["node_id"]) == src_bus:
            src_vm = float(b.get("_vm_pu") or 1.0)
            break
    vm_by_bus = {as_str(b["node_id"]): float(b.get("_vm_pu") or 1.0)
                 for b in bundle.buses}
    bundle.sources.append({
        "source_id": "SRC-MP", "node_id": src_bus,
        "vm_pu": src_vm,
        "angle_deg": 0.0,
        "r1_ohm": 0.0, "x1_ohm": max(1e-6, base_mva / 1000.0),
        "r0_ohm": None, "x0_ohm": None,
        "source_case_id": "MATPOWER", "z2_assumption_ref": "Z2=Z1",
        "_vm_by_bus": vm_by_bus,
    })

    # 支路：按"物理事实优先"判变压器（kV 不同即为变压器）
    for idx, row in enumerate(br_rows, start=1):
        if len(row) < 11:
            collector.add(file=path.name, code="MATPOWER_ROW_SHORT",
                          message=f"MATPOWER branch 第 {idx} 行列数不足",
                          severity="error", blocking=True)
            continue
        fb, tb = as_str(int(row[0])), as_str(int(row[1]))
        r_pu, x_pu, b_pu = float(row[2]), float(row[3]), float(row[4])
        rate_a, ratio, status = float(row[5]), float(row[8]), int(row[10])
        if fb not in bus_vn or tb not in bus_vn:
            collector.add(file=path.name, code="FOREIGN_KEY_MISSING",
                          message=f"支路 {idx} 引用了不存在的母线",
                          severity="error", blocking=True)
            continue
        if ratio < 0:
            raise InputBlockedError(f"MATPOWER 支路 {idx} 的变比为负：{ratio}")
        tap = ratio if ratio > 0 else 1.0
        active = status != 0
        kv_f, kv_t = bus_vn[fb], bus_vn[tb]
        # 物理事实优先：两端电压等级不同 ⇒ 变压器（不信自由文本的 type 列）
        is_xfmr = abs(kv_f - kv_t) > 1e-9

        if is_xfmr:
            r_pct, x_pct = transformer_percent_impedance(r_pu, x_pu)
            bundle.transformers.append({
                "transformer_id": f"T{idx}", "hv_node": fb, "lv_node": tb,
                # kVA 基准 = 系统基准（关键裁定）
                "sn_kva": base_mva * 1000.0,
                "vn_hv_kv": max(kv_f, kv_t), "vn_lv_kv": min(kv_f, kv_t),
                # 由百分值反算 vk/vkr，供本工程既有的建模路径使用
                "vk_percent": math.hypot(r_pct or 0.0, x_pct or 0.0),
                "vkr_percent": (r_pct or 0.0) * 2.0 if r_pct is not None else 0.0,
                "pfe_kw": None, "i0_percent": None,
                "vector_group": "Dyn11",     # MATPOWER 不含接线组别，显式声明假设
                "phase": "ABC", "tap_side": "lv",
                "tap_pos": 0, "tap_neutral": 0,
                "tap_step_percent": None,
                "tap_ratio_direct": tap,
                "zero_sequence_ref": "",
                "in_service": active,
                "_assumed_vector_group": True,
            })
            report.notes.append(
                f"变压器 T{idx} 的接线组别 MATPOWER 未提供，按 Dyn11 假设并已标注")
        else:
            check_zero_impedance(r_pu, x_pu, f"L{idx}", unit="pu")
            bundle.lines.append({
                "line_id": f"L{idx}", "from_node": fb, "to_node": tb,
                "phase": "ABC",
                "length_km": 1.0,       # MATPOWER 无长度，用标幺阻抗直接换算
                "r1_ohm_per_km": r_pu,  # 单位长度=全长（length=1）
                "x1_ohm_per_km": x_pu,
                "r0_ohm_per_km": None, "x0_ohm_per_km": None,
                "c1_nf_per_km": b_pu, "c0_nf_per_km": None,
                "max_i_a": (rate_a * 1e6 / (SQRT3 * kv_f * 1000.0)
                            if rate_a > 0 and kv_f > 0 else None),
                "in_service": active,
                "phase_matrix_ref": "",
                "_from_pu": True,
            })

    # 负荷（PQ 母线，排除平衡母线）
    for b in bundle.buses:
        pd, qd = b.pop("_pd_mw", 0.0), b.pop("_qd_mvar", 0.0)
        btype = b.pop("_matpower_type", 1)
        b.pop("_vm_pu", None)
        if as_str(btype) in BUS_TYPES_SLACK:
            continue
        if abs(pd) <= 1e-12 and abs(qd) <= 1e-12:
            continue
        bundle.loads.append({
            "load_id": f"LD{b['node_id']}", "node_id": as_str(b["node_id"]),
            "phase": "ABC", "connection": "wye",
            "profile_id": "", "model_type": "constant_pq",
            "in_service": True,
            "_p_kw": pd * 1000.0, "_q_kvar": qd * 1000.0,
        })
    report.source_format = "matpower"
    report.assumed_three_phase = len(bundle.buses)


# ------------------------------------------------ 通用表格（xlsx/csv/json）
def _norm_key(k: str) -> str:
    return as_str(k).strip().lower().replace(" ", "_")


def _rows_to_bundle(rows_by_sheet: Dict[str, List[Dict[str, Any]]],
                    bundle: InputBundle, report: ConversionReport,
                    collector: IssueCollector, source: str) -> None:
    """把 {sheet: rows} 规范化为 InputBundle（xlsx/csv/json 共用）。"""
    buses = rows_by_sheet.get("bus") or rows_by_sheet.get("buses") or []
    lines = rows_by_sheet.get("line") or rows_by_sheet.get("lines") or []
    if not buses:
        collector.add(file=source, code="SCHEMA_MISSING_FIELD",
                      message="缺少母线表（bus/buses 工作表或数组）",
                      severity="error", blocking=True)
        return
    if not lines:
        collector.add(file=source, code="SCHEMA_MISSING_FIELD",
                      message="缺少支路表（line/lines 工作表或数组）",
                      severity="error", blocking=True)
        return

    vm_by_bus: Dict[str, float] = {}
    for raw in buses:
        row = {_norm_key(k): v for k, v in raw.items()}
        bid = as_str(row.get("bus_id") or row.get("id")
                     or row.get("name") or row.get("bus"))
        if not bid:
            collector.add(file=source, code="SCHEMA_MISSING_FIELD",
                          message="母线表某行缺少母线标识列",
                          severity="error", blocking=True)
            continue
        vn = as_float(row.get("base_kv") or row.get("kv")
                      or row.get("basekv"))
        if vn is None or vn <= 0:
            collector.add(file=source, object_id=bid,
                          field="base_kv", code="RATING_INVALID",
                          message=f"母线 {bid} 的 base_kv 缺失或非正；"
                                  f"不接受默认值猜测",
                          severity="error", blocking=True)
            continue
        phase = as_str(row.get("phase")) or "ABC"
        nodes = parse_phase_identity(phase)
        if phase not in ("ABC",) and nodes is None:
            collector.add(file=source, object_id=bid, field="phase",
                          code="PHASE_CONFLICT",
                          message=f"母线 {bid} 的相别 {phase!r} 无法解析为相别身份",
                          severity="error", blocking=True)
            continue
        if phase == "ABC" or is_three_phase_nodes(nodes):
            report.assumed_three_phase += 1
        else:
            report.phase_modeled = True
            report.unbalanced_input = True
            report.elements_with_phase += 1
        vm = as_float(row.get("v_set_pu") or row.get("vm_pu"))
        if vm is not None:
            vm_by_bus[bid] = vm
        bundle.buses.append({
            "node_id": bid, "name": bid, "feeder_id": "FD-EXT",
            "dt_id": "", "vn_kv": vn, "phase": phase, "connection": "wye",
            "neutral_model": "",
            "_p_kw": as_float(row.get("p_kw") or row.get("p_load_mw")),
            "_q_kvar": as_float(row.get("q_kvar") or row.get("q_load_mvar")),
            "_type": as_str(row.get("type") or row.get("bus_type")),
        })

    bus_vn = {as_str(b["node_id"]): float(b["vn_kv"]) for b in bundle.buses}
    if not bus_vn:
        return

    slack = [as_str(b["node_id"]) for b in bundle.buses
             if as_str(b.get("_type")).lower() in BUS_TYPES_SLACK]
    if not slack:
        collector.add(file=source, code="NO_SLACK",
                      message="表格中没有平衡母线（type=slack/3/ref）",
                      severity="error", blocking=True)
        return
    if len(slack) > 1:
        collector.add(file=source, code="MULTI_SLACK",
                      message=f"检测到多个平衡母线：{slack}",
                      severity="error", blocking=True)
        return
    src_bus = slack[0]
    bundle.sources.append({
        "source_id": "SRC-EXT", "node_id": src_bus,
        "vm_pu": vm_by_bus.get(src_bus, 1.0), "angle_deg": 0.0,
        "r1_ohm": 0.0, "x1_ohm": 1.0, "r0_ohm": None, "x0_ohm": None,
        "source_case_id": "EXT", "z2_assumption_ref": "Z2=Z1",
    })

    # 系统基准（用于标幺换算）
    sys_rows = rows_by_sheet.get("system") or []
    s_base, freq = 100.0, 50.0
    for raw in sys_rows:
        row = {_norm_key(k): v for k, v in raw.items()}
        s_base = as_float(row.get("s_base_mva")) or s_base
        freq = as_float(row.get("frequency_hz")) or freq

    for idx, raw in enumerate(lines, start=1):
        row = {_norm_key(k): v for k, v in raw.items()}
        lid = as_str(row.get("line_id") or row.get("id") or f"L{idx}")
        fb = as_str(row.get("from_bus") or row.get("from")
                    or row.get("fbus") or row.get("source"))
        tb = as_str(row.get("to_bus") or row.get("to")
                    or row.get("tbus") or row.get("target"))
        if not fb or not tb:
            collector.add(file=source, object_id=lid,
                          code="SCHEMA_MISSING_FIELD",
                          message=f"支路 {lid} 缺少 from_bus/to_bus",
                          severity="error", blocking=True)
            continue
        if fb not in bus_vn or tb not in bus_vn:
            collector.add(file=source, object_id=lid,
                          code="FOREIGN_KEY_MISSING",
                          message=f"支路 {lid} 引用了不存在的母线",
                          severity="error", blocking=True)
            continue
        active = as_bool(row.get("status") or row.get("in_service"), True)
        r_ohm, x_ohm = as_float(row.get("r_ohm")), as_float(row.get("x_ohm"))
        r_pu, x_pu = as_float(row.get("r_pu")), as_float(row.get("x_pu"))
        if r_ohm is None and r_pu is None:
            collector.add(file=source, object_id=lid, field="r/x",
                          code="SCHEMA_MISSING_FIELD",
                          message=f"支路 {lid} 缺少成对阻抗字段"
                                  f"（r_ohm/x_ohm 或 r_pu/x_pu）",
                          severity="error", blocking=True)
            continue
        unit = "ohm" if r_ohm is not None else "pu"
        check_zero_impedance(r_ohm if r_ohm is not None else r_pu,
                             x_ohm if x_ohm is not None else x_pu,
                             lid, unit=unit)

        kv_f, kv_t = bus_vn[fb], bus_vn[tb]
        # 物理事实优先：两端电压等级不同 ⇒ 变压器
        is_xfmr = abs(kv_f - kv_t) > 1e-9
        if is_xfmr:
            if r_pu is None and r_ohm is not None:
                r_pu, _ = ohm_to_pu(r_ohm, None, kv_f, s_base)
            if x_pu is None and x_ohm is not None:
                _, x_pu = ohm_to_pu(None, x_ohm, kv_f, s_base)
            r_pct, x_pct = transformer_percent_impedance(r_pu, x_pu)
            tap = as_float(row.get("tap_ratio") or row.get("tap")) or 1.0
            if tap <= 0:
                raise InputBlockedError(f"支路 {lid} 的 tap_ratio 必须大于 0")
            bundle.transformers.append({
                "transformer_id": lid, "hv_node": fb, "lv_node": tb,
                "sn_kva": s_base * 1000.0,
                "vn_hv_kv": max(kv_f, kv_t), "vn_lv_kv": min(kv_f, kv_t),
                "vk_percent": math.hypot(r_pct or 0.0, x_pct or 0.0),
                "vkr_percent": (r_pct or 0.0) * 2.0,
                "pfe_kw": None, "i0_percent": None,
                "vector_group": "Dyn11",
                "phase": "ABC", "tap_side": "lv", "tap_pos": 0,
                "tap_neutral": 0, "tap_step_percent": None,
                "tap_ratio_direct": tap, "zero_sequence_ref": "",
                "in_service": active, "_assumed_vector_group": True,
            })
            report.notes.append(
                f"变压器 {lid} 按两端电压等级判定；接线组别未提供，假设 Dyn11")
        else:
            z_base = (kv_f ** 2) / s_base if s_base > 0 else 0.0
            if r_ohm is not None:
                rr, xx = r_ohm, (x_ohm or 0.0)
            else:
                rr, xx = (r_pu or 0.0) * z_base, (x_pu or 0.0) * z_base
            b_pu = as_float(row.get("b_pu")) or 0.0
            c1 = b_pu_to_c1_nf(b_pu, z_base, freq)
            bundle.lines.append({
                "line_id": lid, "from_node": fb, "to_node": tb,
                "phase": as_str(row.get("phase")) or "ABC",
                "length_km": 1.0,
                "r1_ohm_per_km": rr, "x1_ohm_per_km": xx,
                "r0_ohm_per_km": None, "x0_ohm_per_km": None,
                "c1_nf_per_km": c1, "c0_nf_per_km": None,
                "max_i_a": as_float(row.get("max_i_a")),
                "in_service": active, "phase_matrix_ref": "",
            })

    # 负荷
    load_rows = rows_by_sheet.get("load") or rows_by_sheet.get("loads") or []
    if load_rows:
        for raw in load_rows:
            row = {_norm_key(k): v for k, v in raw.items()}
            bid = as_str(row.get("bus_id") or row.get("node_id")
                         or row.get("bus"))
            lid = as_str(row.get("load_id") or row.get("id") or f"LD{bid}")
            if bid not in bus_vn:
                collector.add(file=source, object_id=lid,
                              code="FOREIGN_KEY_MISSING",
                              message=f"负荷 {lid} 的 bus_id={bid} 不存在",
                              severity="error", blocking=True)
                continue
            p = as_float(row.get("p_kw"))
            if p is None:
                pmw = as_float(row.get("p_load_mw"))
                p = pmw * 1000.0 if pmw is not None else None
            q = as_float(row.get("q_kvar"))
            if q is None:
                qm = as_float(row.get("q_load_mvar"))
                q = qm * 1000.0 if qm is not None else None
            if p is None and q is None:
                continue
            bundle.loads.append({
                "load_id": lid, "node_id": bid,
                "phase": as_str(row.get("phase")) or "ABC",
                "connection": as_str(row.get("conn")) or "wye",
                "profile_id": "", "model_type": "constant_pq",
                "in_service": True,
                "_p_kw": p or 0.0, "_q_kvar": q or 0.0,
            })
    else:
        # 从母线表派生负荷
        for b in bundle.buses:
            p, q = b.pop("_p_kw", None), b.pop("_q_kvar", None)
            if p is None and q is None:
                continue
            if (p or 0.0) == 0.0 and (q or 0.0) == 0.0:
                continue
            bundle.loads.append({
                "load_id": f"LD{b['node_id']}", "node_id": as_str(b["node_id"]),
                "phase": as_str(b.get("phase")) or "ABC",
                "connection": "wye", "profile_id": "",
                "model_type": "constant_pq", "in_service": True,
                "_p_kw": p or 0.0, "_q_kvar": q or 0.0,
            })
    for b in bundle.buses:
        b.pop("_p_kw", None)
        b.pop("_q_kvar", None)
        b.pop("_type", None)

    report.source_format = "table"
    report.elements_with_phase = max(report.elements_with_phase,
                                     report.assumed_three_phase and 0 or 0)


def convert_table(path: Path, bundle: InputBundle,
                  report: ConversionReport,
                  collector: IssueCollector) -> None:
    """xlsx / csv / json → InputBundle。"""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        sheets = {k.lower(): v for k, v in _read_xlsx_sheets(path).items()}
        _rows_to_bundle(sheets, bundle, report, collector, path.name)
    elif suffix == ".csv":
        rows = _read_csv_rows(path)
        # 混合行：按 type 列分组；否则当作支路表
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            t = as_str(r.get("type") or r.get("sheet")).lower()
            key = {"bus": "bus", "buses": "bus", "母线": "bus",
                   "line": "line", "lines": "line", "branch": "line",
                   "支路": "line", "load": "load", "loads": "load",
                   "system": "system"}.get(t)
            if key:
                grouped.setdefault(key, []).append(r)
        if not grouped:
            collector.add(file=path.name, code="SCHEMA_MISSING_FIELD",
                          message="CSV 缺少 type 列，且无法判定内容类型；"
                                  "请提供含 type 列的混合表，或按工作表分文件",
                          severity="error", blocking=True)
            return
        _rows_to_bundle(grouped, bundle, report, collector, path.name)
    elif suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise InputBlockedError("JSON 顶层必须为对象")
        sheets: Dict[str, List[Dict[str, Any]]] = {}
        for k, v in data.items():
            if isinstance(v, list):
                sheets[k.lower()] = [r for r in v if isinstance(r, dict)]
        _rows_to_bundle(sheets, bundle, report, collector, path.name)


# ---------------------------------------------------------------- 入口
def convert_case(source, *, collector: Optional[IssueCollector] = None
                 ) -> Tuple[InputBundle, ConversionReport]:
    """把外部算例转换为本工程的 InputBundle。

    支持 ``.xlsx`` / ``.csv`` / ``.json`` / ``.m``（MATPOWER）。
    返回 ``(bundle, report)``；``report`` 说明转换保真度与所做假设。
    """
    src = Path(source).resolve()
    col = collector or IssueCollector()
    if not src.exists():
        raise InputBlockedError(f"算例文件不存在：{src}")
    suffix = src.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise InputBlockedError(
            f"不支持的算例格式 {suffix}；支持 {list(SUPPORTED_SUFFIXES)}")

    bundle = InputBundle(root=src.parent, collector=col)
    report = ConversionReport(source_format=suffix.lstrip("."),
                              source_path=str(src))
    report.warnings.append(
        "由外部格式转换而来的算例是**近似等价模型**，"
        "结果不能直接视为原始求解器的等价复现，需交叉核验")
    report.warnings.append(
        "PV/PQ 无功限值、控制器与部分基准语义可能未完全保留")

    if suffix == ".m":
        convert_matpower(src, bundle, report, col)
    else:
        convert_table(src, bundle, report, col)

    # 元信息：转换而来的算例没有原生 meta，补齐最小集
    bundle.meta = {
        "schema_version": "hc-input-v1",
        "snapshot_id": f"CONV-{src.stem}",
        "topology_version": "TOPO-CONVERTED",
        "asset_version": "ASSET-CONVERTED",
        "valid_from": "1970-01-01T00:00:00+00:00",
        "valid_to": "2099-12-31T00:00:00+00:00",
        "base_frequency_hz": "50",
        "timezone": "+08:00",
        "source_system": f"converter:{report.source_format}",
    }
    report.notes.append(
        f"转换器版本 {CONVERTER_VERSION}；已转换 "
        f"{len(bundle.buses)} 母线 / {len(bundle.lines)} 线路 / "
        f"{len(bundle.transformers)} 变压器 / {len(bundle.loads)} 负荷")
    return bundle, report
