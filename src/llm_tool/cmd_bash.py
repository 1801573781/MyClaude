import subprocess


def tool_bash(command: str) -> str:
    """执行 shell 命令"""
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
