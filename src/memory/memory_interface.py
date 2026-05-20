"""
MemoryBackend - 可插拔记忆后端抽象接口

所有记忆模块（src/memory/、src/memory2/ 或未来的自定义模块）只需实现此接口，
MyClaude 其余代码（query_loop、mycli）无需任何改动。

使用方式：
    1. 在 config.yaml 中设置 memory.use_new: true/false
    2. query_loop._init_memory_manager() 自动根据配置选择适配器
    3. 如需自定义记忆模块，实现 MemoryBackend 并更新工厂函数即可

接口设计原则：
    - 只暴露 query_loop / mycli 实际调用的方法
    - 方法签名取两个现有实现的最大公约数
    - 返回值统一，屏蔽内部差异
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class MemoryBackend(ABC):
    """记忆后端抽象接口。"""

    @abstractmethod
    def clear_working_memory(self) -> None:
        """
        新任务开始时清空上一轮的工作记忆。
        由 query_loop.run() 在每个 session 开头调用。
        """
        ...

    @abstractmethod
    def inject_context(self, current_query: str) -> str:
        """
        召回相关记忆，格式化为可注入 LLM 对话的上下文字符串。

        Args:
            current_query: 当前用户查询（用于语义召回）

        Returns:
            格式化好的上下文字符串（可能包含 [系统提醒] 前缀）。
            若无相关记忆，返回空字符串 ""。
        """
        ...

    @abstractmethod
    def add_turn_memory(
        self,
        user_input: str,
        turn: int,
        reasoning_content: str,
        remaining_text: str,
        tools: list,
    ) -> None:
        """
        记录一轮对话到工作记忆。

        Args:
            user_input: 用户原始输入
            turn: 当前轮次编号
            reasoning_content: LLM 推理过程（可能为空）
            remaining_text: LLM 应答文本（去除 think 标签后）
            tools: 工具调用列表 [{"llm_tool": ..., "params": {...}}, ...]
        """
        ...

    @abstractmethod
    def persist_and_maintain(self) -> None:
        """
        会话结束时执行：持久化工作记忆 → 压缩短期记忆 → 遗忘过期记忆。
        由 query_loop.run() 在 session 末尾调用。
        """
        ...

    @abstractmethod
    def clear_all_memories(self) -> int:
        """
        清除所有记忆（短期 + 长期 + 工作记忆）。

        Returns:
            被删除的记忆条目总数。
        """
        ...


def create_memory_backend() -> Optional[MemoryBackend]:
    """
    工厂函数：根据 config.yaml → memory 配置，自动选择记忆后端。

    优先级：
        1. memory.active_module（如 "memory5" / "memory6"）→ 加载对应的适配器
        2. memory.use_new（向后兼容）→ true 用 Memory2Adapter，false 用 Memory1Adapter
        3. 默认：Memory1Adapter

    Returns:
        MemoryBackend 实例，若 memory.enabled 为 false 则返回 None。
    """
    from src.utility.config_loader import global_cfg

    mem_enabled = getattr(global_cfg.memory, 'enabled', False)
    if not mem_enabled:
        return None

    active_module = getattr(global_cfg.memory, 'active_module', None)
    if active_module:
        config_dir = getattr(global_cfg.memory, 'config_dir', 'config/memory')
        project_root = getattr(global_cfg.base_path, 'project_root', '.')
        config_path = Path(project_root) / config_dir / f"{active_module}.yaml"
        module_config = {}
        if config_path.exists():
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                module_config = yaml.safe_load(f) or {}

        if active_module == "memory5":
            from src.memory_ex.memory5.adapter import Memory5Adapter
            return Memory5Adapter(config=module_config)
        elif active_module == "memory6":
            from src.memory_ex.memory6.adapter import Memory6Adapter
            return Memory6Adapter(config=module_config)
        else:
            # 未知 active_module，回退到旧逻辑
            pass

    # ---------- 向后兼容：use_new 标志 ----------
    use_new = getattr(global_cfg.memory, 'use_new', False)

    if use_new:
        from src.memory2.memory2_adapter import Memory2Adapter
        return Memory2Adapter()
    else:
        from src.memory.memory1_adapter import Memory1Adapter
        return Memory1Adapter()
