import subprocess


# LLM 可能输出占位符作为命令，必须检测并拒绝
_INVALID_COMMAND_TOKENS = ["命令", "shell 命令", "<bash>", "bash"]


def tool_bash(command: str) -> str:
    """执行 shell 命令"""
    stripped = command.strip()
    if not stripped:
        return "[BLOCKED] 无效命令：空命令。请提供实际的 shell 命令。"
    for token in _INVALID_COMMAND_TOKENS:
        if token in stripped:
            return f"[BLOCKED] 无效命令：'{command}'。请提供真实的 shell 命令，例如 dir、echo 等。"

    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code {result.returncode}]"
        return output or "（命令执行完毕，无输出）"
    except Exception as e:
        return f"执行错误：{e}"
