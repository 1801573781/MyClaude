"""
记忆模块统一抽象接口 (MemoryInterface)

所有记忆实现（memory_1、memory_2 等）必须继承此 ABC，
query_loop.py 仅依赖此接口编程，不感知具体实现。

参考：OpenClaw MemoryBackend、Claude Code 分层记忆、Hermes 生命周期管理
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class MemoryInterface(ABC):
    """记忆子系统统一抽象基类。

    所有记忆后端实现必须继承此类并实现全部抽象方法。
    约束条件：
    - add() 的 role 参数取值："user" / "assistant" / "system"
    - search() 返回 list[dict]，每个 dict 至少含 id、content、score、timestamp
    - clear_all() 必须真正删除持久化文件及备份
    """

    @abstractmethod
    def add(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加一条记忆，返回记忆 ID。

        Args:
            role: "user" / "assistant" / "system"（与 OpenAI 消息格式对齐）
            content: 记忆文本内容
            metadata: 可选元数据（如 importance、tags 等）

        Returns:
            新创建记忆的唯一标识符
        """
        ...

    @abstractmethod
    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 获取单条记忆。

        Args:
            memory_id: 记忆唯一标识符

        Returns:
            记忆条目字典（至少含 id、role、content、timestamp），不存在返回 None
        """
        ...

    @abstractmethod
    def search(self, query: str, top_k: int = None, **filters: Any) -> List[Dict[str, Any]]:
        """语义检索，返回最相关的 top_k 条记忆。

        Args:
            query: 检索查询文本
            top_k: 返回条数，默认从配置读取（不超过 max_top_k）
            **filters: 可选过滤条件（如 role、tag、time_range 等）

        Returns:
            list[dict]，每个 dict 至少含 id、content、score、timestamp
        """
        ...

    @abstractmethod
    def get_working_memory(self) -> str:
        """获取工作记忆（本次会话上下文）。

        Returns:
            格式化后的文本，可直接以 role="user"、前缀 [记忆上下文] 注入 api_messages
        """
        ...

    @abstractmethod
    def get_context_for_query(self, query: str) -> str:
        """根据用户输入查询关键词获取记忆上下文。

        传入用户输入文本，后端据此检索相关长期/短期记忆，
        返回格式化后的 Markdown 文本，供注入 api_messages 使用。

        Args:
            query: 用户输入的查询文本

        Returns:
            格式化后的文本，含 [检索结果] 和 [当前任务上下文] 区块
        """
        ...

    @abstractmethod
    def update(self, memory_id: str, **fields: Any) -> bool:
        """更新指定记忆的字段（如重要性、标签）。

        Args:
            memory_id: 记忆唯一标识符
            **fields: 要更新的字段键值对

        Returns:
            是否更新成功
        """
        ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """删除单条记忆。

        Args:
            memory_id: 记忆唯一标识符

        Returns:
            是否删除成功
        """
        ...

    @abstractmethod
    def clear_all(self) -> int:
        """清空全部记忆（包括工作/短期/长期及备份文件）。

        Returns:
            删除的记忆总条数
        """
        ...

    @abstractmethod
    def compact(self) -> int:
        """手动触发压缩（短期记忆 → 长期记忆摘要）。

        Returns:
            压缩的记忆条数
        """
        ...

    @abstractmethod
    def stats(self) -> Dict[str, Any]:
        """返回记忆统计信息。

        Returns:
            字典，含各层数量、存储大小等统计字段
        """
        ...

    @abstractmethod
    def maintain(self) -> int:
        """执行维护（遗忘过期记忆、优化索引等）。

        Returns:
            清理的记忆条数
        """
        ...


class NoopMemory(MemoryInterface):
    """空操作记忆实现（当配置 backend="none" 时使用）。

    所有方法返回安全的默认值，不执行任何实际记忆操作。
    """

    def add(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        return "noop-0"

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        return None

    def search(self, query: str, top_k: int = None, **filters: Any) -> List[Dict[str, Any]]:
        return []

    def get_working_memory(self) -> str:
        return ""

    def get_context_for_query(self, query: str) -> str:
        return ""

    def update(self, memory_id: str, **fields: Any) -> bool:
        return False

    def delete(self, memory_id: str) -> bool:
        return False

    def clear_all(self) -> int:
        return 0

    def compact(self) -> int:
        return 0

    def stats(self) -> Dict[str, Any]:
        return {
            "working": 0,
            "short_term": 0,
            "long_term": 0,
            "total": 0,
            "storage_size_bytes": 0,
        }

    def maintain(self) -> int:
        return 0