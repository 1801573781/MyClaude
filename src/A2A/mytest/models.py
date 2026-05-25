"""
MyTest 服务的请求与响应数据模型
"""
from pydantic import BaseModel, Field
from src.A2A.shared.models import SpecDocument, TestReport


class RunTestsRequest(BaseModel):
    """run_tests 请求"""
    code: str = Field(..., description="待测 Python 源代码")
    spec: SpecDocument = Field(..., description="原始需求规格文档对象")
    task_id: str = Field(..., description="任务唯一标识")
    round: int = Field(default=1, description="当前轮次编号")


class RunTestsResponse(BaseModel):
    """run_tests 响应"""
    test_report: TestReport = Field(..., description="测试报告")
