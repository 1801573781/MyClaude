"""
MyOrchestrator 服务的请求与响应数据模型
"""
from typing import List, Optional
from pydantic import BaseModel, Field
from src.A2A.shared.models import SpecDocument, RoundSummary, TaskSummary


class RunTaskRequest(BaseModel):
    """run_task 请求"""
    spec: SpecDocument = Field(..., description="需求规格文档对象")
    max_rounds: Optional[int] = Field(default=None, ge=1, le=20, description="最大循环轮次")


class RunTaskResponse(BaseModel):
    """run_task 响应"""
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
