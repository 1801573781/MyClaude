"""
memory_1 - Embedding + FAISS 向量检索记忆后端

实现 MemoryInterface 接口，提供：
- Memory1Backend: 三层记忆系统主入口
- MemoryStore: JSON 持久化存储
- MemoryRetriever: Embedding + FAISS 语义检索
- MemoryCompressor: LLM 压缩器（短期→长期）
- MemoryInjector: 记忆格式化注入器
- adapter: query_loop 适配层
"""

from src.memory.memory_1.memory1 import Memory1Backend, create_memory

__all__ = ["Memory1Backend", "create_memory"]