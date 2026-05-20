"""Memory 统一接口定义

所有 Memory 实现（memory5、memory6 等）必须继承 MemoryBackend
并实现以下全部抽象方法。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class MemoryBackend(ABC):
    """Memory 实现统一基类，定义核心接口契约"""

    @abstractmethod
    def add_memory(self, content: str, **kwargs) -> str:
        """添加一条记忆

        Args:
            content: 记忆内容文本
            **kwargs: 可选元数据（如 importance、source、tags 等）

        Returns:
            新创建记忆的唯一 ID
        """
        ...

    @abstractmethod
    def search_memory(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """搜索相关记忆

        Args:
            query: 查询文本
            limit: 返回条数上限

        Returns:
            按相关度降序排列的记忆列表，每条为 dict（至少含 id、content、score）
        """
        ...

    @abstractmethod
    def get_all_memories(self) -> List[Dict[str, Any]]:
        """获取所有记忆

        Returns:
            全部记忆列表
        """
        ...

    @abstractmethod
    def delete_memory(self, memory_id: str) -> bool:
        """删除指定记忆

        Args:
            memory_id: 记忆唯一 ID

        Returns:
            是否成功删除
        """
        ...

    @abstractmethod
    def clear_all(self) -> None:
        """清空所有记忆"""
        ...

    @abstractmethod
    def get_working_context(self) -> str:
        """获取当前工作记忆上下文

        Returns:
            格式化后的工作记忆文本，可直接注入 LLM
        """
        ...

    @abstractmethod
    def compress(self) -> int:
        """触发记忆压缩（短期 → 长期）

        Returns:
            本次压缩处理的记忆条数
        """
        ...

    @abstractmethod
    def forget_outdated(self) -> int:
        """执行遗忘策略，清理过期/低价值记忆

        Returns:
            本次遗忘清理的记忆条数
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"