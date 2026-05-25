"""
MyCode A2A Agent Card 定义
"""
from python_a2a import AgentCard, AgentSkill


def get_agent_card() -> AgentCard:
    """获取 MyCode Agent Card"""
    return AgentCard(
        name="MyCode - 代码生成服务 (mycode-001)",
        description="基于 MyClaude 代码生成智能体的 A2A 服务，接收需求规格并生成 Python 代码",
        url="http://localhost:8000",  # url="http://mycode:8000",
        version="1.0.0",
        capabilities={
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False
        },
        skills=[
            AgentSkill(
                id="generate_code",
                name="generate_code",
                description=(
                    "根据需求规格生成 Python 代码文件。"
                    "输入 spec 对象（含 title/description/acceptance_criteria/language/constraints）、"
                    "task_id（任务标识）、round（轮次编号）、previous_attempt（上一轮代码与测试报告）。"
                    "输出 code（源代码）、file_name（建议文件名）、generation_metadata（模型/Token/耗时）。"
                ),
                tags=["code-generation", "python"],
                input_modes=["application/json"],
                output_modes=["application/json"]
            )
        ],
    )


AGENT_CARD = get_agent_card()
