"""
MyOrch A2A Agent Card 定义

定义验证编排服务的身份、能力和端点，符合 python_a2a 标准。
"""

from python_a2a import AgentCard, AgentSkill


MYORCH_AGENT_CARD_DATA = {
    "agent_id": "myorch-ex-001",
    "name": "MyOrch - 验证编排服务",
    "description": (
        "A2A 验证编排引擎，接收 MyClaude 的进化验证请求，"
        "协调 SystemTest Agent 执行系统级测试，返回通过/失败判定"
    ),
    "version": "1.0.0",
    "skills": [
        {
            "id": "validate",
            "name": "validate",
            "description": "提交一个进化验证任务，对增强后的 MyClaude 执行系统测试",
            "tags": ["validation", "orchestration"],
        },
        {
            "id": "get_validation_status",
            "name": "get_validation_status",
            "description": "查询验证任务状态",
            "tags": ["validation", "query"],
        },
        {
            "id": "run_unit_tests",
            "name": "run_unit_tests",
            "description": "编排单元测试执行，委派 SystemTest Agent 执行单元测试用例并返回结果",
            "tags": ["testing", "unit-test", "orchestration"],
        },
    ],
}


def get_agent_card() -> AgentCard:
    """构建并返回 MyOrch 服务的 A2A AgentCard 对象。"""
    card_data = MYORCH_AGENT_CARD_DATA
    skills = [
        AgentSkill(
            id=s["id"],
            name=s["name"],
            description=s["description"],
            tags=s.get("tags", []),
            input_modes=["application/json"],
            output_modes=["application/json"],
        )
        for s in card_data["skills"]
    ]
    return AgentCard(
        name=card_data["name"],
        description=card_data["description"],
        url="http://localhost:8001",
        version=card_data["version"],
        capabilities={
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        skills=skills,
    )


AGENT_CARD = get_agent_card()
