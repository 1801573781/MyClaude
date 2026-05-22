"""
memory_xx - 可插拔记忆子系统

提供：
- MemoryInterface: 统一抽象基类
- NoopMemory: 空操作实现（默认/降级）
- factory.create_memory(): 工厂函数，根据配置创建具体后端实例
- memory_1: Embedding + FAISS 向量检索实现
- memory_2: LLM 召回实现
"""

from src.memory_xx.memory_interface import MemoryInterface, NoopMemory
from src.memory_xx.factory import create_memory

__all__ = ["MemoryInterface", "NoopMemory", "create_memory"]