from pathlib import Path
from src.utility.config_loader import global_cfg


def add_root_path(root: str, path: str) -> str:
    """
        解析 LLM 传来的路径：
        - 绝对路径 → 直接使用
        - 相对路径 → 拼接到 root 下
    """
    p = Path(path)

    # 如果是绝对路径，直接返回
    if p.is_absolute():
        return path

    # 否则，加上code_output_root
    return str(Path(root) / p)


def file_view(root: str, path: str, limit: int = None, offset: int = None) -> str:
    full_path = add_root_path(root, path)

    """查看文件或目录，支持 limit（最多读取行数）和 offset（从第N行开始，1-based）"""
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
        lines = p.read_text(encoding="utf-8").splitlines()
        # offset: 从第几行开始（1-based，默认从第1行）
        start = 0 if offset is None else max(0, offset - 1)

        # limit: 最多读取行数
        end = len(lines) if limit is None else start + limit

        # 防止越界
        start = min(start, len(lines))
        end = min(end, len(lines))

        return "\n".join(lines[start:end])
    except OSError as e:
        return f"读取错误：{e}"


def file_create(root: str, path: str, content: str) -> str:
    full_path = add_root_path(root, path)
    try:
        p = Path(full_path)

        # 如果文件已存在且内容非空，拒绝覆盖，强制要求改用 str_replace
        if p.exists() and p.stat().st_size > 0:
            existing_len = len(p.read_text(encoding="utf-8"))
            return (
                f"[BLOCKED] 文件已存在：{path}（{existing_len} 字符）。\n"
                f"下一步：\n"
                f"1. 调用 <file_view path=\"{full_path}\"/> 查看现有内容\n"
                f"2. 复制原文作为 <old>，用 <str_replace> 修改\n"
                f"严禁再次 <create>，严禁直接 <done>。"
            )

        """创建新文件"""
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已创建 {full_path} ({len(content)} 字符)"
    except OSError as e:
        return f"[ERROR] 创建失败：{e}"


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
            return f"[BLOCKED] 错误：文件不存在 {full_path}。请先用 <create> 创建文件。"
        text = p.read_text(encoding="utf-8")
        if old not in text:
            return f"[BLOCKED] 错误：未找到精确匹配片段，请重新查看文件内容。\n---待匹配片段---\n{old}\n---"
        text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8")
        return f"已修改 {full_path}"
    except OSError as e:
        return f"[ERROR] 修改失败：{e}"
