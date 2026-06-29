"""
MyOrch 请求/响应模型

定义 MyOrch 服务专用的 Pydantic 模型，继承/扩展 shared 模型。
"""

from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel, Field

from src.A2A.shared.models import (
    ValidationRequest,
    ValidationResponse,
    ValidationStatus,
)


# ============================================================
# MyOrch 内部任务模型
# ============================================================

class ValidationTask(BaseModel):
    """MyOrch 内部维护的验证任务状态。"""
    task_id: str
    status: ValidationStatus = ValidationStatus.PENDING
    request: Optional[ValidationRequest] = None
    response: Optional[ValidationResponse] = None
    progress: Dict = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ============================================================
# MyOrch 健康检查
# ============================================================

class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str = "ok"
    service: str = "myorch"


# ============================================================
# 单元测试编排
# ============================================================

class UnitTestOrchestrationRequest(BaseModel):
    """单元测试编排请求（CLI → MyOrch）。"""
    test_cases: list = Field(description="单元测试用例列表（字典格式）")
    myclaude_root: Optional[str] = Field(
        default=None,
        description="MyClaude 项目根目录绝对路径"
    )
    report_output_dir: Optional[str] = Field(
        default=None,
        description="测试报告输出目录绝对路径"
    )


class UnitTestOrchestrationResponse(BaseModel):
    """单元测试编排结果。"""
    task_id: str
    status: str  # PASS / FAIL / ERROR
    passed: int
    total: int
    pass_rate: float
    details: list = Field(default_factory=list)
    execution_time_seconds: float = 0.0
    report_path: Optional[str] = None
    error_message: Optional[str] = None


class MetricsResponse(BaseModel):
    """指标响应。"""
    total_validations: int = 0
    pass_count: int = 0
    fail_count: int = 0
    error_count: int = 0
    pass_rate: float = 0.0
    avg_execution_time_seconds: float = 0.0
