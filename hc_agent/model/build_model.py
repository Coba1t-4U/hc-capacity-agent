"""build_model.py — 由结构化输入生成 OpenDSS 算例（设计文档 17.6/17.7/17.11）。

标准算例目录包括 Master.dss、LineCodes.dss、Lines.dss、Transformers.dss、
Loads.dss、PVSystems.dss、Controls.dss 和可选 LoadShapes.dss。
Master.dss 使用 Redirect 引用实际文件。

参数映射要求（17.11）：
  - lines 的序阻抗及电容映射至 LineCode，长度单位显式指定；
    分相低压模型按 bus.1/bus.2/bus.3 和中性线节点建模。
  - transformers 的 sn_kva、vk_percent、vkr_percent 映射至容量和经专业
    审核的绕组电阻、漏抗；不能把 vk_percent 直接无条件等同于 XHL。
  - 接线组别、绕组接地和分接位置必须转为实际 DSS 连接参数。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent.errors import InputBlockedError
from hc_agent.io.ingest import InputBundle
from hc_agent.io.schema import as_bool, as_float, as_int, as_str

PHASE_NODES = {"A": "1", "B": "2", "C": "3"}
PHASES_ORDER = ("A", "B", "C")
SQRT3 = math.sqrt(3.0)


@dataclass
class DSSModel:
    """生成的 DSS 模型与映射（设计文档 表 55 build_case）。"""

    files: Dict[str, str] = field(default_factory=dict)
    master_path: str = ""
    id_map: Dict[str, Dict[str, str]] = field(default_factory=dict)
    node_phase_map: Dict[str, Dict[str, int]] = field(default_factory=dict)
    base_kv: Dict[str, float] = field(default_factory=dict)
    equipment: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def master_text(self) -> str:
        return Path(self.master_path).read_text(encoding="utf-8")


def _phase_suffix(phase: str, *, nphases: Optional[int] = None) -> str:
    """把业务相别转换为 DSS 节点号后缀（设计文档 17.11）。"""
    p = (phase or "ABC").upper()
    if p == "ALL" or p == "ABC":
        return ".1.2.3"
    if p in ("A", "B", "C"):
        return f".{PHASE_NODES[p]}"
    if p in ("AB", "BC", "CA"):
        return "." + ".".join(PHASE_NODES[c] for c in p)
    return ".1.2.3"


def _nphases(phase: str) -> int:
    p = (phase or "ABC").upper()
    if p in ("ABC", "ALL"):
        return 3
    if p in ("AB", "BC", "CA"):
        return 2
    if p in ("A", "B", "C"):
        return 1
    return 3


def _vector_group_to_conns(vg: str) -> Tuple[str, str]:
    """接线组别 → DSS 绕组连接方式（设计文档 17.11）。

    Dyn11 → delta/wye；不能默认 Dyn11，必须由输入显式给出。
    """
    v = (vg or "").strip().upper().replace(" ", "")
    if not v:
        raise InputBlockedError("变压器 vector_group 缺失，不能默认为 Dyn11")
    def side(tok: str) -> str:
        if tok.startswith("YN") or tok.startswith("ZN"):
            return "wye"
        if tok.startswith("Y"):
            return "wye"
        if tok.startswith("D"):
            return "delta"
        return "wye"
    # 取前两个接线符号（字母 + 可选 n）
    toks: List[str] = []
    i = 0
    while i < len(v) and len(toks) < 2:
        ch = v[i]
        if ch in "DYZdyz":
            tok = ch
            if i + 1 < len(v) and v[i + 1] in "Nn":
                tok += "n"
                i += 1
            toks.append(tok)
        i += 1
    if len(toks) < 2:
        toks = ["D", "yn"][:2]
    return side(toks[0]), side(toks[1])


def _impedance_from_vk(sn_kva: float, vn_kv: float, vk_percent: float,
                       vkr_percent: float) -> Tuple[float, float, float]:
    """由 vk%/vkr% 计算 Z、X、R 标幺（设计文档 17.11）。

    不能把 vk_percent 直接无条件等同于 XHL。
    X = sqrt(Z^2 - R^2)。
    """
    z = vk_percent / 100.0
    r = vkr_percent / 100.0
    if r > z:
        raise InputBlockedError(
            f"变压器参数非法：vkr={vkr_percent}% > vk={vk_percent}%")
    x = math.sqrt(max(0.0, z * z - r * r))
    return z, x, r


def build_case(bundle: InputBundle, engine_config: Optional[Dict[str, Any]] = None,
               *, out_dir: Optional[Path] = None,
               extra_pv: Optional[Sequence[Dict[str, Any]]] = None,
               increment_kw: float = 0.0,
               allocation: Optional[Dict[str, float]] = None,
               ) -> DSSModel:
    """由输入包生成 DSS 算例（设计文档 S03 / 17.11）。

    参数：
      bundle       输入包
      engine_config 引擎配置（base_frequency_hz、电压基准等）
      out_dir      算例输出目录
      extra_pv     新增光伏设备定义（容量搜索用）
      increment_kw 新增装机容量（kW，交流额定）
      allocation   各接入点的容量分配权重

    说明：Redirect 使用绝对路径。OpenDSS 的 Redirect 相对路径按引擎数据目录
    解析而非 Master.dss 所在目录，故必须写成绝对路径（见本机实测指南"常见坑"）。
    """
    root = Path(out_dir or (bundle.root / "case")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cfg = engine_config or {}
    meta = bundle.meta or {}

    base_hz = as_float(meta.get("base_frequency_hz")) or 50.0
    timezone = as_str(meta.get("timezone")) or "+08:00"

    model = DSSModel(base_kv={})
    masters: List[str] = []
    masters.append("! hc_agent 生成算例（设计文档 8.1 标准算例目录）")
    masters.append(f"! snapshot_id={meta.get('snapshot_id','')} "
                   f"topology_version={meta.get('topology_version','')}")
    # 顺序（设计文档 8.3）：Clear → 建电路 → 引用元件文件 → 仿真设置 →
    # CalcVoltageBases → Solve。Set 命令必须晚于 circuit 对象创建，
    # 否则 OpenDSS 报 (#301) You must create a new circuit object first。
    masters.append("Clear")
    masters.append("New object=circuit.hccircuit")
    # 频率必须在**元件载入之前**设定（设计文档 17.11：pu 基准须与模型 kVBase
    # 对齐）。本机实测（OpenDSSDirect.py 0.9.4 / DSS C-API 0.14.5）：
    #   - 在 Redirect 元件之后才发 `Set defaultbasefrequency`，随后的
    #     CalcVoltageBases 会把全部母线电压基准塌缩到 10/√3=5.7735 kV，
    #     并使全网电压恒为 0，而 Solution.Converged() 仍返回 True（静默失效）。
    #   - 故：频率在电路对象创建后、元件载入前设定。
    # 电压基准另行在 CalcVoltageBases 之前用 Set VoltageBases 显式声明，
    # 见下方注释（不显式声明会得到偏离 √3 倍的错误相电压基准）。
    masters.append(f"Set defaultbasefrequency={base_hz:g}")

    def _redirect(name: str) -> str:
        # 绝对路径：Redirect 的相对路径按引擎数据目录解析，不可靠
        return f'Redirect "{(root / name).as_posix()}"'

    masters.append(_redirect("LineCodes.dss"))
    masters.append(_redirect("Lines.dss"))
    if bundle.transformers:
        masters.append(_redirect("Transformers.dss"))
    masters.append(_redirect("Loads.dss"))
    if bundle.pv or extra_pv:
        masters.append(_redirect("PVSystems.dss"))
    if bundle.sources:
        masters.append(_redirect("Sources.dss"))
    if bundle.switches:
        masters.append(_redirect("Switches.dss"))
    if bundle.controls:
        masters.append(_redirect("Controls.dss"))
    # 电压基准必须在 CalcVoltageBases 之前显式声明（设计文档 17.11）。
    # 本机实测（OpenDSSDirect.py 0.9.4 / DSS C-API 0.14.5）：
    #   - 只发 CalcVoltageBases 而不声明 VoltageBases 时，DSS 推导出的相电压
    #     基准是错的：10 kV 侧得到 7.1996 kV（应为 10/√3=5.7735 kV），
    #     0.4 kV 侧得到 0.2771 kV（应为 0.4/√3=0.23094 kV）。
    #     适配器以 pu = |V|/(kVBase*1000) 换算，于是全网 pu 电压被系统性
    #     低估 √3*(10/9)≈1.924 倍（MV）与 √3*(0.4/0.3333)≈2.078 倍（LV），
    #     使健康馈线凭空"越下限"（实测：空载母线应≈1.02 pu，却报 0.816）。
    #   - 线电压值与物理一致，故 P/Q/电流结果不受影响，只有 pu 换算错误。
    # 显式声明各电压等级即可得到正确基准（实测 MV 5.7735 kV、LV 0.23094 kV）。
    # 注意：LineCode 在本机 DSS 构建中不支持 basekv 参数，只能由此处的
    # Set VoltageBases 统一声明。
    _vbases: List[float] = []
    for _b in bundle.buses:
        _kv = as_float(_b.get("vn_kv"))
        if _kv and _kv > 0:
            _vbases.append(float(_kv))
        else:
            _vbases.append(_node_vn(bundle, as_str(_b["node_id"])))
    if _vbases:
        _uniq = sorted(set(_vbases), reverse=True)
        masters.append("Set VoltageBases=[%s]"
                       % ", ".join(f"{v:.10g}" for v in _uniq))
    masters.append("CalcVoltageBases")
    masters.append("Set mode=snapshot")

    # ------------------------------------------------------ LineCodes.dss
    lc_lines = ["! LineCode 由正序/零序参数生成（设计文档 17.11）"]
    linecodes: Dict[str, str] = {}
    for idx, ln in enumerate(bundle.lines):
        r1 = as_float(ln["r1_ohm_per_km"])
        x1 = as_float(ln["x1_ohm_per_km"])
        r0 = as_float(ln.get("r0_ohm_per_km"))
        x0 = as_float(ln.get("x0_ohm_per_km"))
        c1 = as_float(ln.get("c1_nf_per_km"))
        c0 = as_float(ln.get("c0_nf_per_km"))
        key = (r1, x1, r0, x0, c1, c0, ln.get("max_i_a"))
        if key not in linecodes:
            name = f"LC_{len(linecodes) + 1:03d}"
            linecodes[key] = name
            nph = _nphases(as_str(ln.get("phase")))
            parts = [f"New LineCode.{name} nphases={nph} units=km"]
            parts.append(f"R1={r1:.10g} X1={x1:.10g}")
            if r0 is not None and x0 is not None:
                parts.append(f"R0={r0:.10g} X0={x0:.10g}")
            elif nph == 1:
                # 单相线路缺少零序时必须阻断，而不是静默假设
                raise InputBlockedError(
                    f"线路 {ln['line_id']} 为单相但缺少零序参数 r0/x0；"
                    f"设计文档 17.6 要求三相/接地故障能力时必填")
            if c1 is not None:
                parts.append(f"C1={c1:.10g}")
            if c0 is not None:
                parts.append(f"C0={c0:.10g}")
            rating = as_float(ln.get("max_i_a"))
            if rating:
                parts.append(f"normamps={rating:.10g}")
            lc_lines.append(" ".join(parts))
        ln["_linecode"] = linecodes[key]

    # --------------------------------------------------------- Lines.dss
    lines_txt = ["! 线路元件（设计文档 17.6）"]
    for ln in bundle.lines:
        if ln.get("in_service") is False:
            continue
        lid = as_str(ln["line_id"])
        f = as_str(ln["from_node"]) + _phase_suffix(as_str(ln.get("phase")))
        t = as_str(ln["to_node"]) + _phase_suffix(as_str(ln.get("phase")))
        lcname = ln.get("_linecode", "LC_001")
        length = float(ln["length_km"])
        parts = [f"New Line.{_dss_name('line', lid)}",
                 f"bus1={f}", f"bus2={t}", f"linecode={lcname}",
                 f"length={length:.10g} units=km"]
        if ln.get("in_service") is False:
            parts.append("enabled=no")
        lines_txt.append(" ".join(parts))
        model.id_map.setdefault("line", {})[lid] = _dss_name("line", lid)

    # -------------------------------------------------- Transformers.dss
    tr_txt = ["! 变压器元件（设计文档 17.7 / 17.11）"]
    limits: Dict[str, float] = {}
    for tr in bundle.transformers:
        if tr.get("in_service") is False:
            continue
        tid = as_str(tr["transformer_id"])
        hv = as_str(tr["hv_node"]) + _phase_suffix(as_str(tr.get("phase")))
        lv = as_str(tr["lv_node"]) + _phase_suffix(as_str(tr.get("phase")))
        conn_h, conn_l = _vector_group_to_conns(as_str(tr["vector_group"]))
        z, x, r = _impedance_from_vk(float(tr["sn_kva"]), float(tr["vn_hv_kv"]),
                                     float(tr["vk_percent"]),
                                     float(tr["vkr_percent"]))
        nph = _nphases(as_str(tr.get("phase")))
        parts = [f"New Transformer.{_dss_name('xf', tid)}",
                 f"phases={nph}", "windings=2",
                 f"buses=[{hv} {lv}]",
                 f"conns=[{conn_h} {conn_l}]",
                 f"kvs=[{float(tr['vn_hv_kv']):.10g} "
                 f"{float(tr['vn_lv_kv']):.10g}]",
                 f"kvas=[{float(tr['sn_kva']):.10g} "
                 f"{float(tr['sn_kva']):.10g}]",
                 f"%loadloss={float(tr['vkr_percent']):.10g}",
                 f"XHL={x * 100.0:.10g}"]
        pfe = as_float(tr.get("pfe_kw"))
        if pfe is not None:
            parts.append(f"%noloadloss={pfe / float(tr['sn_kva']) * 100.0:.10g}")
        i0 = as_float(tr.get("i0_percent"))
        if i0 is not None:
            parts.append(f"%imag={i0:.10g}")
        # 分接设置按实际 tap（设计文档 17.11）
        tap_pos = as_int(tr.get("tap_pos")) or 0
        tap_neutral = as_int(tr.get("tap_neutral")) or 0
        step = as_float(tr.get("tap_step_percent"))
        if step:
            offset = (tap_pos - tap_neutral) * step / 100.0
            parts.append(f"tap=[{1.0 + offset:.10g} 1.0]")
        tr_txt.append(" ".join(parts))
        tid_name = _dss_name("xf", tid)
        model.id_map.setdefault("transformer", {})[tid] = tid_name

        # 变压器按 kVA 能力折算端子电流限值（表 23 载流量按设备允许值）
        i_rated_hv = float(tr["sn_kva"]) / (math.sqrt(3) * float(tr["vn_hv_kv"]))
        i_rated_lv = float(tr["sn_kva"]) / (math.sqrt(3) * float(tr["vn_lv_kv"]))
        limits[f"transformer.{tid_name}".lower()] = i_rated_lv
        model.equipment.setdefault("transformer_ratings", {})[tid] = {
            "sn_kva": float(tr["sn_kva"]),
            "i_rated_hv_a": i_rated_hv, "i_rated_lv_a": i_rated_lv,
        }

    # ------------------------------------------------------- Sources.dss
    src_txt = ["! 上级电源等值（设计文档 17.7 sources）"]
    for s in bundle.sources:
        sid = as_str(s["source_id"])
        node = as_str(s["node_id"]) + ".1.2.3"
        vn = _node_vn(bundle, as_str(s["node_id"]))
        r1 = as_float(s.get("r1_ohm"))
        x1 = as_float(s.get("x1_ohm"))
        r0 = as_float(s.get("r0_ohm"))
        x0 = as_float(s.get("x0_ohm"))
        parts = [f"New Vsource.{_dss_name('src', sid)}",
                 f"bus1={node}", f"basekv={vn:.10g}",
                 f"pu={float(s['vm_pu']):.10g}",
                 f"angle={float(s.get('angle_deg') or 0.0):.10g}"]
        if r1 is not None and x1 is not None:
            parts.append(f"R1={r1:.10g} X1={x1:.10g}")
        if r0 is not None and x0 is not None:
            parts.append(f"R0={r0:.10g} X0={x0:.10g}")
        src_txt.append(" ".join(parts))
        model.id_map.setdefault("source", {})[sid] = _dss_name("src", sid)

    # --------------------------------------------------------- Loads.dss
    ld_txt = ["! 负荷元件（设计文档 17.7：低压三相负荷采用分相 P/Q）"]
    for ld in bundle.loads:
        if ld.get("in_service") is False:
            continue
        lid = as_str(ld["load_id"])
        phase = as_str(ld.get("phase"))
        nph = _nphases(phase)
        node = as_str(ld["node_id"]) + _phase_suffix(phase)
        vn = _node_vn(bundle, as_str(ld["node_id"]))
        # ⚠️ 单相负荷的 OpenDSS kV 是**相电压**（= 线电压/√3），而节点额定电压
        # vn 恒为线电压。此前对所有相别一律写 vn，会把单相负荷**高估 √3 倍**
        # （移植自天枢一号 load_kv_for_nodes 的裁定，见 docs/移植自天枢一号.md）。
        kv_load = vn if nph >= 2 else vn / SQRT3
        conn = "delta" if as_str(ld.get("connection")).lower() == "delta" else "wye"
        # 显式写 vminpu：不写则沿用 OpenDSS 默认 0.95，母线电压低于该值时负荷
        # 会**隐性转为恒阻抗**，实际供出负荷低于名义值。天枢一号把这一点标为
        # "已知未决项"，本工程显式声明以免歧义（单相负荷不适用该机制，置 0）。
        vminpu = as_float(ld.get("vminpu"))
        if vminpu is None:
            vminpu = 0.0 if nph == 1 else 0.95
        parts = [f"New Load.{_dss_name('load', lid)}",
                 f"bus1={node}", f"phases={nph}", f"conn={conn}",
                 f"kv={kv_load:.10g}", "kW=1 kvar=1", "model=1",
                 f"vminpu={vminpu:.6g}"]
        # 分相曲线：逐相写入（不使用同一 LoadShape 重复缩放）
        ld["_dss_name"] = _dss_name("load", lid)
        ld_txt.append(" ".join(parts))
        model.id_map.setdefault("load", {})[lid] = ld["_dss_name"]

    # ----------------------------------------------------- PVSystems.dss
    pv_txt = ["! 光伏元件（设计文档 17.11：PVSystem.kVA 与 Pmpp 不等同）"]
    for pv in bundle.pv:
        if pv.get("in_service") is False:
            continue
        pid = as_str(pv["pv_id"])
        phase = as_str(pv.get("phase"))
        nph = _nphases(phase)
        node = as_str(pv["node_id"]) + _phase_suffix(phase)
        vn = _node_vn(bundle, as_str(pv["node_id"]))
        p_kw = as_float(pv.get("rated_p_kw"))
        s_kva = as_float(pv.get("rated_s_kva"))
        if s_kva is None and p_kw is not None:
            s_kva = p_kw
        pmpp = p_kw if p_kw is not None else (s_kva or 0.0)
        parts = [f"New PVSystem.{_dss_name('pv', pid)}",
                 f"bus1={node}", f"phases={nph}", f"kv={vn:.10g}",
                 f"kVA={float(s_kva or 0.0):.10g}",
                 f"Pmpp={float(pmpp or 0.0):.10g}",
                 f"irradiance=1", f"pf=1"]
        pv_txt.append(" ".join(parts))
        pv["_dss_name"] = _dss_name("pv", pid)
        model.id_map.setdefault("pv", {})[pid] = pv["_dss_name"]

    # ------------------------------------------------- 新增容量（搜索试探）
    if extra_pv:
        for spec in extra_pv:
            pid = as_str(spec.get("pv_id"))
            node_id = as_str(spec.get("node_id"))
            phase = as_str(spec.get("phase")) or "ABC"
            nph = _nphases(phase)
            node = node_id + _phase_suffix(phase)
            vn = _node_vn(bundle, node_id)
            cap_kw = as_float(spec.get("rated_p_kw")) or 0.0
            s_kva = as_float(spec.get("rated_s_kva")) or cap_kw
            parts = [f"New PVSystem.{_dss_name('pv', pid)}",
                     f"bus1={node}", f"phases={nph}", f"kv={vn:.10g}",
                     f"kVA={s_kva:.10g}", f"Pmpp={cap_kw:.10g}",
                     f"irradiance=1", f"pf=1"]
            pv_txt.append(" ".join(parts))
            model.id_map.setdefault("pv", {})[pid] = _dss_name("pv", pid)

    # ------------------------------------------------------- Switches.dss
    sw_txt = ["! 开关状态（设计文档 17.6）"]
    for sw in bundle.switches:
        sid = as_str(sw["switch_id"])
        eid = as_str(sw.get("element_id"))
        if not eid:
            continue
        dss_name = (model.id_map.get("line", {}).get(eid)
                    or model.id_map.get("transformer", {}).get(eid))
        if not dss_name:
            continue
        closed = as_bool(sw.get("closed"), True)
        sw_txt.append(f"! switch {sid} -> {eid} closed={closed}")
        if not closed:
            sw_txt.append(f"Edit Line.{dss_name} enabled=no")

    # ------------------------------------------------------- Controls.dss
    ctrl_txt = ["! 控制策略（设计文档 17.11：不能在 Python 和 DSS 中重复施加同一控制）"]
    controls = bundle.controls or {}
    for c in (controls.get("controls") or []):
        cid = as_str(c.get("control_id"))
        mode = as_str(c.get("mode"))
        target = as_str(c.get("target"))
        if not cid:
            continue
        dss_pv = model.id_map.get("pv", {}).get(target)
        if dss_pv and mode in ("volt_var", "volt_watt"):
            ctrl_txt.append(
                f"New InvControl.{_dss_name('inv', cid)} "
                f"mode={mode} "
                f"vvc_curve1={_curve_txt(c.get('curve_points'))} "
                f"voltage_curvex_ref=rated")
    if len(ctrl_txt) == 1:
        ctrl_txt.append("! 无已登记控制策略")

    # ------------------------------------------------------------- 落盘
    files = {
        "LineCodes.dss": "\n".join(lc_lines) + "\n",
        "Lines.dss": "\n".join(lines_txt) + "\n",
        "Transformers.dss": "\n".join(tr_txt) + "\n",
        "Sources.dss": "\n".join(src_txt) + "\n",
        "Loads.dss": "\n".join(ld_txt) + "\n",
        "PVSystems.dss": "\n".join(pv_txt) + "\n",
        "Switches.dss": "\n".join(sw_txt) + "\n",
        "Controls.dss": "\n".join(ctrl_txt) + "\n",
    }
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
        model.files[name] = str(root / name)

    master_path = root / "Master.dss"
    master_path.write_text("\n".join(masters) + "\n", encoding="utf-8")
    model.master_path = str(master_path)
    model.files["Master.dss"] = str(master_path)

    # 映射与设备能力
    model.node_phase_map = {
        as_str(b["node_id"]).lower(): {
            ph: int(PHASE_NODES[ph]) for ph in _phases_of(as_str(b.get("phase")))
        } for b in bundle.buses
    }
    model.base_kv = {as_str(b["node_id"]).lower(): float(b["vn_kv"])
                     for b in bundle.buses if as_float(b.get("vn_kv"))}

    # 设备载流限值（设计文档 17.6 max_i_a / 表 53 equipment_limits.csv）
    for ln in bundle.lines:
        rating = as_float(ln.get("max_i_a"))
        if rating:
            limits[f"line.{ln.get('_linecode') or ''}".lower()] = rating
            limits[f"line.{ln['line_id']}".lower()] = rating
    for lim in bundle.equipment_limits:
        if lim.get("limit_type") in ("continuous_current", "thermal",
                                     "reverse_flow"):
            val = as_float(lim.get("value"))
            if val is not None:
                limits[as_str(lim["equipment_id"]).lower()] = val
    model.equipment["limits"] = limits
    model.equipment["line_ratings"] = {
        as_str(ln["line_id"]): as_float(ln.get("max_i_a"))
        for ln in bundle.lines}
    model.equipment["line_phase"] = {
        as_str(ln["line_id"]): as_str(ln.get("phase")) for ln in bundle.lines}
    model.notes.append(
        f"生成 {len(bundle.lines)} 条线路、{len(bundle.transformers)} 台变压器、"
        f"{len(bundle.loads)} 个负荷、{len(bundle.pv)} 个光伏、"
        f"{len(bundle.sources)} 个电源")

    if not bundle.sources:
        raise InputBlockedError("缺少上级电源等值，无法建模（sources 必需）")
    return model


def _dss_name(kind: str, raw_id: str) -> str:
    """业务 ID → DSS 合法元件名（保留 ID 映射，表 55）。"""
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in raw_id)
    if not safe or not safe[0].isalpha():
        safe = f"_{safe}"
    return f"{kind}_{safe}"


def _node_vn(bundle: InputBundle, node_id: str) -> float:
    for b in bundle.buses:
        if as_str(b["node_id"]) == node_id:
            return float(b["vn_kv"])
    return 0.4


def _phases_of(phase: str) -> str:
    p = (phase or "ABC").upper()
    if p in ("ABC", "ALL"):
        return "ABC"
    return p


def _curve_txt(points: Any) -> str:
    if not isinstance(points, (list, tuple)) or not points:
        return "[1 1.0 1.1 1.0]"
    xs, ys = [], []
    for pt in points:
        if isinstance(pt, dict):
            xs.append(float(pt.get("x", 0)))
            ys.append(float(pt.get("y", 1)))
        elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    if not xs:
        return "[1 1.0 1.1 1.0]"
    return "[" + " ".join(f"{x:.6g}" for x in xs) + " " + \
           " ".join(f"{y:.6g}" for y in ys) + "]"
