import subprocess


# LLM 可能输出占位符作为命令，必须检测并拒绝
_INVALID_COMMAND_TOKENS = ["命令", "shell 命令", "<bash>", "bash"]
# 严禁用外部命令搜索文件内容（findstr 受代码页影响，PowerShell 受执行策略影响），文件搜索应走 <file_view>
_FILE_SEARCH_BLOCKLIST = [
    "findstr", "Select-String", "grep", "rg ", "ag ",
    "find ", "awk ", "sed ",
]


def tool_bash(command: str) -> str:
    """执行 shell 命令"""
    stripped = command.strip()
    if not stripped:
        return "[BLOCKED] 无效命令：空命令。请提供实际的 shell 命令。"
    for token in _INVALID_COMMAND_TOKENS:
        if token in stripped:
            return f"[BLOCKED] 无效命令：'{command}'。请提供真实的 shell 命令，例如 dir、echo 等。"

    # 拦截文件内容搜索命令（findstr 受代码页影响，PowerShell 受执行策略影响）
    cmd_lower = stripped.lower()
    for blocked in _FILE_SEARCH_BLOCKLIST:
        if blocked.lower() in cmd_lower:
            return (
                f"[BLOCKED] 禁止用外部命令搜索文件内容：'{command}'。\n"
                f"原因：findstr/Select-String/grep 等命令受 Windows 代码页和 PowerShell 执行策略影响，\n"
                f"对 UTF-8 文件极易因 GBK 解码失败而崩溃。\n"
                f"正确做法：使用 <file_view> 读取文件后在上下文中分析，不要依赖外部命令行工具进行内容搜索。"
            )

    try:
        # 先切换 CMD 代码页为 UTF-8（65001），避免中文输出乱码
        full_command = f"chcp 65001 >nul 2>&1 && {command}"
        result = subprocess.run(
            full_command, shell=True, capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace"
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code {result.returncode}]"
        return output or "（命令执行完毕，无输出）"
    except Exception as e:
        return f"执行错误：{e}"
