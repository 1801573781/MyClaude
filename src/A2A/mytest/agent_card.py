"""MyTest 服务的 A2A Agent Card 定义"""

MYTEST_AGENT_CARD = {
    "agent_id": "mytest-001",
    "name": "MyTest - 测试执行服务",
    "description": "基于 MyClaude 测试智能体的 A2A 服务，在 Docker 沙箱中执行代码并返回结构化测试报告",
    "version": "1.0.0",
    "capabilities": [
        {
            "capability_id": "run_tests",
            "description": "根据需求规格的验收标准，对代码执行测试并生成报告",
            "input_schema": {
                "type": "object",
                "required": ["code", "spec", "task_id"],
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "待测 Python 源代码",
                    },
                    "spec": {
                        "type": "object",
                        "description": "原始需求规格文档对象",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "任务唯一标识",
                    },
                    "round": {
                        "type": "integer",
                        "description": "当前轮次编号",
                    },
                },
            },
            "output_schema": {
                "type": "object",
                "required": ["test_report"],
                "properties": {
                    "test_report": {
                        "type": "object",
                        "required": [
                            "passed",
                            "total",
                            "pass_rate",
                            "details",
                            "execution_time_ms",
                        ],
                        "properties": {
                            "passed": {"type": "integer"},
                            "total": {"type": "integer"},
                            "pass_rate": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1.0,
                            },
                            "details": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": [
                                        "test_id",
                                        "status",
                                        "description",
                                    ],
                                    "properties": {
                                        "test_id": {"type": "string"},
                                        "status": {
                                            "type": "string",
                                            "enum": ["PASS", "FAIL", "ERROR"],
                                        },
                                        "description": {"type": "string"},
                                        "expected": {"type": "string"},
                                        "actual": {"type": "string"},
                                        "error_message": {"type": "string"},
                                    },
                                },
                            },
                            "execution_time_ms": {"type": "integer"},
                            "coverage_percent": {"type": "number"},
                        },
                    },
                },
            },
        }
    ],
    "endpoints": {
        "run_tests": "POST /a2a/run_tests",
    },
}