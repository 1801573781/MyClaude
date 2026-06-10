"""
A2A 共享数据模型

定义 MyOrch 和 SystemTest 共用的 Pydantic 数据模型。
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ============================================================
# 枚举
# ============================================================

class ValidationStatus(str, Enum):
    """验证任务状态。"""
    PENDING = "pending"
    RUNNING = "running"
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class TestResult(str, Enum):
    """单个测试用例结果。"""
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    INCONCLUSIVE = "inconclusive"


# ============================================================
# 进化规格
# ============================================================

class EvolutionSpec(BaseModel):
    """进化需求描述。"""
    title: str
    description: str
    pre_evolution_commit: Optional[str] = Field(
        default=None,
        description="进化前的 git commit hash（用于回滚）"
    )


# ============================================================
# 测试用例
# ============================================================

class TestCase(BaseModel):
    """新功能测试用例。"""
    id: str
    description: str
    user_prompt: str = Field(description="发送给 MyClaude 的测试指令")
    expected_behavior: str = Field(description="期望的行为描述（用于 LLM 评判）")
    check_type: Optional[str] = Field(
        default="general",
        description="评判类型，用于指导 LLMJudge 的评判侧重点。"
                    "可选值：file_created, file_modified, tool_chain, log_generated, "
                    "startup, memory_aware, skill_triggered, path_safety, general"
    )


# ============================================================
# 验证请求
# ============================================================

class ValidationRequest(BaseModel):
    """验证请求（MyClaude → MyOrch）。"""
    evolution_spec: EvolutionSpec
    changed_files: List[str] = Field(description="本次进化修改的文件列表（相对路径）")
    test_cases: List[TestCase] = Field(description="新功能测试用例列表")
    regression_test_ids: Optional[List[str]] = Field(
        default=None,
        description="指定要运行的回归测试 ID 列表（空则全跑）"
    )


# ============================================================
# 测试详情
# ============================================================

class TestDetail(BaseModel):
    """单个测试用例的执行详情。"""
    test_id: str
    description: str
    result: TestResult
    message: str = ""
    execution_time_seconds: float = 0.0
    raw_output: Optional[str] = None


# ============================================================
# 测试报告
# ============================================================

class TestSuiteReport(BaseModel):
    """测试套件报告（回归或新功能）。"""
    passed: int
    total: int
    pass_rate: float
    details: List[TestDetail]
    execution_time_seconds: float = 0.0


class ValidationReport(BaseModel):
    """验证报告。"""
    regression: TestSuiteReport
    new_feature: TestSuiteReport
    overall_pass: bool
    summary: str = ""
    execution_time_seconds: float = 0.0


# ============================================================
# 验证响应
# ============================================================

class ValidationResponse(BaseModel):
    """验证结果响应。"""
    task_id: str
    status: ValidationStatus
    report: Optional[ValidationReport] = None
    error_message: Optional[str] = None


class ValidationStatusResponse(BaseModel):
    """验证状态查询响应。"""
    task_id: str
    status: ValidationStatus
    progress: dict = Field(default_factory=dict)


# ============================================================
# 回归测试输入/输出
# ============================================================

class RegressionRequest(BaseModel):
    """回归测试请求。"""
    task_id: str
    test_ids: Optional[List[str]] = None
    myclaude_root: Optional[str] = None


class NewFeatureTestRequest(BaseModel):
    """新功能测试请求。"""
    task_id: str
    test_cases: List[TestCase]
    changed_files: Optional[List[str]] = None
    myclaude_root: Optional[str] = None


# ============================================================
# 工具函数
# ============================================================

def generate_task_id() -> str:
    """生成唯一的任务 ID。"""
    return str(uuid4()).replace("-", "")[:12]


def generate_test_id(prefix: str = "T") -> str:
    """生成唯一的测试用例 ID。"""
    return f"{prefix}-{str(uuid4()).replace('-', '')[:8]}"
