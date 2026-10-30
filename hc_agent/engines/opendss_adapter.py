"""OpenDSS 适配器：唯一电气计算引擎调用层（设计文档第 8 章）。

职责（8.2）：
  - 仅建设 OpenDSS 适配器；算例用 Master.dss 与引用文件，
    计算通过 OpenDSSDirect.py 启动。
  - 每次计算创建唯一且隔离的工作目录；目录包含只读输入副本、
    参数清单、原生日志和结构化输出。
  - OpenDSS 异常映射为标准错误码。
  - 禁止通过界面截图或自然语言摘要作为唯一计算证据；
    必须保存机器可读结果与原始引擎产物。

潮流调用顺序（8.3）：
  新建独立上下文 → Compile 经审核的 Master.dss → 设置该场景的开关、分接与控制
  → 写入该时间片负荷和光伏参数 → Solution.Solve() → 检查 Solution.Converged()
  → 按母线节点和设备端子提取结果 → 保存原生模型、参数与输出。
"""

from __future__ import annotations

import math
import os
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from hc_agent import runtime_env
from hc_agent.errors import (
    ERR_DSS_COMPILE_FAILED,
    ERR_DSS_DEPENDENCY_LOAD_FAILED,
    ERR_DSS_NOT_CONVERGED,
    ERR_DSS_OBJECT_ERROR,
    ERR_DSS_PARSE_FAILED,
    ERR_DSS_RESOURCE_EXHAUSTED,
    ERR_DSS_TIMEOUT,
    ERR_DSS_UNKNOWN,
    ComputeFailedError,
)
from hc_agent.evidence import sha256_file, utc_now_iso, write_json

# 相别名 → OpenDSS 节点号（设计文档 17.6：节点 node_id 为字符串主键）
PHASE_TO_NODE = {"A": 1, "B": 2, "C": 3}
PHASE_ORDER = ("A", "B", "C")
SQRT3 = math.sqrt(3.0)

# OpenDSS 内部等值母线：非业务节点，不参与约束判定与电压基准比对
EXCLUDED_BUSES = ("sourcebus",)


@dataclass
class EngineResult:
    """统一引擎返回对象（设计文档 17.11）。

    每个返回对象携带 status、converged、engine_version、artifact_refs 与
    error_code，不能只返回文字或单个容量数字。
    """

    status: str
    converged: bool
    engine_version: str = ""
    artifact_refs: List[str] = field(default_factory=list)
    error_code: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    bus_voltages: List[Dict[str, Any]] = field(default_factory=list)
    element_results: List[Dict[str, Any]] = field(default_factory=list)
    violations: List[Dict[str, Any]] = field(default_factory=list)
    runtime_s: float = 0.0
    iterations: int = 0
    engine_fingerprint: Dict[str, Any] = field(default_factory=dict)
    work_dir: str = ""
    scenario_id: str = ""
    timestamp: str = ""
    increment_kw: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        return d


@dataclass
class CaseRef:
    """算例引用（build_case 的返回值，设计文档 17.11）。"""

    master_dss: str
    work_dir: str
    node_phase_map: Dict[str, Dict[str, int]] = field(default_factory=dict)
    id_map: Dict[str, Dict[str, str]] = field(default_factory=dict)
    base_kv: Dict[str, float] = field(default_factory=dict)
    equipment: Dict[str, Any] = field(default_factory=dict)
    model_hash: str = ""
    status: str = "ok"
    error_code: Optional[str] = None

    def fresh_copy(self) -> "CaseRef":
        """返回可重新 Compile 的算例引用（等价于从冻结基线重建）。

        设计文档 17.12：每次从冻结基线复制或重建，避免新增容量累积。
        """
        return CaseRef(
            master_dss=self.master_dss,
            work_dir=self.work_dir,
            node_phase_map=self.node_phase_map,
            id_map=self.id_map,
            base_kv=self.base_kv,
            equipment=self.equipment,
            model_hash=self.model_hash,
            status=self.status,
            error_code=self.error_code,
        )


class OpenDSSAdapter:
    """OpenDSS 适配器。

    每个实例创建独立上下文（设计文档 8.4：每个工作进程创建独立 OpenDSS
    上下文并使用独立目录，不共享活动电路或可变求解状态）。
    """

    def __init__(self, *, work_dir: Path, engine_config: Optional[Dict[str, Any]] = None,
                 logger=None) -> None:
        self.work_dir = Path(work_dir).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.engine_config = engine_config or {}
        self.logger = logger
        self.log_lines: List[str] = []
        self._dss = None
        self._ctx = None
        self._case: Optional[CaseRef] = None
        self._fingerprint = runtime_env.engine_fingerprint()

    # ------------------------------------------------------------ 日志
    def log(self, msg: str) -> None:
        line = f"[{utc_now_iso()}] {msg}"
        self.log_lines.append(line)
        if self.logger:
            self.logger(line)

    def _flush_log(self, name: str = "opendss_adapter.log") -> str:
        p = self.work_dir / name
        p.write_text("\n".join(self.log_lines) + "\n", encoding="utf-8")
        return str(p)

    # ------------------------------------------------------- 上下文管理
    def _ensure_context(self):
        """每个适配器实例持有自己的 OpenDSS 上下文（表 55：create_context）。"""
        if self._dss is not None:
            return self._dss
        lib = runtime_env.ensure_engine_on_path()
        if lib is None:
            raise ComputeFailedError(
                "OpenDSSDirect.py 不可用；无法创建计算上下文",
                code=ERR_DSS_DEPENDENCY_LOAD_FAILED,
            )
        import opendssdirect as dss
        try:
            # 独立实例：不共享活动电路或可变求解状态
            self._ctx = dss.NewContext()
            self._dss = dss
            self.log(f"OpenDSS 上下文已创建 (pylib={lib})")
        except Exception as exc:  # pragma: no cover
            raise ComputeFailedError(f"创建 OpenDSS 上下文失败: {exc}",
                                     code=ERR_DSS_DEPENDENCY_LOAD_FAILED) from exc
        return self._dss

    def probe(self, *, timeout_s: float = 15.0) -> Optional[bool]:
        """引擎活性探针（看门狗复检接缝，移植自天枢一号的设计）。

        返回 True 表示引擎仍能响应；False 表示卡死/无响应；None 表示无法判定。

        **为什么是"再问一句 Version"而不是看收敛标志**：同一个超时现象有两种
        成因——算例太慢、或引擎中毒。唯一可靠的区分手段是**再问引擎一句**：
        通道干净则秒回，通道中毒则超时（天枢一号实测：OpenDSSEngine.dll 加载
        即挂死时，任何调用都无响应）。

        探测动作刻意选最轻的属性读取，避免探测本身成为负担。
        """
        import threading as _threading
        box: Dict[str, Any] = {}

        def _run() -> None:
            # 捕获 BaseException：探针线程内的 SystemExit/KeyboardInterrupt
            # 只被 Exception 漏掉，会让 box 留空 → 误判为"无法判定"
            try:
                dss = self._ensure_context()
                # 取版本是纯属性读取，不触碰电路状态；能返回即引擎可用
                _ = dss.Basic.Version()
                box["ok"] = True
            except BaseException as exc:
                box["ok"] = False
                box["err"] = f"{type(exc).__name__}: {exc}"

        t = _threading.Thread(target=_run, name="adapter-probe", daemon=True)
        t.start()
        # 外层超时兜底：线程可能卡在原生调用里，内层超时根本执行不到
        t.join(max(1.0, float(timeout_s)))
        if t.is_alive():
            # 卡在原生调用里 —— 这是"引擎中毒"的确定信号
            self.log(f"引擎探针超过 {timeout_s:g}s 未返回，判定无响应")
            return False
        return box.get("ok")

    def engine_diagnostics(self) -> Dict[str, Any]:
        """引擎侧诊断快照（与看门狗 diagnostics 合并输出）。"""
        return {
            "fingerprint": self._fingerprint,
            "context_created": self._ctx is not None,
            "case_compiled": bool(self._case and self._case.master_dss),
            "model_hash": (self._case.model_hash[:12] if self._case else ""),
            "work_dir": str(self.work_dir),
            "log_lines": len(self.log_lines),
        }

    def close(self) -> None:
        """释放上下文（表 55：新实例、独立电路状态）。"""
        if self._ctx is not None:
            try:
                self._ctx.__exit__(None, None, None)
            except Exception:
                pass
            self._ctx = None
            self._dss = None

    def __enter__(self) -> "OpenDSSAdapter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------- 错误映射
    def _map_engine_error(self, exc: Exception, stage: str) -> ComputeFailedError:
        """OpenDSS 异常映射为标准错误码（设计文档 8.2）。"""
        text = str(exc)
        low = text.lower()
        if "converg" in low:
            code = ERR_DSS_NOT_CONVERGED
        elif "compile" in low or "redirect" in low or "cannot open" in low:
            code = ERR_DSS_COMPILE_FAILED
        elif "timeout" in low or "time out" in low:
            code = ERR_DSS_TIMEOUT
        elif "memory" in low or "resource" in low:
            code = ERR_DSS_RESOURCE_EXHAUSTED
        elif "not found" in low or "object" in low or "unknown" in low:
            code = ERR_DSS_OBJECT_ERROR
        else:
            code = ERR_DSS_UNKNOWN
        return ComputeFailedError(f"[{stage}] {text}", code=code,
                                  detail={"stage": stage,
                                          "exception": type(exc).__name__})

    def _check_dss_errors(self, stage: str) -> None:
        """检查 OpenDSS 内部错误（含 Text 命令错误）。"""
        dss = self._dss
        if dss is None:
            return
        try:
            code = dss.Error.Number()
            desc = dss.Error.Description()
        except Exception:
            return
        if code:
            self.log(f"OpenDSS 报错 stage={stage} code={code} desc={desc}")
            mapping = {
                301: ERR_DSS_COMPILE_FAILED,
                302: ERR_DSS_OBJECT_ERROR,
                303: ERR_DSS_OBJECT_ERROR,
                304: ERR_DSS_OBJECT_ERROR,
                305: ERR_DSS_OBJECT_ERROR,
            }
            err_code = mapping.get(int(code), ERR_DSS_UNKNOWN)
            if int(code) in (300, 301):
                err_code = ERR_DSS_COMPILE_FAILED
            raise ComputeFailedError(
                f"[{stage}] OpenDSS 错误 {code}: {desc}", code=err_code,
                detail={"stage": stage, "dss_code": int(code)},
            )

    def cmd(self, command: str, *, stage: str = "command") -> str:
        """执行登记模板生成的 DSS 命令（设计文档 8.3）。

        路径须校验，不直接执行用户上传的任意 DSS 脚本。
        """
        dss = self._ensure_context()
        try:
            dss.Text.Command(command)
        except Exception as exc:
            raise self._map_engine_error(exc, stage) from exc
        self._check_dss_errors(stage)
        self.log(f"DSS> {command}")
        return dss.Text.Result()

    # ------------------------------------------------------------ 建模
    def build_case(self, dss_text: str, *, case_name: str = "hc_case",
                   node_phase_map: Optional[Dict[str, Dict[str, int]]] = None,
                   id_map: Optional[Dict[str, Dict[str, str]]] = None,
                   base_kv: Optional[Dict[str, float]] = None,
                   equipment: Optional[Dict[str, Any]] = None,
                   ) -> CaseRef:
        """生成并 Compile 算例（设计文档 17.11：build_case(bundle, engine_config)）。

        算例目录使用 Master.dss 与引用文件（8.1）。
        """
        case_dir = self.work_dir / f"case_{case_name}"
        case_dir.mkdir(parents=True, exist_ok=True)
        master = case_dir / "Master.dss"
        master.write_text(dss_text, encoding="utf-8")
        model_hash = sha256_file(master)

        self.cmd('Clear', stage="build_case")
        self.cmd(f'Compile "{master.as_posix()}"', stage="build_case")
        # Set 命令必须在 circuit 对象存在之后执行（OpenDSS #301）
        self.cmd("Set mode=snapshot", stage="build_case")
        self.cmd("CalcVoltageBases", stage="build_case")

        # 采集母线/节点信息，建立 相别 → 节点号 映射（表 55）。
        dss = self._dss
        resolved_phase_map = node_phase_map or {}
        resolved_base_kv = dict(base_kv or {})
        try:
            for bus in dss.Circuit.AllBusNames():
                dss.Circuit.SetActiveBus(bus)
                nodes = dss.Bus.Nodes()
                kvbase = dss.Bus.kVBase()
                resolved_base_kv.setdefault(bus.lower(), kvbase)
                if bus not in resolved_phase_map:
                    pmap: Dict[str, int] = {}
                    for n in nodes:
                        if int(n) in PHASE_TO_NODE.values():
                            inv = {v: k for k, v in PHASE_TO_NODE.items()}
                            pmap[inv[int(n)]] = int(n)
                    resolved_phase_map[bus] = pmap
        except Exception as exc:
            self.log(f"母线信息采集告警: {exc}")

        case = CaseRef(
            master_dss=str(master),
            work_dir=str(case_dir),
            node_phase_map=resolved_phase_map,
            id_map=id_map or {},
            base_kv=resolved_base_kv,
            equipment=equipment or {},
            model_hash=model_hash,
            status="ok",
        )
        self._case = case
        self.log(f"算例已 Compile: {master} (hash={model_hash[:12]})")

        # 电压基准物理自检：不通过即阻断，绝不带着错误基准进入约束判定
        # （设计文档 17.14：专业程序验收须逐项确认相别与单位换算正确）
        chk = self.verify_voltage_bases()
        self.log(f"电压基准自检: {'通过' if chk['pass'] else '未通过'}")
        if not chk["pass"]:
            for c in chk["checks"][:5]:
                self.log(f"  ! {c['detail']}")
            raise ComputeFailedError(
                "电压基准自检未通过：kVBase 与额定电压不匹配，pu 电压将被"
                "系统性错误缩放，禁止继续计算",
                code=ERR_DSS_OBJECT_ERROR,
                detail={"voltage_base_checks": chk["checks"][:10]})
        case.status = "ok"
        return case

    # ------------------------------------------------------------ 求解
    def solve(self, *, scenario_id: str = "S0", timestamp: str = "",
              max_iter: int = 100, tolerance: float = 1e-4,
              timeout_s: Optional[float] = None) -> EngineResult:
        """求解快照潮流并提取结果（设计文档 8.3 调用顺序）。"""
        dss = self._ensure_context()
        t0 = time.perf_counter()
        art: List[str] = []

        if self.engine_config.get("max_control_iter", None) is not None:
            self.cmd(f"Set maxcontroliter={int(self.engine_config['max_control_iter'])}",
                     stage="solve")
        self.cmd(f"Set maxiterations={int(max_iter)}", stage="solve")
        self.cmd(f"Set tolerance={float(tolerance):g}", stage="solve")

        try:
            dss.Solution.Solve()
        except Exception as exc:
            art.append(self._flush_log())
            return EngineResult(
                status="unknown", converged=False,
                engine_version=self._fingerprint.get("engine_version", ""),
                artifact_refs=art,
                error_code=self._map_engine_error(exc, "solve").code,
                engine_fingerprint=self._fingerprint,
                work_dir=str(self.work_dir), scenario_id=scenario_id,
                timestamp=timestamp, runtime_s=time.perf_counter() - t0,
            )

        try:
            converged = bool(dss.Solution.Converged())
            iterations = int(dss.Solution.Iterations())
        except Exception as exc:
            raise self._map_engine_error(exc, "converged") from exc

        runtime = time.perf_counter() - t0
        result = EngineResult(
            status="ok" if converged else "unknown",
            converged=converged,
            engine_version=self._fingerprint.get("engine_version", ""),
            artifact_refs=art,
            error_code=None if converged else ERR_DSS_NOT_CONVERGED,
            runtime_s=runtime, iterations=iterations,
            engine_fingerprint=self._fingerprint,
            work_dir=str(self.work_dir), scenario_id=scenario_id,
            timestamp=timestamp,
        )

        if not converged:
            # 设计文档 8.3：失败后保留模型和日志；不收敛标为 unknown，
            # 不可等同于电气不可行。
            self.log(f"潮流不收敛 scenario={scenario_id} ts={timestamp} "
                     f"iter={iterations}")
            art.append(self._flush_log())
            return result

        result.bus_voltages = self.extract_bus_voltages(scenario_id, timestamp)
        result.element_results = self.extract_element_results(scenario_id, timestamp)
        result.metrics = self._circuit_metrics()
        # 静默失效检测（设计文档 8.3：不能通过关闭必要控制、改变拓扑或放宽
        # 业务约束使结果表面收敛）。本机实测过一类危险情形：电压基准被错误
        # 重置后，Solution.Converged() 仍为 True 但全网电压恒为 0。此时代码
        # 必须判为不收敛/无解，绝不能当作合法解进入约束判定。
        if result.bus_voltages:
            vmax = max((abs(v["v_v"]) for v in result.bus_voltages
                        if v["v_v"] == v["v_v"]), default=0.0)
            if vmax <= 1.0:  # 全网电压 < 1 V 视为无解
                self.log(f"检测到静默失效：全网最大节点电压 {vmax:.6g} V，"
                         f"判定为无解（电压基准或电源配置异常）")
                result.status = "unknown"
                result.converged = False
                result.error_code = ERR_DSS_NOT_CONVERGED
                result.metrics["silent_failure"] = {
                    "v_max_volts": vmax,
                    "reason": "Solution.Converged()=True 但全网电压为 0；"
                              "通常由电压基准被重置（如仅发 "
                              "Set defaultbasefrequency 而未给 voltagebases）"
                              "或上级电源未接入引起",
                }
                result.artifact_refs.append(self._flush_log())
                return result
        art.append(self._flush_log())
        return result

    # -------------------------------------------------------- 结果提取
    def extract_bus_voltages(self, scenario_id: str,
                             timestamp: str) -> List[Dict[str, Any]]:
        """按母线节点提取电压（表 55：AllBusNames/SetActiveBus/Nodes/Voltages/kVBase）。

        设计文档 17.11：OpenDSS 电压 pu 采用相电压基准时必须与模型 kVBase 对齐。
        """
        dss = self._ensure_context()
        rows: List[Dict[str, Any]] = []
        inv = {v: k for k, v in PHASE_TO_NODE.items()}
        case = self._case
        # OpenDSS 母线名统一小写；映射表按小写键查找（设计文档 17.11：
        # 导体编号不等于业务相别的假设必须经映射校验）
        node_map = {}
        if case and case.node_phase_map:
            node_map = {str(k).lower(): v
                        for k, v in case.node_phase_map.items()}

        for bus in dss.Circuit.AllBusNames():
            dss.Circuit.SetActiveBus(bus)
            nodes = [int(n) for n in dss.Bus.Nodes()]
            volts = [float(v) for v in dss.Bus.Voltages()]
            kvbase = float(dss.Bus.kVBase())
            pmap = node_map.get(str(bus).lower(), {})
            inv_pmap = {v: k for k, v in pmap.items()}

            for idx, node in enumerate(nodes):
                if node == 0:
                    continue
                re = volts[2 * idx] if 2 * idx < len(volts) else float("nan")
                im = volts[2 * idx + 1] if 2 * idx + 1 < len(volts) else float("nan")
                mag = math.hypot(re, im)
                angle = math.degrees(math.atan2(im, re))
                # 设计文档 17.11：OpenDSS 电压 pu 采用相电压基准时必须与模型
                # kVBase 对齐。本机实测（OpenDSSDirect.py 0.9.4 / DSS C-API
                # 0.14.5）DSS 的 Bus.kVBase() 返回的是"相对地（line-to-neutral）
                # 电压基准"，而 Bus.Voltages() 返回的正是各节点相对地电压，
                # 二者量纲一致，故 pu = |V| / (kVBase*1000)，不得再除 sqrt(3)。
                v_base = kvbase * 1000.0 if kvbase > 0 else float("nan")
                v_pu = mag / v_base if v_base and v_base == v_base else float("nan")
                rows.append({
                    "scenario_id": scenario_id,
                    "timestamp": timestamp,
                    "node_id": f"{bus}.{node}",
                    "bus": bus,
                    "node": node,
                    "phase": inv_pmap.get(node, inv.get(node, str(node))),
                    "v_v": mag,
                    "v_pu": v_pu,
                    "angle_deg": angle,
                    "kv_base": kvbase,
                    "v_ll_kv": mag * SQRT3 / 1000.0,
                    "converged": True,
                })
        return rows

    def extract_element_results(self, scenario_id: str,
                                timestamp: str) -> List[Dict[str, Any]]:
        """按端子提取元件电流与功率（表 55：CurrentsMagAng/Powers）。

        设计文档 表 55：按端子、导体解析电流及 P/Q，不用总功率冒充分相量。
        反向 P 的符号由节点方向与适配器归一化确认（17.11）。
        """
        dss = self._ensure_context()
        rows: List[Dict[str, Any]] = []
        inv = {node: ph for ph, node in PHASE_TO_NODE.items()}

        try:
            names = list(dss.Circuit.AllElementNames())
        except Exception as exc:
            raise self._map_engine_error(exc, "element_list") from exc

        for name in names:
            cls = name.split(".")[0].lower()
            # 只关心承担载流的电力传输/转换元件
            if cls not in ("line", "transformer", "reactor", "pvsystem",
                           "generator", "storage", "capacitor"):
                continue
            try:
                dss.Circuit.SetActiveElement(name)
            except Exception:
                continue
            try:
                powers = [float(x) for x in dss.CktElement.Powers()]
                currents = [float(x) for x in dss.CktElement.CurrentsMagAng()]
                nph = int(dss.CktElement.NumPhases())
                ncond = int(dss.CktElement.NumConductors())
                nterm = int(dss.CktElement.NumTerminals())
                rating = self._element_rating(name, cls)
            except Exception as exc:
                self.log(f"元件 {name} 结果解析失败: {exc}")
                continue

            npairs = len(powers) // 2
            nc_pairs = len(currents) // 2
            for terminal in range(1, nterm + 1):
                for cond in range(nph):
                    pidx = (terminal - 1) * ncond + cond
                    if pidx >= npairs:
                        continue
                    p_kw = powers[2 * pidx]
                    q_kvar = powers[2 * pidx + 1]
                    i_a = (currents[2 * pidx]
                           if 2 * pidx < nc_pairs else float("nan"))
                    phase = inv.get(cond + 1, str(cond + 1))
                    loading = (100.0 * abs(i_a) / rating
                               if rating and rating > 0 and i_a == i_a else None)
                    rows.append({
                        "scenario_id": scenario_id,
                        "timestamp": timestamp,
                        "equipment_id": name,
                        "element_class": cls,
                        "terminal": terminal,
                        "phase": phase,
                        "p_kw": p_kw,
                        "q_kvar": q_kvar,
                        "i_a": i_a,
                        "rating_a": rating,
                        "loading_pct": loading,
                    })

            # 三相合计行（供载流/反送校核使用，避免分相重复计数）
            total = self._element_total(name, nterm, ncond, powers, currents, rating)
            if total is not None:
                rows.append(total)

        return rows

    def _element_rating(self, name: str, cls: str) -> Optional[float]:
        """取元件持续载流限值（设计文档 17.6：max_i_a）。"""
        case = self._case
        eq = (case.equipment if case else {}) or {}
        limits = eq.get("limits", {}) if isinstance(eq, dict) else {}
        key = name.lower()
        if key in limits:
            try:
                return float(limits[key])
            except (TypeError, ValueError):
                pass
        dss = self._dss
        try:
            if cls == "line":
                return float(dss.Lines.NormAmps())
            if cls == "transformer":
                return None  # 变压器按 kVA 判定，另行处理
            if cls in ("pvsystem", "generator", "storage"):
                kva = float(dss.CktElement.NumPhases())
                return None
        except Exception:
            return None
        return None

    def _element_total(self, name: str, nterm: int, ncond: int,
                       powers: Sequence[float], currents: Sequence[float],
                       rating: Optional[float]) -> Optional[Dict[str, Any]]:
        """元件端子 1 三相合计。

        符号标定（本机实测 OpenDSSDirect.py 0.9.4 / DSS C-API 0.14.5）：
          Powers() 按"端子×导体"展开，每量 2 个浮点 (P,Q)，索引
          = 2*((terminal-1)*ncond + conductor)。符号含义为"流入该元件的功率"：
            线路/变压器端子 1：P<0 => 由 bus1 流入元件（正常正向供电）；
                              P>0 => 倒送回 bus1（反向潮流）。
            PVSystem/Generator：P<0 => 向电网注入。
        这里统一输出**端子 1 的原始 P/Q**，不做二次取反，反向判定由
        constraints.evaluate_reverse_flow 按上述约定执行，避免两处各翻一次符号。
        """
        npairs = len(powers) // 2
        if npairs < ncond:
            return None
        # 仅取**端子 1** 的 ncond 个导体做三相合计与最大电流。
        # 不得跨端子累加：两段端子的电流会重复计入，实测会把载流率放大到
        # 1600% 量级的伪越限。载流校核按"元件最大相电流 vs 允许值"（17.11）。
        p_kw = sum(powers[2 * c] for c in range(ncond))
        q_kvar = sum(powers[2 * c + 1] for c in range(ncond))
        i_mags = [currents[2 * c] for c in range(ncond)
                  if 2 * c < len(currents) // 2]
        i_max = max(i_mags) if i_mags else float("nan")
        loading = (100.0 * i_max / rating
                   if rating and rating > 0 and i_max == i_max else None)
        return {
            "scenario_id": None,
            "timestamp": None,
            "equipment_id": name,
            "element_class": name.split(".")[0].lower(),
            "terminal": 0,
            "phase": "ALL",
            "p_kw": p_kw,
            "q_kvar": q_kvar,
            "i_a": i_max,
            "rating_a": rating,
            "loading_pct": loading,
            "sign_convention": "terminal1_p_inflow_positive_generation",
        }

    def verify_voltage_bases(self, *, source_vm_pu: Optional[float] = None,
                             tol: float = 0.05) -> Dict[str, Any]:
        """电压基准物理自检（设计文档 17.11：pu 基准必须与模型 kVBase 对齐）。

        判据（物理上无可争辩）：空载且上级电源 pu = vm 时，各母线电压应
        恰好为 vm（忽略线路充电与损耗的微小影响）。

        本机实测到的静默错误：不显式下发 Set VoltageBases 时，
        CalcVoltageBases 推出的 Bus.kVBase() 偏离正确相电压基准，
        导致全网 pu 电压被系统性夸大（MV 偏差 10/9、LV 偏差 6/5），
        健康馈线被误判为电压越下限。Solution.Converged() 仍为 True，
        因此必须用物理判据主动校验，而不能只看收敛标志。

        返回 {"pass": bool, "checks": [...], "base_map": {...}}。
        """
        dss = self._ensure_context()
        case = self._case
        base_map: Dict[str, Dict[str, Any]] = {}
        checks: List[Dict[str, Any]] = []

        expected_vn: Dict[str, float] = {}
        for bus, kv in (case.base_kv if case else {}).items():
            expected_vn[str(bus).lower()] = float(kv)

        # 计算各电压等级下 kVBase 应有的相电压基准 = vn_kv/sqrt(3)*1000
        for bus in dss.Circuit.AllBusNames():
            dss.Circuit.SetActiveBus(bus)
            kvbase = float(dss.Bus.kVBase())
            vn = expected_vn.get(str(bus).lower())
            rec: Dict[str, Any] = {"bus": bus, "kvbase_kv": kvbase,
                                   "vn_kv": vn}
            if str(bus).strip().lower() in EXCLUDED_BUSES:
                # sourcebus 是 Vsource 的内部等值母线，其基准等于电源 basekv，
                # 不参与额定电压比对（与约束判定口径一致）。
                rec["skipped"] = "OpenDSS 内部等值母线"
                base_map[bus] = rec
                continue
            if vn and kvbase > 0:
                expected_phase_base = vn * 1000.0 / SQRT3
                ratio = kvbase * 1000.0 / expected_phase_base
                rec["expected_phase_base_v"] = expected_phase_base
                rec["ratio"] = ratio
                rec["ok"] = abs(ratio - 1.0) <= tol
                if not rec["ok"]:
                    checks.append({
                        "check": "voltage_base", "bus": bus, "pass": False,
                        "detail": f"kVBase={kvbase:.6g} kV 与额定 "
                                  f"{vn:g} kV 的相电压基准 "
                                  f"{expected_phase_base:.2f} V 之比为 "
                                  f"{ratio:.4f}，偏离 1.0 超过容差 {tol}；"
                                  f"pu 电压将被系统性错误缩放",
                    })
            base_map[bus] = rec

        # 若调用方给出电源 pu，进一步用负载无关的物理不变量校验
        if source_vm_pu is not None:
            for bus, rec in base_map.items():
                dss.Circuit.SetActiveBus(bus)
                V = [float(x) for x in dss.Bus.Voltages()]
                mags = [math.hypot(V[i], V[i + 1])
                        for i in range(0, len(V) - 1, 2)]
                if not mags:
                    continue
                v_ll_kv = max(mags) * SQRT3 / 1000.0
                vn = rec.get("vn_kv")
                if vn:
                    rec["v_ll_kv"] = v_ll_kv
                    rec["v_ll_ratio_to_vn"] = v_ll_kv / vn

        return {
            "pass": not checks,
            "checks": checks,
            "base_map": base_map,
            "note": "kVBase 为相电压（line-to-neutral）基准；"
                    "pu = |V_LN| / (kVBase*1000)，不得再除 sqrt(3)",
        }

    def self_check_signs(self, elements: Sequence[Dict[str, Any]]
                         ) -> Dict[str, Any]:
        """功率符号与相别的自检（设计文档 17.14 专业程序验收要求）。

        逐项确认：相别和功率符号正确；新增注入不累积。
        返回检查结论，异常以 blocking=True 标出，供上层阻断发布。
        """
        issues: List[Dict[str, Any]] = []
        totals = {r["equipment_id"]: r for r in elements
                  if as_str(r.get("phase")) == "ALL"}

        # 1) 线路/变压器端子 1 与端子 2 的功率差应等于元件损耗，
        #    且端子 1 与端子 2 符号相反（功率穿过元件）。
        dss = self._dss
        if dss is not None:
            for name, row in totals.items():
                cls = row.get("element_class")
                if cls not in ("line", "transformer"):
                    continue
                try:
                    dss.Circuit.SetActiveElement(name)
                    powers = [float(x) for x in dss.CktElement.Powers()]
                    ncond = int(dss.CktElement.NumConductors())
                    nterm = int(dss.CktElement.NumTerminals())
                except Exception:
                    continue
                if nterm < 2 or len(powers) < 4 * ncond:
                    continue
                p1 = sum(powers[2 * c] for c in range(ncond))
                p2 = sum(powers[2 * c + 2 * ncond] for c in range(ncond))
                if p1 * p2 > 0 and abs(p1) > 1e-6:
                    issues.append({
                        "check": "terminal_power_opposition",
                        "equipment_id": name, "blocking": False,
                        "detail": f"端子1 P={p1:.1f} kW 与端子2 P={p2:.1f} kW "
                                  f"同号，通行元件两端功率应反号",
                    })

        # 2) 光伏/发电元件在出力非零时，端子 1 P 应为负（向网注入）
        pv_rows = [r for r in elements if r.get("element_class") in
                   ("pvsystem", "generator")
                   and as_str(r.get("phase")) == "ALL"]
        for r in pv_rows:
            p = r.get("p_kw")
            if p is None or p != p:
                continue
            if "pv_new" in as_str(r.get("equipment_id")).lower():
                continue  # 新增容量试探件由调用方控制
            if p > 1e-6:
                issues.append({
                    "check": "generator_injection_sign",
                    "equipment_id": r.get("equipment_id"), "blocking": False,
                    "detail": f"注入型元件端子1 P={p:.2f} kW 为正（消费方向），"
                              f"与出力曲线预期不符",
                })

        # 3) 相别映射：三相元件应映射到 A/B/C 三个不同节点号
        case = self._case
        if case and case.node_phase_map:
            for bus, pmap in case.node_phase_map.items():
                if len(pmap) == 3 and len(set(pmap.values())) != 3:
                    issues.append({
                        "check": "phase_node_mapping", "equipment_id": bus,
                        "blocking": True,
                        "detail": f"母线 {bus} 的相别到节点号映射不唯一：{pmap}",
                    })
        else:
            issues.append({
                "check": "phase_node_mapping", "equipment_id": "ALL",
                "blocking": False,
                "detail": "node_phase_map 为空，相别映射未经校验",
            })

        return {
            "checked_elements": len(elements),
            "issues": issues,
            "blocking_issues": [i for i in issues if i.get("blocking")],
            "pass": not any(i.get("blocking") for i in issues),
        }

    def _circuit_metrics(self) -> Dict[str, Any]:
        dss = self._dss
        out: Dict[str, Any] = {}
        try:
            tp = dss.Circuit.TotalPower()
            out["total_p_kw"], out["total_q_kvar"] = float(tp[0]), float(tp[1])
        except Exception:
            pass
        try:
            losses = dss.Circuit.Losses()
            # 表 69：Losses 按底层 W/var 转 MW/Mvar，不按 kW 误转换
            out["losses_w"], out["losses_var"] = float(losses[0]), float(losses[1])
            out["losses_kw"] = float(losses[0]) / 1000.0
            out["losses_kvar"] = float(losses[1]) / 1000.0
        except Exception:
            pass
        return out

    # -------------------------------------------------------- 短路研究
    def short_circuit(self, *, fault_spec: Dict[str, Any],
                      scenario_id: str = "SC", timestamp: str = "") -> EngineResult:
        """故障研究（设计文档 17.11 / 表 55）。

        FaultStudy 的母线等值量不能直接当成任意接地或相间故障的实际电流，
        具体故障电流须按故障方案求解并读取对应端子结果。
        """
        dss = self._ensure_context()
        t0 = time.perf_counter()
        bus = fault_spec.get("bus", "")
        fault_type = fault_spec.get("fault_type", "single_line_ground")
        r_fault = float(fault_spec.get("r_fault_ohm", 0.0))

        try:
            self.cmd("Set mode=FaultStudy", stage="short_circuit")
            self.cmd("Solve", stage="short_circuit")
            isc = None
            zsc = None
            if bus:
                dss.Circuit.SetActiveBus(bus)
                try:
                    isc = list(dss.Bus.Isc())
                except Exception:
                    isc = None
                try:
                    zsc = list(dss.Bus.ZscMatrix())
                except Exception:
                    zsc = None
            # 显式 Fault 元件方案（更贴近实际故障电流）
            fault_rows = self._solve_fault_element(bus, fault_type, r_fault)
            self.cmd("Set mode=snapshot", stage="short_circuit")
            metrics = {
                "fault_bus": bus,
                "fault_type": fault_type,
                "r_fault_ohm": r_fault,
                "bus_isc_a": isc,
                "bus_zsc": zsc,
                "fault_element_currents": fault_rows,
                "note": ("FaultStudy 母线等值量不等同于具体故障电流；"
                         "fault_element_currents 为按故障方案求解结果"),
            }
            return EngineResult(
                status="ok", converged=True,
                engine_version=self._fingerprint.get("engine_version", ""),
                metrics=metrics, iterations=0,
                runtime_s=time.perf_counter() - t0,
                engine_fingerprint=self._fingerprint,
                work_dir=str(self.work_dir), scenario_id=scenario_id,
                timestamp=timestamp,
                artifact_refs=[self._flush_log()],
            )
        except ComputeFailedError:
            raise
        except Exception as exc:
            err = self._map_engine_error(exc, "short_circuit")
            return EngineResult(
                status="unknown", converged=False,
                engine_version=self._fingerprint.get("engine_version", ""),
                error_code=err.code, runtime_s=time.perf_counter() - t0,
                engine_fingerprint=self._fingerprint, work_dir=str(self.work_dir),
                scenario_id=scenario_id, timestamp=timestamp,
                artifact_refs=[self._flush_log()],
            )
        finally:
            try:
                self.cmd("Set mode=snapshot", stage="short_circuit_restore")
            except Exception:
                pass

    def _solve_fault_element(self, bus: str, fault_type: str,
                             r_fault: float) -> List[Dict[str, Any]]:
        """用显式 Fault 元件求解具体故障电流。"""
        if not bus:
            return []
        rows: List[Dict[str, Any]] = []
        fname = f"HC_FAULT_{uuid.uuid4().hex[:6]}"
        try:
            self.cmd(f"New Fault.{fname} bus1={bus} phases=3 "
                     f"r={r_fault:g} enabled=yes", stage="fault_element")
            self.cmd("Set mode=snapshot", stage="fault_element")
            self.cmd("Solve", stage="fault_element")
            dss = self._dss
            dss.Circuit.SetActiveElement(f"Fault.{fname}")
            currents = [float(x) for x in dss.CktElement.CurrentsMagAng()]
            nph = int(dss.CktElement.NumPhases())
            # 只报告**相导体**（前 nph 个）。故障元件可含中性线/回流导体
            # （例如 6 导体 = 3 相 + 3 回流），其电流与相导体相同，
            # 若一并按相别列出会重复计数并把节点号误报成相别。
            n_report = max(1, min(nph, len(currents) // 2))
            for c in range(n_report):
                rows.append({
                    "phase": PHASE_ORDER[c] if c < len(PHASE_ORDER) else str(c + 1),
                    "i_fault_a": currents[2 * c],
                    "angle_deg": currents[2 * c + 1],
                })
        except Exception as exc:
            self.log(f"Fault 元件求解失败（保留 FaultStudy 等值量）: {exc}")
        finally:
            try:
                self.cmd(f"Edit Fault.{fname} enabled=no", stage="fault_cleanup")
                self.cmd("Set mode=snapshot", stage="fault_cleanup")
                self.cmd("Solve", stage="fault_cleanup")
            except Exception:
                pass
        return rows

    # -------------------------------------------------------- 元件操作
    def set_load(self, load_id: str, *, kw: Optional[float] = None,
                 kvar: Optional[float] = None, pf: Optional[float] = None,
                 phases: Sequence[str] = ()) -> None:
        """写入该时间片负荷参数（设计文档 8.3 调用顺序）。

        低压三相负荷采用分相 P/Q，不能把总功率复制到 A、B、C 三相（17.7）。
        """
        parts = [f"Edit Load.{load_id}"]
        if kw is not None:
            parts.append(f"kW={float(kw):.10g}")
        if kvar is not None:
            parts.append(f"kvar={float(kvar):.10g}")
        if pf is not None:
            parts.append(f"pf={float(pf):.10g}")
        if not phases:
            self.cmd(" ".join(parts), stage="set_load")
            return
        # 分相：使用 shape 之外的显式设置，逐相写入
        for ph in phases:
            node = PHASE_TO_NODE.get(ph.upper())
            if node is None:
                continue
            p_parts = [f"Edit Load.{load_id}.{node}"]
            if kw is not None:
                p_parts.append(f"kW={float(kw):.10g}")
            if kvar is not None:
                p_parts.append(f"kvar={float(kvar):.10g}")
            self.cmd(" ".join(p_parts), stage="set_load")

    def set_pv(self, pv_id: str, *, pmpp_kw: Optional[float] = None,
               kva: Optional[float] = None, pf: Optional[float] = None,
               irradiance: Optional[float] = None) -> None:
        """写入该时间片光伏参数（设计文档 17.11 / 8.1）。

        新增容量若指逆变器交流额定 kW，应分别记录交流容量和直流阵列 Pmpp；
        PVSystem.kVA 为视在容量，Pmpp 为阵列功率参数，两者不能默认等同。

        出力控制要点（本机实测 OpenDSSDirect.py 0.9.4 / DSS C-API 0.14.5）：
          - 改变 PVSystem 出力必须**同时**写 kVA 与 Pmpp；只写其一不出力。
          - 以 kVA=0 Pmpp=0 建立的 PVSystem 无法再被 Edit 抬高，故新增容量
            试探件按非零铭牌建立（见 make_new_pv_specs）。
          - 若 Python 逐时间片设置出力，则不再套用同一时序 LoadShape，
            避免出力重复缩放（设计文档 8.1）。
        """
        parts = [f"Edit PVSystem.{pv_id}"]
        target = pmpp_kw if pmpp_kw is not None else kva
        if target is not None:
            # 用同一个数同时设定视在容量与阵列功率，使出力等于目标值；
            # 额定交流/直流容量的区分由输入台账记录，不在此处混用。
            parts.append(f"kVA={float(target):.10g}")
            parts.append(f"Pmpp={float(target):.10g}")
        if pf is not None:
            parts.append(f"pf={float(pf):.10g}")
        if irradiance is not None:
            parts.append(f"irradiance={float(irradiance):.10g}")
        if len(parts) > 1:
            self.cmd(" ".join(parts), stage="set_pv")

    def set_switch(self, switch_id: str, closed: bool) -> None:
        """设置开关状态（设计文档 8.3：设置该场景的开关、分接与控制）。"""
        state = "yes" if closed else "no"
        self.cmd(f"Edit SwtControl.{switch_id} enabled={state}",
                 stage="set_switch")

    def set_tap(self, transformer_id: str, tap_pos: float) -> None:
        parts = [f"Edit Transformer.{transformer_id}"]
        parts.append(f"tap={float(tap_pos):g}")
        self.cmd(" ".join(parts), stage="set_tap")

    def set_storage(self, storage_id: str, *, kw: Optional[float] = None,
                    kvar: Optional[float] = None, state: str = "discharging",
                    pct_reserve: Optional[float] = None) -> None:
        """储能外部调度模式（设计文档 21.4）。

        储能时序采用明确的外部调度模式并管理功率、状态和 kWhStored。
        """
        parts = [f"Edit Storage.{storage_id}", "State=IDLING"]
        self.cmd(" ".join(parts), stage="set_storage")
        parts = [f"Edit Storage.{storage_id}", f"State={state}"]
        if kw is not None:
            parts.append(f"kW={float(kw):.10g}")
        if kvar is not None:
            parts.append(f"kvar={float(kvar):.10g}")
        if pct_reserve is not None:
            parts.append(f"%reserve={float(pct_reserve):.10g}")
        self.cmd(" ".join(parts), stage="set_storage")

    def set_generator(self, gen_id: str, *, kw: Optional[float] = None,
                      kvar: Optional[float] = None, pf: Optional[float] = None) -> None:
        parts = [f"Edit Generator.{gen_id}"]
        if kw is not None:
            parts.append(f"kW={float(kw):.10g}")
        if kvar is not None:
            parts.append(f"kvar={float(kvar):.10g}")
        if pf is not None:
            parts.append(f"pf={float(pf):.10g}")
        if len(parts) > 1:
            self.cmd(" ".join(parts), stage="set_generator")
