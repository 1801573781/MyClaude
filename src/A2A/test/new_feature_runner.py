"""
兼容性 shim — 已迁移至 src.A2A.test.st.new_feature_runner

所有新功能测试逻辑已移至 st/ 子目录。
此文件仅做重导出，保证旧引用仍可用。
"""

from src.A2A.test.st.new_feature_runner import NewFeatureRunner  # noqa: F401
