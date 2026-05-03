import yaml
from pathlib import Path
from types import SimpleNamespace


def _dict_to_namespace(obj):
    """递归把字典转成 SimpleNamespace，支持点号访问"""
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _dict_to_namespace(v) for k, v in obj.items()})
    elif isinstance(obj, list):
        return [_dict_to_namespace(i) for i in obj]
    return obj


def find_project_root(start: Path, marker: str = "config.yaml") -> Path:
    """
    从 start 目录出发，逐级向上查找包含 marker 文件/目录的目录。
    优先按"包含 config.yaml"来识别项目根目录，更鲁棒。
    """
    current = start.resolve()

    # 遍历当前目录及其所有上级目录
    for path in [current] + list(current.parents):
        if (path / marker).exists():
            return path

    # 如果按文件找不到，可以按目录名兜底（比如你固定叫 MyClaude）
    for path in [current] + list(current.parents):
        if path.name == "MyClaude":
            return path

    raise FileNotFoundError(
        f"从 {start} 向上查找到根目录，也未找到包含 {marker} 或目录名为 MyClaude 的项目根目录"
    )


def load_config(filename="config.yaml"):
    """
    读取 YAML 配置文件，返回支持点号访问的命名空间对象。
    配置文件默认放在项目根目录（入口脚本的上级目录）。
    """
    # 从本文件所在目录开始找
    start_dir = Path(__file__).resolve().parent
    base_dir = find_project_root(start_dir, filename)
    config_path = base_dir / filename

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return _dict_to_namespace(raw)


"""
其他代码，如果有多个地方 import config_loader，global_cfg = load_config()会被执行多次吗？
答案是：不会！

Python 内部流程是：
1. 第一次 import：找到 config_loader.py，从头到尾执行一遍（包括 global_cfg = load_config()），然后把生成的模块对象塞进 sys.modules 缓存。
2. 第二次及以后 import：直接从 sys.modules 里取出已存在的模块对象，一行代码都不会再执行。
所以 global_cfg = load_config() 只会执行一次，之后任何地方 import config_loader 拿到的都是同一个对象。
"""
# 全局变量，很多地方都需要配置文件中的配置信息
global_cfg = load_config()

# ========== 使用示例 ==========

if __name__ == "__main__":
    # 现在可以用点号访问了
    print(global_cfg.model.api_key)
    print(global_cfg.model.model_name)
    print(global_cfg.memory.root_dir)
    print(global_cfg.paths.code_output)
    print(global_cfg.cli.max_turns)
    print(global_cfg.cli.show_thinking)
