"""MyOrchestrator 服务的数据模型定义"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


# ---- 复用共享的需求规格模型（与 mycode/mytest 保持一致） ----

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


# ---- Orchestrator 特有模型 ----

class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    GENERATING = "GENERATING"
    TESTING = "TESTING"
    EVALUATING = "EVALUATING"
    SUCCESS = "SUCCESS"
    MAX_ROUNDS = "MAX_ROUNDS_REACHED"
    MELT_DOWN = "MELT_DOWN"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class RoundSummary(BaseModel):
    """单轮总结"""
    round: int = Field(..., ge=1, description="轮次编号")
    pass_rate: float = Field(..., ge=0.0, le=1.0, description="通过率")
    failed_tests: List[str] = Field(default_factory=list, description="失败用例ID列表")


class TaskSummary(BaseModel):
    """任务总结"""
    rounds: List[RoundSummary] = Field(default_factory=list, description="每轮概要")
    total_time_seconds: float = Field(default=0.0, description="总耗时（秒）")
    final_verdict: str = Field(default="", description="最终判定")


class RunTaskRequest(BaseModel):
    """提交任务请求"""
    spec: Spec = Field(..., description="符合规范的需求规格文档对象")
    max_rounds: int = Field(
        default=10,
        ge=1,
        le=20,
        description="最大循环轮次（覆盖全局默认值 10）",
    )


class RunTaskResponse(BaseModel):
    """任务提交响应（同步模式下返回最终结果）"""
    task_id: str = Field(..., description="任务唯一标识")
    status: TaskStatus = Field(..., description="任务终态")
    total_rounds: int = Field(default=0, ge=0, description="实际执行轮次")
    final_code: str = Field(default="", description="最终版本代码")
    summary: TaskSummary = Field(default_factory=TaskSummary, description="任务总结")


class GetTaskStatusResponse(BaseModel):
    """查询任务状态响应"""
    task_id: str = Field(..., description="任务唯一标识")
    status: TaskStatus = Field(..., description="当前状态")
    current_round: int = Field(default=0, ge=0, description="当前轮次")
    history: List[Dict[str, Any]] = Field(default_factory=list, description="历史轮次记录")


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误详情")
    detail: Optional[str] = Field(default=None, description="详细错误信息")


# ---- 指标模型 ----

class GlobalMetrics(BaseModel):
    """全局指标"""
    total_tasks: int = 0
    success_count: int = 0
    fail_count: int = 0
    meltdown_count: int = 0
    avg_rounds: float = 0.0
    p95_latency_seconds: float = 0.0