"""
兼容性 shim — 已迁移至 src.A2A.test.st.main

所有系统测试服务入口逻辑已移至 st/ 子目录。
此文件仅做重导出，保证旧引用（如 src.A2A.test.main:app）仍可用。
"""

from src.A2A.test.st.main import (  # noqa: F401
    app,
    sandbox_mgr,
    judge,
    a2a_server,
    serve_agent_card,
    health,
    run_regression,
    run_new_feature_tests,
    run_unit_tests,
    metrics,
)
