"""knowledge — 配网承载力知识库系统。

## 定位

照 [ai-localbase](https://github.com/veyliss/ai-localbase) 的**系统形态**做一个
本地优先的 RAG 知识库，内容面向配网承载力与主配微协同：

| ai-localbase | 本包 |
|---|---|
| 知识库管理（建/删/文档列表） | `service.KnowledgeService` |
| 文档上传与解析（txt/md/pdf/xlsx/csv） | `loaders` + `service.add_files` |
| 文本切分与嵌入 | `chunking` + `index`（TF-IDF，离线） |
| 候选召回 / 关键词重排 / MMR / 二次扩召回 | `search` |
| 异步索引 Job（进度 + 取消） | `jobs` + `store.job` |
| 聊天记录持久化 | `store.session` / `store.message` |
| 生成 RAG 评估数据集 | `eval` |

## 与 ai-localbase 的两处必要差异

1. **向量层用本地 TF-IDF，不是 Qdrant + 嵌入模型**
   本工程要离线运行、打包成单机程序；DeepSeek 也没有 embeddings 接口
   （实测 `/v1/embeddings` 返回 404）。TF-IDF 同样是向量空间检索，
   零外部服务、毫秒级，对术语密集的电力文本够用。

2. **单进程内嵌，不是 Docker + Go 后端 + React 前端**
   这里是原生桌面程序（Tk），知识库直接跑在同一进程里，
   界面用原生控件，不引入浏览器引擎。

## 快速上手

```python
from hc_agent.knowledge import KnowledgeService

svc = KnowledgeService()                    # 首次会自动建默认库并建索引
svc.list_kbs()                              # 多知识库
svc.add_files(svc.default_kb_id(), ["规范.pdf", "细则.docx"])
job = svc.index_kb(svc.default_kb_id())     # 异步，可查进度
while job.running: ...
svc.search("电压越限的判据", top_k=3)
svc.as_context("为什么容量是 0")             # 拼给大模型的上下文
```
"""

from __future__ import annotations

from typing import Any, Dict, List

from hc_agent.knowledge import loaders
from hc_agent.knowledge.chunking import Chunk, chunk_document
from hc_agent.knowledge.eval import generate_eval, run_eval
from hc_agent.knowledge.index import (DOMAIN_TERMS, VectorIndex, fingerprint,
                                      key_phrases, significant_terms,
                                      tokenize)
from hc_agent.knowledge.jobs import JobManager, Progress
from hc_agent.knowledge.search import Hit, Searcher, SYNONYMS
from hc_agent.knowledge.service import (DEFAULT_KB_ID, DEFAULT_KB_NAME,
                                        KnowledgeService, builtin_dir,
                                        cache_root, db_path, files_root,
                                        knowledge_root)
from hc_agent.knowledge.store import Store

#: 兼容旧名字。早期版本直接暴露一个扁平的 KnowledgeBase，
#: 现在真正的系统是 KnowledgeService；保留别名避免调用方全改。
KnowledgeBase = KnowledgeService


def quick_context(query: str, top_k: int = 3) -> str:
    """便捷函数：在默认知识库里取一段上下文（问答用）。"""
    try:
        return KnowledgeService().as_context(query, top_k=top_k)
    except Exception:
        return ""


__all__ = [
    "KnowledgeService", "KnowledgeBase", "Store", "JobManager", "Progress",
    "Hit", "Searcher", "Chunk", "chunk_document", "VectorIndex",
    "tokenize", "key_phrases", "significant_terms", "fingerprint",
    "DOMAIN_TERMS", "SYNONYMS", "loaders", "generate_eval", "run_eval",
    "quick_context", "knowledge_root", "files_root", "cache_root",
    "builtin_dir", "db_path", "DEFAULT_KB_ID", "DEFAULT_KB_NAME",
]
