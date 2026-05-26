"""
Docker 沙箱管理

提供 MyClaude 隔离运行环境：容器创建、命令执行、销毁。
若 Docker 不可用，自动降级为直接子进程调用（隔离性降低）。
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Docker 基础镜像
BASE_IMAGE = "python:3.12-slim"
CONTAINER_TIMEOUT = 300  # 秒


class Sandbox:
    """单个沙箱实例，封装一个 Docker 容器或子进程上下文"""

    def __init__(self, container_id: Optional[str] = None):
        self._container_id = container_id
        self._is_docker = container_id is not None

    # ------------------------------------------------------------------

    def run_myclaude_command(self,
                             user_prompt: str,
                             myclaude_root: Optional[str] = None) -> tuple[str, str, int]:
        """在沙箱中运行一条 MyClaude 指令，返回 (stdout, stderr, exit_code)"""
        if self._is_docker and self._container_id:
            return self._run_in_docker(user_prompt)
        else:
            return self._run_locally(user_prompt, myclaude_root)

    # ------------------------------------------------------------------

    def destroy(self):
        """销毁沙箱"""
        if self._is_docker and self._container_id:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", self._container_id],
                    capture_output=True,
                    timeout=10,
                )
                logger.info("Destroyed Docker container %s", self._container_id)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("Failed to destroy container %s: %s",
                               self._container_id, exc)

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _run_in_docker(self, user_prompt: str) -> tuple[str, str, int]:
        """在已有容器内执行命令"""
        cmd = [
            "docker", "exec", self._container_id,
            "python", "-m", "src.myclaude",
            "--test-mode",
            "--prompt", user_prompt,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=CONTAINER_TIMEOUT)
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            logger.error("Docker exec timed out after %ds", CONTAINER_TIMEOUT)
            return "", f"Timeout after {CONTAINER_TIMEOUT}s", -1

    # ------------------------------------------------------------------

    @staticmethod
    def _run_locally(user_prompt: str,
                     myclaude_root: Optional[str] = None) -> tuple[str, str, int]:
        """降级模式：直接在当前进程启动 MyClaude"""
        root = myclaude_root or os.getcwd()
        cmd = [
            "python", "-m", "src.myclaude",
            "--test-mode",
            "--prompt", user_prompt,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=CONTAINER_TIMEOUT, cwd=root)
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            logger.error("Local run timed out after %ds", CONTAINER_TIMEOUT)
            return "", f"Timeout after {CONTAINER_TIMEOUT}s", -1


class SandboxManager:
    """沙箱生命周期管理器"""

    def __init__(self):
        self._container: Optional[str] = None
        self._available: Optional[bool] = None  # None = 未检测

    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """检测 Docker 是否可用"""
        if self._available is None:
            self._available = self._check_docker()
        return self._available

    # ------------------------------------------------------------------

    def acquire(self, myclaude_root: Optional[str] = None) -> Sandbox:
        """获取一个沙箱实例"""
        if self.is_available():
            return self._create_docker_sandbox(myclaude_root)
        else:
            logger.warning("Docker unavailable, using local fallback (reduced isolation)")
            return Sandbox(container_id=None)

    # ------------------------------------------------------------------

    def release(self):
        """释放当前沙箱"""
        if self._container:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", self._container],
                    capture_output=True, timeout=10,
                )
                logger.info("Released sandbox container %s", self._container)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("Failed to release container: %s", exc)
            self._container = None

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    @staticmethod
    def _check_docker() -> bool:
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError, FileNotFoundError):
            return False

    # ------------------------------------------------------------------

    def _create_docker_sandbox(self,
                               myclaude_root: Optional[str] = None) -> Sandbox:
        """创建并启动一个 Docker 容器作为沙箱"""
        root = myclaude_root or os.getcwd()
        mounts = [
            ("-v", f"{root}/src:/app/src:ro"),  # noqa: E231
            ("-v", f"{root}/config:/app/config:ro"),  # noqa: E231
        ]
        env_vars = [
            ("-e", "MYCLAUDE_TEST_MODE=true"),
        ]

        cmd = ["docker", "run", "-d", "--rm",
               "--cpus=2", "--memory=2g",
               "-w", "/app"]
        for m in mounts:
            cmd.extend(m)
        for e in env_vars:
            cmd.extend(e)
        cmd.append(BASE_IMAGE)
        cmd.extend(["sleep", str(CONTAINER_TIMEOUT)])

        logger.info("Starting Docker sandbox...")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f"Docker run failed: {proc.stderr}")

        container_id = proc.stdout.strip()[:12]
        self._container = container_id
        logger.info("Sandbox container started: %s", container_id)

        # 等待容器就绪
        time.sleep(2)
        return Sandbox(container_id=container_id)
