"""process.py — 读取"运算过程"类产物（阶段轨迹 / 搜索轨迹 / 场景结果）。

这三类数据回答的是"它是怎么算出来的"，与 `runs.py` 的"结论是什么"互补：

  * ``checkpoints.json``     —— S01→S09 各阶段的状态与耗时（**过程时间线**）
  * ``search_trace.csv``     —— 每次容量试探的 delta / 是否可行 / 卡在什么约束
  * ``constraint_results.csv`` —— 逐场景、逐时刻、逐对象的约束实测（**每场景结果**）
  * ``scenarios.json``       —— 场景定义（典型日 / 高光伏低负荷 / 低光伏高负荷）

全部只读，缺失就以空列表返回 —— 界面据此显示"未获取"，不猜。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

STAGE_ZH = {
    "S01_freeze_inputs": "冻结输入",
    "S02_validate_bundle": "校验输入包",
    "S03_build_case": "构建算例",
    "S04_run_baseline": "基线潮流",
    "S05_build_scenarios": "构建场景",
    "S06_search_capacity": "容量搜索",
    "S07_verify_run": "独立复算",
    "S08_generate_report": "生成报告",
    "S09_seal_evidence": "签封证据",
}

SCENARIO_ZH = {
    "typical_day": "典型日",
    "high_pv_low_load": "高光伏·低负荷",
    "low_pv_high_load": "低光伏·高负荷",
}

CONSTRAINT_ZH = {
    "VOLTAGE_LIMIT": "电压越限",
    "THERMAL_LIMIT": "载流/热稳",
    "IMBALANCE_LIMIT": "三相不平衡",
    "SHORT_CIRCUIT_LIMIT": "短路容量",
    "REVERSE_FLOW_LIMIT": "反向潮流",
    "PENETRATION_LIMIT": "渗透率",
}


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_csv(p: Path) -> List[Dict[str, Any]]:
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except Exception:
        return []


@dataclass
class Stage:
    key: str
    label: str
    status: str
    elapsed_s: Optional[float] = None
    note: str = ""


@dataclass
class Trial:
    """一次容量试探。"""
    iteration: int
    delta_kw: float
    status: str
    binding: str = ""
    scenario_id: str = ""
    timestamp: str = ""
    pass_count: int = 0
    fail_count: int = 0
    unknown_count: int = 0

    @property
    def feasible(self) -> bool:
        return self.status == "feasible"


@dataclass
class ScenarioBlock:
    """一个场景的汇总 + 明细。"""
    scenario_id: str
    cls: str
    slice_count: int = 0
    rows: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return SCENARIO_ZH.get(self.cls, self.cls or self.scenario_id)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.rows if str(r.get("status")) == "fail")

    @property
    def unknown_count(self) -> int:
        return sum(1 for r in self.rows if str(r.get("status")) == "unknown")

    def worst_voltage(self) -> Optional[float]:
        vals = []
        for r in self.rows:
            if str(r.get("constraint_id")) != "VOLTAGE_LIMIT":
                continue
            try:
                vals.append(float(r.get("value")))
            except (TypeError, ValueError):
                pass
        return min(vals) if vals else None

    def max_loading(self) -> Optional[float]:
        vals = []
        for r in self.rows:
            if str(r.get("constraint_id")) != "THERMAL_LIMIT":
                continue
            try:
                vals.append(float(r.get("value")))
            except (TypeError, ValueError):
                pass
        return max(vals) if vals else None


@dataclass
class RunCode:
    """这次运行"是怎么跑起来的"，供界面如实展示。

    **exact=True** 表示读到了运行时记录的真实 argv；
    **exact=False** 表示产物里没有记录，命令是按参数**重建**的等效复现。
    两者必须在界面上区分开 —— 把重建的命令说成"实际执行"是误导。
    """

    exact: bool = False
    channel: str = ""               # cli / engine-host / gui / unknown
    cmdline: str = ""               # 可直接复制执行的命令行
    argv: List[str] = field(default_factory=list)
    cwd: str = ""
    python: str = ""
    equivalent: List[str] = field(default_factory=list)
    note: str = ""
    #: 引擎适配器日志（相对运行目录），供"查看引擎侧日志"
    engine_log: str = ""
    engine_log_lines: int = 0


class Process:
    """某次运行的"过程"视图。"""

    def __init__(self, run_dir: Path) -> None:
        self.dir = Path(run_dir)
        self.stages: List[Stage] = self._load_stages()
        self.trials: List[Trial] = self._load_trials()
        self.scenarios: List[ScenarioBlock] = self._load_scenarios()
        self.issues: List[Dict[str, Any]] = _read_csv(
            self.dir / "results" / "issues.csv")
        self.code: RunCode = self._load_code()

    # ---- 运行代码
    def _load_code(self) -> RunCode:
        """收集"怎么跑的"。

        优先用 manifest 里的 `invocation`（新版本运行会写）；
        没有就按参数**重建**一条等效命令，并明确标注是重建的。
        """
        man = _read_json(self.dir / "manifest.json") or {}
        inv = man.get("invocation") if isinstance(man, dict) else None
        rc = RunCode()
        params = (man.get("parameters") or {}) if isinstance(man, dict) else {}

        if isinstance(inv, dict) and (inv.get("equivalent_cli")
                                      or inv.get("argv")):
            rc.exact = True
            rc.channel = str(inv.get("channel") or "")
            rc.argv = [str(x) for x in (inv.get("argv") or [])]
            rc.cwd = str(inv.get("cwd") or "")
            rc.python = str(inv.get("python") or "")
            rc.equivalent = [str(x) for x in (inv.get("equivalent_cli") or [])]
            rc.cmdline = str(inv.get("equiv_cmdline") or
                             " ".join(rc.equivalent))
            rc.note = "以上为运行时记录的实际触发信息。"
        else:
            # 重建：产物里没有 invocation（本次改版之前产生的运行都是这样）
            inp = str(man.get("input_root") or "") if isinstance(man, dict) else ""
            equiv = ["python", "-m", "hc_agent.cli", "run"]
            if inp:
                equiv += ["--input", inp]
            equiv += ["--out", str(self.dir)]
            if params.get("max_slices") is not None:
                equiv += ["--max-slices", str(params["max_slices"])]
            rc.equivalent = equiv
            rc.cmdline = " ".join(
                f'"{a}"' if (" " in a or "\\" in a) else a for a in equiv)
            rc.channel = "unknown"
            rc.note = ("本次运行的产物里没有记录原始命令行，"
                       "上面这条是按运行参数重建的等效复现命令，"
                       "不是当时实际执行的那条。")

        # 引擎侧日志
        for rel in ("engine/opendss_adapter.log", "opendss_adapter.log"):
            f = self.dir / rel
            if f.is_file():
                rc.engine_log = rel
                try:
                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                        for _ in fh:
                            rc.engine_log_lines += 1
                except Exception:
                    pass
                break
        return rc

    def code_summary(self) -> List[str]:
        """把"执行过程"压成几句可读的说明（都基于产物，不推断）。"""
        out: List[str] = []
        if self.stages:
            seq = " → ".join(f"{s.key.split('_')[0]}" for s in self.stages)
            out.append(f"实际执行了 {len(self.stages)} 个阶段：{seq}")
        okc = sum(1 for s in self.stages if s.status == "ok")
        if self.stages and okc != len(self.stages):
            bad = [s.key for s in self.stages if s.status != "ok"]
            out.append(f"其中 {len(bad)} 个阶段不是 ok：{'、'.join(bad)}")
        if self.trials:
            out.append(f"调用引擎 {len(self.trials)} 次做容量试探")
        if self.code.engine_log:
            out.append(f"引擎侧日志 {self.code.engine_log}"
                       f"（{self.code.engine_log_lines} 行）")
        if not out:
            out.append("这次运行没有留下可展示的执行记录。")
        return out

    # ---- 阶段轨迹
    def _load_stages(self) -> List[Stage]:
        raw = _read_json(self.dir / "checkpoints.json") or []
        out: List[Stage] = []
        if not isinstance(raw, list):
            return out
        for x in raw:
            if not isinstance(x, dict):
                continue
            key = str(x.get("step") or "")
            out.append(Stage(
                key=key,
                label=STAGE_ZH.get(key, key),
                status=str(x.get("status") or ""),
                elapsed_s=(float(x["elapsed_s"])
                           if isinstance(x.get("elapsed_s"), (int, float))
                           else None),
                note=str(x.get("note") or ""),
            ))
        return out

    # ---- 容量搜索轨迹
    def _load_trials(self) -> List[Trial]:
        """读搜索轨迹。

        ⚠️ `iteration` **不是全局唯一**：粗扫描（0→3000 等距）与二分细化各自
        从 1 开始编号，所以 1、2… 会出现两次。按 iteration 排序会把两段轨迹
        搅在一起（实测踩过：试算顺序变成 1,1,2,2,3,3…）。
        这里**保持 CSV 原始顺序**（它本身就是引擎的调用顺序），并把序号换成
        全局连续的 `seq`，另存 `iteration` 供对照。
        """
        rows = _read_csv(self.dir / "results" / "search_trace.csv")
        out: List[Trial] = []
        for seq, r in enumerate(rows, start=1):
            try:
                it = int(float(r.get("iteration") or 0))
            except (TypeError, ValueError):
                it = 0
            try:
                delta = float(r.get("delta_kw") or 0)
            except (TypeError, ValueError):
                delta = 0.0

            def _i(k: str) -> int:
                try:
                    return int(float(r.get(k) or 0))
                except (TypeError, ValueError):
                    return 0

            t = Trial(
                iteration=it, delta_kw=delta,
                status=str(r.get("status") or ""),
                binding=str(r.get("binding_constraint") or ""),
                scenario_id=str(r.get("scenario_id") or ""),
                timestamp=str(r.get("timestamp") or ""),
                pass_count=_i("pass_count"), fail_count=_i("fail_count"),
                unknown_count=_i("unknown_count"))
            t.seq = seq                      # type: ignore[attr-defined]
            out.append(t)
        return out

    # ---- 分场景结果
    #: 这些 scenario_id 不是"时间场景"，而是独立校核，单独归类。
    #: 短路校核在产物里以 scenario_id='SC' 出现，若混进场景表格会让人误以为
    #: 它也是一个典型日工况（实测踩过）。
    SPECIAL_SCENARIOS = {"SC": "短路校核"}

    def _load_scenarios(self) -> List[ScenarioBlock]:
        meta = _read_json(self.dir / "results" / "scenarios.json") or {}
        blocks: Dict[str, ScenarioBlock] = {}
        order: List[str] = []
        for s in (meta.get("scenarios") or []):
            if not isinstance(s, dict):
                continue
            sid = str(s.get("scenario_id") or "")
            if not sid:
                continue
            order.append(sid)
            blocks[sid] = ScenarioBlock(
                scenario_id=sid,
                cls=str(s.get("scenario_class") or ""),
                slice_count=int(s.get("slice_count") or 0))
        # 逐行归入场景
        for r in _read_csv(self.dir / "results" / "constraint_results.csv"):
            sid = str(r.get("scenario_id") or "")
            if not sid:
                continue
            if sid not in blocks:
                blocks[sid] = ScenarioBlock(
                    scenario_id=sid,
                    cls=self.SPECIAL_SCENARIOS.get(sid, ""))
                order.append(sid)
            blocks[sid].rows.append(r)
        return [blocks[k] for k in order]

    @property
    def time_scenarios(self) -> List[ScenarioBlock]:
        """真正的时间场景（排除短路校核等独立校核）。"""
        return [s for s in self.scenarios
                if s.scenario_id not in self.SPECIAL_SCENARIOS]

    @property
    def special_blocks(self) -> List[ScenarioBlock]:
        return [s for s in self.scenarios
                if s.scenario_id in self.SPECIAL_SCENARIOS]

    # ---- 便捷
    @property
    def total_trials(self) -> int:
        return len(self.trials)

    @property
    def feasible_trials(self) -> List[Trial]:
        return [t for t in self.trials if t.feasible]

    def has_data(self) -> bool:
        return bool(self.stages or self.trials or self.scenarios)


def load(run_dir) -> Process:
    return Process(Path(run_dir))
