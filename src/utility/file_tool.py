from pathlib import Path

# 读取文件时优先尝试的编码顺序
_FALLBACK_ENCODINGS = ["utf-8", "gbk", "gb2312", "latin-1"]


def _read_text_safe(file_path: Path) -> str:
    """尝试多种编码读取文件内容，避免因非 UTF-8 文件导致崩溃"""
    for enc in _FALLBACK_ENCODINGS:
        try:
            return file_path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise UnicodeDecodeError(
        "utf-8",
        b"",
        0,
        1,
        f"无法用任何已知编码读取文件：{file_path}"
    )


# LLM 可能输出占位符作为路径（如"绝对路径"、"命令"等），这些不是合法路径，必须拒绝
_INVALID_PATH_TOKENS = [
    "绝对路径", "项目代码目录", "项目需求目录", "{项目代码目录}",
    "{项目需求目录}", "{文件名}", "{子目录}", "文件路径",
    "命令", "shell 命令", "摘要", "旧代码", "新代码",
]


def _is_invalid_path(path: str) -> bool:
    """检测路径是否为 LLM 未替换的占位符或空字符串"""
    stripped = path.strip()
    if not stripped:
        return True
    for token in _INVALID_PATH_TOKENS:
        if token in stripped:
            return True
    return False


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
    if _is_invalid_path(path):
        return f"[BLOCKED] 无效路径：'{path}'。请使用真实的绝对路径，例如 D:\\AI\\MyClaude\\src\\..."  # noqa: E231

    full_path = add_root_path(root, path)

    """查看文件或目录，支持 limit（最多读取行数）和 offset（从第N行开始，1-based）"""
    p = Path(full_path)
    if not p.exists():
        return f"错误：路径不存在 {full_path}"
    if p.is_dir():
        items = []
        try:
            for f in p.iterdir():
                prefix = "[DIR]" if f.is_dir() else "[FILE]"
                items.append(f"{prefix} {f.name}")
        except OSError as e:
            return f"错误：无法读取目录 {full_path}（{e}）"
        return "\n".join(items) if items else "（空目录）"
    try:
        lines = _read_text_safe(p).splitlines()
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
    if _is_invalid_path(path):
        return f"[BLOCKED] 无效路径：'{path}'。请使用真实的绝对路径，例如 D:\\AI\\MyClaude\\src\\..."  # noqa: E231

    full_path = add_root_path(root, path)
    try:
        p = Path(full_path)

        # 如果文件已存在且内容非空，拒绝覆盖，强制要求改用 str_replace
        if p.exists() and p.stat().st_size > 0:
            existing_len = len(_read_text_safe(p))
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
    if _is_invalid_path(path):
        return f"[BLOCKED] 无效路径：'{path}'。请使用真实的绝对路径，例如 D:\\AI\\MyClaude\\src\\..."  # noqa: E231

    full_path = add_root_path(root, path)

    """精确替换文件内容"""
    try:
        p = Path(full_path)
        if not p.exists():
            return f"[BLOCKED] 错误：文件不存在 {full_path}。请先用 <create> 创建文件。"
        text = _read_text_safe(p)
        if old not in text:
            return f"[BLOCKED] 错误：未找到精确匹配片段，请重新查看文件内容。\n---待匹配片段---\n{old}\n---"
        text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8")
        return f"已修改 {full_path}"
    except OSError as e:
        return f"[ERROR] 修改失败：{e}"
