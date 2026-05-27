"""MyCode A2A Agent Card 定义"""

from python_a2a import AgentCard, AgentCapability, AgentEndpoint


MYCODE_AGENT_CARD = AgentCard(
    agent_id="mycode-001",
    name="MyCode - 代码生成服务",
    description="基于 MyClaude 代码生成智能体的 A2A 服务，接收需求规格并生成 Python 代码",
    version="1.0.0",
    capabilities=[
        AgentCapability(
            capability_id="generate_code",
            description="根据需求规格生成 Python 代码文件",
            input_schema={
                "type": "object",
                "required": ["spec", "task_id"],
                "properties": {
                    "spec": {
                        "type": "object",
                        "description": "需求规格文档对象，必含 title/description/acceptance_criteria/language/constraints",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Orchestrator 分配的任务唯一标识",
                    },
                    "round": {
                        "type": "integer",
                        "description": "当前轮次编号（从 1 开始）",
                        "minimum": 1,
                    },
                    "previous_attempt": {
                        "type": "object",
                        "description": "上一轮生成的代码及其测试报告（用于增量修复）",
                        "properties": {
                            "code": {"type": "string"},
                            "test_report": {"type": "object"},
                        },
                    },
                },
            },
            output_schema={
                "type": "object",
                "required": ["code", "file_name", "generation_metadata"],
                "properties": {
                    "code": {"type": "string", "description": "生成的 Python 源代码"},
                    "file_name": {"type": "string", "description": "建议的文件名"},
                    "generation_metadata": {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string"},
                            "tokens_used": {"type": "integer"},
                            "generation_time_ms": {"type": "integer"},
                        },
                    },
                },
            },
        )
    ],
    endpoints={
        "generate_code": "POST /a2a/generate_code",
    },
)