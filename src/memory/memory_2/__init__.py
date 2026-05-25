"""
memory_2 - LLM 召回记忆后端

实现 MemoryInterface 接口，提供：
- Memory2Backend: 三层记忆系统主入口
- MemoryStore: 纯 JSON 持久化存储（无向量）
- MemoryRetriever: LLM 召回检索器
- MemoryCompressor: LLM 压缩器（短期→长期）
- MemoryInjector: 记忆格式化注入器
- adapter: query_loop 适配层

核心理念：不依赖 Embedding 和向量检索，所有记忆以原始文本存储，
检索时交由 LLM 直接判断相关性并打分。
"""

from src.memory.memory_2.memory2 import Memory2Backend, create_memory

__all__ = ["Memory2Backend", "create_memory"]