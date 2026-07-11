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

    def destroy(self):
        """销毁沙箱实例。Docker 模式下移除容器，本地模式下 no-op。"""
        if self._is_docker and self._container_id:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", self._container_id],
                    capture_output=True, timeout=10,
                )
                logger.info("Destroyed sandbox container %s", self._container_id)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("Failed to destroy container: %s", exc)
            self._container_id = None
            self._is_docker = False

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

    def run_myclaude_command_with_test_output(
            self,
            user_prompt: str,
            myclaude_root: str | None = None,
    ) -> tuple[str, str, int, dict | None]:
        """运行 MyClaude 测试指令，并获取结构化 JSON 测试结果。
        
        在原有 stdout/stderr/exit_code 基础上，额外返回 mycli.py run_test_mode()
        输出的结构化 JSON 数据（含 tool_calls、key_outputs 等字段）。
        
        Args:
            user_prompt: 测试指令
            myclaude_root: MyClaude 源码根目录
            
        Returns:
            (stdout, stderr, exit_code, test_data_dict)
            test_data_dict 为解析后的 JSON 字典，解析失败则为 None
        """
        import json
        import tempfile
        from pathlib import Path
        
        # 创建临时 JSON 输出文件
        tmp_dir = Path(tempfile.gettempdir()) / "myclaude_test_output"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = tmp_dir / f"test_{hash(user_prompt) & 0x7FFFFFFF:08x}.json"
        
        if self._is_docker and self._container_id:
            # 容器内对应的路径
            container_tmp_file = f"/tmp/myclaude_test_output/{tmp_file.name}"
            stdout, stderr, exit_code = self._run_in_docker_with_test_output(
                user_prompt, container_tmp_file
            )
        else:
            stdout, stderr, exit_code = self._run_locally_with_test_output(
                user_prompt, str(tmp_file), myclaude_root
            )
        
        # 读取并解析 JSON 测试结果
        test_data = None
        try:
            if tmp_file.exists():
                with open(tmp_file, 'r', encoding='utf-8') as f:
                    test_data = json.load(f)
                tmp_file.unlink()  # 清理临时文件
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read test output JSON: %s", e)
        
        return stdout, stderr, exit_code, test_data

    # ------------------------------------------------------------------
    # 私有方法（带 test_output）
    # ------------------------------------------------------------------

    def _run_in_docker_with_test_output(
            self, user_prompt: str, test_output_path: str
    ) -> tuple[str, str, int]:
        """在已有容器内执行命令，生成结构化测试 JSON"""
        cmd = [
            "docker", "exec", self._container_id,
            "python", "-m", "src.myclaude",
            "--test-mode",
            "--prompt", user_prompt,
            "--test-output", test_output_path,
        ]
        logger.info("Launching MyClaude [docker test-mode]: prompt=%r", user_prompt[:80])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=CONTAINER_TIMEOUT)
            logger.info("MyClaude subprocess finished: exit_code=%d, stdout_len=%d",
                        proc.returncode, len(proc.stdout or ""))
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            logger.error("Docker exec timed out after %ds", CONTAINER_TIMEOUT)
            return "", f"Timeout after {CONTAINER_TIMEOUT}s", -1

    # ------------------------------------------------------------------

    @staticmethod
    def _run_locally_with_test_output(
            user_prompt: str, test_output_path: str,
            myclaude_root: Optional[str] = None
    ) -> tuple[str, str, int]:
        """降级模式：本地执行，生成结构化测试 JSON"""
        import sys as _sys
        root = myclaude_root or os.getcwd()
        cmd = [
            _sys.executable, "-m", "src.myclaude",
            "--test-mode",
            "--prompt", user_prompt,
            "--test-output", test_output_path,
        ]
        logger.info("Launching MyClaude [local test-mode]: prompt=%r", user_prompt[:80])
        logger.info("Subprocess cmd: %s, cwd=%s", cmd, root)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=CONTAINER_TIMEOUT, cwd=root)
            logger.info("MyClaude subprocess finished: exit_code=%d, stdout_len=%d, stderr_len=%d",
                        proc.returncode, len(proc.stdout or ""), len(proc.stderr or ""))
            if proc.returncode != 0:
                logger.warning("Subprocess non-zero exit, stderr: %s", (proc.stderr or "")[:500])
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            logger.error("Local run timed out after %ds", CONTAINER_TIMEOUT)
            stdout = e.stdout or ""
            stderr = e.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode('utf-8', errors='replace')
            if isinstance(stderr, bytes):
                stderr = stderr.decode('utf-8', errors='replace')
            timeout_msg = f"Timeout after {CONTAINER_TIMEOUT}s"
            stderr = f"{timeout_msg}\n{stderr}" if stderr else timeout_msg
            return stdout, stderr, -1
        except Exception as e:
            logger.error("Failed to launch subprocess: %s", e)
            return "", f"Subprocess launch error: {type(e).__name__}: {e}", -1

    # ------------------------------------------------------------------

    def _run_in_docker(self, user_prompt: str) -> tuple[str, str, int]:
        """在已有容器内执行命令"""
        cmd = [
            "docker", "exec", self._container_id,
            "python", "-m", "src.myclaude",
            "--test-mode",
            "--prompt", user_prompt,
        ]
        logger.info("Launching MyClaude [docker test-mode]: prompt=%r", user_prompt[:80])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=CONTAINER_TIMEOUT)
            logger.info("MyClaude subprocess finished: exit_code=%d, stdout_len=%d",
                        proc.returncode, len(proc.stdout or ""))
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
        logger.info("Launching MyClaude [local test-mode]: prompt=%r", user_prompt[:80])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=CONTAINER_TIMEOUT, cwd=root)
            logger.info("MyClaude subprocess finished: exit_code=%d, stdout_len=%d",
                        proc.returncode, len(proc.stdout or ""))
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            logger.error("Local run timed out after %ds", CONTAINER_TIMEOUT)
            return "", f"Timeout after {CONTAINER_TIMEOUT}s", -1


class SandboxManager:
    """沙箱生命周期管理器

    支持管理多个并行容器，防止容器泄漏。
    """

    def __init__(self):
        self._containers: set[str] = set()
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

    def release(self, sandbox: Optional[Sandbox] = None):
        """释放沙箱。传入 sandbox 则释放指定实例，不传则释放全部。"""
        if sandbox is not None:
            sandbox.destroy()
            if sandbox._container_id and sandbox._container_id in self._containers:
                self._containers.discard(sandbox._container_id)
            return

        # 释放全部容器
        for cid in list(self._containers):
            try:
                subprocess.run(
                    ["docker", "rm", "-f", cid],
                    capture_output=True, timeout=10,
                )
                logger.info("Released sandbox container %s", cid)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("Failed to release container %s: %s", cid, exc)
        self._containers.clear()

    # ------------------------------------------------------------------

    def release_all(self):
        """释放所有沙箱容器"""
        self.release()

    # ------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        """当前活跃容器数"""
        return len(self._containers)

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
        import tempfile
        from pathlib import Path
        
        root = myclaude_root or os.getcwd()
        
        # 宿主机临时目录，用于存放测试输出 JSON
        host_tmp_dir = Path(tempfile.gettempdir()) / "myclaude_test_output"
        host_tmp_dir.mkdir(parents=True, exist_ok=True)
        
        mounts = [
            ("-v", f"{root}/src:/app/src:rw"),  # noqa: E231
            ("-v", f"{root}/config:/app/config:rw"),  # noqa: E231
            ("-v", f"{root}/code_output:/app/code_output:rw"),  # noqa: E231
            ("-v", f"{root}/log:/app/log:rw"),  # noqa: E231
            ("-v", f"{host_tmp_dir}:/tmp/myclaude_test_output:rw"),  # noqa: E231
        ]

        env_vars = [
            ("-e", "MYCLAUDE_TEST_MODE=true"),
        ]

        # 传递所有已知的 API Key 环境变量
        api_key_envs = [
            "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "MINIMAX_API_KEY",
            "API_KEY", "MODEL_API_KEY",
        ]
        for env_name in api_key_envs:
            val = os.environ.get(env_name)
            if val:
                env_vars.append(("-e", f"{env_name}={val}"))

        # 从 config 加载 API Key 并注入（作为后备）
        try:
            from src.utility.config_loader import global_cfg
            model_provider = global_cfg.model.provider
            provider_cfg = getattr(global_cfg, model_provider)
            if hasattr(provider_cfg, "api_key") and provider_cfg.api_key:
                env_vars.append(("-e", f"DEEPSEEK_API_KEY={provider_cfg.api_key}"))
            if hasattr(provider_cfg, "base_url") and provider_cfg.base_url:
                env_vars.append(("-e", f"OPENAI_BASE_URL={provider_cfg.base_url}"))
            if hasattr(global_cfg, "model_key") and hasattr(global_cfg.model_key, "embedding"):
                emb = global_cfg.model_key.embedding
                if hasattr(emb, "api_key") and emb.api_key:
                    env_vars.append(("-e", f"EMBEDDING_API_KEY={emb.api_key}"))
        except Exception:
            pass

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
        self._containers.add(container_id)
        logger.info("Sandbox container started: %s", container_id)

        # 安装项目依赖（pip install）
        try:
            subprocess.run(
                ["docker", "exec", container_id, "pip", "install",
                 "openai", "rich", "pyyaml", "numpy"],
                capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Failed to install deps in container: %s", exc)

        # 等待容器就绪
        time.sleep(2)
        return Sandbox(container_id=container_id)
