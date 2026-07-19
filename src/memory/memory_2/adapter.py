"""
memory_2 兼容适配层（重构版）

重构后 Memory2Backend 已直接实现 MemoryInterface 并内联了原 Adapter 的全部逻辑
（LLM 构建、工作记忆管理、注入编排等），因此 Adapter 不再需要独立实现。

此类仅作为向后兼容入口保留，防止外部代码中可能存在的
    from src.memory.memory_2.adapter import Memory2Adapter
导入路径断裂。新代码应直接使用 Memory2Backend。
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from src.memory.memory_2.memory2 import Memory2Backend
from src.memory.memory_interface import MemoryInterface

logger = logging.getLogger(__name__)


class Memory2Adapter(Memory2Backend):
    """memory_2 兼容适配器（直接继承 Backend，无额外逻辑）。

    重构前：Adapter 包装 Backend，注入 Injector/Compressor/LLM，双重管理工作记忆。
    重构后：Backend 已自包含全部逻辑，Adapter 仅保持类名兼容。

    迁移指南：
        旧代码: from src.memory.memory_2.adapter import Memory2Adapter
        新代码: from src.memory.memory_2.memory2 import Memory2Backend
    """

    def __init__(self, config: Any = None):
        super().__init__(config=config)
        logger.debug("Memory2Adapter (兼容层) 初始化完成")
