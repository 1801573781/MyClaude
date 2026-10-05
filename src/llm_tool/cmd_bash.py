import re
import subprocess

# ANSI/VT 转义序列清理：
# 子进程（npm/git/node 等）输出常含 \x1b[?25l（隐藏光标）、\x1b[?7l（关闭自动
# 换行 DECAWM）、\x1b[2K\r（行擦除）等控制码。若不清理，会随工具结果打印到终端，
# 篡改 VT 状态机（DECAWM/DECSTBM/光标可见性），导致后续输出与用户输入在行尾
# 不自动换行、回行首覆盖已有内容（间歇性复现：仅当 bash 输出恰好携带控制码时触发）。
# 在源头剥离后，LLM 上下文与 CLI 显示均不受污染。
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]")          # CSI 序列（含 private）
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")  # OSC 序列（如改窗口标题）
_ANSI_ESC_RE = re.compile(r"\x1b[@-Z\\-_]")                       # 单字符转义序列


def _strip_ansi(text: str) -> str:
    """移除子进程输出中的 ANSI/VT 转义序列，返回纯文本。

    同时清理裸 \r（回车字符）：终端将其解释为"光标回行首"，
    残留在输出中会导致后续字符覆盖行首内容。保留 \r\n 正常换行。
    """
    if not text:
        return text
    text = _ANSI_CSI_RE.sub("", text)
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_ESC_RE.sub("", text)
    # 先保护 \r\n，再剥离裸 \r，最后还原
    text = text.replace("\r\n", "\x00RN\x00")
    text = text.replace("\r", "")
    return text.replace("\x00RN\x00", "\r\n")


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
        # 剥离 ANSI/VT 转义序列，防止子进程控制码污染终端状态
        return _strip_ansi(output) or "（命令执行完毕，无输出）"
    except Exception as e:
        return f"执行错误：{e}"
