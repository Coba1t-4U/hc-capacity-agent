"""index.py — 本地 TF-IDF 向量索引（离线、无外部依赖）。

## 为什么不用真嵌入模型

本项目要**离线可用**、**打包成单机程序**（200 MB 量级）、**不联网也能检索**。
引入 embedding 模型（Ollama / 向量库）会让体积和启动时间都失控，
而且 DeepSeek 官方没有 embeddings 接口（实测 /v1/embeddings 返回 404）。

所以用 **TF-IDF + 余弦相似度**代替语义向量：
它同样是"向量空间检索"，能捕捉词的共现权重，
对术语密集的电力规范文本效果不错，且零依赖、毫秒级。

## 与关键词检索的分工

TF-IDF 擅长"词相关但字面不同"的近似（如"倒送"与"反向"），
关键词擅长"精确命中术语"。两者**融合**使用（见 search.py 的 hybrid）。

## 缓存

词汇表和 IDF 每次都重算太慢，缓存到数据目录 `knowledge/.index/`。
用内容哈希做键：文档变了自动失效，不用手动清缓存。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

#: 分词/停用词规则版本。改动 tokenize / STOP / significant_terms 时**必须**加一，
#: 否则索引缓存不会失效。
TEXT_VERSION = 7

#: 停用词。除了高频虚词，**必须包含疑问词**：
#: 问"电压越限的判据是什么"时，"什么"会命中那些正文里带"它能做什么"的段落，
#: 把真正讲判据的小节挤下去（实测踩过）。疑问词不承载领域信息，一律不参与打分。
STOP = {
    "的", "了", "是", "在", "和", "与", "有", "为", "对", "从", "到", "把",
    "被", "这", "那", "个", "就", "也", "都", "而", "或", "及", "但", "并",
    # 疑问 / 泛用词
    "什么", "怎么", "怎样", "如何", "为何", "为什么", "多少", "哪些", "哪个",
    "哪里", "是否", "能否", "可否", "请问", "一下", "告诉", "什么样", "意思",
    "情况", "时候", "我们", "他们", "以及", "一个", "这个", "那个", "一般",
    # 比较类意图词：问"A 和 B 有什么区别"时，"区别"本身不含领域信息，
    # 留着会让只提到"区别"的段落凭空命中（实测：GBD/ADMM 被不相干段落抢占）。
    "区别", "差异", "不同", "对比", "比较", "异同", "优缺点",
    "the", "a", "an", "of", "to", "in", "is", "are", "and", "or", "for",
    "on", "at", "by", "as", "be", "it", "this", "that", "with", "what",
    "how", "why", "which", "when", "where", "do", "does", "can",
}

import re
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-\.]*")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def tokenize(text: str) -> List[str]:
    """中英数字混排分词。

    中文用 **1-gram + 2-gram** 混合：
    纯 2-gram 会漏掉单个字的关键词（如"限"），
    纯 1-gram 又太碎。两者都收，靠 IDF 自动压低无意义的组合。
    数字单独成词，便于命中"0.90""20000"这类阈值。
    """
    text = str(text or "")
    toks: List[str] = []
    toks.extend(w.lower() for w in _WORD.findall(text))
    toks.extend(n for n in _NUM.findall(text))
    for run in _CJK_RUN.findall(text):
        for ch in run:
            toks.append(ch)
        for i in range(len(run) - 1):
            toks.append(run[i:i + 2])
    return [t for t in toks if t and t not in STOP]


#: 虚词字符。含这些字的 2-gram 基本是跨词边界切出来的噪声
#: （如"限的""的判""据是""是什"），对本域没有区分度。
_FUNC_CHARS = set("的是了在和与有为对从到把被这那个也就都而或及但并什么吗呢吧啊之其"
                  "在上下中前后内外时以及则可需应当能会要还要")


#: 领域术语表：抽关键短语时**优先按最长匹配**识别这些词。
#:
#: 为什么需要它：纯 2-gram 固定步长对齐会错切。例如「物理可开放容量」被切成
#: 物理/可开/放容 —— "放容"是伪词，真正的术语"可开放容量"反而被切碎，
#: 于是检索命中不到讲这个概念的小节（实测踩过）。
#: 这份表只收本域高频术语，宁少勿滥；未收录的部分仍由 2-gram 兜底。
DOMAIN_TERMS: Tuple[str, ...] = (
    "物理可开放容量", "管理可开放容量", "可开放容量", "开放容量", "可接入容量",
    "承载力", "电压越限", "电压约束", "电压偏差", "电压等级", "母线电压",
    "载流", "热稳", "负载率", "过载", "反向潮流", "倒送", "三相不平衡",
    "不平衡度", "负序", "短路电流", "短路容量", "渗透率", "容量搜索",
    "二分法", "搜索区间", "单调性", "独立复算", "发布门禁", "专业复核",
    "红黄绿", "分级", "灰色", "数据风险", "同时率", "裕度比例", "裕度",
    "余量", "时间片", "场景", "工况", "典型日", "高光伏", "低负荷",
    "高负荷", "基线潮流", "基线", "台区", "配变", "馈线", "母线", "支路",
    "变压器", "线路", "网损", "标幺值", "规则包", "约束", "限值",
    "判据", "阈值", "越限", "违规", "证据链", "结果哈希", "运行号",
    "算例", "报告", "结论", "口径", "主配微", "协同", "微网",
    "分布式电源", "光伏", "储能", "未覆盖",
    # —— 主配微协同方向（文献改编补充）
    "主配微", "主配一体化", "输配协同", "配微协同", "三级协同",
    "目标级联", "级联", "分解协调", "一致性约束", "罚函数",
    "分布鲁棒", "鲁棒优化", "随机规划", "场景法", "不确定性",
    "联络线", "关口", "交换功率", "耦合变量", "收敛",
    "最优性条件分解", "多参数规划", "多层级自治", "自治",
    "碳强度", "碳约束", "低碳", "可开放容量", "时序",
    "风电场", "光伏设备", "母版曲线", "微网", "储能",
    "文献", "综述", "引用",
)
_LEX_SORTED = tuple(sorted({t.lower() for t in DOMAIN_TERMS},
                           key=len, reverse=True))


def key_phrases(text: str) -> List[str]:
    """从**原始文本**抽关键短语（用于覆盖率与标题匹配）。

    两步：
      1. **最长匹配领域词表**，命中位置标记为已消费；
      2. 剩余位置做**非重叠** 2-gram 兜底，跳过含虚词的组合。

    效果：「电压越限的判据是什么」→ [电压越限, 判据]；
    「什么是物理可开放容量」→ [物理可开放容量]。
    既不会切出"压越""放容"这类伪词，也不会把术语拆散。
    """
    text = str(text or "")
    low = text.lower()
    used = [False] * len(low)
    found: List[Tuple[int, str]] = []
    for term in _LEX_SORTED:
        start = 0
        while True:
            p = low.find(term, start)
            if p < 0:
                break
            if not any(used[p:p + len(term)]):
                found.append((p, term))
                for k in range(p, p + len(term)):
                    used[k] = True
            start = p + 1
    # 兜底：未被词表覆盖的中文片段按非重叠 2-gram 切
    for m in _CJK_RUN.finditer(text):
        base = m.start()
        run = m.group()
        i = 0
        while i < len(run) - 1:
            pos = base + i
            if used[pos] or used[pos + 1]:
                i += 1
                continue
            bg = run[i:i + 2]
            if bg in STOP or any(c in _FUNC_CHARS for c in bg):
                i += 1
                continue
            found.append((pos, bg))
            i += 2
    toks: List[str] = [t for _, t in sorted(found, key=lambda x: x[0])]
    toks.extend(w.lower() for w in _WORD.findall(text) if len(w) >= 2)
    toks.extend(_NUM.findall(text))
    return list(dict.fromkeys(t for t in toks if t and t not in STOP))


def significant_terms(tokens: Sequence[str]) -> List[str]:
    """挑出**实词**用于覆盖率与标题匹配。

    为什么必须区分：`电压越限` 的 1-gram（电/压/越/限）在所有电力文档里都有，
    拿它们算覆盖率会让每篇文档的覆盖率都接近 1，区分度归零
    （实测：问"电压越限的判据"，覆盖率在 6 个不同段落上都是 0.53~0.76）。
    只保留实词后，覆盖率才真正反映"这一段是否在讲同一件事"。
    """
    out: List[str] = []
    for t in tokens:
        if not t or t in STOP:
            continue
        if len(t) == 1:
            # 单个汉字只在非虚词时保留；ASCII 单字母丢弃
            if _CJK_RUN.match(t) and t not in _FUNC_CHARS:
                continue          # 单个汉字仍太泛，不用于覆盖率
            continue
        if len(t) == 2 and all(_CJK_RUN.match(c) for c in t):
            if any(c in _FUNC_CHARS for c in t):
                continue          # 跨词边界的 2-gram
        out.append(t)
    # 去重但保序；若全被过滤掉则退回原词表（避免空集导致覆盖率恒 0）
    uniq = list(dict.fromkeys(out))
    return uniq or list(dict.fromkeys(t for t in tokens if t))


@dataclass
class VectorIndex:
    """TF-IDF 索引。"""
    vocab: Dict[str, int] = field(default_factory=dict)
    idf: np.ndarray = field(default_factory=lambda: np.zeros(0))
    matrix: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    chunk_ids: List[str] = field(default_factory=list)
    #: 每块长度（词数），用于 BM25 的长度归一
    lengths: List[int] = field(default_factory=list)
    avg_len: float = 1.0

    # ---- 构建
    @classmethod
    def build(cls, docs_tokens: Sequence[Sequence[str]],
              chunk_ids: Sequence[str]) -> "VectorIndex":
        n = len(docs_tokens)
        if n == 0:
            return cls()
        # 词表
        df: Dict[str, int] = {}
        for toks in docs_tokens:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        # 只保留出现 >=1 的词；出现 1 次且只在一篇里的词也留着（可能是专有名词）
        vocab = {t: i for i, t in enumerate(sorted(df))}
        # IDF（平滑）
        idf = np.zeros(len(vocab), dtype=np.float32)
        for t, i in vocab.items():
            idf[i] = math.log((n + 1) / (df[t] + 1)) + 1.0
        # TF-IDF 矩阵，行做 L2 归一
        mat = np.zeros((n, len(vocab)), dtype=np.float32)
        for r, toks in enumerate(docs_tokens):
            tf: Dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t, c in tf.items():
                j = vocab.get(t)
                if j is None:
                    continue
                mat[r, j] = (1.0 + math.log(c)) * idf[j]
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = mat / norms
        lengths = [len(t) for t in docs_tokens]
        return cls(vocab=vocab, idf=idf, matrix=mat,
                   chunk_ids=list(chunk_ids), lengths=lengths,
                   avg_len=(sum(lengths) / n if n else 1.0))

    # ---- 查询
    def vectorize(self, tokens: Sequence[str]) -> np.ndarray:
        v = np.zeros(len(self.vocab), dtype=np.float32)
        tf: Dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        for t, c in tf.items():
            j = self.vocab.get(t)
            if j is None:
                continue
            v[j] = (1.0 + math.log(c)) * self.idf[j]
        n = float(np.linalg.norm(v))
        if n > 0:
            v /= n
        return v

    def scores(self, tokens: Sequence[str]) -> np.ndarray:
        """余弦相似度（矩阵已归一，点积即余弦）。"""
        if self.matrix.size == 0:
            return np.zeros(0, dtype=np.float32)
        return self.matrix @ self.vectorize(tokens)

    # ---- 持久化
    def save(self, path: Path) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                str(path), idf=self.idf, matrix=self.matrix,
                chunk_ids=np.array(self.chunk_ids, dtype=object),
                lengths=np.array(self.lengths, dtype=np.int32))
            meta = {"vocab": self.vocab, "avg_len": self.avg_len}
            path.with_suffix(".meta.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            return True
        except Exception:
            return False

    @classmethod
    def load(cls, path: Path) -> Optional["VectorIndex"]:
        try:
            meta_p = path.with_suffix(".meta.json")
            if not path.is_file() or not meta_p.is_file():
                return None
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            z = np.load(str(path), allow_pickle=True)
            return cls(
                vocab={str(k): int(v) for k, v in meta["vocab"].items()},
                idf=z["idf"], matrix=z["matrix"],
                chunk_ids=[str(x) for x in z["chunk_ids"]],
                lengths=[int(x) for x in z["lengths"]],
                avg_len=float(meta.get("avg_len") or 1.0))
        except Exception:
            return None


def fingerprint(parts: Sequence[str]) -> str:
    """索引缓存指纹。

    **必须带分词器版本**：缓存只按文本内容做键时，改了停用词/分词规则
    仍会命中旧缓存，于是新规则"看起来没生效"（实测踩过：加了疑问词停用
    却没任何变化）。TEXT_VERSION 变更即自动失效。
    """
    h = hashlib.sha256()
    h.update(f"v{TEXT_VERSION}\x00".encode())
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:16]
