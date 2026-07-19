"""记忆系统抽象接口定义。

包含：
- MemoryInterface: 基础接口（向后兼容旧 src/memory/ 接口签名）
- MemoryExInterface: 扩展接口（继承 MemoryInterface，新增提取/进化/自动触发等方法）
- NoopMemory: 空实现（backend="none" 或初始化失败时降级使用）
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class MemoryInterface(ABC):
    """基础记忆接口，保持与旧 MemoryInterface 完全相同的方法签名。

    确保 query_loop.py 等调用方在切换到 memory_ex 后端时无需修改方法调用。
    """

    @abstractmethod
    def add(self, role: str, content: str, metadata: Optional[dict] = None) -> str:
        """存储记忆，返回记忆 ID。"""
        ...

    @abstractmethod
    def get(self, memory_id: str) -> Optional[Dict]:
        """按 ID 获取单条记忆。"""
        ...

    @abstractmethod
    def search(self, query: str, top_k: int = 5, **filters) -> List[Dict]:
        """搜索记忆。"""
        ...

    @abstractmethod
    def get_working_memory(self) -> str:
        """获取工作记忆（新架构中返回空字符串，仅为接口兼容）。"""
        ...

    @abstractmethod
    def get_context_for_query(self, query: str) -> str:
        """获取当前 Query 的记忆上下文，供注入 api_messages。"""
        ...

    @abstractmethod
    def update(self, memory_id: str, **fields) -> bool:
        """更新记忆字段。"""
        ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """删除记忆。"""
        ...

    @abstractmethod
    def clear_all(self) -> int:
        """清空所有记忆，返回清除的条目数。"""
        ...

    @abstractmethod
    def compact(self) -> int:
        """整理记忆，返回处理的条目数。"""
        ...

    @abstractmethod
    def stats(self) -> Dict:
        """返回记忆统计信息。"""
        ...

    @abstractmethod
    def maintain(self) -> int:
        """执行轻量维护。"""
        ...


class MemoryExInterface(MemoryInterface):
    """扩展接口，在 MemoryInterface 基础上新增提取、进化、自动触发等方法。

    新增方法对应设计文档第一、三、四、七章中的功能。
    """

    @abstractmethod
    def compact_detailed(self) -> dict:
        """执行完整三段式整理，返回 dict 统计信息（含 merged/demoted/evicted）。"""
        ...

    @abstractmethod
    def extract(self) -> dict:
        """从 Layer 0 中 status=raw 的条目中提取结构化记忆。

        Query 结束后由 query_loop.py 显式调用。
        """
        ...

    @abstractmethod
    def evolve(self) -> dict:
        """手动触发全类型记忆进化，返回进化统计信息。"""
        ...

    @abstractmethod
    def check_compaction_needed(self) -> bool:
        """检查是否需要整理（基于水位和 Query 间隔）。"""
        ...

    @abstractmethod
    def check_evolution_needed(self) -> bool:
        """检查是否需要进化（基于 Layer 0 积累量）。"""
        ...

    @abstractmethod
    def auto_compact(self) -> dict:
        """自动整理入口。检查配置开关和水位，满足条件则同步执行。"""
        ...

    @abstractmethod
    def auto_evolve(self) -> dict:
        """自动进化入口。检查配置开关和积累量，满足条件则同步执行。"""
        ...


class NoopMemory(MemoryExInterface):
    """空实现，所有方法均为安全空返回。

    用途：
    1. config.memory.backend == "none" 时使用
    2. MemoryEx 初始化失败时降级使用
    """

    def add(self, role: str, content: str, metadata: Optional[dict] = None) -> str:
        return ""

    def get(self, memory_id: str) -> Optional[Dict]:
        return None

    def search(self, query: str, top_k: int = 5, **filters) -> List[Dict]:
        return []

    def get_working_memory(self) -> str:
        return ""

    def get_context_for_query(self, query: str) -> str:
        return ""

    def update(self, memory_id: str, **fields) -> bool:
        return False

    def delete(self, memory_id: str) -> bool:
        return False

    def clear_all(self) -> int:
        return 0

    def compact(self) -> int:
        return 0

    def compact_detailed(self) -> dict:
        return {"skipped": True, "reason": "noop"}

    def stats(self) -> Dict:
        return {"backend": "noop", "total": 0}

    def maintain(self) -> int:
        return 0

    def extract(self) -> dict:
        return {"skipped": True, "reason": "noop"}

    def evolve(self) -> dict:
        return {"skipped": True, "reason": "noop"}

    def check_compaction_needed(self) -> bool:
        return False

    def check_evolution_needed(self) -> bool:
        return False

    def auto_compact(self) -> dict:
        return {"skipped": True, "reason": "noop"}

    def auto_evolve(self) -> dict:
        return {"skipped": True, "reason": "noop"}
