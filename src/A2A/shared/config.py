"""共享配置模块

从环境变量加载各项服务、路径、阈值等配置，生成全局配置对象。
"""

import os
from typing import Optional


class A2AConfig:
    """A2A 系统全局配置"""

    def __init__(self):
        # ---- 服务地址 ----
        self.mycode_url: str = os.getenv("MYCODE_URL", "http://localhost:8000")
        self.mytest_url: str = os.getenv("MYTEST_URL", "http://localhost:8001")
        self.myorch_url: str = os.getenv("MYORCH_URL", "http://localhost:8002")

        # ---- 认证 ----
        self.auth_token: Optional[str] = os.getenv("A2A_AUTH_TOKEN", None)

        # ---- Orchestrator 参数 ----
        self.max_rounds: int = int(os.getenv("MAX_ROUNDS", "10"))
        self.melt_down_window: int = int(os.getenv("MELT_DOWN_WINDOW", "3"))
        self.code_gen_timeout_sec: int = int(os.getenv("CODE_GEN_TIMEOUT_SEC", "10"))
        self.test_exec_timeout_sec: int = int(os.getenv("TEST_EXEC_TIMEOUT_SEC", "30"))

        # ---- 沙箱参数 ----
        self.sandbox_cpu_limit: str = os.getenv("SANDBOX_CPU_LIMIT", "1.0")
        self.sandbox_memory_mb: int = int(os.getenv("SANDBOX_MEMORY_MB", "512"))
        self.sandbox_timeout_sec: int = int(os.getenv("SANDBOX_TIMEOUT_SEC", "30"))

        # ---- 存储 ----
        self.context_store_path: str = os.getenv(
            "CONTEXT_STORE_PATH", "./data/tasks"
        )

        # ---- 日志 ----
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")

        # ---- LLM ----
        self.llm_model: str = os.getenv("LLM_MODEL", "deepseek-v3")
        self.llm_api_key: Optional[str] = os.getenv("LLM_API_KEY", None)

    def as_dict(self) -> dict:
        """导出为字典"""
        return {
            "mycode_url": self.mycode_url,
            "mytest_url": self.mytest_url,
            "myorch_url": self.myorch_url,
            "max_rounds": self.max_rounds,
            "melt_down_window": self.melt_down_window,
            "code_gen_timeout_sec": self.code_gen_timeout_sec,
            "test_exec_timeout_sec": self.test_exec_timeout_sec,
            "sandbox_cpu_limit": self.sandbox_cpu_limit,
            "sandbox_memory_mb": self.sandbox_memory_mb,
            "log_level": self.log_level,
            "llm_model": self.llm_model,
            "context_store_path": self.context_store_path,
        }


# 全局单例
global_config = A2AConfig()