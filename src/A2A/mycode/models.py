"""MyCode 服务的数据模型定义"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator


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


class PreviousAttempt(BaseModel):
    """上一轮生成的代码及其测试报告（用于增量修复）"""
    code: str = Field(..., description="上一轮生成的源代码")
    test_report: Dict[str, Any] = Field(..., description="上一轮的测试报告")


class GenerateCodeRequest(BaseModel):
    """代码生成请求"""
    spec: Spec = Field(..., description="需求规格文档对象")
    task_id: str = Field(..., min_length=1, description="任务唯一标识")
    round: int = Field(default=1, ge=1, description="当前轮次编号")
    previous_attempt: Optional[PreviousAttempt] = Field(
        default=None, description="上一轮尝试（从第2轮开始传入）"
    )


class GenerationMetadata(BaseModel):
    """生成元数据"""
    model: str = Field(default="deepseek-v3", description="使用的模型名称")
    tokens_used: int = Field(default=0, ge=0, description="消耗的Token数")
    generation_time_ms: int = Field(default=0, ge=0, description="生成耗时（毫秒）")


class GenerateCodeResponse(BaseModel):
    """代码生成响应"""
    code: str = Field(..., description="生成的Python源代码")
    file_name: str = Field(..., description="建议的文件名")
    generation_metadata: GenerationMetadata = Field(
        default_factory=GenerationMetadata, description="生成元数据"
    )


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误详情")
    detail: Optional[str] = Field(default=None, description="详细错误信息")