"""
MyTest A2A Agent Card 定义
"""
from python_a2a import AgentCard, AgentSkill


def get_agent_card() -> AgentCard:
    """获取 MyTest Agent Card"""
    return AgentCard(
        name="MyTest - 测试执行服务 (mytest-001)",
        description="基于 MyClaude 测试智能体的 A2A 服务，在 Docker 沙箱中执行代码并返回结构化测试报告",
        url="http://localhost:8001",  # url="http://mytest:8001",
        version="1.0.0",
        capabilities={
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False
        },
        skills=[
            AgentSkill(
                id="run_tests",
                name="run_tests",
                description=(
                    "根据需求规格的验收标准，对代码执行测试并生成报告。"
                    "输入 code（待测 Python 源代码）、spec（原始需求规格文档对象）、task_id（任务标识）、round（轮次编号）。"
                    "输出 test_report（含 passed/total/pass_rate/details/execution_time_ms/coverage_percent）。"
                ),
                tags=["testing", "code-execution"],
                input_modes=["application/json"],
                output_modes=["application/json"]
            )
        ],
    )


AGENT_CARD = get_agent_card()
