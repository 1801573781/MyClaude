"""Docker 沙箱隔离执行模块

在一次性 Docker 容器中安全执行 Python 代码，执行完毕后自动销毁容器。
"""

import subprocess
import tempfile
import os
import logging
from typing import List, Dict, Any

logger = logging.getLogger("mytest.sandbox")

# 沙箱默认配置
SANDBOX_CPU_LIMIT = "1.0"
SANDBOX_MEMORY_MB = 512
SANDBOX_TIMEOUT_SEC = 30
DOCKER_IMAGE = "python:3.12-slim"


def execute_in_sandbox(
    code: str,
    test_inputs: List[Dict[str, str]],
    timeout_sec: int = SANDBOX_TIMEOUT_SEC,
) -> Dict[str, Any]:
    """在 Docker 沙箱中执行代码并收集各测试用例的输出

    Args:
        code: 待执行的 Python 源代码
        test_inputs: 测试用例列表，每项含 test_id、input、expected
        timeout_sec: 容器最长存活时间（秒）

    Returns:
        {"results": [{"test_id": ..., "output": ..., "error": ...}], "exit_code": ...}
    """
    with tempfile.TemporaryDirectory(prefix="sandbox_") as tmpdir:
        # 写入待测代码
        code_path = os.path.join(tmpdir, "code_under_test.py")
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(code)

        # 写入测试运行器
        runner_path = os.path.join(tmpdir, "test_runner.py")
        runner_code = _build_runner_code(test_inputs)
        with open(runner_path, "w", encoding="utf-8") as f:
            f.write(runner_code)

        # 尝试 Docker 执行，失败则降级为子进程
        try:
            return _run_in_docker(tmpdir, timeout_sec)
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.warning(f"Docker 执行失败，降级为子进程执行: {e}")
            return _run_in_subprocess(tmpdir, timeout_sec)


def _build_runner_code(test_inputs: List[Dict[str, str]]) -> str:
    """构建测试运行器代码，逐个执行测试用例并输出 JSON 结果"""
    import json

    lines = [
        "import sys",
        "import json",
        "import traceback",
        "",
        "try:",
        "    from code_under_test import *",
        "except Exception as e:",
        "    print(json.dumps({'error': f'ImportError: {e}'}))",
        "    sys.exit(1)",
        "",
        "results = []",
        "",
    ]

    for ti in test_inputs:
        tid = ti["test_id"]
        inp = ti["input"]
        lines.append(f"# Test: {tid}")
        lines.append("try:")
        lines.append(f"    _result = {inp}")
        lines.append(f"    results.append({{'test_id': '{tid}', 'output': str(_result)}})")
        lines.append("except Exception as e:")
        lines.append(f"    results.append({{'test_id': '{tid}', 'error': str(e) + '\\n' + traceback.format_exc()}})")

    lines.extend([
        "",
        "print(json.dumps(results, ensure_ascii=False))",
    ])

    return "\n".join(lines)


def _run_in_docker(tmpdir: str, timeout_sec: int) -> Dict[str, Any]:
    """在 Docker 容器中执行代码（安全隔离）"""
    abs_tmpdir = os.path.abspath(tmpdir)

    cmd = [
        "docker", "run", "--rm",
        "--cpus", SANDBOX_CPU_LIMIT,
        "--memory", f"{SANDBOX_MEMORY_MB}m",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=100M",
        "-v", f"{abs_tmpdir}:/app:ro",
        "-w", "/app",
        DOCKER_IMAGE,
        "python", "test_runner.py",
    ]

    logger.debug(f"Docker 命令: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec + 5,
        )
    except subprocess.TimeoutExpired:
        return {
            "results": [{"test_id": "__global__", "error": "容器执行超时"}],
            "exit_code": -1,
        }

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if stderr and not stdout:
        return {
            "results": [{"test_id": "__global__", "error": stderr}],
            "exit_code": result.returncode,
        }

    import json
    try:
        results = json.loads(stdout)
        return {"results": results, "exit_code": result.returncode}
    except json.JSONDecodeError:
        return {
            "results": [{"test_id": "__global__", "error": f"无法解析输出: {stdout[:500]}"}],
            "exit_code": result.returncode,
        }


def _run_in_subprocess(tmpdir: str, timeout_sec: int) -> Dict[str, Any]:
    """子进程降级执行（无 Docker 时的兜底方案）"""
    import json

    runner_path = os.path.join(tmpdir, "test_runner.py")

    try:
        result = subprocess.run(
            ["python", runner_path],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=tmpdir,
        )
    except subprocess.TimeoutExpired:
        return {
            "results": [{"test_id": "__global__", "error": "子进程执行超时"}],
            "exit_code": -1,
        }

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if stderr and not stdout:
        return {
            "results": [{"test_id": "__global__", "error": stderr}],
            "exit_code": result.returncode,
        }

    try:
        results = json.loads(stdout)
        return {"results": results, "exit_code": result.returncode}
    except json.JSONDecodeError:
        return {
            "results": [{"test_id": "__global__", "error": f"无法解析输出: {stdout[:500]}"}],
            "exit_code": result.returncode,
        }