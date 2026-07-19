"""记忆后端工厂函数。

对应设计文档第六章 6.2 节策略三。

根据 config.yaml 中 memory.backend 配置动态加载后端。
兼容现有调用：create_memory(global_cfg)
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_backend_name(config: Any) -> str:
    """从全局配置中读取 memory.backend 值。

    Args:
        config: 全局配置对象

    Returns:
        后端名称字符串
    """
    # 尝试通过属性链读取
    try:
        if hasattr(config, "memory"):
            memory_cfg = config.memory
            if hasattr(memory_cfg, "backend"):
                return str(memory_cfg.backend)
    except Exception:
        pass

    # 尝试从字典读取
    try:
        if isinstance(config, dict):
            return str(config.get("memory", {}).get("backend", ""))
    except Exception:
        pass

    return ""


def create_memory(config: Any) -> Any:
    """根据配置创建记忆后端实例。

    兼容现有调用：create_memory(global_cfg)

    Args:
        config: 全局配置对象（SimpleNamespace 或 dict）

    Returns:
        MemoryExInterface 实例：
        - backend="memory_ex" → MemoryEx
        - backend="none" → NoopMemory
        - 未知后端或加载失败 → NoopMemory（降级）
    """
    backend = _get_backend_name(config)
    logger.info(f"创建记忆后端: backend={backend!r}")

    if backend == "memory_ex":
        try:
            from src.memory_ex.memory_ex import MemoryEx

            return MemoryEx(config)
        except Exception as e:
            logger.error(f"MemoryEx 初始化失败，降级为 NoopMemory: {e}")
            from src.memory_ex.memory_interface import NoopMemory

            return NoopMemory()

    elif backend == "none" or not backend:
        from src.memory_ex.memory_interface import NoopMemory

        return NoopMemory()

    else:
        # 兼容期：如果配置仍指向旧后端（如 memory_2），降级为 NoopMemory
        logger.warning(f"后端 '{backend}' 不存在，降级为 NoopMemory")
        from src.memory_ex.memory_interface import NoopMemory

        return NoopMemory()
