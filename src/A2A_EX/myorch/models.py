"""
MyOrch 请求/响应模型

定义 MyOrch 服务专用的 Pydantic 模型，继承/扩展 shared 模型。
"""

from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel, Field

from src.A2A_EX.shared.models import (
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


class MetricsResponse(BaseModel):
    """指标响应。"""
    total_validations: int = 0
    pass_count: int = 0
    fail_count: int = 0
    error_count: int = 0
    pass_rate: float = 0.0
    avg_execution_time_seconds: float = 0.0
