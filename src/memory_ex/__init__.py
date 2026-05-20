"""memory_ex 包 —— 统一 Memory 扩展模块

对外暴露：
- MemoryBackend：抽象接口基类
- create_memory()：工厂函数，根据配置动态创建 Memory 实例
"""

from .memory_interface import MemoryBackend


def create_memory(config: dict) -> MemoryBackend:
    """工厂函数，根据配置动态创建 Memory 实例

    Args:
        config: 完整配置字典（如 config_loader 返回的全局配置）
                需包含 memory.active_module 字段，
                可选 memory.config_dir 指定模块配置文件目录

    Returns:
        MemoryBackend 实例（memory5 或 memory6 等）

    Raises:
        ValueError: 配置缺失或 active_module 无效
        ImportError: 对应模块无法导入
        FileNotFoundError: 模块配置文件不存在
    """
    import os
    import yaml
    import importlib
    from pathlib import Path

    # ---------- 1. 提取 memory 配置 ----------
    memory_cfg = config.get("memory")
    if memory_cfg is None:
        raise ValueError(
            "配置中缺少 'memory' 节点，请在 config.yaml 中新增：\n"
            "memory:\n"
            "  active_module: \"memory5\"\n"
            "  config_dir: \"config/memory\""
        )

    active_module = memory_cfg.get("active_module")
    if not active_module:
        raise ValueError(
            "未指定 memory.active_module，"
            "有效值：'memory5' / 'memory6'"
        )
    if active_module not in ("memory5", "memory6"):
        raise ValueError(
            f"不支持的 memory 模块 '{active_module}'，"
            f"当前仅支持：memory5 / memory6"
        )

    # ---------- 2. 定位模块配置文件 ----------
    config_dir = memory_cfg.get("config_dir", "config/memory")
    module_config_path = Path(config_dir) / f"{active_module}.yaml"

    if not module_config_path.exists():
        raise FileNotFoundError(
            f"模块配置文件不存在：{module_config_path}\n"
            f"请在 config/memory/ 目录下创建 {active_module}.yaml"
        )

    with open(module_config_path, "r", encoding="utf-8") as f:
        module_config = yaml.safe_load(f) or {}

    # ---------- 3. 动态导入并实例化 ----------
    try:
        module_pkg = importlib.import_module(
            f".{active_module}.{active_module}",
            package="src.memory_ex"
        )
    except ImportError as e:
        raise ImportError(
            f"无法导入模块 'memory_ex.{active_module}.{active_module}'：{e}"
        ) from e

    # 约定类名 = 模块名首字母大写
    class_name = active_module.capitalize()  # "Memory5" / "Memory6"
    cls = getattr(module_pkg, class_name, None)
    if cls is None:
        raise AttributeError(
            f"模块 'memory_ex.{active_module}.{active_module}' "
            f"未导出类 '{class_name}'"
        )

    return cls(module_config)