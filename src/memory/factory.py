"""
记忆模块工厂函数

根据 config.yaml 中 memory.backend 的值创建对应的记忆实现实例。
支持动态模块加载，使 query_loop.py 无需感知具体实现。

用法:
    from src.memory.factory import create_memory
    memory = create_memory(config)
"""

import importlib
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from src.memory.memory_interface import MemoryInterface, NoopMemory

logger = logging.getLogger(__name__)

# 后端模块映射表
_BACKEND_MODULE_MAP = {
    "memory_1": "src.memory.memory_1.memory1",
    "memory_2": "src.memory.memory_2.memory2",
}

# 已知适配器模块（用于自动检测适配器是否存在）
_ADAPTER_MODULE_MAP = {
    "memory_1": "src.memory.memory_1.adapter",
    "memory_2": "src.memory.memory_2.adapter",
}

# 适配器类名映射
_ADAPTER_CLASS_MAP = {
    "memory_1": "Memory1Adapter",
    "memory_2": "Memory2Adapter",
}


# ---- 主入口 ----

def create_memory(config: Any) -> MemoryInterface:
    """根据配置创建记忆后端实例。

    通过适配器（Adapter）创建实例，适配器内部根据 model_key.yaml 中
    对应 memory 模块的配置自行初始化 llm_chat_fn / embed_fn，
    实现高内聚低耦合。

    Args:
        config: 全局配置对象（SimpleNamespace），需包含 memory.backend 字段

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

    if backend not in _BACKEND_MODULE_MAP:
        available = ", ".join(_BACKEND_MODULE_MAP.keys())
        raise ValueError(
            f"不支持的记忆后端 '{backend}'。可用选项: {available}。"
            f"请在 config.yaml 中修改 memory.backend 配置。"
        )

    # 优先使用适配器（适配器内部自行从 config 构建 llm_chat_fn / embed_fn）
    instance = _try_load_adapter(backend, config)
    if instance is not None:
        return instance

    # Fallback：直接使用后端工厂函数
    module_path = _BACKEND_MODULE_MAP[backend]
    logger.info(f"适配器不可用，回退到后端模块: {module_path}")
    instance = _load_backend_instance(module_path, backend, config)

    if not isinstance(instance, MemoryInterface):
        raise TypeError(
            f"记忆后端 '{backend}' 返回的实例未继承 MemoryInterface。"
            f"所有记忆实现必须继承 src.memory.memory_interface.MemoryInterface。"
        )

    logger.info(f"记忆后端 '{backend}' 加载成功")
    return instance


def _try_load_adapter(backend: str, config: Any) -> Optional[MemoryInterface]:
    """尝试通过适配器创建实例。

    adapter 内部根据 model_key.yaml 中对应的 memory 配置节
    自行构建 llm_chat_fn 和 embed_fn，factory 只传 config。

    Returns:
        成功返回 MemoryInterface 实例，失败返回 None
    """
    adapter_module_path = _ADAPTER_MODULE_MAP.get(backend)
    if not adapter_module_path:
        return None

    adapter_class_name = _ADAPTER_CLASS_MAP.get(backend)
    if not adapter_class_name:
        return None

    try:
        module = importlib.import_module(adapter_module_path)
    except ImportError as e:
        logger.debug(f"适配器模块 {adapter_module_path} 导入失败: {e}")
        return None

    adapter_cls = getattr(module, adapter_class_name, None)
    if adapter_cls is None:
        logger.debug(f"适配器类 {adapter_class_name} 在模块 {adapter_module_path} 中未找到")
        return None

    try:
        instance = adapter_cls(config=config)
        logger.info(f"通过适配器 {adapter_class_name} 创建记忆后端 '{backend}'")
        return instance
    except TypeError as e:
        logger.warning(f"适配器 {adapter_class_name} 参数错误（config 结构不匹配）: {e}")
        return None
    except Exception as e:
        logger.warning(f"适配器 {adapter_class_name} 创建实例时出错: {e}，将在下次启动时重试")
        logger.debug("适配器异常详情", exc_info=True)
        return None


def _get_backend_name(config: Any) -> Optional[str]:
    """从配置中提取 backend 名称，兼容多种配置结构。

    支持以下配置格式：
    1. config.memory.backend（YAML 顶层 memory.backend）
    2. config.memory.backend['name']（字典格式）
    3. 直接 config.backend（扁平配置）
    """
    if hasattr(config, "memory") and hasattr(config.memory, "backend"):
        return config.memory.backend
    if hasattr(config, "memory") and isinstance(config.memory, dict):
        return config.memory.get("backend", "none")
    if hasattr(config, "backend"):
        return config.backend
    return "none"


def _load_backend_instance(
    module_path: str,
    backend: str,
    config: Any,
) -> MemoryInterface:
    """动态加载后端模块并创建实例（fallback 路径）。

    尝试顺序：
    1. 带适配器的工厂函数 create_memory(config)
    2. 直接类实例化 MemoryBackend(config=config) / MemoryBackend()
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        _check_directory_exists(backend, module_path)
        raise ImportError(
            f"记忆后端 '{backend}' 模块加载失败: {e}。"
            f"请确认 {module_path.replace('.', '/')}.py 文件存在且无语法错误。"
        ) from e

    # 尝试工厂函数
    factory_func = getattr(module, "create_memory", None)
    if factory_func and callable(factory_func):
        instance = factory_func(config)
        _inject_llm_fn_if_needed(instance, backend, config)
        return instance

    # 尝试直接类实例化
    for cls_name in ("MemoryBackend", "MemoryManager", "Memory1Backend", "Memory2Backend"):
        cls = getattr(module, cls_name, None)
        if cls is not None:
            try:
                instance = cls(config=config)
            except TypeError:
                try:
                    instance = cls()
                except Exception:
                    continue
            _inject_llm_fn_if_needed(instance, backend, config)
            return instance

    raise ImportError(
        f"记忆后端 '{backend}' 模块中未找到可用的工厂函数或构造类。"
        f"模块应提供 create_memory(config) 函数或 MemoryBackend 类。"
    )


def _inject_llm_fn_if_needed(instance: Any, backend: str, config: Any) -> None:
    """为 fallback 路径创建的实例注入 llm_chat_fn。

    适配器内部自行从 model_key.yaml 构建 llm_chat_fn，
    fallback 路径（直接实例化后端类）缺少此步骤，
    导致 MemoryRetriever 的 LLM 函数为 None。
    """
    if not hasattr(instance, "set_llm_chat_fn"):
        return

    # 复用 Memory2Adapter._build_models 的 LLM 构建逻辑
    try:
        from src.memory.memory_2.adapter import Memory2Adapter
        chat_fn = Memory2Adapter._build_models(config)
        if chat_fn is not None:
            instance.set_llm_chat_fn(chat_fn)
            logger.info(f"fallback 路径: 已为 {backend} 注入 llm_chat_fn")
        else:
            logger.warning(f"fallback 路径: {backend} 未能构建 llm_chat_fn（检查 model_key.yaml 的 memory_2 节）")
    except Exception as e:
        logger.warning(f"fallback 路径: 注入 llm_chat_fn 失败: {e}")


def _check_directory_exists(backend: str, module_path: str) -> None:
    """检查后端模块是否可以导入，给出友好报错。"""
    import importlib.util
    spec = importlib.util.find_spec(module_path)
    if spec is None:
        raise ImportError(
            f"记忆后端 '{backend}' 的模块不可导入: {module_path}。"
            f"请确认 {backend} 实现已部署或修改 config.yaml 中的 memory.backend 配置。"
        )