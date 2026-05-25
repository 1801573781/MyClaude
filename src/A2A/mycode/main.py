"""
MyCode A2A 服务 — FastAPI 应用入口
封装代码生成智能体 A，接收需求规格并生成 Python 代码
"""
import logging
# import sys
import time
# from pathlib import Path

from fastapi import FastAPI, HTTPException, Header
from python_a2a import A2AServer

# 将项目根目录加入 sys.path 以支持 shared 导入
# sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.A2A.shared.config import get_config
from src.A2A.mycode.agent_card import AGENT_CARD
from src.A2A.mycode.models import GenerateCodeRequest, GenerateCodeResponse, GenerationMetadata

logger = logging.getLogger("mycode")
app = FastAPI(title="MyCode", description="代码生成 A2A 服务", version="1.0.0")


def verify_auth(authorization: str | None = Header(default=None)) -> None:
    """验证 Bearer Token"""
    config = get_config()
    if not config.a2a_auth_token:
        return  # 未配置 Token 时跳过验证
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    if token != config.a2a_auth_token:
        raise HTTPException(status_code=401, detail="Invalid token")


def call_agent_a(request: GenerateCodeRequest) -> GenerateCodeResponse:
    """
    内部调用代码生成智能体 A（MyClaude 实例，sys_prompt=代码生成）
    当前为 Mock 实现，实际应调用 MyClaude 的 query_loop
    """
    start_time = time.time()
    logger.info(f"[{request.task_id}] Round {request.round}: 开始代码生成")
    
    # TODO: 替换为真实的 MyClaude 调用
    # from src.query.query_loop import QueryLoop
    # from src.utility.config_loader import load_global_config
    # ...
    
    # Mock 实现：根据需求规格生成占位代码
    spec = request.spec
    func_name = spec.title.replace(" ", "_").lower()
    
    code_lines = [
        f"def {func_name}(a, b): ",
        "    # TODO: 实现实际逻辑",
        "    return a + b",
    ]
    code = "\n".join(code_lines)
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    return GenerateCodeResponse(
        code=code,
        file_name=f"{func_name}.py",
        generation_metadata=GenerationMetadata(
            model="deepseek-v3",
            tokens_used=len(code) // 4,
            generation_time_ms=elapsed_ms,
        )
    )


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "ok", "service": "mycode"}


@app.get("/.well-known/agent-card.json")
async def agent_card():
    """A2A Agent Card 发现端点"""
    return AGENT_CARD.model_dump()


@app.post("/a2a/generate_code")
async def generate_code(
    request: GenerateCodeRequest,
    authorization: str | None = Header(default=None),
):
    """A2A generate_code 能力端点"""
    verify_auth(authorization)
    
    # config = get_config()
    
    # 校验必填字段
    if not request.task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    
    try:
        response = call_agent_a(request)
        logger.info(f"[{request.task_id}] Round {request.round}: 代码生成完毕, "
                    f"{response.generation_metadata.generation_time_ms}ms")
        return response.model_dump()
    except TimeoutError:
        raise HTTPException(status_code=408, detail="Code generation timeout")
    except Exception as e:
        logger.error(f"[{request.task_id}] 代码生成失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


# 创建 A2A Server 包装
a2a_server = A2AServer(
    agent_card=AGENT_CARD,
    app=app,
)

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=get_config().log_level)
    uvicorn.run(app, host="0.0.0.0", port=8000)
