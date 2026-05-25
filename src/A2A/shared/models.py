"""
A2A 系统公共数据模型
定义任务规格、代码生成、测试与编排流程的请求/响应数据结构
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class AcceptanceCriterion(BaseModel):
    """验收标准条目"""
    input: str = Field(..., description="输入描述")
    expected_output: str = Field(..., description="期望输出")


class Constraints(BaseModel):
    """约束条件"""
    max_execution_time_ms: int = Field(..., description="最大执行时间（毫秒）")


class SpecDocument(BaseModel):
    """需求规格文档对象"""
    title: str = Field(..., min_length=1, max_length=100, description="功能名称")
    description: str = Field(..., min_length=10, max_length=2000, description="功能描述")
    acceptance_criteria: List[AcceptanceCriterion] = Field(..., min_length=2, description="验收标准列表")
    language: str = Field(default="python", description="编程语言")
    constraints: Constraints = Field(..., description="约束条件")


class PreviousAttempt(BaseModel):
    """上一轮生成的代码及其测试报告（用于增量修复）"""
    code: str = Field(..., description="上一轮生成的源代码")
    test_report: Optional["TestReport"] = Field(default=None, description="上一轮的测试报告")


class GenerationMetadata(BaseModel):
    """代码生成元数据"""
    model: str = Field(default="", description="使用的模型名称")
    tokens_used: int = Field(default=0, description="消耗的 Token 数")
    generation_time_ms: int = Field(default=0, description="生成耗时（毫秒）")


class CodeGenerationRequest(BaseModel):
    """MyCode generate_code 请求"""
    spec: SpecDocument = Field(..., description="需求规格文档对象")
    task_id: str = Field(..., description="Orchestrator 分配的任务唯一标识")
    round: int = Field(default=1, ge=1, description="当前轮次编号")
    previous_attempt: Optional[PreviousAttempt] = Field(default=None, description="上一轮尝试信息")


class CodeGenerationResponse(BaseModel):
    """MyCode generate_code 响应"""
    code: str = Field(..., description="生成的 Python 源代码")
    file_name: str = Field(..., description="建议的文件名")
    generation_metadata: GenerationMetadata = Field(default_factory=GenerationMetadata)


class TestDetail(BaseModel):
    """单条测试详情"""
    test_id: str = Field(..., description="测试用例 ID")
    status: str = Field(..., description="测试状态：PASS / FAIL / ERROR")
    description: str = Field(..., description="测试描述")
    expected: Optional[str] = Field(default=None, description="期望输出")
    actual: Optional[str] = Field(default=None, description="实际输出")
    error_message: Optional[str] = Field(default=None, description="错误信息")


class TestReport(BaseModel):
    """测试报告"""
    passed: int = Field(..., description="通过的测试数")
    total: int = Field(..., description="总测试数")
    pass_rate: float = Field(..., ge=0, le=1.0, description="通过率")
    details: List[TestDetail] = Field(default_factory=list, description="测试详情列表")
    execution_time_ms: int = Field(default=0, description="执行耗时（毫秒）")
    coverage_percent: Optional[float] = Field(default=None, description="覆盖率百分比")


class TestRunRequest(BaseModel):
    """MyTest run_tests 请求"""
    code: str = Field(..., description="待测 Python 源代码")
    spec: SpecDocument = Field(..., description="原始需求规格文档对象")
    task_id: str = Field(..., description="任务唯一标识")
    round: int = Field(default=1, description="当前轮次编号")


class TestRunResponse(BaseModel):
    """MyTest run_tests 响应"""
    test_report: TestReport = Field(..., description="测试报告")


class RoundSummary(BaseModel):
    """单轮摘要"""
    round: int = Field(..., description="轮次编号")
    pass_rate: float = Field(..., description="通过率")
    failed_tests: List[str] = Field(default_factory=list, description="失败的测试 ID 列表")


class TaskSummary(BaseModel):
    """任务总结"""
    rounds: List[RoundSummary] = Field(default_factory=list, description="各轮摘要")
    total_time_seconds: float = Field(default=0.0, description="总耗时（秒）")
    final_verdict: str = Field(default="", description="最终判定")


class RunTaskRequest(BaseModel):
    """MyOrchestrator run_task 请求"""
    spec: SpecDocument = Field(..., description="需求规格文档对象")
    max_rounds: Optional[int] = Field(default=None, ge=1, le=20, description="最大循环轮次")


class RunTaskResponse(BaseModel):
    """MyOrchestrator run_task 响应"""
    task_id: str = Field(..., description="任务唯一标识")
    status: str = Field(..., description="任务状态：SUCCESS / MAX_ROUNDS_REACHED / MELT_DOWN / ERROR")
    total_rounds: int = Field(default=0, description="总轮次")
    final_code: str = Field(default="", description="最终代码")
    summary: TaskSummary = Field(default_factory=TaskSummary)


class TaskStatusResponse(BaseModel):
    """任务状态查询响应"""
    task_id: str = Field(..., description="任务唯一标识")
    status: str = Field(..., description="当前状态")
    current_round: int = Field(default=0, description="当前轮次")
    history: List[RoundSummary] = Field(default_factory=list, description="历史摘要列表")
