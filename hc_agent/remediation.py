"""remediation.py — 整改建议候选（新应用场景）。

## 定位

承载力结论常是"不能再接了"。用户真正想要的下一句话是
**"那我能做什么？"**

本模块就是回答这个。**产出的是"候选方案"，不是"结论"**：

| 是 | 不是 |
|---|---|
| 基于实测约束的候选方向 | 经过专业复核的方案 |
| 每条都带"依据哪个实测值" | 拍脑袋的经验值 |
| 标明"本版算不出效果" | 承诺能提升多少 kW |

设计上沿用两件事：
1. 候选**必须能追到约束**（哪条越限、越多少），否则不算候选；
2. 需要「候选生成」权限（`propose_remediation`），不是随便就能出。

## 不做什么

- **不**给出改造后的承载力数字（需要重新建模 + 容量搜索，本版不做）；
- **不**做经济性比较（见知识库《经济性与投资评估》）；
- **不**做设备选型（那是设计院的事）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: 候选方向库。每条都写清楚"依据什么实测信号触发"。
#: `trigger` 是判据函数，输入一个上下文 dict。
CANDIDATES: List[Dict[str, Any]] = [
    {
        "id": "RM-V-01",
        "title": "缩短低压供电半径 / 加大低压线截面",
        "constraint": "VOLTAGE_LIMIT",
        "why": "低压线路电阻是光伏顶高电压的主要成因；本方案直击线路阻抗。",
        "effect": "通常最有效，但需要改造投资与停电作业。",
        "limits": "若越限发生在中压侧，改低压线无效。",
        "basis": "知识库《配电网规划与供电半径》《电压调节与无功补偿》",
    },
    {
        "id": "RM-V-02",
        "title": "调整配变分接头（降一档）",
        "constraint": "VOLTAGE_LIMIT",
        "why": "整体平移低压侧电压，可快速缓解末端过压。",
        "effect": "不改线路、成本低、可立即实施（无载调压需短时停电）。",
        "limits": "只平移不改分布；若越限仅出现在少数末端且首端已接近下限，"
                  "平移可能把首端压低到下限以下。",
        "basis": "知识库《电压调节与无功补偿》",
    },
    {
        "id": "RM-V-03",
        "title": "让逆变器吸收无功（提高功率因数运行）",
        "constraint": "VOLTAGE_LIMIT",
        "why": "逆变器可通过无功吸收压低电压，无需新增设备。",
        "effect": "零投资、可立即实施。",
        "limits": "无功能力上限约 33% 装机（pf=0.95 时 Q≈0.329P），"
                  "对长低压线的抬升往往抵不掉；且牺牲有功出力。",
        "basis": "知识库《电压调节与无功补偿》",
    },
    {
        "id": "RM-C-01",
        "title": "配置储能吸收反向潮流",
        "constraint": "REVERSE_FLOW_LIMIT",
        "why": "光伏大发时段充电，削减净倒送功率。",
        "effect": "同时缓解电压上限与反向潮流两条约束。",
        "limits": "功率与时长要匹配越限时段；短时储能挡不住长时大发；"
                  "充电工况可能让别处载流越限。本版不给储能容量推荐值。",
        "basis": "知识库《储能与灵活性资源》",
    },
    {
        "id": "RM-T-01",
        "title": "限制接入容量 / 分批接入",
        "constraint": "THERMAL_LIMIT",
        "why": "在设备载流能力内分批接入，是唯一不需要改造的手段。",
        "effect": "零投资，可立即执行。",
        "limits": "直接减少可接入量，是「保守兜底」而不是「提升」。",
        "basis": "知识库《承载力基本概念》",
    },
    {
        "id": "RM-T-02",
        "title": "配变增容或负荷分流",
        "constraint": "THERMAL_LIMIT",
        "why": "过载发生在配变时，增容或把部分负荷分到相邻台区。",
        "effect": "直接解除载流约束。",
        "limits": "需停电施工；分流方案要看相邻台区是否有余量。",
        "basis": "知识库《台区与低压网拓扑》",
    },
    {
        "id": "RM-B-01",
        "title": "调整单相接入相别，平衡三相",
        "constraint": "IMBALANCE_LIMIT",
        "why": "单相光伏集中在一相会显著加剧不平衡。",
        "effect": "改动小、成本低。",
        "limits": "需要现场作业；效果取决于负荷本身的相别分布。",
        "basis": "知识库《台区与低压网拓扑》",
    },
    {
        "id": "RM-S-01",
        "title": "核查短路电流余量后再定接法",
        "constraint": "SHORT_CIRCUIT_LIMIT",
        "why": "多点接入叠加会抬升短路电流，可能超开关开断能力。",
        "effect": "明确可行边界，避免盲目接入。",
        "limits": "本版不做完整短路计算，只能提示需要专项校核。",
        "basis": "知识库《短路电流与设备校验》",
    },
]


def _matched(constraint: str, detail: Dict[str, Any]) -> bool:
    """判断某条约束是否真的触发（只按实测，不猜）。"""
    c = str(constraint or "").upper()
    if c == "VOLTAGE_LIMIT":
        return bool(detail.get("voltage_violations")
                    or (detail.get("voltage_margin") is not None
                        and detail["voltage_margin"] < 0))
    if c == "THERMAL_LIMIT":
        return bool(detail.get("overloaded")
                    or (detail.get("loading_margin") is not None
                        and detail["loading_margin"] < 0))
    if c == "REVERSE_FLOW_LIMIT":
        return bool(detail.get("reverse_flow_exceeded"))
    if c == "IMBALANCE_LIMIT":
        return bool(detail.get("imbalance_exceeded"))
    if c == "SHORT_CIRCUIT_LIMIT":
        return bool(detail.get("short_circuit_exceeded"))
    return False


def propose(*, binding_constraint: str = "",
            voltage_violations: Optional[List[Any]] = None,
            overloaded: Optional[List[Any]] = None,
            voltage_margin: Optional[float] = None,
            loading_margin: Optional[float] = None,
            reverse_flow_exceeded: bool = False,
            imbalance_exceeded: bool = False,
            short_circuit_exceeded: bool = False,
            extra: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """根据实测约束给出候选整改方向。

    **只为"真正触发了"的约束给候选**：没越限的约束不给建议，
    否则会列一堆无关方案，用户反而看不出重点。
    """
    detail = {
        "voltage_violations": voltage_violations or [],
        "overloaded": overloaded or [],
        "voltage_margin": voltage_margin,
        "loading_margin": loading_margin,
        "reverse_flow_exceeded": reverse_flow_exceeded,
        "imbalance_exceeded": imbalance_exceeded,
        "short_circuit_exceeded": short_circuit_exceeded,
    }
    hits: List[Dict[str, Any]] = []
    for cand in CANDIDATES:
        if _matched(cand["constraint"], detail):
            hits.append(dict(cand))
    # 若给了绑定约束但没匹配上（例如约束名不在库里），至少给一条兜底
    bc = str(binding_constraint or "").upper()
    if not hits and bc:
        hits.append({
            "id": "RM-GEN-01",
            "title": f"针对 {bc} 做专项分析",
            "constraint": bc,
            "why": "该约束被判定为绑定约束，但没有匹配到预置候选。",
            "effect": "需要专业判断。",
            "limits": "本版无对应候选库条目，不建议凭猜测给方案。",
            "basis": "—",
        })
    if extra:
        hits.extend(extra)

    return {
        "ok": True,
        "binding_constraint": bc,
        "count": len(hits),
        "candidates": hits,
        "disclaimer": (
            "以上为**候选方向**，不是整改结论：本版不计算改造后的承载力，"
            "也不做经济性比较。实际方案需专业设计与复核，"
            "并完成独立复算与人工审批。"),
    }


def from_run(run_dir) -> Dict[str, Any]:
    """从一次运行的产物里读取约束状态并给候选。"""
    import json
    from pathlib import Path

    root = Path(str(run_dir))
    summary: Dict[str, Any] = {}
    for rel in ("results/run_summary.json", "run_outcome.json"):
        f = root / rel
        if f.is_file():
            try:
                summary = json.loads(f.read_text(encoding="utf-8"))
                if summary:
                    break
            except Exception:
                continue
    if not summary:
        return {"ok": False, "error": "读不到该运行的结论"}, 0

    bd = summary.get("binding_detail") or {}
    vv = summary.get("voltage_violations") or []
    ov = summary.get("overloaded") or []
    vmargin = None
    if bd.get("metric") == "voltage_pu" and bd.get("margin") is not None:
        vmargin = float(bd["margin"])
    return propose(
        binding_constraint=str(summary.get("binding_constraint") or ""),
        voltage_violations=vv,
        overloaded=ov,
        voltage_margin=vmargin,
        loading_margin=None,
        reverse_flow_exceeded=(str(summary.get("binding_constraint") or "")
                              .upper() == "REVERSE_FLOW_LIMIT"),
        imbalance_exceeded=(str(summary.get("binding_constraint") or "")
                            .upper() == "IMBALANCE_LIMIT"),
        short_circuit_exceeded=(str(summary.get("binding_constraint") or "")
                                .upper() == "SHORT_CIRCUIT_LIMIT"),
    ), 1


def format_proposals(res: Dict[str, Any]) -> str:
    """排成可读文本（问答直接用）。"""
    if not res.get("ok"):
        return f"没能给出候选：{res.get('error') or '未知原因'}"
    lines = [f"**绑定约束**：{res.get('binding_constraint') or '未标注'}",
             f"共 {res.get('count')} 个候选方向：", ""]
    for i, c in enumerate(res.get("candidates") or [], 1):
        lines.append(f"{i}. **{c['title']}**（{c['id']}）")
        lines.append(f"   · 为什么：{c['why']}")
        lines.append(f"   · 效果：{c['effect']}")
        lines.append(f"   · 局限：{c['limits']}")
        lines.append(f"   · 依据：{c['basis']}")
    lines.append("")
    lines.append(res.get("disclaimer") or "")
    return "\n".join(lines)
