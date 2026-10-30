"""dss_case.py — 直接分析 OpenDSS 算例（.dss）。

## 为什么单独一条路径，而不是塞进现有转换器

现有转换器（`io/converters.py`）把外部表格**转成本工程的多文件模型**，再由
`build_model.py` 生成 DSS。那条路适合 xlsx/csv/MATPOWER，但对 `.dss` 是错的：

* `.dss` 本身就是 OpenDSS 的原生算例，**再转一手只会丢失信息**（控制器、
  LoadShape、谐波、自定义元件都可能丢）；
* 用户拿 IEEE13/IEEE37 这类标准算例，期待的是"OpenDSS 怎么算就怎么报"。

所以这里**直接交给引擎跑**：`Redirect → Solve → 读回全部结果`。不做模型重建，
也不假装它是本工程的承载力算例 —— 报告里会明确标注这是"原生算例分析"。

## 输出什么

一次潮流 + 一句容量判读所需的基础量：
母线电压（含最值）、线路/变压器负载率、总负荷、源侧供电、网损、收敛情况。
**不产出"开放容量"** —— 那个结论需要本工程的梯度/二分搜索与规则包，
对原生算例不适用。这一点必须在结果里说清楚，不能让用户误以为拿到了承载力。
"""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hc_agent import runtime_env
from hc_agent.errors import ComputeFailedError, InputBlockedError


@dataclass
class BusResult:
    name: str
    phases: int
    vm_pu: float
    vmin_pu: float
    va_deg: float
    kv_base: float

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "phases": self.phases,
                "vm_pu": self.vm_pu, "vmin_pu": self.vmin_pu,
                "va_deg": self.va_deg, "kv_base": self.kv_base}


@dataclass
class BranchResult:
    name: str
    kind: str            # Line / Transformer
    from_bus: str
    to_bus: str
    loading_pct: float
    p_kw: float
    q_kvar: float
    rating_kva: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "kind": self.kind,
                "from_bus": self.from_bus, "to_bus": self.to_bus,
                "loading_pct": self.loading_pct, "p_kw": self.p_kw,
                "q_kvar": self.q_kvar, "rating_kva": self.rating_kva}


@dataclass
class CaseAnalysis:
    """一次 OpenDSS 原生算例的分析结果。"""

    source: str = ""
    source_name: str = ""
    engine: str = ""
    converged: bool = False
    iterations: int = 0
    elapsed_s: float = 0.0
    buses: List[BusResult] = field(default_factory=list)
    branches: List[BranchResult] = field(default_factory=list)
    total_load_kw: float = 0.0
    total_load_kvar: float = 0.0
    #: 分布式电源有功总出力（用于网损的能量平衡与反向潮流判读）
    total_gen_kw: float = 0.0
    source_kw: float = 0.0
    #: 源侧交换无功（引擎符号：注入为正，受电为负）
    source_kvar: float = 0.0
    losses_kw: float = 0.0
    #: 网损无功：按同一条能量平衡式在无功侧求得
    losses_kvar: float = 0.0
    losses_pct: float = 0.0
    #: 明确声明"本次没有产出开放容量"，避免与承载力结论混淆
    capacity_note: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def bus_count(self) -> int:
        return len(self.buses)

    def worst_voltage(self) -> Optional[BusResult]:
        cand = [b for b in self.buses if b.vmin_pu > 1e-6]
        return min(cand, key=lambda b: b.vmin_pu) if cand else None

    def best_voltage(self) -> Optional[BusResult]:
        cand = [b for b in self.buses if b.vmin_pu > 1e-6]
        return max(cand, key=lambda b: b.vmin_pu) if cand else None

    def worst_branch(self) -> Optional[BranchResult]:
        cand = [b for b in self.branches if b.loading_pct > 0]
        return max(cand, key=lambda b: b.loading_pct) if cand else None

    #: 默认电压判据：与本工程规则包 HC-RULES@1.0 的批准值一致（0.90~1.10 pu）。
    #: 不要另外设一套 0.95~1.05 —— 那会让页面与报告给出不同的"越限个数"。
    DEFAULT_VMIN = 0.90
    DEFAULT_VMAX = 1.10

    def voltage_violations(self, vmin: float = DEFAULT_VMIN,
                           vmax: float = DEFAULT_VMAX) -> List[BusResult]:
        return [b for b in self.buses
                if b.vmin_pu > 1e-6 and not (vmin <= b.vmin_pu <= vmax)]

    def overloaded(self, limit: float = 100.0) -> List[BranchResult]:
        return [b for b in self.branches if b.loading_pct > limit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source, "source_name": self.source_name,
            "engine": self.engine, "converged": self.converged,
            "iterations": self.iterations, "elapsed_s": self.elapsed_s,
            "bus_count": self.bus_count,
            "buses": [b.to_dict() for b in self.buses],
            "branches": [b.to_dict() for b in self.branches],
            "total_load_kw": self.total_load_kw,
            "total_load_kvar": self.total_load_kvar,
            "total_gen_kw": self.total_gen_kw,
            "source_kw": self.source_kw,
            "source_kvar": self.source_kvar,
            "losses_kw": self.losses_kw,
            "losses_kvar": self.losses_kvar,
            "losses_pct": self.losses_pct,
            "capacity_note": self.capacity_note,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------- 入口判断
_DSS_SUFFIXES = (".dss",)


def is_dss_case(path) -> bool:
    return Path(path).suffix.lower() in _DSS_SUFFIXES


def resolve_master(path) -> Path:
    """定位可 Redirect 的主文件。

    OpenDSS 算例常有多层结构：
      * 直接给的就是主文件（IEEE13Nodeckt.dss / ieee37.dss）
      * 给的是目录 → 找 Master.dss
      * 给的是子文件（如 IEEELineCodes.DSS）→ 找同目录 Master.dss
    """
    p = Path(path).resolve()
    if p.is_dir():
        for cand in ("Master.dss", "master.dss", "MASTER.DSS"):
            f = p / cand
            if f.is_file():
                return f
        dss = sorted(p.glob("*.dss")) + sorted(p.glob("*.DSS"))
        if dss:
            return dss[0]
        raise InputBlockedError(f"目录里没有 .dss 文件：{p}")
    if not p.is_file():
        raise InputBlockedError(f"算例文件不存在：{p}")
    if p.name.lower() != "master.dss":
        # 同目录有 Master.dss 就优先用它（OpenDSS 习惯）
        m = p.parent / "Master.dss"
        if m.is_file() and m != p:
            return m
    return p


# ---------------------------------------------------------------- 分析
def analyze(path, *, vmin_pu: float = 0.90, vmax_pu: float = 1.10,
            loading_limit: float = 100.0) -> CaseAnalysis:
    """跑一次 OpenDSS 原生算例的潮流并读回结果。"""
    master = resolve_master(path)
    lib = runtime_env.ensure_engine_on_path()
    if lib is None:
        raise ComputeFailedError(
            "OpenDSS 引擎不可用，无法分析 .dss 算例",
            code="DSS_DEPENDENCY_LOAD_FAILED")

    import opendssdirect as dss

    # 算例名：主文件若叫 Master.dss（OpenDSS 惯例），用**目录名**更好认；
    # 否则所有算例在报告里都叫 "Master"（实测踩过）。
    case_name = master.stem
    if master.name.lower() == "master.dss":
        case_name = master.parent.name or case_name

    res = CaseAnalysis(source=str(master),
                       source_name=case_name,
                       engine=str(dss.Basic.Version())[:120])
    t0 = time.time()
    d = dss.NewContext()
    try:
        d.Text.Command("Clear")
        try:
            d.Text.Command(f'Redirect "{master}"')
        except Exception as exc:
            raise ComputeFailedError(
                f"算例编译失败：{exc}", code="DSS_COMPILE_FAILED") from exc
        # snapshot 模式：只算一个稳态断面，符合"算例分析"的语义
        d.Text.Command("Set mode=snapshot")

        try:
            d.Solution.Solve()
        except Exception as exc:
            raise ComputeFailedError(
                f"潮流求解失败：{exc}", code="DSS_NOT_CONVERGED") from exc
        res.converged = bool(d.Solution.Converged())
        res.iterations = int(d.Solution.Iterations())
        res.elapsed_s = round(time.time() - t0, 3)

        # 基准电压防呆：某些算例在元件定义完之前就调用 CalcVoltageBases，
        # 导致母线 kVBase=0。此时 `puVmagAngle()` 会**返回伏特而不是标幺值**
        # （实测：6166 而不是 1.017），后面所有电压判据都会失效。
        # 这里检测到就补算一次并留下告警，而不是拿错量纲继续算。
        zero_base = 0
        for name in d.Circuit.AllBusNames():
            try:
                d.Circuit.SetActiveBus(name)
                if float(d.Bus.kVBase()) <= 1e-9:
                    zero_base += 1
            except Exception:
                continue
        if zero_base:
            try:
                d.Text.Command("CalcVoltageBases")
                d.Solution.Solve()
                res.warnings.append(
                    f"有 {zero_base} 个母线基准电压为 0，已自动补算 "
                    f"CalcVoltageBases 并重解一次（原始算例的基准设置可能不完整）。")
                res.iterations = int(d.Solution.Iterations())
                res.converged = bool(d.Solution.Converged())
            except Exception:
                res.warnings.append(
                    f"有 {zero_base} 个母线基准电压为 0，补算失败，"
                    f"电压标幺值可能不可用。")

        # ---- 母线电压
        for name in d.Circuit.AllBusNames():
            try:
                d.Circuit.SetActiveBus(name)
                phases = int(d.Bus.NumNodes())
                kv = float(d.Bus.kVBase())
                mags = d.Bus.puVmagAngle()
                vms = [mags[i] for i in range(0, len(mags), 2)]
                angs = [mags[i] for i in range(1, len(mags), 2)]
                if not vms:
                    continue
                res.buses.append(BusResult(
                    name=str(name), phases=phases,
                    vm_pu=sum(vms) / len(vms),
                    vmin_pu=min(vms),
                    va_deg=(angs[0] if angs else 0.0),
                    kv_base=kv))
            except Exception:
                continue

        # ---- 支路负载
        # 走 `Circuit.AllElementNames()` 逐元素判断类型，而不是分别遍历
        # Lines/Transformers 再切活动元件 —— 后者一旦某步抛异常就会被
        # `except: continue` 静默吞掉，结果是"母线有、支路全 0"（实测踩过）。
        try:
            element_names = list(d.Circuit.AllElementNames())
        except Exception:
            element_names = []
        for full in element_names:
            kind = ""
            if full.startswith("Line."):
                kind = "Line"
            elif full.startswith("Transformer."):
                kind = "Transformer"
            if not kind:
                continue
            try:
                d.Circuit.SetActiveElement(full)
                bus_names = list(d.CktElement.BusNames())
                bus1 = str(bus_names[0]).split(".")[0] if bus_names else ""
                bus2 = (str(bus_names[1]).split(".")[0]
                        if len(bus_names) > 1 else "")
                # `CktElement.Powers()` / `Currents()` 返回的是
                # **每个端子 × 每相**的序列（[P1,Q1,P2,Q2,…]）。
                # 两绕组变压器有 2 个端子，把全部端子加起来功率会互相抵消
                # （实测：220 kW 的线路算出来只有 10 kW，变压器只剩损耗）。
                # 所以只取**送端**的前 nph 相，才是真实的通过功率。
                try:
                    nph = int(d.CktElement.NumPhases()) or 3
                except Exception:
                    nph = 3
                pw = list(d.CktElement.Powers() or [])
                pairs = len(pw) // 2
                take = min(nph, pairs)
                p = sum(float(pw[2 * i]) for i in range(take)) if take else 0.0
                q = sum(float(pw[2 * i + 1]) for i in range(take)) if take else 0.0
                # 电流同理：送端各相取最大值作为"最重相电流"
                cur = list(d.CktElement.Currents() or [])
                i_max = max((abs(float(cur[i])) for i in range(min(take, len(cur)))),
                            default=0.0)
                try:
                    rated = float(d.CktElement.NormalAmps())
                except Exception:
                    rated = 0.0
                loading = (i_max / rated * 100.0) if rated > 1e-9 else 0.0
                rating_kva = None
                if kind == "Transformer":
                    # 变压器按**视在功率 / 额定容量**算负载率，
                    # 不能用有功除以容量（忽略无功会低估）。
                    try:
                        kva = list(d.Transformers.kva() or [])
                        rating_kva = float(kva[0]) if kva else None
                        if rating_kva and rating_kva > 1e-9:
                            loading = math.hypot(p, q) / rating_kva * 100.0
                    except Exception:
                        pass
                res.branches.append(BranchResult(
                    name=str(full.split(".", 1)[1]), kind=kind,
                    from_bus=bus1, to_bus=bus2,
                    loading_pct=round(loading, 4),
                    p_kw=round(p, 6), q_kvar=round(q, 6),
                    rating_kva=rating_kva))
            except Exception:
                continue

        # ---- 功率汇总
        try:
            tp = d.Circuit.TotalPower()
            res.source_kw = round(float(tp[0]), 6)   # 引擎符号：注入为正
            res.source_kvar = round(float(tp[1]), 6)
        except Exception:
            res.source_kw = 0.0
            res.source_kvar = 0.0
        load_kw = load_kvar = 0.0
        try:
            # 逐负荷累加，得到"总负荷"。
            # **不能拿源侧供电当负荷**：两者差一个网损，混用会把损耗算成 0。
            # `Loads` 类没有 Enabled()，要用 CktElement.Enabled()。
            n = int(d.Loads.Count())
            if n > 0 and d.Loads.First() > 0:
                while True:
                    try:
                        if d.CktElement.Enabled():
                            load_kw += float(d.Loads.kW())
                            load_kvar += float(d.Loads.kvar())
                    except Exception:
                        pass
                    if not d.Loads.Next():
                        break
        except Exception:
            pass
        res.total_load_kw = round(load_kw, 6)
        res.total_load_kvar = round(load_kvar, 6)

        # ---- 分布式电源出力（有功总和）----
        gen_kw = 0.0
        try:
            if int(d.Generators.Count()) > 0 and d.Generators.First() > 0:
                while True:
                    try:
                        if d.CktElement.Enabled():
                            gen_kw += float(d.Generators.kW())
                    except Exception:
                        pass
                    if not d.Generators.Next():
                        break
        except Exception:
            pass
        res.total_gen_kw = round(gen_kw, 6)

        # ---- 网损：按**能量平衡**算，不能用 |源侧| − 负荷 ----
        # 那个式子只在"源侧供电"时成立。一旦光伏反送（源侧吸收），
        # 它会把倒送功率也算成损耗（实测：真实 15.7 kW 被算成 34.3 kW、
        # 损耗率 40%，明显不合理）。
        # 正确关系：源侧供给 + 分布式电源出力 = 负荷 + 网损
        source_supply = -res.source_kw        # 正数表示源在供电
        res.losses_kw = round(max(0.0, source_supply + gen_kw - load_kw), 6)
        # 无功侧用同一关系：源侧供入 + 电源无功 − 负荷无功（电源按 pf=1 计 0）
        # 无功不做 max(0,...) 夹取：容性/感性都可能让结果为负，夹取会掩盖
        # 真实的补偿效应，报告里也不该出现"凭空为零"的无功损耗。
        res.losses_kvar = round((-res.source_kvar) - load_kvar, 6)
        if load_kw > 1e-9:
            res.losses_pct = round(res.losses_kw / load_kw * 100.0, 4)
        else:
            res.losses_pct = 0.0

        # ---- 明确声明没有开放容量
        res.capacity_note = (
            "本次为 OpenDSS 原生算例分析：只做潮流与约束校核，"
            "**不产出开放容量**。容量结论需要本工程的梯度/二分搜索与"
            "经批准的规则包，对原生算例不适用。")
        if not res.converged:
            res.warnings.append("潮流未收敛：以下数值不可作为结论使用。")
    finally:
        try:
            close = getattr(d, "Close", None)
            if callable(close):
                close()
        except Exception:
            pass
    return res


def summarize_for_qa(a: CaseAnalysis, *, vmin: float = 0.90,
                     vmax: float = 1.10,
                     loading_limit: float = 100.0) -> Dict[str, Any]:
    """压成问答可用的事实字典（数字全部来自实测）。"""
    wv = a.worst_voltage()
    wb = a.worst_branch()
    return {
        "source": a.source_name,
        "converged": a.converged,
        "iterations": a.iterations,
        "bus_count": a.bus_count,
        "branch_count": len(a.branches),
        "total_load_kw": a.total_load_kw,
        "source_kw": a.source_kw,
        "losses_kw": a.losses_kw,
        "losses_pct": a.losses_pct,
        "min_voltage_pu": (wv.vmin_pu if wv else None),
        "min_voltage_bus": (wv.name if wv else None),
        "max_voltage_pu": (a.best_voltage().vmin_pu
                           if a.best_voltage() else None),
        "max_loading_pct": (wb.loading_pct if wb else None),
        "max_loading_branch": (wb.name if wb else None),
        "voltage_violations": [
            {"bus": b.name, "vmin_pu": b.vmin_pu}
            for b in a.voltage_violations(vmin, vmax)][:20],
        "overloaded": [
            {"branch": b.name, "loading_pct": b.loading_pct}
            for b in a.overloaded(loading_limit)][:20],
        "capacity_note": a.capacity_note,
    }
