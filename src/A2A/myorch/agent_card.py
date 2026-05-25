"""
MyOrchestrator A2A Agent Card 定义
"""
from python_a2a import AgentCard, AgentSkill


def get_agent_card() -> AgentCard:
    """获取 MyOrchestrator Agent Card"""
    return AgentCard(
        name="MyOrchestrator - 任务编排服务 (myorch-001)",
        description="A2A 任务编排引擎，协调 MyCode 与 MyTest 完成代码生成→测试→修复循环",
        url="http://localhost:8002",  # url="http://myorch:8002",
        version="1.0.0",
        capabilities={
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False
        },
        skills=[
            AgentSkill(
                id="run_task",
                name="run_task",
                description=(
                    "提交一个代码生成任务，Orchestrator 自动完成多轮循环直至通过或达到上限。"
                    "输入 spec（需求规格文档对象）、max_rounds（最大循环轮次，1-20，默认 10）。"
                    "输出 task_id、status（SUCCESS/MAX_ROUNDS_REACHED/MELT_DOWN/ERROR）、final_code、summary。"
                ),
                tags=["orchestration", "code-generation"],
                input_modes=["application/json"],
                output_modes=["application/json"]
            ),
            AgentSkill(
                id="get_task_status",
                name="get_task_status",
                description=(
                    "查询任务当前状态与进度。"
                    "输入 task_id。"
                    "输出 task_id、status、current_round、history。"
                ),
                tags=["orchestration", "monitoring"],
                input_modes=["application/json"],
                output_modes=["application/json"]
            )
        ],
    )


AGENT_CARD = get_agent_card()
