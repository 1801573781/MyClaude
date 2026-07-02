"""
SystemTest A2A Agent Card 定义

定义系统测试执行服务的身份、能力和端点，符合 python_a2a 标准。
"""

import os

from python_a2a import AgentCard, AgentSkill


SYSTEMTEST_AGENT_CARD = {
    "agent_id": "systemtest-ex-001",
    "name": "SystemTest - 系统测试执行器 (systemtest-001)",
    "description": (
        "在 Docker 沙箱中启动增强后的 MyClaude，执行回归测试套件和新功能测试用例，"
        "返回结构化测试报告"
    ),
    "version": "1.0.0",
    "skills": [
        {
            "id": "run_regression",
            "name": "run_regression",
            "description": "执行 MyClaude 回归测试套件，验证老功能无退化",
            "tags": ["testing", "regression"],
        },
        {
            "id": "run_new_feature_tests",
            "name": "run_new_feature_tests",
            "description": "执行新功能测试用例，验证新增能力是否生效",
            "tags": ["testing", "new-feature"],
        },
        {
            "id": "run_unit_tests",
            "name": "run_unit_tests",
            "description": "执行单元测试用例，直接调用被测函数并通过 LLM 评判返回值",
            "tags": ["testing", "unit-test"],
        },
    ],
}


def _get_service_url() -> str:
    """从环境变量或配置获取服务 URL。"""
    url = os.environ.get("SYSTEMTEST_URL")
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
    return "http://localhost:8002"


def get_agent_card() -> AgentCard:
    """构建并返回 SystemTest 服务的 A2A AgentCard 对象。"""
    card_data = SYSTEMTEST_AGENT_CARD
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
