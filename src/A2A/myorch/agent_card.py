"""MyOrchestrator 服务的 A2A Agent Card 定义"""

MYORCH_AGENT_CARD = {
    "agent_id": "myorch-001",
    "name": "MyOrchestrator - 任务编排服务",
    "description": "A2A 任务编排引擎，协调 MyCode 与 MyTest 完成代码生成→测试→修复循环",
    "version": "1.0.0",
    "capabilities": [
        {
            "capability_id": "run_task",
            "description": "提交一个代码生成任务，Orchestrator 自动完成多轮循环直至通过或达到上限",
            "input_schema": {
                "type": "object",
                "required": ["spec"],
                "properties": {
                    "spec": {
                        "type": "object",
                        "description": "符合规范的需求规格文档对象",
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "最大循环轮次（覆盖全局默认值 10）",
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
            },
            "output_schema": {
                "type": "object",
                "required": ["task_id", "status", "final_code", "summary"],
                "properties": {
                    "task_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["SUCCESS", "MAX_ROUNDS_REACHED", "MELT_DOWN", "ERROR"],
                    },
                    "final_code": {"type": "string"},
                    "summary": {"type": "object"},
                },
            },
        },
        {
            "capability_id": "get_task_status",
            "description": "查询任务当前状态与进度",
            "input_schema": {
                "type": "object",
                "required": ["task_id"],
                "properties": {
                    "task_id": {"type": "string"},
                },
            },
            "output_schema": {
                "type": "object",
                "required": ["task_id", "status", "current_round", "history"],
                "properties": {
                    "task_id": {"type": "string"},
                    "status": {"type": "string"},
                    "current_round": {"type": "integer"},
                    "history": {"type": "array"},
                },
            },
        },
    ],
    "endpoints": {
        "run_task": "POST /a2a/run_task",
        "get_task_status": "GET /a2a/tasks/{task_id}",
    },
}