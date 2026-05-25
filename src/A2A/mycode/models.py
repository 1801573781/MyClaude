"""
MyCode 服务的请求与响应数据模型
"""
from typing import Optional
from pydantic import BaseModel, Field
from src.A2A.shared.models import SpecDocument, PreviousAttempt, GenerationMetadata


class GenerateCodeRequest(BaseModel):
    """generate_code 请求"""
    spec: SpecDocument = Field(..., description="需求规格文档对象")
    task_id: str = Field(..., description="任务唯一标识")
    round: int = Field(default=1, ge=1, description="当前轮次")
    previous_attempt: Optional[PreviousAttempt] = Field(default=None, description="上一轮尝试信息")


class GenerateCodeResponse(BaseModel):
    """generate_code 响应"""
    code: str = Field(..., description="生成的 Python 源代码")
    file_name: str = Field(..., description="建议的文件名")
    generation_metadata: GenerationMetadata = Field(default_factory=GenerationMetadata)
