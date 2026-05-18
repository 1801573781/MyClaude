"""
memory2 - 全新记忆模块（完全独立于原 src/memory/）

架构亮点：
- 三层记忆：Working → Short-term → Long-term
- 双路召回：Embedding 粗筛 → LLM 精排
- 艾宾浩斯遗忘曲线
- 原子持久化 + 滚动备份
- 真正的向量语义召回（非字符级 TF-IDF）

使用方式：
    from memory2 import (
        MemoryStore, MemoryEntry,
        EmbeddingRetriever, EmbeddingGenerator,
        LLMRetriever,
        MemoryManager, MemoryConfig,
    )
"""

from .memory_store import MemoryStore, MemoryEntry
from .embedding_retriever import (
    EmbeddingRetriever,
    EmbeddingGenerator,
    RetrievalResult,
)
from .llm_retriever import LLMRetriever
from .memory_manager import MemoryManager, MemoryConfig

__all__ = [
    "MemoryStore",
    "MemoryEntry",
    "EmbeddingRetriever",
    "EmbeddingGenerator",
    "RetrievalResult",
    "LLMRetriever",
    "MemoryManager",
    "MemoryConfig",
]