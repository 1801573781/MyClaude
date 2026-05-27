"""共享的 Pydantic 数据模型

MyCode、MyTest、MyOrchestrator 三个服务共用的数据结构。
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class AcceptanceCriterion(BaseModel):
    """验收标准"""
    input: str = Field(..., description="测试输入")
    expected_output: str = Field(..., description="期望输出")


class SpecConstraints(BaseModel):
    """约束条件"""
    max_execution_time_ms: int = Field(..., ge=1, description="最大执行时间（毫秒）")


class Spec(BaseModel):
    """需求规格文档对象"""
    title: str = Field(..., min_length=1, max_length=100, description="功能名称")
    description: str = Field(..., min_length=10, max_length=2000, description="功能描述")
    acceptance_criteria: List[AcceptanceCriterion] = Field(
        ..., min_length=2, description="验收标准列表（至少2条）"
    )
    language: str = Field(default="python", description="编程语言")
    constraints: SpecConstraints = Field(..., description="约束条件")


class TestDetail(BaseModel):
    """单条测试结果"""
    test_id: str = Field(..., description="测试用例ID")
    status: str = Field(..., description="测试状态", pattern="^(PASS|FAIL|ERROR)$")
    description: str = Field(..., description="测试描述")
    expected: Optional[str] = Field(default=None, description="期望值")
    actual: Optional[str] = Field(default=None, description="实际值")
    error_message: Optional[str] = Field(default=None, description="错误信息")


class TestReport(BaseModel):
    """测试报告"""
    passed: int = Field(..., ge=0, description="通过用例数")
    total: int = Field(..., ge=0, description="总用例数")
    pass_rate: float = Field(..., ge=0.0, le=1.0, description="通过率")
    details: List[TestDetail] = Field(..., description="每条用例的详细结果")
    execution_time_ms: int = Field(..., ge=0, description="执行耗时（毫秒）")
    coverage_percent: float = Field(default=0.0, ge=0.0, le=100.0, description="代码覆盖率")


class PreviousAttempt(BaseModel):
    """上一轮生成的代码及其测试报告（用于增量修复）"""
    code: str = Field(..., description="上一轮生成的源代码")
    test_report: Dict[str, Any] = Field(..., description="上一轮的测试报告")


class ErrorResponse(BaseModel):
    """统一错误响应"""
    error: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误详情")
    detail: Optional[str] = Field(default=None, description="详细错误信息")