"""
SystemTest 请求/响应模型

定义 SystemTest 服务专用的 Pydantic 模型。
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

from src.A2A.shared.models import (
    TestCase,
    TestDetail,
    TestResult as SharedTestResult,
)

# 别名，兼容 judge.py / new_feature_runner.py / main.py 的原有导入
TestStatus = SharedTestResult


# ============================================================
# 枚举
# ============================================================

class TestRunState(str, Enum):
    """测试运行状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ============================================================
# 模型
# ============================================================

class TestResult(BaseModel):
    """新功能测试用例结果。"""
    test_id: str
    description: str
    status: TestStatus
    stdout_preview: str = ""
    stderr_preview: str = ""
    exit_code: int = 0
    duration_seconds: float = 0.0


class RunRegressionRequest(BaseModel):
    """回归测试请求。"""
    task_id: Optional[str] = None
    test_ids: Optional[List[str]] = None
    myclaude_root: Optional[str] = None


class RunRegressionResponse(BaseModel):
    """回归测试响应。"""
    task_id: str
    state: TestRunState
    passed: int
    total: int
    pass_rate: float
    details: List[TestDetail]
    execution_time_seconds: float


class RunNewFeatureRequest(BaseModel):
    """新功能测试请求。"""
    task_id: Optional[str] = None
    test_cases: List[TestCase]
    changed_files: Optional[List[str]] = None
    myclaude_root: Optional[str] = None


class RunNewFeatureResponse(BaseModel):
    """新功能测试响应。"""
    task_id: str
    state: TestRunState
    passed: int
    total: int
    pass_rate: float
    details: List[TestResult]
    execution_time_seconds: float


class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str = "ok"
    service: str = "systemtest"


class UnitTestCase(BaseModel):
    """单元测试用例（直接调用被测函数）。"""
    id: str
    description: str
    target_module: str = ""
    target_function: str = ""
    test_input: str = ""
    reasoning_input: str = ""
    expected_behavior: str = ""
    check_type: str = "general"


class UnitTestResult(BaseModel):
    """单个单元测试用例的执行结果。"""
    test_id: str
    description: str
    status: TestStatus
    actual_output: str = ""
    reason: str = ""
    duration_seconds: float = 0.0


class RunUnitTestRequest(BaseModel):
    """单元测试请求。"""
    task_id: Optional[str] = None
    test_cases: List[UnitTestCase]
    myclaude_root: Optional[str] = None


class RunUnitTestResponse(BaseModel):
    """单元测试响应。"""
    task_id: str
    state: TestRunState
    passed: int
    total: int
    pass_rate: float
    details: List[UnitTestResult]
    execution_time_seconds: float


class SandboxStatus(BaseModel):
    """沙箱状态。"""
    available: bool
    message: str = ""
    docker_version: Optional[str] = None
