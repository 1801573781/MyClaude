"""
Docker 沙箱代码执行器
在隔离容器中执行 Python 代码，并返回结构化结果
"""
import logging
import tempfile
import time
from pathlib import Path

import requests

try:
    import docker
    from docker import from_env
    from docker.errors import APIError, DockerException
    DOCKER_AVAILABLE = True
except ImportError:
    # 哑元异常类，仅供类型检查使用，永远不会在运行时被捕获
    class APIError(Exception):
        pass

    class DockerException(Exception):
        pass

    docker = None
    from_env = None
    DOCKER_AVAILABLE = False

from src.A2A.shared.config import get_config

logger = logging.getLogger("mytest.sandbox")


class SandboxResult:
    """沙箱执行结果"""
    def __init__(self, stdout: str = "", stderr: str = "",
                 exit_code: int = -1, timed_out: bool = False,
                 execution_time_ms: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.execution_time_ms = execution_time_ms


class DockerSandbox:
    """Docker 容器沙箱"""

    IMAGE_NAME = "python:3.12-slim"
    CPU_LIMIT = 1.0
    MEMORY_LIMIT = "512m"
    TIMEOUT_SECONDS = 30

    def __init__(self):
        self.config = get_config()
        self._client = None

    @property
    def client(self):
        if self._client is None and from_env is not None:
            try:
                self._client = from_env()
            except Exception as e:
                logger.warning(f"无法连接 Docker: {e}，将使用子进程回退模式")
                self._client = None
        return self._client

    def execute(self, code: str) -> SandboxResult:
        """在沙箱中执行代码"""
        if self.client:
            return self._execute_docker(code)
        else:
            return self._execute_subprocess(code)

    def _execute_docker(self, code: str) -> SandboxResult:
        """在 Docker 容器中执行"""
        start_time = time.time()
        timeout = self.config.test_exec_timeout_sec or self.TIMEOUT_SECONDS

        stdout = ""
        stderr = ""
        exit_code = -1
        timed_out = False

        # 将代码写入临时文件
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix="sandbox_", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            container = self.client.containers.run(
                self.IMAGE_NAME,
                command=["python", "-u", f"/app/{Path(tmp_path).name}"],
                volumes={str(Path(tmp_path).parent): {"bind": "/app", "mode": "ro"}},
                working_dir="/app",
                cpu_period=100000,
                cpu_quota=int(self.config.sandbox_cpu_limit * 100000),
                mem_limit=f"{self.config.sandbox_memory_mb}m",
                network_mode="none",
                detach=True,
                remove=True,
            )

            try:
                result = container.wait(timeout=timeout)
                exit_code = result.get("StatusCode", -1)
                stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
                timed_out = False
            except requests.exceptions.ReadTimeout:
                # 等待超时
                try:
                    container.kill()
                except APIError:
                    pass
                exit_code = -1
                stdout = ""
                stderr = "执行超时（超过 {} 秒）".format(timeout)
                timed_out = True
            except APIError:
                # Docker API 调用异常
                try:
                    container.kill()
                except APIError:
                    pass
                exit_code = -1
                stdout = ""
                stderr = "Docker API 异常"
                timed_out = True

        except DockerException as e:
            stdout = ""
            stderr = f"Docker 执行异常: {e}"
            exit_code = -1
            timed_out = False
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

        elapsed_ms = int((time.time() - start_time) * 1000)
        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            execution_time_ms=elapsed_ms,
        )

    def _execute_subprocess(self, code: str) -> SandboxResult:
        """回退方案：在子进程中执行（非完全隔离，仅开发/测试用）"""
        import subprocess

        start_time = time.time()
        timeout = self.config.test_exec_timeout_sec or self.TIMEOUT_SECONDS

        stdout = ""
        stderr = ""
        exit_code = -1
        timed_out = False

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix="sandbox_", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            proc = subprocess.run(
                ["python", tmp_path],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            exit_code = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            stdout = ""
            stderr = "执行超时（超过 {} 秒）".format(timeout)
            exit_code = -1
            timed_out = True
        except (subprocess.SubprocessError, OSError) as e:
            stdout = ""
            stderr = f"执行异常: {e}"
            exit_code = -1
            timed_out = False
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

        elapsed_ms = int((time.time() - start_time) * 1000)
        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            execution_time_ms=elapsed_ms,
        )
