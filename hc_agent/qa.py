"""qa.py — 智能体问答引擎（本地规则引擎，完全离线）。

## 设计原则

1. **答案只能来自真实产物。** 所有数字都从 `runs/<id>/` 下的 JSON/CSV 里读，
   一个都不现算、不估算。查不到就明确说查不到 —— 这是本工程一贯的底线。
2. **不编造。** 规则引擎不懂的问题，如实回答"我没理解"并给出能问什么，
   而不是猜一个答案糊弄。
3. **每个回答都带出处。** 回答里标注来自哪个运行的哪个文件，便于核对。

## 为什么用规则引擎而不是大模型

本机没有可用的大模型（无 Ollama、无 API Key）。而且本领域的问题高度结构化：
容量多少、卡在哪、为什么、能不能接 X kW、最低电压多少……这些用意图+参数槽
就能稳定识别，且**可审计**——大模型则可能把 333.98 说成 340。

架构上留了升级口：`answer()` 返回结构化结果，将来接大模型只需替换
`intent()` 与 `compose()`，事实层（`Facts`）完全复用。
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hc_agent import paths


# ---------------------------------------------------------------- 事实层
def _read_json(p: Path) -> Optional[Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


@dataclass
class RunFacts:
    """一次运行的全部可查事实。构造时即读取，缺失的字段保持 None。"""

    run_id: str
    path: Path
    summary: Dict[str, Any] = field(default_factory=dict)
    outcome: Dict[str, Any] = field(default_factory=dict)
    gate: Dict[str, Any] = field(default_factory=dict)
    verification: Dict[str, Any] = field(default_factory=dict)
    scenarios: List[Dict[str, Any]] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)
    capacity_rows: List[Dict[str, Any]] = field(default_factory=list)
    constraint_rows: List[Dict[str, Any]] = field(default_factory=list)
    #: 运行清单（输入/快照/规则/版本/引擎指纹/参数）
    manifest: Dict[str, Any] = field(default_factory=dict)
    #: 报告清单（报告路径、章节、可追溯链接、结果哈希）
    report_manifest: Dict[str, Any] = field(default_factory=dict)

    # ---- 常用派生量（全部来自 summary，不做推算）
    @property
    def cap_physical(self) -> Optional[float]:
        v = self.summary.get("capacity_physical_kw")
        return None if v is None else float(v)

    @property
    def cap_managed(self) -> Optional[float]:
        v = self.summary.get("capacity_managed_kw")
        return None if v is None else float(v)

    @property
    def level(self) -> str:
        return str(self.summary.get("level") or "")

    @property
    def binding(self) -> str:
        return str(self.summary.get("binding_constraint") or "")

    @property
    def exit_code(self) -> Optional[int]:
        v = self.outcome.get("exit_code")
        return None if v is None else int(v)

    @property
    def ok(self) -> bool:
        """是否有可用结论（有摘要即视为有结论）。"""
        return bool(self.summary)


class Facts:
    """所有运行的只读视图 + 绑定到某个运行。"""

    def __init__(self) -> None:
        self.runs: List[RunFacts] = []
        self.current: Optional[RunFacts] = None
        self.refresh()

    def refresh(self) -> None:
        from hc_agent.desktop import runs as runs_mod
        self.runs = []
        for d in runs_mod.list_run_dirs():
            rf = self._load(d)
            if rf is not None:
                self.runs.append(rf)
        # 默认绑定"最近一次有结论的运行"
        if self.current is None or not self.current.ok:
            withcap = [r for r in self.runs if r.ok]
            self.current = (withcap or self.runs or [None])[0]

    def _load(self, d: Path) -> Optional[RunFacts]:
        rf = RunFacts(run_id=d.name, path=d)
        rf.summary = _read_json(d / "results" / "run_summary.json") or {}
        rf.outcome = _read_json(d / "run_outcome.json") or {}
        rf.gate = _read_json(d / "review" / "release_gate.json") or {}
        rf.verification = (_read_json(d / "verification" / "verification.json")
                           or {})
        rf.state = _read_json(d / "state.json") or {}
        rf.manifest = _read_json(d / "manifest.json") or {}
        rf.report_manifest = (_read_json(d / "report" / "report_manifest.json")
                              or {})
        sc = _read_json(d / "results" / "scenarios.json") or {}
        rf.scenarios = sc.get("scenarios") or [] if isinstance(sc, dict) else []
        rf.capacity_rows = _read_csv(d / "results" / "capacity.csv")
        rf.constraint_rows = _read_csv(d / "results" / "constraint_results.csv")
        if not (rf.summary or rf.outcome):
            return None
        return rf

    # ---- 选择运行：按名字 / 编号 / 关键词
    @staticmethod
    def _norm(s: str) -> str:
        """归一化：去掉连字符与大小写差异。

        `FD002`、`FD-002`、`fd-2` 说的是同一个对象，而产物里 scope_ids 存的
        是 `FD-002`、目录名却是 `FD002`。不归一化就会"问 FD002 却拿到别的运行"
        （实测踩过：返回了当前绑定的 DESKTOP）。
        """
        return re.sub(r"[\s\-_]", "", str(s or "")).upper()

    def find(self, hint: str) -> Optional[RunFacts]:
        if not hint:
            return self.current
        h = self._norm(hint)
        # 1) 目录名精确
        for r in self.runs:
            if self._norm(r.run_id) == h:
                return r
        # 2) 任务号
        for r in self.runs:
            if self._norm(r.summary.get("task_id")) == h:
                return r
        # 3) 拓扑对象（scope_ids，如 FD-002）
        for r in self.runs:
            ids = str(r.summary.get("scope_ids") or "").split(",")
            if any(self._norm(x) == h for x in ids if x.strip()):
                return r
        # 4) 模糊：目录名包含
        for r in self.runs:
            if h and h in self._norm(r.run_id):
                return r
        return None


def _read_csv(p: Path) -> List[Dict[str, Any]]:
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except Exception:
        return []


# ---------------------------------------------------------------- 意图识别
@dataclass
class Intent:
    name: str
    run_hint: str = ""
    number: Optional[float] = None
    unit: str = ""
    raw: str = ""


#: 约束名称中文映射（回答里用中文，便于阅读）
CONSTRAINT_ZH = {
    "VOLTAGE_LIMIT": "电压越限",
    "THERMAL_LIMIT": "载流/热稳越限",
    "IMBALANCE_LIMIT": "三相不平衡",
    "SHORT_CIRCUIT_LIMIT": "短路容量",
    "PENETRATION_LIMIT": "渗透率",
}


def _extract_run_hint(text: str) -> str:
    """抽出算例/运行标识，**保留原本的位数**。

    `FD002` 不能归一成 `FD2`：产物目录叫 `FD002`、scope 叫 `FD-002`，
    丢零之后就匹配不上了（实测踩过：问 FD002 却答的是别的运行）。
    归一化统一交给 Facts._norm 处理。

    除 FD/APP/RM 外，也接受**运行目录名本身**（如 DESKTOP、PKG-TEST），
    因为用户很可能直接说"DESKTOP 能接多少"。
    """
    for prefix in ("FD", "APP", "RM"):
        m = re.search(rf"{prefix}[- ]?(\d+)", text, re.I)
        if m:
            return f"{prefix}{m.group(1)}"
    # 运行目录名：取第一个像标识的英文词
    m = re.search(r"\b([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\b", text)
    if m:
        return m.group(1)
    return ""


def _extract_number(text: str) -> Tuple[Optional[float], str]:
    """从问题里抽"容量数字"。

    注意要**排除算例编号**（FD002 / APP001 里的数字），否则"FD002 还能接多少"
    会把 002 当成 2 kW 的接入量，给出完全错误的结论（实测踩过）。
    """
    cleaned = re.sub(r"(FD|APP|RM)[- ]?\d+", " ", text, flags=re.I)
    cleaned = re.sub(r"\d+\s*个", " ", cleaned)          # "3 个场景"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kw|千瓦|mw|兆瓦|w)", cleaned, re.I)
    if not m:
        return None, ""
    v = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit in ("mw", "兆瓦"):
        v *= 1000.0
    elif unit == "w":
        v /= 1000.0
    return v, unit


#: 意图规则表：(正则, 意图名)。**顺序有意义**，先匹配更具体的。
RULES: List[Tuple[str, str]] = [
    # ---- 验收指标 / 可观测性
    # ---- 引擎交叉校核（必须排在「验收」与普通「引擎」之前）
    (r"(交叉校核|双引擎|两个引擎|pandapower|第二引擎|引擎互证)",
     "engine_crosscheck"),
    (r"(验收|达标|指标矩阵|满不满足门槛|能不能验收|适配引擎数|引擎数|几个引擎)",
     "acceptance"),
    # ---- 数据质量（排在"口径"之前，避免被当成口径问题）
    (r"(数据质量|质量检查|质量治理|质量问题|有没有问题|数据有没有问题|"
     r"阻断|blocking|quality)", "quality_check"),
    # ---- 口径绑定校验 / 标准口径（必须排在普通"口径"规则前）
    (r"(口径).{0,6}(绑定|凭证|失效|还有效|是否有效|变没变)", "caliber_check"),
    (r"(结论).{0,6}(还能用|还有效|是否有效|失效|过期)", "caliber_check"),
    (r"(规则包|口径).{0,8}(变了|改了|更新了|换版本)", "caliber_check"),
    (r"DL.?T.?2041|2041", "standard_2041"),
    (r"(系统级|设备级).{0,4}承载力", "standard_2041"),
    # ---- 整改建议 / 审计校验（新能力，必须排在其它规则前）
    (r"(整改|治理|改善|缓解|怎么办|怎么解决|能做什么|有什么办法|"
     r"提升(承载力|可开放容量)|扩容|改造)", "remediation"),
    (r"(审计|哈希|hash|有没有被改|被改过|篡改|完整性|可追溯|验真)", "audit_check"),
    # ---- 算例内部（静态元件与假设分析）
    # **必须排在 dss_analyze 之前**：这几类问题里也会出现"算例"二字，
    # 放在后面会被"算例 → 跑一次潮流"的规则全部抢走（实测踩过：
    # 问"这个算例有什么元件"却跑了一次完整分析并出了报告）。
    (r"(如果|假如|要是|假设).{0,10}(光伏|分布式|发电|容量).{0,10}"
     r"(加到|增到|增加到|改成|减到|降到|到\s*\d)", "case_whatif"),
    (r"(光伏|分布式电源|发电机|PV).{0,8}(接在|接于|位置|在哪|几处|几台|哪里)",
     "case_pv"),
    (r"(算例|里面|内部).{0,8}(有什么|有哪些|元件|设备|清单|结构|组成|构成)",
     "case_inventory"),
    (r"(线路|导线).{0,6}(多长|长度|型号|截面|参数)", "case_inventory"),
    (r"(配变|变压器).{0,6}(多大|容量|几台|参数)", "case_inventory"),
    # OpenDSS 算例分析：要"真的跑一次潮流并出报告"，不是查已有结论。
    # 必须放在最前，否则会被后面的"容量/场景/复算"等规则抢先匹配。
    (r"(算例|opendss|\.dss|(?i:case)\b)", "dss_analyze"),
    (r"(算|计算|分析|跑).{0,8}(桌面|desktop|算例)", "dss_analyze"),
    (r"(能|可以|够|能否).{0,6}(接|装|加|并|接入|上)\s*\d+", "can_absorb"),
    (r"接?\s*\d+\s*(kw|千瓦|mw|兆瓦)", "can_absorb"),
    (r"(还有|剩|余量|剩余).{0,8}(多少|几|空间)", "headroom"),
    (r"(还能|可).{0,4}(接|装).{0,6}(多少|几)", "capacity"),
    # "能接多少" / "可以接多少" —— 没有"还"、也没有具体数字的常见问法，
    # 必须单独覆盖，否则最自然的一句反而落到 unknown（实测踩过）。
    (r"(能|可以|可).{0,3}(接|装|接入).{0,4}(多少|几|多大)", "capacity"),
    # "为什么接不了更多" 这类句子后半段是开放式的，不能要求必须出现"少/低/卡"，
    # 否则最自然的问法反而匹配不上（实测踩过）。改为：出现"为什么"就算 why。
    (r"(为什么|为何|原因|咋回事|怎么会|凭什么|凭什么)", "why"),
    (r"(卡|受限|瓶颈|限制).{0,6}(在|是|哪)", "why"),
    (r"(可开放|可接入|承载力|容量).{0,6}(多少|几|是多大)", "capacity"),
    (r"(物理|管理).{0,4}容量", "capacity"),
    (r"(最低|最小).{0,4}电压", "min_voltage"),
    (r"(负载|载流|loading|利用率|负荷率)", "loading"),
    (r"(越限|违规|不通过|没通过|未通过|没过|不合格|fail|超限)", "violations"),
    (r"(等级|红黄绿|颜色|绿|黄|红|灰)", "level"),
    (r"(复算|验证|独立|核验).{0,6}(过|通过|结果|怎么样)", "verification"),
    (r"(发布|门禁|审批|复核).{0,6}(吗|了|过|状态|能不能)", "gate"),
    (r"(场景|工况).{0,6}(有|是|哪|几个)", "scenarios"),
    (r"(规则|阈值|限值).{0,6}(是|哪|来自|什么)", "rules"),
    (r"(什么没覆盖|未覆盖|局限|不能算|做不到|边界)", "limits"),
    (r"(有哪些|列出|所有|看看).{0,6}(运行|任务|算例)", "list_runs"),
    (r"(口径|假设|模型|怎么算的|依据)", "caliber"),
    # ---- 报告 9 章（按章节提问，与报告结构一一对应）
    (r"(任务概况|任务的基本情况|这次(跑|执行)的是什么|谁发起|发起与复核|时间窗|范围是)", "chapter_task"),
    (r"(数据与口径|用了哪版(规则|规则包)|哪版规则|数据质量|快照|输入快照|假设是什么)", "chapter_data"),
    (r"(计算过程|算了几个(场景|时间片)|收敛|搜索记录|迭代了|用的什么方法)", "chapter_process"),
    (r"(接入校核|基线.{0,2}增量|增量对比|整改建议|结论候选)", "chapter_access"),
    (r"(风险|缺口|未解决|遗留问题|风险与缺口|有什么风险)", "chapter_risk"),
    (r"(证据|哈希|审计|证据索引|结果哈希|可追溯)", "chapter_evidence"),
    # 泛问"某算例怎么样" —— 必须放在具体意图之后，作为兜底概述
    (r"(怎么样|什么情况|概述|概要|总结|汇总|介绍一下|说说)", "summary"),
    (r"(你好|您好|嗨|hi|hello|在吗|你是谁|能做什么|帮助|help)", "help"),
]


def intent(text: str) -> Intent:
    t = (text or "").strip()
    low = t.lower()
    run_hint = _extract_run_hint(t)
    num, unit = _extract_number(t)

    if re.search(r"(你好|您好|嗨|^hi$|^hello$|在吗|你是谁|能做什么|怎么用|帮助)", low):
        return Intent("help", run_hint, raw=t)

    for pat, name in RULES:
        if re.search(pat, low, re.I):
            if name == "can_absorb":
                return Intent("can_absorb", run_hint, num, unit, t)
            return Intent(name, run_hint, raw=t)

    if not t:
        return Intent("help", raw=t)
    return Intent("unknown", run_hint, raw=t)


# ---------------------------------------------------------------- 回答
@dataclass
class Answer:
    text: str
    source: str = ""
    followups: List[str] = field(default_factory=list)
    #: 若有计算报告，这里是报告文件绝对路径（界面据此显示"打开计算报告"）
    report: str = ""
    report_label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out = {"text": self.text, "source": self.source,
               "followups": self.followups}
        if self.report:
            out["report_path"] = self.report
            out["report_label"] = self.report_label or "打开计算报告"
        return out


def _fmt(v: Any, digits: int = 4) -> str:
    if v is None or v == "":
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if n != n:
        return "—"
    return f"{n:,.{digits}f}".rstrip("0").rstrip(".")


def _pct(a: Optional[float], b: Optional[float]) -> str:
    if not a or not b:
        return ""
    return f"（约 {a / b * 100:.0f}%）"


SUGGESTIONS = [
    "算一下桌面上的 OpenDSS 算例，并给我计算报告",
    "FD002 还能接多少光伏？",
    "为什么接不了更多？",
    "最低电压是多少？",
    "FD001 是什么情况？",
    "复算通过了吗？",
    "哪些约束没通过？",
]


class Responder:
    """把意图 + 事实变成回答。"""

    def __init__(self, facts: Facts) -> None:
        self.facts = facts

    # ---- 工具
    def _pick(self, it: Intent) -> Optional[RunFacts]:
        if it.run_hint:
            r = self.facts.find(it.run_hint)
            if r is not None:
                return r
        return self.facts.current

    def _src(self, r: RunFacts, what: str) -> str:
        return f"来源：runs/{r.path.name}/results/run_summary.json（{what}）"

    def _no_run(self) -> Answer:
        return Answer(
            "我还没读到任何运行结果，所以现在没有可回答的事实依据。\n"
            "请先在「容量分析」页发起一次评估，或运行一次 "
            "`python -m hc_agent.cli run`。\n"
            "我不会在缺少数据时编一个答案给你。",
            source="（无运行产物）", followups=[])

    # ---- 各意图
    def answer(self, text: str) -> Answer:
        it = intent(text)
        fn = getattr(self, f"_a_{it.name}", None)
        if fn is None:
            return self._a_unknown(it)
        r = self._pick(it)
        # 这几种意图不依赖运行产物：help/list_runs 是通用问题；
        # dss_analyze 与 case_* 直接读算例文件、自己跑引擎，
        # 没有历史运行也应当能回答（否则"问算例里有什么"会被拦成
        # "我还没读到任何运行结果"）。
        _NO_RUN_NEEDED = ("help", "list_runs", "dss_analyze",
                          "remediation", "audit_check",
                          "caliber_check", "standard_2041",
                          "quality_check", "acceptance",
                          "case_inventory", "case_pv", "case_whatif")
        if r is None and it.name not in _NO_RUN_NEEDED:
            return self._no_run()
        return fn(it, r)

    # --- 总体情况（泛问时的兜底概述）
    def _a_summary(self, it: Intent, r: RunFacts) -> Answer:
        if not r.ok:
            # 没有 summary：给出它能提供的一切（可能只有 outcome）
            oc = r.outcome or {}
            if oc:
                return Answer(
                    f"**{r.run_id}** 有运行记录但没有完整摘要，"
                    f"退出码 {oc.get('exit_code', '—')}。",
                    source=f"runs/{r.path.name}/run_outcome.json")
            return Answer(f"{r.run_id} 没有可读的产物。",
                          source=f"runs/{r.path.name}/")
        cs = r.summary.get("constraint_summary") or {}
        zh = {"green": "绿色", "yellow": "黄色", "red": "红色",
              "grey": "灰色（内部态）"}
        lines = [
            f"**{r.run_id}** 的情况：",
            f"  · 物理可开放容量：**{_fmt(r.cap_physical)} kW**"
            f"；管理可开放 {_fmt(r.cap_managed)} kW",
            f"  · 等级：{zh.get(r.level, r.level or '—')}"
            f"；主导限制：{CONSTRAINT_ZH.get(r.binding, r.binding) or '—'}",
            f"  · 约束：共 {cs.get('total', '—')} 项，通过 {cs.get('pass', '—')}，"
            f"不通过 {cs.get('fail', '—')}，未覆盖 {cs.get('unknown', '—')}",
        ]
        if r.cap_physical == 0:
            lines.append(
                "\n⚠️ 容量是 **0 kW**：不加任何新增的基线状态就已经越限，"
                "所以连 1 kW 都接不了，必须先解决基线问题。")
        v = r.verification
        if v:
            lines.append(f"  · 独立复算：{'通过' if v.get('pass') else '未通过'}")
        g = r.gate
        if g:
            lines.append(f"  · 发布门禁：{'已通过' if g.get('passed') else '未通过'}"
                         + (f"（卡在：{'、'.join(g.get('failed_gates') or [])}）"
                            if g.get("failed_gates") else ""))
        lines.append(f"\n想细看可以问：为什么是这个值？哪些约束没通过？复算通过了吗？")
        return Answer("\n".join(lines), source=self._src(r, "run_summary.json"),
                      followups=["为什么是这个值？", "哪些约束没通过？",
                                 "复算通过了吗？"])

    # ================= 验收指标 =================
    def _a_acceptance(self, it: Intent, r: RunFacts) -> Answer:
        """验收指标矩阵（开发文档 11.2）。"""
        from hc_agent import acceptance as A

        if r is None or not r.ok:
            return Answer(
                "验收指标是对**某一次运行**算的，现在还没有可查的运行。"
                "\n\n先跑一次评估再问。", source="")
        res = A.single_run(r.path)
        return Answer(A.format_matrix(res), source=str(r.path))

    # ================= 引擎交叉校核 =================
    def _a_engine_crosscheck(self, it: Intent, r: RunFacts) -> Answer:
        """第二引擎（pandapower，正序平衡）与 OpenDSS 的交叉校核。"""
        from hc_agent.engines import pandapower_adapter as PA

        info = PA.available()
        if not info.get("available"):
            return Answer(
                "pandapower 不可用：" + str(info.get("error") or "未知原因")
                + "\n\n第二引擎没接上时，验收矩阵的「适配引擎数」只算 1 种。",
                source="hc_agent/engines/pandapower_adapter.py")
        if r is None or not r.ok:
            return Answer(
                "交叉校核要对**某一次运行**做（需要它的冻结输入与产物）。\n\n"
                "先跑一次评估，再问「对这个运行做交叉校核」。", source="")
        rep = PA.crosscheck(Path(r.path))
        return Answer(PA.format_crosscheck(rep), source=str(r.path))

    # ================= 数据质量 =================
    def _a_quality_check(self, it: Intent, r: RunFacts) -> Answer:
        """数据质量治理结果。"""
        from hc_agent.io import quality as Q

        if r is None or not r.ok:
            return Answer(
                "数据质量是对**某一次运行**的输入检查结果，现在还没有可查的运行。"
                "\n\n先跑一次评估，之后问「数据质量怎么样」就能看到。",
                source="")
        res = Q.read(r.path)
        return Answer(f"**{r.run_id}** 的数据质量\n\n"
                      + Q.format_report(res), source=str(r.path))

    # ================= 口径绑定 / 标准口径 =================
    def _a_caliber_check(self, it: Intent, r: RunFacts) -> Answer:
        """口径绑定校验：这份结论现在还能不能引用。"""
        from hc_agent import caliber as cal

        if r is None or not r.ok:
            return Answer(
                "口径绑定是对**某一次运行**做的校验，现在还没有可校验的运行。\n\n"
                "先跑一次评估，之后问「这次结论的口径还有效吗」就能校验。",
                source="")
        res = cal.check(r.path)
        return Answer(f"**{r.run_id}** 的口径绑定校验\n\n"
                      + cal.format_check(res), source=str(r.path))

    def _a_standard_2041(self, it: Intent, r: RunFacts) -> Answer:
        """DL/T 2041 与"我们这套怎么对应"的问题。"""
        from hc_agent.knowledge.service import KnowledgeService
        try:
            sv = KnowledgeService()
            hits = sv.search(it.raw or "DL/T 2041 承载力", top_k=3)
        except Exception:
            hits = []
        body = ""
        for h in hits:
            t = (getattr(h, "text", "") or "").strip()
            if t:
                body += (t if body.endswith("\n") else t + "\n\n")
        head = ("**关于 DL/T 2041**（配电网承载力唯一的直接行业标准）\n\n"
                "两处要注意：\n"
                "· **版本**：DL/T 2041-2019 已被 DL/T 2041-2025 替代"
                "（2026-06-18 实施），处在交替期，先问清按哪个版本。\n"
                "· **层级**：它把承载力分成**系统级**（时序生产模拟，行政区/调度区）"
                "与**设备级**（变压器反向负载率，220/330kV 及以下）。\n"
                "· 本工程做的是**设备级**；系统级需要 8760 电量模拟，本工具不做。\n"
                "· 新增 2025 版取消了红黄绿「80% 硬指标」，改按可开放容量区间判定。\n\n")
        if body:
            head += "---\n\n" + body[:1800]
        else:
            head += "（知识库没检索到正文，请检查知识库是否已建索引。）"
        return Answer(head, source="知识库 · 《DL/T 2041 承载力评估导则》")

    # ================= 整改建议 / 审计校验 =================
    def _a_remediation(self, it: Intent, r: RunFacts) -> Answer:
        """整改建议候选。

        优先用**当前运行的产物**（最贴近用户正在看的东西）；
        没有运行产物时，用算例的实测约束。
        产出是"候选方向"，明确不承诺能提升多少 kW。
        """
        from hc_agent import remediation as rm

        if r is not None and r.ok:
            res, _ = rm.from_run(r.path)
            if res.get("ok"):
                head = (f"针对 **{r.run_id}** 的绑定约束"
                        f"（{res.get('binding_constraint') or '未标注'}）"
                        f"给出候选整改方向：\n\n")
                return Answer(head + rm.format_proposals(res),
                              source=str(r.path))

        # 退回：用当前算例
        from hc_agent.io import dss_facts, dss_case
        from hc_agent.io.dss_report import resolve_case_path
        path = resolve_case_path(it.raw or "")
        if path:
            try:
                a = dss_case.analyze(path)
                vv = a.voltage_violations()
                ov = a.overloaded()
                bv = a.best_voltage()
                wv = a.worst_voltage()
                wb = a.worst_branch()
                vmargin = None
                if bv is not None:
                    vmargin = min(1.10 - bv.vmin_pu, 0.0) if bv.vmin_pu > 1.10 \
                        else None
                if wv is not None and vmargin is None:
                    vmargin = None
                lm = (100.0 - wb.loading_pct) if wb is not None else None
                res = rm.propose(
                    binding_constraint=("VOLTAGE_LIMIT" if vv else
                                        "THERMAL_LIMIT" if ov else ""),
                    voltage_violations=[b.name for b in vv],
                    overloaded=[b.name for b in ov],
                    voltage_margin=vmargin,
                    loading_margin=lm)
                head = (f"**{a.source_name}** 的实测约束："
                        f"电压越限 {len(vv)} 个、过载 {len(ov)} 个。\n\n")
                return Answer(head + rm.format_proposals(res), source=path)
            except Exception:
                pass

        return Answer(
            "要给出整改建议，我需要先知道**哪条约束越限**。\n\n"
            "可以：\n"
            "· 先在「容量分析」页跑一次评估，或让我算一个算例；\n"
            "· 或者直接问「算一下配网承载力算例_双台区对比」再问整改。",
            source="")

    def _a_audit_check(self, it: Intent, r: RunFacts) -> Answer:
        """审计链校验：这份结果有没有被改过。"""
        from hc_agent import audit_chain_summary as acs

        if r is None or not r.ok:
            return Answer(
                "审计链是对**某一次运行**的产物做的完整性校验，"
                "现在还没有可校验的运行。\n\n"
                "先跑一次评估（或算一个算例），之后问「这次运行有没有被改过」"
                "就能校验了。", source="")

        res = acs.verify(str(r.path))
        lines = [f"**{r.run_id}** 的审计链校验", ""]
        if res.get("count") == 0 and "还没有审计链" in str(res.get("reason")):
            lines.append("这次运行**还没有审计链**（它是加入该机制之前产生的）。")
            lines.append("")
            lines.append("可以把现有的 `audit/events.jsonl` 逐条补建一条哈希链，"
                         "补建后即可校验；原事件文件不改。")
            return Answer("\n".join(lines), source=str(r.path))

        if res.get("ok"):
            lines.append(f"✅ 通过：共 {res.get('count')} 条事件，"
                         f"整条链一致。")
            lines.append("")
            lines.append("说明：这说明**记录没有被改动或插入删除**，"
                         "不代表结论本身一定正确（正确性由独立复算与审批负责）。")
        else:
            lines.append(f"❌ 未通过：在第 {res.get('broken_at')} 条处断开。")
            lines.append(f"原因：{res.get('reason')}")
            lines.append("")
            lines.append("这条链路一旦对不上，就无法证明该条之后的记录未被修改。"
                         "请以原始产物为准并追查改动来源。")
        hh = res.get("head_hash") or ""
        if hh:
            lines.append("")
            lines.append(f"链头哈希：`{hh}`")
        return Answer("\n".join(lines), source=str(r.path))

    # ================= 算例内部（静态元件 / 假设分析） =================
    def _case_path_from(self, it: Intent, r: Optional[RunFacts]) -> str:
        """定位这次要问的算例：句子里的名字 > 登记的算例 > 默认桌面算例。"""
        from hc_agent.io.dss_report import resolve_case_path
        return resolve_case_path(it.raw or "")

    def _a_case_inventory(self, it: Intent, r: RunFacts) -> Answer:
        from hc_agent.io import dss_facts
        from hc_agent.io.dss_report import resolve_case_path
        path = resolve_case_path(it.raw or "")
        if not path:
            return Answer(
                "我还没找到要解析的算例。可以把 .dss 文件夹拖进对话，"
                "或说「算一下配网承载力算例_双台区对比」再问。",
                source="")
        f = dss_facts.case_facts(path)
        if not f.get("ok"):
            return Answer(f"解析失败：{f.get('error') or '未知原因'}",
                          source=str(path))
        return Answer(dss_facts.format_inventory(f), source=str(path))

    def _a_case_pv(self, it: Intent, r: RunFacts) -> Answer:
        from hc_agent.io import dss_facts
        from hc_agent.io.dss_report import resolve_case_path
        path = resolve_case_path(it.raw or "")
        if not path:
            return Answer("还没找到算例。把算例文件夹拖进来，或说出算例名。",
                          source="")
        f = dss_facts.case_facts(path)
        if not f.get("ok"):
            return Answer(f"解析失败：{f.get('error') or '未知原因'}",
                          source=str(path))
        gens = f["generators"]
        if not gens:
            return Answer(f"**{f['name']}** 里没有分布式电源元件"
                          f"（Generator / PVSystem 都没找到）。",
                          source=str(path))
        total = sum(g["kw"] or 0 for g in gens)
        lines = [f"**{f['name']}** 的分布式电源（按元件参数）", ""]
        for g in gens:
            lines.append(f"· `{g['name']}`：{g['kw']} kW @ `{g['bus']}`"
                         + (f"，pf={g['pf']}" if g["pf"] is not None else ""))
        lines.append("")
        lines.append(f"合计 {total:.1f} kW，共 {len(gens)} 处。")
        # 与本地负荷对照（同样来自元件参数）
        load = f["load_kw_declared"]
        if load:
            lines.append(f"本地负荷合计 {load:.1f} kW，"
                         + ("电源出力小于负荷，这个断面不会有整站倒送。"
                            if total <= load
                            else "电源出力大于负荷，整站会出现倒送。"))
        lines.append("")
        lines.append("想看「加到多少会越限」，可以问："
                     "「如果把光伏加到 300 kW 会怎样」。")
        return Answer("\n".join(lines), source=str(path))

    def _a_case_whatif(self, it: Intent, r: RunFacts) -> Answer:
        from hc_agent.io import dss_facts
        from hc_agent.io.dss_report import resolve_case_path
        path = resolve_case_path(it.raw or "")
        if not path:
            return Answer("还没找到算例。把算例文件夹拖进来，或说出算例名。",
                          source="")
        # 从问题里取目标容量（"加到 300 kW"）
        num, _unit = _extract_number(it.raw or "")
        if num is None:
            return Answer(
                "要做假设分析，请给出目标容量，例如"
                "「如果把光伏加到 300 kW 会怎样」。", source=str(path))
        res = dss_facts.what_if_pv(path, float(num))
        if not res.get("ok"):
            return Answer(f"假设分析没做成：{res.get('error')}", source=str(path))

        def d(a, b, digits=4):
            if a is None or b is None:
                return "未获取"
            return f"{a:.{digits}f} → {b:.{digits}f}"

        lines = [f"**{res['case']}** 如果把分布式电源改到 "
                 f"{res['gen_after_kw']:.0f} kW（现为 "
                 f"{res['gen_before_kw']:.0f} kW）：", ""]
        lines.append(f"· 最高电压：{d(res['vmax_before'], res['vmax_after'])} pu")
        lines.append(f"· 最低电压：{d(res['vmin_before'], res['vmin_after'])} pu")
        lines.append(f"· 电压越限母线：{res['violations_before']} → "
                     f"{res['violations_after']} 个")
        lines.append(f"· 过载元件：{res['overload_before']} → "
                     f"{res['overload_after']} 个")
        lines.append(f"· 最大负载率：{d(res['maxload_before'], res['maxload_after'], 2)}%")
        lines.append(f"· 网损：{d(res['losses_before_kw'], res['losses_after_kw'], 2)} kW")
        lines.append(f"· 整站倒送：{'无' if not res['reverse_before'] else '有'}"
                     f" → {'无' if not res['reverse_after'] else '有'}")
        if not res.get("converged", True):
            lines.append("")
            lines.append("⚠ 改了之后潮流**不收敛**，上面的数值不可用。")
        lines.append("")
        lines.append("说明：这是**真的把算例复制一份、改掉光伏容量后重跑引擎**"
                     "得到的结果，不是按比例外推；你原来的算例没有被改动。")
        return Answer("\n".join(lines), source=str(path))

    # ================= 报告 9 章（逐章可问） =================
    # 统一原则：**只说产物里有的**；本章所需数据缺失就明说，
    # 不拿别的章节凑、也不推断。
    def _chapter_head(self, r: RunFacts, title: str, idx: int) -> List[str]:
        return [f"**{r.run_id}** · 报告第 {idx} 章：{title}", ""]

    def _a_chapter_task(self, it: Intent, r: RunFacts) -> Answer:
        if not r.ok:
            return self._no_run()
        sm = r.summary
        man = r.manifest or {}
        lines = self._chapter_head(r, "任务概况", 1)
        rows = [
            ("运行号", sm.get("run_id") or r.run_id),
            ("任务号", sm.get("task_id") or man.get("task_id") or "未标注"),
            ("业务场景", sm.get("business_scene") or "未标注"),
            ("评估范围", sm.get("scope_type") or "未标注"),
            ("范围对象", "、".join(str(x) for x in (sm.get("scope_ids") or []))
             or "未标注"),
            ("时间窗", f"{sm.get('time_window_start') or '—'} 至 "
                       f"{sm.get('time_window_end') or '—'}"),
            ("快照", sm.get("snapshot_id") or man.get("snapshot_id") or "—"),
            ("发起时间", man.get("frozen_at") or sm.get("generated_at") or "—"),
        ]
        for k, v in rows:
            lines.append(f"  · {k}：{v}")
        g = r.gate or {}
        gates = g.get("gates") or []
        if gates:
            done = [x.get("gate") for x in gates if x.get("passed")]
            todo = [x.get("gate") for x in gates if not x.get("passed")]
            lines.append("")
            lines.append(f"复核状态：已通过 {len(done)} 道，未通过 {len(todo)} 道"
                         + (f"（{'、'.join(str(x) for x in todo)}）" if todo else ""))
        else:
            lines.append("")
            lines.append("本章要求的“发起与复核信息”：产物里没有门禁记录。")
        return Answer("\n".join(lines), source=self._src(r, "run_summary.json"))

    def _a_chapter_data(self, it: Intent, r: RunFacts) -> Answer:
        if not r.ok:
            return self._no_run()
        sm, man = r.summary, (r.manifest or {})
        lines = self._chapter_head(r, "数据与口径", 2)
        eng = sm.get("engine_fingerprint") or man.get("engine_fingerprint") or {}
        lines.append(f"  · 规则包：{sm.get('rule_package') or man.get('rule_version') or '—'}")
        lines.append(f"  · 快照：{sm.get('snapshot_id') or man.get('snapshot_id') or '—'}")
        dig = sm.get("input_digest") or man.get("input_digest") or ""
        if dig:
            lines.append(f"  · 输入摘要（sha256 前 16 位）：`{str(dig)[:16]}…`")
        if eng:
            lines.append(f"  · 计算引擎：{eng.get('engine_id') or '—'} / "
                         f"{eng.get('interface') or '—'}")
            if eng.get("python_version"):
                lines.append(f"  · Python：{eng.get('python_version')}")
        sv = sm.get("skill_version") or man.get("skill_version")
        if isinstance(sv, dict) and sv:
            lines.append(f"  · 技能版本：{len(sv)} 个技能，"
                         f"如 {next(iter(sv.items()))}")
        cal = sm.get("caliber") or {}
        items = cal.get("items") or []
        if items:
            lines.append("")
            lines.append("**计算口径**（报告第 2 章原文条目）：")
            for x in items:
                lines.append(f"  · {x.get('label')}：{x.get('value')}")
        asm = cal.get("assumptions") or []
        if asm:
            lines.append("")
            lines.append("**假设**：")
            for x in asm[:8]:
                lines.append(f"  · {x if isinstance(x, str) else x}")
        if not items and not asm:
            lines.append("")
            lines.append("产物里没有口径条目（caliber 为空）。")
        return Answer("\n".join(lines), source=self._src(r, "run_summary.json"))

    def _a_chapter_process(self, it: Intent, r: RunFacts) -> Answer:
        if not r.ok:
            return self._no_run()
        sm = r.summary
        lines = self._chapter_head(r, "计算过程", 3)
        lines.append(f"  · 场景数：{sm.get('scenario_count', '—')}"
                     f"（{'、'.join(str(x) for x in (sm.get('scenario_ids') or [])) or '—'}）")
        lines.append(f"  · 时间片：{sm.get('slice_count', '—')} 个")
        conv = sm.get("all_converged")
        lines.append(f"  · 是否全部收敛：{'是' if conv else '否' if conv is False else '—'}")
        lines.append(f"  · 搜索方法：{sm.get('search_method') or '—'}")
        lines.append(f"  · 搜索迭代：{sm.get('search_iterations', '—')} 次")
        lines.append(f"  · 搜索状态：{sm.get('search_status') or '—'}")
        cr = sm.get("constraint_rows")
        if isinstance(cr, dict):
            # 它是 {count, status_counts, digest, rows:[...]}，
            # 直接把整包打出来会把答案刷屏（实测踩过）
            lines.append(f"  · 约束校核记录：{cr.get('count', '—')} 行"
                         + (f"（{'、'.join(f'{k} {v}' for k, v in (cr.get('status_counts') or {}).items())}）"
                            if cr.get('status_counts') else ""))
        elif cr is not None:
            lines.append(f"  · 约束校核记录：{cr} 行")
        notes = sm.get("search_notes") or []
        if notes:
            lines.append("")
            lines.append("**搜索说明**：")
            for n in notes[:8]:
                lines.append(f"  · {n}")
        # 阶段轨迹（若有 checkpoints 就一并给出，便于定位卡点）
        try:
            from hc_agent.desktop import process as proc_mod
            pr = proc_mod.load(r.path)
            if pr.stages:
                lines.append("")
                lines.append(f"**阶段轨迹**（{len(pr.stages)} 步）：")
                for st in pr.stages:
                    t = f"{st.elapsed_s}s" if st.elapsed_s is not None else "—"
                    lines.append(f"  · {st.key} {st.label}：{st.status}（{t}）")
        except Exception:
            pass
        return Answer("\n".join(lines), source=self._src(r, "run_summary.json"))

    def _a_chapter_access(self, it: Intent, r: RunFacts) -> Answer:
        """第 5 章：接入校核。**只在真有接入校核产物时才答**。"""
        lines = self._chapter_head(r, "接入校核", 5)
        found = None
        for rel in ("results/access_check.json", "results/access.json",
                    "results/access_result.json"):
            v = _read_json(r.path / rel) if hasattr(r, "path") else None
            if v:
                found = (rel, v)
                break
        if found is None:
            lines.append("这次运行**没有接入校核产物**，所以第 5 章为空。")
            lines.append("")
            lines.append("按设计，只有在业务场景是“接入校核/并网申请”时才生成该章；"
                         "纯容量评估不会产出它。这不是漏算。")
            return Answer("\n".join(lines), source=str(r.path))
        rel, v = found
        lines.append(f"  · 结论：{v.get('verdict') or v.get('conclusion') or '—'}")
        if v.get("requested_kw") is not None:
            lines.append(f"  · 申请容量：{_fmt(v.get('requested_kw'))} kW")
        for k in ("baseline", "increment"):
            if isinstance(v.get(k), dict):
                lines.append(f"  · {k}：{json.dumps(v[k], ensure_ascii=False)[:200]}")
        sug = v.get("suggestions") or v.get("remediation") or []
        if sug:
            lines.append("")
            lines.append("**整改建议**：")
            for x in sug[:8]:
                lines.append(f"  · {x}")
        return Answer("\n".join(lines), source=self._src(r, rel))

    def _a_chapter_risk(self, it: Intent, r: RunFacts) -> Answer:
        if not r.ok:
            return self._no_run()
        sm = r.summary
        cal = sm.get("caliber") or {}
        lines = self._chapter_head(r, "风险与缺口", 6)
        unk = sm.get("unknowns") or []
        risks = sm.get("risks") or []
        uns = cal.get("unsupported") or []
        if unk:
            lines.append(f"**未覆盖（unknown）共 {len(unk)} 项**：")
            for x in unk[:10]:
                lines.append(f"  · {x if isinstance(x, str) else x}")
            lines.append("")
        if risks:
            lines.append(f"**风险共 {len(risks)} 条**：")
            for x in risks[:10]:
                lines.append(f"  · {x if isinstance(x, str) else x}")
            lines.append("")
        if uns:
            lines.append("**本版能力边界（做不到的）**：")
            for x in uns:
                lines.append(f"  · {x}")
        if not (unk or risks or uns):
            lines.append("产物里没有记录风险或未覆盖项，本章为空。")
        return Answer("\n".join(lines), source=self._src(r, "run_summary.json"))

    def _a_chapter_evidence(self, it: Intent, r: RunFacts) -> Answer:
        lines = self._chapter_head(r, "证据索引", 9)
        rm = r.report_manifest or {}
        if rm.get("result_hash"):
            lines.append(f"  · 结果哈希：`{rm.get('result_hash')}`")
        if rm.get("report_path"):
            lines.append(f"  · 报告：{rm.get('report_path')}")
        if rm.get("generated_at"):
            lines.append(f"  · 报告生成时间：{rm.get('generated_at')}")
        trace = rm.get("traceability") or {}
        links = trace.get("links") if isinstance(trace, dict) else {}
        if isinstance(links, dict) and links:
            lines.append("")
            lines.append("**可追溯链接**：")
            for k, v in links.items():
                if isinstance(v, dict):
                    v = (v.get("engine_id") or
                         json.dumps(v, ensure_ascii=False))
                lines.append(f"  · {k}：{str(v)[:120]}")
        refs = (r.summary or {}).get("evidence_refs") or []
        if refs:
            lines.append("")
            lines.append(f"**证据文件（{len(refs)} 项）**：")
            for x in refs[:12]:
                lines.append(f"  · {x}")
        # 审计事件数
        ev = r.path / "audit" / "events.jsonl"
        if ev.is_file():
            try:
                n = sum(1 for _ in ev.open("r", encoding="utf-8",
                                          errors="replace"))
                lines.append("")
                lines.append(f"审计事件 `audit/events.jsonl`：{n} 条")
            except Exception:
                pass
        if len(lines) <= 2:
            lines.append("产物里没有证据索引信息，本章为空。")
        return Answer("\n".join(lines),
                      source=self._src(r, "report/report_manifest.json"))

    # --- OpenDSS 算例分析（真的跑一次潮流并生成报告）
    def _a_dss_analyze(self, it: Intent, r=None) -> Answer:
        try:
            from hc_agent.io.dss_report import analyze_and_report
        except Exception as exc:
            return Answer(f"当前环境无法做 OpenDSS 算例分析：{exc}")

        # 从问题里抽路径（支持引号包裹、Windows 盘符、正反斜杠）
        import re as _re
        m = (_re.search(r'["\']([^"\']+\.dss)["\']', it.raw, _re.I)
             or _re.search(r"([A-Za-z]:[^\s\"']+)", it.raw)
             or _re.search(r"([^\s\"']+\.dss)", it.raw, _re.I))
        path = m.group(1).strip() if m else ""
        # 没有显式路径时，把整句话交给解析器 —— 它会去句子中找算例名。
        res = analyze_and_report(path or it.raw)
        if not res.get("ok"):
            err = str(res.get("error") or "分析失败")
            hint = str(res.get("hint") or "")
            return Answer(f"没能分析这个算例：{err}"
                          + (f"\n{hint}" if hint else ""))

        s = res["summary"]
        lines = [f"**{s['case_name']}** 算完了（OpenDSS 原生算例，单断面潮流）。", ""]
        lines.append(f"  · 求解：{'收敛' if s['converged'] else '**未收敛**'}，"
                     f"{s['iterations']} 次迭代，"
                     f"{s['bus_count']} 母线 / {s['branch_count']} 支路")
        lines.append(f"  · 负荷 {_fmt(s['total_load_kw'])} kW，"
                     f"分布式电源 {_fmt(s['total_gen_kw'])} kW，"
                     f"源侧 {_fmt(s['source_kw'])} kW（正数=倒送）")
        lines.append(f"  · 网损 {_fmt(s['losses_kw'])} kW"
                     f"（占负荷 {_fmt(s['losses_pct'])}%）")
        if s.get("max_voltage_pu") is not None:
            lines.append(f"  · 最高电压 **{s['max_voltage_pu']:.5f} pu**"
                         f" @ `{s['max_voltage_bus']}`")
        if s.get("min_voltage_pu") is not None:
            lines.append(f"  · 最低电压 **{s['min_voltage_pu']:.5f} pu**"
                         f" @ `{s['min_voltage_bus']}`")
        if s.get("max_loading_pct") is not None:
            lines.append(f"  · 最大负载率 **{s['max_loading_pct']:.2f}%**"
                         f" @ `{s['max_loading_branch']}`")
        vv = s.get("voltage_violations") or []
        ov = s.get("overloaded") or []
        lines.append("")
        lines.append(f"越限情况：电压越限 **{len(vv)}** 个母线，"
                     f"过载 **{len(ov)}** 个元件。")
        for f in (s.get("findings") or []):
            lines.append(f"  · {f}")
        note = str(s.get("capacity_note") or "").replace("**", "")
        if note:
            lines.append("")
            lines.append("⚠️ " + note)
        for w in (s.get("warnings") or []):
            lines.append(f"⚠️ {w}")
        rp = res.get("report_path") or ""
        if rp:
            lines.append("")
            lines.append("计算报告已生成，点下面的按钮可以直接打开。")
        return Answer("\n".join(lines),
                      source=rp or s.get("case_path", ""),
                      report=rp, report_label="打开计算报告")

    # --- 帮助
    def _a_help(self, it: Intent, r) -> Answer:
        return Answer(
            "我是配网承载力分析智能体。你可以直接用大白话问我，比如：\n"
            + "\n".join(f"  · {s}" for s in SUGGESTIONS)
            + "\n\n我只回答**有真实数据支撑**的问题：所有数字都从已完成的"
              "评估产物里读，读不到我会直说，不会估算或编造。",
            source="（功能说明）", followups=SUGGESTIONS[:3])

    def _a_unknown(self, it: Intent, r=None) -> Answer:
        return Answer(
            f"这个问题我暂时没理解：「{it.raw}」。\n"
            "我是按规则匹配的本地引擎，只认得下面这些问法：\n"
            + "\n".join(f"  · {s}" for s in SUGGESTIONS)
            + "\n\n如果问题确实超出范围，也可以直接看「结果与证据」页的原始产物。",
            source="", followups=SUGGESTIONS[:3])

    # --- 容量
    def _a_capacity(self, it: Intent, r: RunFacts) -> Answer:
        if not r.ok:
            return Answer(f"{r.run_id} 没有可用的容量结论（可能未跑完或输入被阻断）。",
                          source=self._src(r, "无 summary"))
        lines = [
            f"**{r.run_id}** 的开放容量：",
            f"  · 物理可开放容量：**{_fmt(r.cap_physical)} kW**"
            f"（受物理约束的最大可接入量）",
            f"  · 管理可开放容量：**{_fmt(r.cap_managed)} kW**"
            f"（再叠加管理裕度后的可发布值）",
            f"  · 红黄绿等级：{r.level or '—'}",
            f"  · 主导限制：{CONSTRAINT_ZH.get(r.binding, r.binding) or '—'}",
        ]
        if r.cap_managed and r.cap_physical:
            lines.append(
                f"\n管理值约为物理值的 {r.cap_managed / r.cap_physical * 100:.0f}%，"
                f"差额是管理裕度，不是损耗。")
        return Answer("\n".join(lines), source=self._src(r, "capacity_*"),
                      followups=["为什么是这个值？", "还能接多少？", "复算通过了吗？"])

    # --- 剩余空间
    def _a_headroom(self, it: Intent, r: RunFacts) -> Answer:
        if not r.ok:
            return Answer(f"{r.run_id} 没有容量结论。", source=self._src(r, "无"))
        lo = r.summary.get("feasible_lower_kw")
        hi = r.summary.get("infeasible_upper_kw")
        notes = r.summary.get("search_notes") or []
        lines = [f"**{r.run_id}** 的剩余接入空间：",
                 f"  · 已验证可行：**{_fmt(lo)} kW**（这个值一定可行）",
                 f"  · 已验证不可行：{_fmt(hi)} kW（到这个值就过不了了）"]
        if lo is not None and hi is not None:
            lines.append(f"  · 搜索区间：{_fmt(lo)} ~ {_fmt(hi)} kW，"
                         f"宽度 {_fmt(float(hi) - float(lo), 3)} kW")
        if notes:
            lines.append("\n" + "\n".join(f"  {n}" for n in notes))
        return Answer("\n".join(lines), source=self._src(r, "搜索区间"),
                      followups=["能接 500 kW 吗？", "为什么卡在这里？"])

    # --- 能不能接某个量
    def _a_can_absorb(self, it: Intent, r: RunFacts) -> Answer:
        if it.number is None:
            return self._a_capacity(it, r)
        want = it.number
        cap = r.cap_physical
        if cap is None:
            return Answer(f"{r.run_id} 没有容量结论，无法判断。",
                          source=self._src(r, "无"))
        verdict = "**可以**" if want <= cap else "**不可以**"
        margin = cap - want
        lines = [
            f"接入 **{_fmt(want)} kW**：{verdict}",
            f"  · {r.run_id} 的物理可开放容量是 **{_fmt(cap)} kW**",
            f"  · {'余量 ' + _fmt(margin) + ' kW' if margin >= 0 else '超出 ' + _fmt(-margin) + ' kW'}",
        ]
        if want > cap:
            lines.append(
                f"\n注意：这是**物理上限**。管理可开放容量只有 "
                f"{_fmt(r.cap_managed)} kW，若按可发布口径判断，"
                f"门槛更低。")
        return Answer("\n".join(lines), source=self._src(r, "capacity_physical_kw"),
                      followups=["为什么不能再多点？", "管理容量是多少？"])

    # --- 为什么
    def _a_why(self, it: Intent, r: RunFacts) -> Answer:
        if not r.ok:
            return Answer(f"{r.run_id} 没有结论。", source=self._src(r, "无"))
        cs = r.summary.get("constraint_summary") or {}
        bd = r.summary.get("binding_detail") or {}
        bc = cs.get("binding_constraints") or []
        casc = CONSTRAINT_ZH.get(r.binding, r.binding)

        lines = [f"**{r.run_id}** 卡在 {casc}（{r.binding or '—'}）。"]
        if bd:
            obj = bd.get("object_id", "")
            metric = bd.get("metric", "")
            val = bd.get("value")
            lo, hi = bd.get("limit_lower"), bd.get("limit_upper")
            unit = bd.get("unit", "")
            lines.append(f"  · 卡在对象：`{obj}` 的 {metric}")
            lines.append(f"  · 实测值：**{_fmt(val, 6)} {unit}**")
            if lo is not None or hi is not None:
                lines.append(f"  · 限值区间：[{_fmt(lo)}, {_fmt(hi)}] {unit}")
            m = bd.get("margin")
            if m is not None:
                lines.append(f"  · 余量：{_fmt(m, 6)} {unit}"
                             f"（负值表示已越限）")
        if r.cap_physical is not None and r.cap_physical == 0:
            lines.append(
                "\n容量是 **0 kW**：说明**不加任何新增**的基线状态就已经"
                "越限了，所以连 1 kW 都接不了。要先解决基线问题。")
        if bc:
            lines.append(f"\n共 {len(bc)} 条越限记录，涉及对象："
                         + "、".join(sorted({str(b.get('object_id')) for b in bc})[:5]))
        cs2 = r.summary.get("constraint_summary") or {}
        if cs2.get("unknown"):
            lines.append(
                f"\n另有 {cs2['unknown']} 项约束为 **unknown**（能力未覆盖），"
                f"它既不算通过也不算不可行 —— 这种情况下不会出正式红黄绿结论。")
        return Answer("\n".join(lines), source=self._src(r, "binding_detail"),
                      followups=["哪些约束没通过？", "还能接多少？", "最低电压是多少？"])

    # --- 最低电压
    def _a_min_voltage(self, it: Intent, r: RunFacts) -> Answer:
        best = None
        for row in r.constraint_rows:
            if str(row.get("constraint_id")) != "VOLTAGE_LIMIT":
                continue
            try:
                v = float(row.get("value"))
            except (TypeError, ValueError):
                continue
            if best is None or v < best[0]:
                best = (v, row)
        if best is None:
            return Answer(
                f"{r.run_id} 的产物里没有电压约束明细"
                f"（可能是该算例未请求 powerflow_3ph / imbalance 分析）。",
                source=f"runs/{r.path.name}/results/constraint_results.csv")
        v, row = best
        return Answer(
            f"**{r.run_id}** 的最低电压：**{_fmt(v, 6)} pu**\n"
            f"  · 位置：`{row.get('object_id')}`\n"
            f"  · 限值：[{_fmt(row.get('lower_limit') or row.get('limit_lower'))}, "
f"{_fmt(row.get('upper_limit') or row.get('limit_upper'))}] pu\n"
            f"  · 场景/时刻：{row.get('scenario_id')} @ "
            f"{str(row.get('timestamp'))[:19]}",
            source=f"runs/{r.path.name}/results/constraint_results.csv",
            followups=["还有哪些越限？", "能接多少？"])

    # --- 载流
    def _a_loading(self, it: Intent, r: RunFacts) -> Answer:
        rows = [x for x in r.constraint_rows
                if str(x.get("constraint_id")) == "THERMAL_LIMIT"]
        if not rows:
            return Answer(f"{r.run_id} 没有载流约束记录。",
                          source=f"runs/{r.path.name}/results/constraint_results.csv")
        rows.sort(key=lambda x: -float(x.get("value") or 0))
        top = rows[0]
        return Answer(
            f"**{r.run_id}** 的最大负载率：**{_fmt(top.get('value'))}%**\n"
            f"  · 设备：`{top.get('object_id')}`\n"
            f"  · 限值：{_fmt(top.get('upper_limit') or top.get('limit_upper'))}%\n"
            f"  · 是否越限：{'是' if float(top.get('value') or 0) > float(top.get('upper_limit') or top.get('limit_upper') or 1e9) else '否'}",
            source=f"runs/{r.path.name}/results/constraint_results.csv",
            followups=["哪些约束没通过？", "为什么卡在这里？"])

    # --- 越限
    def _a_violations(self, it: Intent, r: RunFacts) -> Answer:
        cs = r.summary.get("constraint_summary") or {}
        bc = cs.get("binding_constraints") or []
        lines = [f"**{r.run_id}** 约束校验：",
                 f"  · 共 {cs.get('total', '—')} 项，通过 {cs.get('pass', '—')}，"
                 f"不通过 **{cs.get('fail', '—')}**，未覆盖 {cs.get('unknown', '—')}"]
        if bc:
            agg: Dict[str, List] = {}
            for b in bc:
                agg.setdefault(str(b.get("constraint_id")), []).append(b)
            lines.append("\n不通过的约束：")
            for cid, items in agg.items():
                objs = sorted({str(i.get("object_id")) for i in items})
                worst = min(items, key=lambda i: float(i.get("margin") or 0))
                lines.append(
                    f"  · **{CONSTRAINT_ZH.get(cid, cid)}**（{cid}）："
                    f"{len(items)} 条，涉及 {len(objs)} 个对象"
                    f"（如 `{objs[0]}`）；最差余量 "
                    f"{_fmt(worst.get('margin'), 4)}")
        else:
            lines.append("\n**没有不通过的约束。**")
        return Answer("\n".join(lines), source=self._src(r, "constraint_summary"),
                      followups=["为什么卡在这里？", "能接多少？"])

    # --- 等级
    def _a_level(self, it: Intent, r: RunFacts) -> Answer:
        zh = {"green": "绿色", "yellow": "黄色", "red": "红色",
              "grey": "灰色（内部态，不出正式结论）"}
        return Answer(
            f"**{r.run_id}** 的红黄绿等级：**{zh.get(r.level, r.level or '—')}**\n"
            f"  · 判定理由：{r.summary.get('level_reason') or '—'}",
            source=self._src(r, "level / level_reason"),
            followups=["为什么是这个等级？", "能接多少？"])

    # --- 复算
    def _a_verification(self, it: Intent, r: RunFacts) -> Answer:
        v = r.verification
        if not v:
            return Answer(f"{r.run_id} 没有独立复算产物。",
                          source=f"runs/{r.path.name}/verification/")
        cmp_ = v.get("comparison") or {}
        ok = v.get("pass")
        return Answer(
            f"**{r.run_id}** 的独立复算：{'**通过**' if ok else '**未通过**'}\n"
            f"  · 输入是否一致：{'是' if v.get('input_match') else '否'}\n"
            f"  · 差异项数：{cmp_.get('mismatch_count', '—')}\n"
            f"  · 复算运行号：{v.get('verify_run_id', '—')}\n"
            f"\n复算由**另一条引擎通道**重跑并逐字段比对，用来发现单一通道的"
            f"系统性错误。",
            source=f"runs/{r.path.name}/verification/verification.json",
            followups=["发布门禁过了吗？", "能接多少？"])

    # --- 门禁
    def _a_gate(self, it: Intent, r: RunFacts) -> Answer:
        g = r.gate
        if not g:
            return Answer(f"{r.run_id} 没有发布门禁产物。",
                          source=f"runs/{r.path.name}/review/")
        gates = g.get("gates") or []
        zh = {"independent_verification": "独立复算",
              "professional_review": "专业复核", "approval": "审批"}
        lines = [f"**{r.run_id}** 的发布门禁："
                 f"{'**已通过**' if g.get('passed') else '**未通过**'}"]
        for x in gates:
            lines.append(f"  · {zh.get(x.get('gate'), x.get('gate'))}："
                         f"{'通过' if x.get('passed') else '未满足'}")
        if g.get("failed_gates"):
            lines.append(
                "\n未满足的门禁："
                + "、".join(zh.get(x, x) for x in g["failed_gates"])
                + "。\n按设计，三道门禁任一道未满足**即不得发布** —— "
                  "所以结果只能作为候选，不能对外发布。")
        return Answer("\n".join(lines),
                      source=f"runs/{r.path.name}/review/release_gate.json",
                      followups=["复算通过了吗？", "能接多少？"])

    # --- 场景
    def _a_scenarios(self, it: Intent, r: RunFacts) -> Answer:
        s = r.summary
        ids = s.get("scenario_ids") or []
        cls = (s.get("caliber") or {}).get("items") or []
        note = next((x.get("note") for x in cls
                     if x.get("key") == "scenario_coverage"), "")
        lines = [f"**{r.run_id}** 覆盖了 {s.get('scenario_count', '—')} 个场景 / "
                 f"{s.get('slice_count', '—')} 个时间片。"]
        if ids:
            lines.append("  · 场景 ID：" + "、".join(map(str, ids)))
        if note:
            lines.append(f"  · 场景类别：{note}")
        lines.append(f"  · 时间窗：{s.get('time_window_start', '—')} 至 "
                     f"{s.get('time_window_end', '—')}")
        return Answer("\n".join(lines), source=self._src(r, "scenario_count"),
                      followups=["复算通过了吗？", "能接多少？"])

    # --- 规则
    def _a_rules(self, it: Intent, r: RunFacts) -> Answer:
        rp = r.summary.get("rule_package") or "—"
        bd = r.summary.get("binding_detail") or {}
        lines = [f"阈值来自规则包 **{rp}** —— Agent 不内置任何未经确认的常数。"]
        if bd:
            lines.append(f"  · 本次生效的电压限值："
                         f"[{_fmt(bd.get('limit_lower'))}, "
                         f"{_fmt(bd.get('limit_upper'))}] pu")
        lines.append(
            "\n规则里为 `null` 的限值表示**未批准**，必须阻断而不是猜测 —— "
            "这是本工程的一条硬规矩。")
        return Answer("\n".join(lines), source=self._src(r, "rule_package"),
                      followups=["有哪些约束？", "为什么卡在这里？"])

    # --- 局限
    def _a_limits(self, it: Intent, r: RunFacts) -> Answer:
        cal = r.summary.get("caliber") or {}
        uns = cal.get("unsupported") or []
        lines = ["本版**做不到**或**没覆盖**的："]
        lines += [f"  · {u}" for u in uns] or ["  · （产物中未记录）"]
        lines.append(
            "\n另外，界面显示「未获取」的地方就是真的取不到，"
            "不会用 0 或估算值填充。")
        return Answer("\n".join(lines), source=self._src(r, "caliber.unsupported"),
                      followups=["能接多少？", "复算通过了吗？"])

    # --- 口径
    def _a_caliber(self, it: Intent, r: RunFacts) -> Answer:
        cal = r.summary.get("caliber") or {}
        items = cal.get("items") or []
        if not items:
            return Answer(f"{r.run_id} 没有口径声明。", source=self._src(r, "caliber"))
        keys = ("phase_model", "load_model", "generation_caliber",
                "storage_caliber", "units")
        lines = [f"**{r.run_id}** 的求解口径："]
        for k in keys:
            it0 = next((x for x in items if x.get("key") == k), None)
            if it0:
                lines.append(f"  · {it0.get('label')}：{it0.get('value')}")
        if cal.get("phase_statement"):
            lines.append(f"\n{str(cal['phase_statement']).replace('**', '')}")
        return Answer("\n".join(lines), source=self._src(r, "caliber.items"),
                      followups=["有什么没覆盖？", "能接多少？"])

    # --- 列运行
    def _a_list_runs(self, it: Intent, r) -> Answer:
        if not self.facts.runs:
            return self._no_run()
        lines = ["目前读到的运行："]
        for x in self.facts.runs[:12]:
            cap = (_fmt(x.cap_physical) + " kW") if x.cap_physical is not None else "无结论"
            mark = " ← 当前" if x is self.facts.current else ""
            lines.append(f"  · **{x.run_id}**：{cap}，等级 {x.level or '—'}{mark}")
        return Answer("\n".join(lines), source="runs/", followups=SUGGESTIONS[:3])


# ---------------------------------------------------------------- 对外
def ask(question: str, facts: Optional[Facts] = None) -> Dict[str, Any]:
    """回答一个问题，返回结构化结果（供界面与 CLI 共用）。"""
    f = facts or Facts()
    if facts is None:
        pass
    ans = Responder(f).answer(question)
    return ans.to_dict()


def ask_with_facts(question: str, facts: Facts) -> Dict[str, Any]:
    return Responder(facts).answer(question).to_dict()
