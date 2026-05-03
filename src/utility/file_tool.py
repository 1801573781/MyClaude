from pathlib import Path
from utility.config_loader import global_cfg


'''
def add_root_path(path: str) -> str:
    """
        解析 LLM 传来的路径：
        - 绝对路径 → 直接使用
        - 相对路径 → 拼接到 code_output 下
    """
    p = Path(path)

    # 如果是绝对路径，直接返回
    if p.is_absolute():
        return path

    # 否则，加上code_output_root
    return str(Path(global_cfg.code_project.code_output_root) / p)
'''


def add_root_path(root: str, path: str) -> str:
    """
        解析 LLM 传来的路径：
        - 绝对路径 → 直接使用
        - 相对路径 → 拼接到 code_output 下
    """
    p = Path(path)

    # 如果是绝对路径，直接返回
    if p.is_absolute():
        return path

    # 否则，加上code_output_root
    return str(Path(root) / p)


def file_view(root: str, path: str) -> str:
    full_path = add_root_path(root, path)

    """查看文件或目录"""
    p = Path(full_path)
    if not p.exists():
        return f"错误：路径不存在 {full_path}"
    if p.is_dir():
        items = []
        for f in p.iterdir():
            prefix = "[DIR]" if f.is_dir() else "[FILE]"
            items.append(f"{prefix} {f.name}")
        return "\n".join(items) if items else "（空目录）"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"读取错误：{e}"


def file_create(root: str, path: str, content: str) -> str:
    full_path = add_root_path(root, path)
    try:
        p = Path(full_path)

        # 新增：如果文件已存在且内容非空，拒绝覆盖，提示用 str_replace
        if p.exists() and p.stat().st_size > 0:
            existing_len = len(p.read_text(encoding="utf-8"))  # 这样可能有点慢，不过不纠结，先这样吧
            return (
                f"警告：文件{path}已存在，当前内容 {existing_len} 字符。"
                f"如需修改请使用 <str_replace>，如需查看请使用 <view>。"
                f"严禁重复 <create> 同一文件。"
            )

        """创建新文件"""
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已创建 {full_path} ({len(content)} 字符)"
    except Exception as e:
        return f"创建失败：{e}"


def file_append(root: str, path: str, content: str):
    """
    如果文件不存在则创建，存在则在尾部追加内容。
    'a' = append 模式，文件不存在会自动创建
    """
    full_path = add_root_path(root, path)

    with open(full_path, "a", encoding="utf-8") as f:
        f.write(content)
        # 每次追加后自动换行
        f.write("\n")


def file_str_replace(root: str, path: str, old: str, new: str) -> str:
    full_path = add_root_path(root, path)

    """精确替换文件内容"""
    try:
        p = Path(full_path)
        if not p.exists():
            return f"错误：文件不存在 {full_path}"
        text = p.read_text(encoding="utf-8")
        if old not in text:
            return f"错误：未找到精确匹配片段，请重新查看文件内容。\n---待匹配片段---\n{old}\n---"
        text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8")
        return f"已修改 {full_path}"
    except Exception as e:
        return f"修改失败：{e}"
