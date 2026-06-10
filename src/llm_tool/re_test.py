import re

# 示例响应文本（包含符合正则的字符串）
response = """
<str_replace path="D:/AI/MyClaude/tests/system_test_cases_03.json" summary="将测试用例改为 A2A TestCase 兼容格式">
<old>[
  {
    "id": "TC-SL-001",
    "name": "运行 MyClaude 后自动生成日志文件（Markdown 和 HTML）",
    "category": "session_log",
    "spec_ref": "SL-001",
    "input": {
      "command": "python -m src.myclaude --role mycode",
      "user_prompt": "写一个 Python 函数 hello()，输出 'Hello, world!'"
    },
    "expected": {
      "exit_code": 0,
      "output_contains": ["Hello, world!"],
      "output_not_contains": ["[ERROR]", "[BLOCKED]"],
      "done_present": true,
      "judge_prompt": "检查 D:/AI/MyClaude/logs/ 目录下是否生成了两个日志文件：一个 .md 文件和一个 .html 文件，文件名均以 'MyClaude' 开头并包含时间戳（格式 YYYY-MM-DD
HH-MM-SS）。如果两种格式的日志文件都存在，判定为通过；如果缺少任一格式，判定为不通过。"
    },
    "pass_criteria": "执行完成后，logs/ 目录下自动生成 .md 和 .html 两种格式的会话日志文件"
  },
  {
    "id": "TC-CL-007",
    "name": "/clear 命令清除当前会话记忆",
    "category": "cli_interaction",
    "spec_ref": "CL-007",
    "input": {
      "command": "python -m src.myclaude --role mycode",
      "user_prompt": "/clear"
    },
    "expected": {
      "exit_code": 0,
      "output_contains": ["已清除"],
      "output_not_contains": ["[ERROR]", "[BLOCKED]", "[CRITICAL ERROR]"]
    },
    "pass_criteria": "MyClaude 输出包含 '已清除' 字样，命令正常执行无报错"
  },
  {
    "id": "TC-CL-008",
    "name": "/stats 命令展示当前会话 token 统计",
    "category": "cli_interaction",
    "spec_ref": "CL-008",
    "input": {
      "command": "python -m src.myclaude --role mycode",
      "user_prompt": "/stats"
    },
    "expected": {
      "exit_code": 0,
      "output_contains": ["token", "Token"],
      "output_not_contains": ["[ERROR]", "[BLOCKED]"]
    },
    "pass_criteria": "MyClaude 输出包含 token/Token 字样，展示请求和响应的 token 统计"
  },
  {
    "id": "TC-ER-006",
    "name": "file_view 查看不存在的路径时返回错误提示",
    "category": "error_handling",
    "spec_ref": "ER-006",
    "input": {
      "command": "python -m src.myclaude --role mycode",
      "user_prompt": "使用 file_view 工具查看路径 D:/AI/MyClaude/code_output/nonexistent_xyz_123.py 的内容"
    },
    "expected": {
      "exit_code": 0,
      "output_contains": ["路径不存在"],
      "output_not_contains": ["[BLOCKED]", "[CRITICAL ERROR]"],
      "done_present": true
    },
    "pass_criteria": "MyClaude 调用 file_view 后输出包含 '路径不存在' 的错误提示，不会崩溃或返回其他异常"
  },
  {
    "id": "TC-CM-002",
    "name": "bash 命令超时后终止进程并返回超时提示",
    "category": "command_execution",
    "spec_ref": "CM-002",
    "input": {
      "command": "python -m src.myclaude --role mycode",
      "user_prompt": "执行命令 ping -n 130 127.0.0.1（这会运行约130秒，超过默认120秒超时）"
    },
    "expected": {
      "exit_code": 0,
      "output_contains": ["超时", "timeout", "Timeout"],
      "output_not_contains": ["[CRITICAL ERROR]"],
      "done_present": true,
      "max_turns": 10
    },
    "pass_criteria": "MyClaude 执行长时间命令后输出包含 '超时'/'timeout' 提示，进程被终止，系统正常退出"
  }
]</old>
<new>[
  {
    "id": "TC-SL-001",
    "description": "运行 MyClaude 后自动生成会话日志文件（Markdown 和 HTML）",
    "user_prompt": "写一个 Python 函数 hello()，输出 'Hello, world!'",
    "expected_behavior": "执行完成后，logs/ 目录下自动生成 .md 和 .html 两种格式的会话日志文件"
  },
  {
    "id": "TC-CL-007",
    "description": "/clear 命令清除当前会话记忆",
    "user_prompt": "/clear",
    "expected_behavior": "MyClaude 输出包含 '已清除' 字样，命令正常执行无报错"
  },
  {
    "id": "TC-CL-008",
    "description": "/stats 命令展示当前会话 token 统计",
    "user_prompt": "/stats",
    "expected_behavior": "MyClaude 输出包含 token/Token 字样，展示请求和响应的 token 统计信息"
  },
  {
    "id": "TC-ER-006",
    "description": "file_view 查看不存在的路径时返回错误提示",
    "user_prompt": "使用 file_view 工具查看路径 D:/AI/MyClaude/code_output/nonexistent_xyz_123.py 的内容",
    "expected_behavior": "MyClaude 调用 file_view 后输出包含 '路径不存在' 的错误提示，不会崩溃或返回 [CRITICAL ERROR]"
  },
  {
    "id": "TC-CM-002",
    "description": "bash 命令执行超时后应终止进程并返回超时提示",
    "user_prompt": "执行命令 ping -n 130 127.0.0.1（这会运行约130秒，超过默认120秒超时）",
    "expected_behavior": "MyClaude 执行长时间命令后输出包含 '超时'/'timeout' 提示，进程被终止，系统正常退出并输出 done"
  }
]</new>
</str_replace>
"""

# 定义正则模式列表
patterns = [
    ("str_replace", re.compile(
        r'<str_replace\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>(?:.*?)<old>(.*?)</old>(?:.*?)<new>(.*?)</new>(?:.*?)</str_replace>',
        re.DOTALL
    )),
]

# 解析并提取内容
print("=== 提取结果 ===\n")
for name, pattern in patterns:
    for match in pattern.finditer(response):
        path = match.group(1)
        summary = match.group(2) if match.group(2) is not None else "(无 summary)"
        old = match.group(3).strip()
        new = match.group(4).strip()
        print(f"标签类型: {name}")
        print(f"  path   : {path}")
        print(f"  summary: {summary}")
        print(f"  old    : {old}")
        print(f"  new    : {new}\n")

# 打印原始响应及其 repr
print("=== 原始响应 ===\n")
print("print(response):")
print(response)
print("\nprint(repr(response)):")
print(repr(response))

