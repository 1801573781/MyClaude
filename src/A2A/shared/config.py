"""
A2A 系统全局配置数据模型
支持环境变量加载与全局单例管理
"""
import os
# from dataclasses import dataclass, field
from pydantic import BaseModel


class A2AGlobalConfig(BaseModel):
    """A2A 系统全局配置"""
    mycode_url: str = "http://localhost:8000"
    mytest_url: str = "http://localhost:8001"
    a2a_auth_token: str = ""
    max_rounds: int = 10
    melt_down_window: int = 3
    code_gen_timeout_sec: int = 10
    test_exec_timeout_sec: int = 30
    sandbox_cpu_limit: float = 1.0
    sandbox_memory_mb: int = 512
    context_store_path: str = "./data/tasks"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "A2AGlobalConfig":
        """从环境变量加载配置"""
        return cls(
            mycode_url=os.getenv("MYCODE_URL", "http://localhost:8000"),
            mytest_url=os.getenv("MYTEST_URL", "http://localhost:8001"),
            a2a_auth_token=os.getenv("A2A_AUTH_TOKEN", ""),
            max_rounds=int(os.getenv("MAX_ROUNDS", "10")),
            melt_down_window=int(os.getenv("MELT_DOWN_WINDOW", "3")),
            code_gen_timeout_sec=int(os.getenv("CODE_GEN_TIMEOUT_SEC", "10")),
            test_exec_timeout_sec=int(os.getenv("TEST_EXEC_TIMEOUT_SEC", "30")),
            sandbox_cpu_limit=float(os.getenv("SANDBOX_CPU_LIMIT", "1.0")),
            sandbox_memory_mb=int(os.getenv("SANDBOX_MEMORY_MB", "512")),
            context_store_path=os.getenv("CONTEXT_STORE_PATH", "./data/tasks"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


# 全局单例
_global_config: A2AGlobalConfig | None = None


def get_config() -> A2AGlobalConfig:
    """获取全局配置单例"""
    global _global_config
    if _global_config is None:
        _global_config = A2AGlobalConfig.from_env()
    return _global_config


def set_config(config: A2AGlobalConfig) -> None:
    """设置全局配置（用于测试注入）"""
    global _global_config
    _global_config = config
