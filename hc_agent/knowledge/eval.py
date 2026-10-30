"""eval.py — 从知识库文档生成 RAG 评估数据集。

对齐 ai-localbase 的「可从现有知识库文档生成 RAG 评估数据集，用于小样本效果验证」。

## 为什么需要它

改了切分参数、换了检索权重、调了 MMR 的 lambda —— 这些改动到底有没有让
检索变好？凭几个例子肉眼看会自欺。得有一批**带标准答案的题目**反复跑。

## 题目怎么来（不需要人工标注）

从已入库的块反向构造：

1. 取一个有标题的块
2. 用它的**标题路径 + 关键短语**造一个问句（如"电压约束的判据是什么"）
3. **标准答案就是这个块本身** —— 检索若把它排到 top-1 即算命中

这种"已知出处反查"是最小可用的检索评估方式，业界常用。

## 指标

* `hit@1` / `hit@3` / `hit@5`：正确块出现在前 K 名的比例
* `mrr`：平均倒数排名（1/rank 的均值），比 hit@K 更细
* `avg_score`：top-1 的平均分，用于观察分数分布是否合理

## 诚实边界

这些题目是从文档自身生成的，只验证**检索**是否找得到出处，
**不验证**回答是否正确（那需要人工判分）。报告里必须写明这一点，
不能把 hit@5 = 1.0 说成"问答准确率 100%"。
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from hc_agent.knowledge.index import key_phrases

#: 问句模板：围绕"这一段在讲什么"
_Q_TEMPLATES = (
    "{topic}是什么",
    "{topic}的判据是什么",
    "{topic}怎么算",
    "{topic}有哪些要求",
    "关于{topic}，结论是什么",
)


def _clean_title(s: str) -> str:
    """去掉编号、代码、加粗符号，只留人能读的主题词。"""
    t = re.sub(r"[*`#]", "", str(s or ""))
    t = re.sub(r"^[\d\.\s、]+", "", t)
    # 去掉英文代号（VOLTAGE_LIMIT 之类），保留中文主题
    t = re.sub(r"\b[A-Z][A-Z0-9_]{3,}\b", "", t)
    t = re.sub(r"\s+", " ", t).strip(" :：-—")
    return t


def _topic_of(chunk: Dict[str, Any]) -> str:
    """从块的标题路径里提炼一个可用于提问的主题词。"""
    crumb = chunk.get("breadcrumb") or chunk.get("heading") or ""
    if crumb:
        last = crumb.split("/")[-1]
        t = _clean_title(last)
        if len(t) >= 2:
            return t
    t = _clean_title(chunk.get("heading") or "")
    if len(t) >= 2:
        return t
    # 退化为正文里的第一个领域短语
    kp = key_phrases(chunk.get("text") or "")
    return kp[0] if kp else ""


def generate_eval(service, kb_id: str, *, max_items: int = 30,
                  seed: int = 20261017) -> Dict[str, Any]:
    """生成并保存评估集。返回 {'ok', 'count', 'path', 'items'}。"""
    chunks = service.store.list_chunks(kb_id)
    if not chunks:
        return {"ok": False, "error": "该知识库还没有已索引的内容",
                "count": 0, "items": []}

    rng = random.Random(seed)
    # 优先带有标题路径的块（问句更自然）
    cand = [c for c in chunks if (c.get("breadcrumb") or c.get("heading"))]
    if len(cand) < max_items:
        cand += [c for c in chunks if c not in cand]
    rng.shuffle(cand)

    items: List[Dict[str, Any]] = []
    seen_q: set = set()
    for c in cand:
        if len(items) >= max_items:
            break
        topic = _topic_of(c)
        if len(topic) < 2:
            continue
        tpl = _Q_TEMPLATES[len(items) % len(_Q_TEMPLATES)]
        q = tpl.format(topic=topic)
        if q in seen_q:
            continue
        seen_q.add(q)
        items.append({
            "question": q,
            "expected_chunk_id": c["id"],
            "expected_doc_id": c["doc_id"],
            "topic": topic,
            "source": c.get("breadcrumb") or c.get("heading") or "",
            # 只留前 200 字做人工核对用，避免文件过大
            "reference": (c.get("text") or "")[:200],
        })

    if not items:
        return {"ok": False, "error": "无法从现有内容构造出评估题",
                "count": 0, "items": []}

    out_dir = service.store.path.parent
    path = out_dir / f"eval-{kb_id}.json"
    payload = {
        "kb_id": kb_id,
        "generated_at": __import__("datetime").datetime.now().isoformat(
            timespec="seconds"),
        "note": ("题目由文档自身反推生成，只验证**检索能否找到出处**，"
                 "不验证回答是否正确。hit@K=1 不等于问答准确率 100%。"),
        "count": len(items),
        "items": items,
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"写入失败：{exc}", "count": len(items),
                "items": items}
    return {"ok": True, "count": len(items), "path": str(path),
            "items": items, "note": payload["note"]}


def load_eval(service, kb_id: str) -> Dict[str, Any]:
    """读取已落盘的评估集（不存在返回 ok=False）。"""
    path = service.store.path.parent / f"eval-{kb_id}.json"
    if not path.is_file():
        return {"ok": False, "error": "还没有评估集", "count": 0, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"读不出来：{exc}", "count": 0,
                "items": []}
    items = data.get("items") or []
    if not items:
        return {"ok": False, "error": "评估集为空", "count": 0, "items": []}
    return {"ok": True, "count": len(items), "items": items,
            "path": str(path), "generated_at": data.get("generated_at", "")}


def run_eval(service, kb_id: str, *, max_items: int = 30,
             ks: tuple = (1, 3, 5), items=None,
             stable: bool = True, regenerate: bool = False) -> Dict[str, Any]:
    """跑一轮检索评估。

    题目来源优先级：
      1. 显式传入的 `items`（测试/回归用，完全可控）
      2. 已落盘的评估集（`stable=True` 时的默认）—— 保证指标可复现
      3. 新抽样生成（首次运行，或 `regenerate=True`）

    **为什么默认复用**：每次重抽会让 hit@K 随"抽到哪些题"抖动，
    文档一多就从 0.9 掉到 0.5，看着像检索坏了，其实是题目换了。
    """
    if items:
        # 显式给题目时也要**落盘**：评估结果必须能留档、能被别人复核，
        # 不能只在内存里算完就丢（踩过：传 items 后 path 为空，
        # 下游按 path 读文件直接 PermissionError）。
        _items = list(items)
        try:
            _p = service.store.path.parent / f"eval-{kb_id}.json"
            _p.write_text(json.dumps({
                "kb_id": kb_id,
                "generated_at": __import__("datetime").datetime.now()
                    .isoformat(timespec="seconds"),
                "note": ("题目由调用方给定（回归/基线场景）。"
                         "只验证**检索能否找到出处**，不验证回答是否正确；"
                         "hit@K=1 不等于问答准确率 100%。"),
                "count": len(_items),
                "items": _items,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            gen = {"ok": True, "items": _items, "count": len(_items),
                   "path": str(_p), "source": "explicit"}
        except Exception:
            gen = {"ok": True, "items": _items, "count": len(_items),
                   "path": "", "source": "explicit"}
    elif stable and not regenerate:
        gen = load_eval(service, kb_id)
        if not gen.get("ok"):
            gen = generate_eval(service, kb_id, max_items=max_items)
            gen["source"] = "generated"
        else:
            gen["source"] = "stored"
    else:
        gen = generate_eval(service, kb_id, max_items=max_items)
        gen["source"] = "generated"
    if not gen.get("ok"):
        return gen
    items = gen["items"]
    ranks: List[int] = []
    top1_scores: List[float] = []
    misses: List[Dict[str, Any]] = []
    for it in items:
        hits = service.search(it["question"], kb_id=kb_id,
                              top_k=max(ks))
        rank = 0
        for i, h in enumerate(hits, start=1):
            if h.chunk_id == it["expected_chunk_id"]:
                rank = i
                break
        ranks.append(rank)
        if hits:
            top1_scores.append(hits[0].score)
        if rank == 0 or rank > 1:
            misses.append({
                "question": it["question"],
                # 题目可能来自调用方（回归集），不一定带 source 字段；
                # 缺失时用 topic 兜底，不能让评估因为缺一个展示字段就崩。
                "expected": it.get("source") or it.get("topic") or "",
                "got": hits[0].breadcrumb if hits else "(无结果)",
                "rank": rank,
            })
    n = len(items) or 1
    metrics: Dict[str, Any] = {
        "count": len(items),
        "items_source": gen.get("source", ""),
        # 显式题目也是落盘的，路径要带出去，否则调用方拿不到留档位置
        "eval_path": gen.get("path", ""),
        # 口径声明必须**随结果一起返回**，不能只写在落盘文件里：
        # 调用方（界面/问答）要能直接告诉用户这是检索指标，不是回答准确率。
        "note": ("题目由文档自身反推生成，只验证检索能否找到出处，"
                 "不验证回答是否正确；hit@K=1 不等于问答准确率 100%。"),
        "hit@1": round(sum(1 for r in ranks if r == 1) / n, 4),
        **{f"hit@{k}": round(sum(1 for r in ranks if 0 < r <= k) / n, 4)
           for k in ks},
        "mrr": round(sum((1.0 / r) if r else 0.0 for r in ranks) / n, 4),
        "avg_top1_score": (round(sum(top1_scores) / len(top1_scores), 4)
                           if top1_scores else 0.0),
        "eval_path": gen.get("path", ""),
        # note 兜底成固定文案：gen 可能是"显式传入题目"分支，那里没有 note，
        # 直接取空串会让调用方拿不到口径声明（踩过：界面上什么都不显示）。
        "note": (gen.get("note") or
                 "只验证检索能否找到出处，不验证回答是否正确；"
                 "hit@K=1 不等于问答准确率 100%。"),
        # 给界面直接展示用的样例（含标准答案与命中排名）
        "samples": [{"question": it["question"],
                     "expected": it.get("source") or it.get("topic") or "",
                     "rank": ranks[i] if i < len(ranks) else 0}
                    for i, it in enumerate(items[:8])],
        "misses": misses[:10],
    }
    # 落盘指标，便于前后对比
    try:
        p = Path(gen["path"])
        p.with_suffix(".metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        pass
    return {"ok": True, **metrics}
