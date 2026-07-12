"""
UnitTest A2A Agent Card 定义

定义单元测试执行服务的身份、能力和端点，符合 python_a2a 标准。
与 st/agent_card.py 结构对齐。
"""

import os

from python_a2a import AgentCard, AgentSkill


UNITTEST_AGENT_CARD = {
    "agent_id": "unittest-ex-001",
    "name": "UnitTest - 单元测试执行器 (unittest-001)",
    "description": (
        "直接 import 被测模块、调用被测函数，"
        "通过 LLMJudge 评判返回值/异常，返回结构化测试报告"
    ),
    "version": "1.0.0",
    "skills": [
        {
            "id": "run_unit_tests",
            "name": "run_unit_tests",
            "description": "执行单元测试用例，动态导入被测函数并通过 LLM 评判结果",
            "tags": ["testing", "unit-test"],
        },
    ],
}


def _get_service_url() -> str:
    """从环境变量或配置获取服务 URL。"""
    url = os.environ.get("UNITTEST_URL")
    if url:
        return url
    try:
        from src.utility.config_loader import global_cfg
        a2a_cfg = getattr(global_cfg, "a2a", None)
        if a2a_cfg:
            test_cfg = getattr(a2a_cfg, "test", None)
            if test_cfg and hasattr(test_cfg, "url"):
                return test_cfg.url
    except Exception:
        pass
    return "http://localhost:8003"


def get_agent_card() -> AgentCard:
    """构建并返回 UnitTest 服务的 A2A AgentCard 对象。"""
    card_data = UNITTEST_AGENT_CARD
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
        url=_get_service_url(),
        version=card_data["version"],
        capabilities={
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        skills=skills,
    )


AGENT_CARD = get_agent_card()
