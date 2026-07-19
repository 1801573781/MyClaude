"""
记忆模块工厂函数（重构版）

重构要点：
- 移除 adapter 路径：直接通过后端模块的 create_memory() 创建实例
- 移除 _inject_llm_fn_if_needed：Backend 已自包含 LLM 构建
- 移除 _BACKEND_MODULE_MAP / _ADAPTER_MODULE_MAP 双路映射
- 简化为单一映射表 + 动态加载
"""

import importlib
import logging
from typing import Any, Optional

from src.memory.memory_interface import MemoryInterface, NoopMemory

logger = logging.getLogger(__name__)

# 后端模块映射表（模块路径 → 工厂函数名）
_BACKEND_MODULES = {
    "memory_2": ("src.memory.memory_2.memory2", "create_memory"),
    "memory_1": ("src.memory.memory_1.memory1", "create_memory"),
}


def create_memory(config: Any) -> MemoryInterface:
    """根据配置创建记忆后端实例。

    通过后端模块的 create_memory(config) 工厂函数创建实例，
    后端内部自行初始化 LLM 函数和所有子组件。

    Args:
        config: 全局配置对象（SimpleNamespace）

    Returns:
        MemoryInterface 实现实例

    Raises:
        ValueError: 指定的后端不支持
        ImportError: 后端模块无法加载
    """
    backend = _get_backend_name(config)

    if backend is None or backend == "none":
        logger.info("记忆后端配置为 'none'，使用 NoopMemory 空实现")
        return NoopMemory()

    if backend not in _BACKEND_MODULES:
        available = ", ".join(_BACKEND_MODULES.keys())
        raise ValueError(
            f"不支持的记忆后端 '{backend}'。可用选项: {available}。"
            f"请在 config.yaml 中修改 memory.backend 配置。"
        )

    module_path, factory_func_name = _BACKEND_MODULES[backend]

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        _check_module_importable(backend, module_path)
        raise ImportError(
            f"记忆后端 '{backend}' 模块加载失败: {e}。"
            f"请确认 {module_path.replace('.', '/')}.py 文件存在且无语法错误。"
        ) from e

    factory_func = getattr(module, factory_func_name, None)
    if factory_func is None or not callable(factory_func):
        raise ImportError(
            f"记忆后端 '{backend}' 模块中未找到工厂函数 '{factory_func_name}'。"
        )

    instance = factory_func(config)

    if not isinstance(instance, MemoryInterface):
        raise TypeError(
            f"记忆后端 '{backend}' 返回的实例未继承 MemoryInterface。"
            f"所有记忆实现必须继承 src.memory.memory_interface.MemoryInterface。"
        )

    logger.info(f"记忆后端 '{backend}' 加载成功")
    return instance


def _get_backend_name(config: Any) -> Optional[str]:
    """从配置中提取 backend 名称，兼容多种配置结构。"""
    if hasattr(config, "memory") and hasattr(config.memory, "backend"):
        return config.memory.backend
    if hasattr(config, "memory") and isinstance(config.memory, dict):
        return config.memory.get("backend", "none")
    if hasattr(config, "backend"):
        return config.backend
    return "none"


def _check_module_importable(backend: str, module_path: str) -> None:
    """检查后端模块是否可以导入，给出友好报错。"""
    import importlib.util
    spec = importlib.util.find_spec(module_path)
    if spec is None:
        raise ImportError(
            f"记忆后端 '{backend}' 的模块不可导入: {module_path}。"
            f"请确认 {backend} 实现已部署或修改 config.yaml 中的 memory.backend 配置。"
        )
