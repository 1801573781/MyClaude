"""
A2A 配置模块

从环境变量加载配置，提供统一的全局配置命名空间。
"""

import os
from types import SimpleNamespace


def _env(key: str, default: str = "") -> str:
    """读取环境变量，若未设置则返回默认值。"""
    return os.environ.get(key, default)


def load_config() -> SimpleNamespace:
    """加载 A2A 全局配置。

    Returns:
        SimpleNamespace: 配置命名空间。
    """
    cfg = SimpleNamespace(
        # MyOrch 服务配置
        myorch=SimpleNamespace(
            host=_env("MYORCH_HOST", "127.0.0.1"),
            port=int(_env("MYORCH_PORT", "8200")),
            auth_token=_env("MYORCH_AUTH_TOKEN", ""),
        ),
        # SystemTest 服务配置
        system_test=SimpleNamespace(
            host=_env("SYSTEMTEST_HOST", "127.0.0.1"),
            port=int(_env("SYSTEMTEST_PORT", "8201")),
            auth_token=_env("SYSTEMTEST_AUTH_TOKEN", ""),
        ),
        # UnitTest 服务配置
        unit_test=SimpleNamespace(
            host=_env("UNITTEST_HOST", "127.0.0.1"),
            port=int(_env("UNITTEST_PORT", "8202")),
            auth_token=_env("UNITTEST_AUTH_TOKEN", ""),
        ),
        # MyClaude 源码根目录（用于沙箱挂载）
        myclaude_root=_env(
            "MYCLAUDE_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )))
        ),
        # 数据存储根目录
        data_root=_env("A2A_EX_DATA_ROOT", os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            ))),
            "data",
            "a2a_ex"
        )),
        # Docker 配置
        docker=SimpleNamespace(
            image=_env("DOCKER_IMAGE", "python:3.12-slim"),
            cpu_limit=_env("DOCKER_CPU_LIMIT", "2"),
            memory_limit=_env("DOCKER_MEMORY_LIMIT", "2g"),
            test_timeout=int(_env("DOCKER_TEST_TIMEOUT", "300")),
        ),
        # LLM 评判配置（复用 MyClaude 的 API 配置）
        llm=SimpleNamespace(
            api_key=_env("DEEPSEEK_API_KEY", ""),
            api_base=_env("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"),
            model=_env("JUDGE_MODEL", "deepseek-chat"),
            max_tokens=int(_env("JUDGE_MAX_TOKENS", "2048")),
        ),
        # 判定阈值
        threshold=SimpleNamespace(
            regression_pass_rate=float(_env("THRESHOLD_REGRESSION", "0.95")),
            new_feature_pass_rate=float(_env("THRESHOLD_NEW_FEATURE", "1.0")),
        ),
        # 重试配置
        retry=SimpleNamespace(
            max_retries=int(_env("A2A_RETRY_MAX", "3")),
            base_delay=float(_env("A2A_RETRY_BASE_DELAY", "1.0")),
        ),
    )
    return cfg


# 全局单例
a2a_global_cfg = load_config()
