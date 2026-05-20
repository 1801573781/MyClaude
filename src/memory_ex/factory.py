"""
Memory 工厂函数：根据配置自动选择 memory5 / memory6 等后端。

所有 memory_ex 下的模块（memory5、memory6、...）都由此工厂统一创建，
query_loop 只需导入这一个函数。
"""

from pathlib import Path
from typing import Optional
from src.memory_ex.memory_interface import MemoryBackend


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