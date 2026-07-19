"""memory_ex 模块 —— 三层物理隔离记忆系统。

提供以下导出：
- create_memory: 工厂函数，根据配置创建记忆后端实例
- MemoryExInterface: 扩展接口（继承 MemoryInterface）
- NoopMemory: 空实现（backend="none" 时使用）
- ContextCompressor: 独立组件，Session 内上下文压缩
"""

from src.memory_ex.factory import create_memory
from src.memory_ex.memory_interface import MemoryExInterface, NoopMemory
from src.memory_ex.context_compressor import ContextCompressor

__all__ = [
    "create_memory",
    "MemoryExInterface",
    "NoopMemory",
    "ContextCompressor",
]
