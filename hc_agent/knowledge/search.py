"""search.py — 混合检索：关键词 + 向量 → 重排 → MMR → 二次扩召回。

对齐 ai-localbase 的检索流水线，但全部在本地、零外部服务：

    ① 动态召回     候选池 = 关键词 TopN ∪ 向量 TopM
    ② 融合         hybrid score = w_kw × 关键词分 + w_vec × 向量分
    ③ 关键词覆盖重排 命中查询词种类越多越靠前（避免"只撞上一个词"的高分噪声）
    ④ 低置信度扩召回 top1 分数过低时，用领域同义词扩展查询再召回一次
    ⑤ MMR 去冗余   抑制"同一段话被切成多块"占满结果

## 为什么需要第 ③ 步

只按 TF-IDF/BM25 排序时，一个只重复出现"电压"这一词的段落，
可能因为词频高而压过真正解释了"电压越限判据"的段落。
关键词**覆盖率**（查询里有几类词被命中）能有效纠正这一点。

## 为什么需要第 ⑤ 步

切块带重叠，同一段内容会有 2~3 个高度相似的块。
不去冗余的话，Top5 可能全是同一段的重复，用户看不到别的角度。
MMR 用"与已选结果的相似度"做惩罚，保证结果多样。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from hc_agent.knowledge.index import (VectorIndex, key_phrases,
                                      significant_terms, tokenize)

#: 领域同义词：低置信度时用它扩展查询。
#: 只收本域真实会混用的说法，避免引入噪声。
SYNONYMS = {
    "倒送": ["反向", "反向潮流"],
    "反向": ["倒送", "反向潮流"],
    "渗透率": ["接入比例", "容量占比"],
    "台区": ["配变", "配电站"],
    "配变": ["台区", "变压器"],
    "限值": ["阈值", "判据", "上限"],
    "阈值": ["限值", "判据"],
    "越限": ["超标", "不通过", "违规"],
    "承载力": ["开放容量", "可开放容量", "可接入容量"],
    "开放容量": ["承载力", "可开放容量"],
    "复算": ["独立验证", "复核"],
    "门禁": ["发布条件", "审批"],
    "灰色": ["grey", "数据风险"],
    "不平衡": ["负序", "三相不平衡"],
    "载流": ["热稳", "负载率"],
    "负载率": ["载流", "利用率"],
    "短路": ["短路容量", "故障电流"],
    "场景": ["工况", "情形"],
    "工况": ["场景"],
    "光伏": ["分布式电源", "新能源"],
    "容量": ["装机", "可接入"],
}

#: 低置信度阈值：低于它就认为"没搜到像样的东西"，触发二次扩召回
LOW_CONFIDENCE = 0.28

#: 查询里出现这些词，说明用户要的是**判据/数值**（不是能力清单）。
#: 此时优先"真的给出了数值"的段落，而不是"提到了这个词"的段落。
CRITERION_WORDS = ("判据", "限值", "阈值", "上限", "下限", "标准", "依据",
                   "多少", "几", "范围", "精度", "容差", "要求")

#: 查询里出现这些词，说明用户要的是**文献出处/综述**，
#: 应优先文献索引类章节，而不是恰好提到文献二字的正文段落。
LITERATURE_WORDS = ("文献", "参考", "引用", "综述", "出处", "哪篇",
                    "有哪些研究")

#: 查询里出现这些词，说明用户要的是**定义**。
DEFINITION_WORDS = ("什么是", "是什么", "含义", "定义", "概念", "指的是",
                    "什么意思", "怎么理解")

#: 判据类数值的形态：区间、比较符、阈值句。
_NUMERIC_LIMIT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:~|～|-|—|至|到)\s*\d+(?:\.\d+)?"
    r"|(?:<=|>=|≤|≥|<|>)\s*\d+(?:\.\d+)?"
    r"|between\s+\d"
    r"|0\.\d+\s*(?:~|～|至)\s*1\.\d+", re.I
)


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    text: str
    breadcrumb: str
    score: float
    kw_score: float = 0.0
    vec_score: float = 0.0
    coverage: float = 0.0
    matched: Tuple[str, ...] = ()
    expanded: bool = False

    def to_dict(self) -> Dict:
        return {"chunk_id": self.chunk_id, "doc_id": self.doc_id,
                "breadcrumb": self.breadcrumb, "score": round(self.score, 4),
                "kw_score": round(self.kw_score, 4),
                "vec_score": round(self.vec_score, 4),
                "coverage": round(self.coverage, 3),
                "matched": list(self.matched),
                "text": self.text}


def _kw_scores(query_tokens: Sequence[str],
               chunk_tokens: Sequence[Sequence[str]],
               lengths: Sequence[int], avg_len: float,
               k1: float = 1.2, b: float = 0.75) -> np.ndarray:
    """BM25 关键词打分，归一化为「命中了多少查询信息量」。

    ## 为什么不用「除以当次最高分」归一

    按最高分归一有个坏毛病：**只命中一个泛词的段落也能拿 1.0**。
    实测例子：问「GBD 和 ADMM 有什么区别」，某段只出现"区别"二字就拿到
    kw=1.0 排到第一，而真正同时含 GBD 与 ADMM 的段落只有 0.45。
    最高分是谁取决于当次候选，量纲不稳定。

    改成：原始 BM25 分 ÷ 查询词 IDF 总量。含义是
    "这个段落覆盖了查询里多少信息量"，是绝对量，与候选集无关。
    """
    n = len(chunk_tokens)
    out = np.zeros(n, dtype=np.float32)
    if n == 0:
        return out
    qset = list(dict.fromkeys(query_tokens))
    if not qset:
        return out
    # 文档频率（只统计查询词）
    df: Dict[str, int] = {}
    for toks in chunk_tokens:
        for t in set(toks):
            if t in qset:
                df[t] = df.get(t, 0) + 1
    if not df:
        return out
    # 查询词 IDF 总量（作为归一化分母）
    idf_of: Dict[str, float] = {
        t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    denom_total = sum(idf_of.values()) or 1.0
    avg = avg_len or 1.0
    for i, toks in enumerate(chunk_tokens):
        L = lengths[i] or 1
        s = 0.0
        tf: Dict[str, int] = {}
        for t in toks:
            if t in idf_of:
                tf[t] = tf.get(t, 0) + 1
        for t, c in tf.items():
            denom = c + k1 * (1 - b + b * L / avg)
            if denom:
                s += idf_of[t] * (c * (k1 + 1)) / denom
        out[i] = s
    # 除以查询 IDF 总量并截断到 [0,1]
    out = np.clip(out / denom_total, 0.0, 1.0)
    return out


class Searcher:
    """在已建好的块与索引上做检索。"""

    def __init__(self, chunks: List, index: VectorIndex,
                 chunk_tokens: List[List[str]]) -> None:
        self.chunks = chunks
        self.index = index
        self.chunk_tokens = chunk_tokens
        # 标题（breadcrumb）词集合：标题命中是**强信号**。
        # 不加权时会出现"问电压判据却先命中 OpenDSS 段落"这种错位 ——
        # 因为那一段正文里"母线电压"出现多次，而真正讲判据的小节
        # 标题虽含"电压约束"，正文里该词反而少（实测踩过）。
        self._head_tokens: List[set] = []
        for c in chunks:
            label = f"{c.breadcrumb} {c.heading}"
            self._head_tokens.append(set(tokenize(label)))

    # ---------------------------------------------------------- 主流程
    def search(self, query: str, *, top_k: int = 5,
               pool: int = 24, mmr_lambda: float = 0.72,
               allow_expand: bool = True) -> List[Hit]:
        q = str(query or "").strip()
        if not q or not self.chunks:
            return []
        hits = self._recall(q, pool=pool)
        if not hits:
            return []
        best = max(h.score for h in hits)
        if allow_expand and best < LOW_CONFIDENCE:
            extra_q = self._expand(q)
            if extra_q != q:
                more = self._recall(extra_q, pool=pool, expanded=True)
                hits = self._merge(hits, more)
        hits.sort(key=lambda h: -h.score)
        return self._mmr(hits, top_k=top_k, lam=mmr_lambda)

    def _doc_title_low(self, i: int) -> str:
        """块的所在文档标题（小写）。用于"问文献"这类意图的加权。"""
        try:
            return (getattr(self.chunks[i], "doc_title", "") or "").lower()
        except Exception:
            return ""

    # ---------------------------------------------------------- 召回
    def _recall(self, query: str, *, pool: int = 24,
                expanded: bool = False) -> List[Hit]:
        qt = tokenize(query)
        if not qt:
            return []
        kw = _kw_scores(qt, self.chunk_tokens, self.index.lengths,
                        self.index.avg_len)
        vec = self.index.scores(qt)
        if vec.shape[0] != kw.shape[0]:
            # 索引与块不同步（缓存过期），退化为纯关键词
            vec = np.zeros_like(kw)
        qset = [t for t in dict.fromkeys(qt)]
        # 两套词表，用途不同：
        #   qsig  —— 严匹配（领域短语），用于**正文覆盖率**：判断这一段是否在讲同一件事
        #   qhead —— 松匹配（2-gram），用于**标题命中**：标题用词常与问法不一致
        #            （问"电压越限"，小节标题写的是"电压约束"，严匹配会整块漏掉）
        qsig = key_phrases(query)
        qhead = significant_terms(qset)
        qlow = query.lower()
        wants_criterion = any(w in qlow for w in CRITERION_WORDS)
        wants_definition = any(w in qlow for w in DEFINITION_WORDS)
        wants_literature = any(w in qlow for w in LITERATURE_WORDS)

        # 按 IDF 给短语加权：稀有词（OpenDSS、IEEE13）比泛词（承载力、约束）
        # 更能定位到具体小节。不加权时"OpenDSS 能算承载力吗"会被只讲承载力的
        # 通用章节抢走（实测踩过）。
        def _idf_w(ph: str) -> float:
            ws: List[float] = []
            cand_toks = [ph] + [ph[j:j + 2] for j in range(len(ph) - 1)]
            for t in cand_toks:
                j = self.index.vocab.get(t)
                if j is not None and j < len(self.index.idf):
                    ws.append(float(self.index.idf[j]))
            return max(ws) if ws else 1.0

        sig_w = [_idf_w(ph) for ph in qsig]
        sig_total = sum(sig_w) or 1.0
        # 标题匹配同样按 IDF 加权。否则"OpenDSS 能算承载力吗"里
        # "承载力/载力"这些常见词会让讲通用概念的章节命中更多标题词，
        # 压过真正回答 OpenDSS 边界的章节（实测踩过）。
        head_w = {t: _idf_w(t) for t in qhead}
        head_total = sum(head_w.values()) or 1.0
        # 候选池：两路各自的 TopN 取并集，避免一路漏掉另一路的好结果
        idx_kw = list(np.argsort(-kw)[:pool])
        idx_vec = list(np.argsort(-vec)[:pool])
        cand = list(dict.fromkeys(list(idx_kw) + list(idx_vec)))
        hits: List[Hit] = []
        for i in cand:
            k = float(kw[i])
            v = float(vec[i])
            # 混合分：向量体现"整体相关性"，关键词体现"精确命中"
            hybrid = 0.5 * k + 0.5 * v
            body_tokens = set(self.chunk_tokens[i])
            body_low = self.chunks[i].text.lower()
            # 严匹配：短语在正文里出现，或拆成 2-gram 后全部出现
            hit_ph: List[str] = []
            hit_w = 0.0
            for ph, w in zip(qsig, sig_w):
                ok = ph in body_low
                if not ok and len(ph) > 2:
                    ok = all(ph[j:j + 2] in body_tokens
                             for j in range(len(ph) - 1))
                if ok:
                    hit_ph.append(ph)
                    hit_w += w
            coverage = (hit_w / sig_total) if qsig else 0.0
            # 标题命中（松匹配 + IDF 加权）
            head_hit = [t for t in qhead if t in self._head_tokens[i]]
            hcov = (sum(head_w[t] for t in head_hit) / head_total) \
                if qhead else 0.0
            # 组合打分：标题权重显著高于正文覆盖 ——
            # 正文顺带提到一个词很常见，标题命中意味着整节就在讲这件事。
            score = hybrid * (1.0 + 0.40 * coverage + 3.0 * hcov)
            # 文档标题命中：问的问题正好是这篇文档的主题（如"国标限值"）。
            # 加了新文档后，不同文档会讲到同一批术语（电压偏差/不平衡），
            # 只靠正文覆盖容易张冠李戴；标题命中是很强的归属信号。
            try:
                _tl = self._doc_title_low(i)
                _th = sum(1 for _t in qsig if _t and _t in _tl)
                if _th:
                    score *= (1.0 + min(0.30, 0.10 * _th))
            except Exception:
                pass
            # 覆盖率闸门：一个查询短语都没命中，基本不是在讲这件事。
            # 为什么必须显式压制：BM25 是**按当次最高分归一化**的，
            # 只要命中了某个泛词（如"区别"）就可能拿到 kw=1.0 而排到第一，
            # 哪怕 gbd/admm 一个都不在（实测踩过）。
            if coverage <= 0.001:
                score *= 0.35
            # ---- 答案承载信号（两条，都是"这一段是否真的回答了问题"）
            ch_breadcrumb_low = (self.chunks[i].breadcrumb or "").lower()
            # ① 问判据/数值 → 优先真的给出数值区间的段落。
            #    反例：问"电压越限的判据"，某段只写了"按判据列出电压越限母线"
            #    （字面全命中但没给数），而真正讲判据的段落写着
            #    "between 0.90 ~ 1.10 pu"。只看字面会把前者排前面。
            if wants_criterion and _NUMERIC_LIMIT.search(body_low):
                score *= 1.45
            # ② 问文献 → 优先文献索引/归类类章节
            if wants_literature:
                lab = ch_breadcrumb_low + self._doc_title_low(i)
                if ("文献" in lab or "参考" in lab or "索引" in lab
                        or "综述" in lab or "归类" in lab):
                    score *= 1.55
            # ③ 问定义 → 优先概念性章节与术语表
            if wants_definition:
                label = f"{ch_breadcrumb_low}"
                if ("概念" in label or "什么是" in label
                        or "术语" in label or "基本" in label):
                    score *= 1.35
            if score <= 0:
                continue
            ch = self.chunks[i]
            hits.append(Hit(chunk_id=ch.chunk_id, doc_id=ch.doc_id,
                            text=ch.text, breadcrumb=ch.breadcrumb,
                            score=score, kw_score=k, vec_score=v,
                            coverage=coverage, matched=tuple(hit_ph[:8]),
                            expanded=expanded))
        return hits

    @staticmethod
    def _merge(a: List[Hit], b: List[Hit]) -> List[Hit]:
        """合并两次召回，同块保留分高的一次。"""
        by: Dict[str, Hit] = {}
        for h in list(a) + list(b):
            old = by.get(h.chunk_id)
            if old is None or h.score > old.score:
                by[h.chunk_id] = h
        return list(by.values())

    @staticmethod
    def _expand(query: str) -> str:
        """用领域同义词扩展查询（低置信度时用）。"""
        extra: List[str] = []
        for word, syns in SYNONYMS.items():
            if word in query:
                for s in syns:
                    if s not in query:
                        extra.append(s)
        return (query + " " + " ".join(dict.fromkeys(extra))).strip() \
            if extra else query

    # ---------------------------------------------------------- MMR
    def _mmr(self, hits: List[Hit], *, top_k: int,
             lam: float = 0.72) -> List[Hit]:
        """最大边际相关：既相关又彼此不重复。"""
        pool = hits[:max(top_k * 6, 24)]
        if len(pool) <= top_k:
            return pool[:top_k]
        vecs = []
        for h in pool:
            v = self.index.vectorize(tokenize(h.text))
            vecs.append(v)
        selected: List[int] = []
        remaining = list(range(len(pool)))
        while remaining and len(selected) < top_k:
            best_i = -1
            best_val = -1e9
            for i in remaining:
                rel = pool[i].score
                if selected:
                    sims = [float(np.dot(vecs[i], vecs[j]))
                            for j in selected]
                    red = max(sims) if sims else 0.0
                else:
                    red = 0.0
                val = lam * rel - (1 - lam) * red
                if val > best_val:
                    best_val = val
                    best_i = i
            if best_i < 0:
                break
            selected.append(best_i)
            remaining.remove(best_i)
        return [pool[i] for i in selected]

    # ---------------------------------------------------------- 上下文
    def context(self, query: str, *, top_k: int = 3,
                max_chars: int = 2600) -> str:
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""
        parts: List[str] = []
        used = 0
        for h in hits:
            body = h.text.strip()
            if not body:
                continue
            remain = max_chars - used
            if remain < 120:
                break
            body = body[:remain]
            parts.append(f"【{h.breadcrumb or h.chunk_id}】\n{body}")
            used += len(body)
        return "\n\n".join(parts)
