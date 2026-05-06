import re

from utility.config_loader import global_cfg
from utility.file_tool import file_view, file_create, file_str_replace
from llm_tool.cmd_bash import tool_bash


def parse_tools(response: str):
    """
    按顺序解析 AI 响应中的 XML 工具调用。
    返回: (剩余普通文本, 工具列表)
    ("code_view", re.compile(r'<code_view\s+path="([^"]*)"\s*/>')),
    ("spec_view", re.compile(r'<spec_view\s+path="([^"]*)"\s*/>')),
    """
    patterns = [
        ("file_view", re.compile(r'<file_view\s+path="([^"]*)"\s*/>')),
        ("create", re.compile(r'<create\s+path="([^"]*)">(.*?)</create>', re.DOTALL)),
        ("bash", re.compile(r'<bash>(.*?)</bash>', re.DOTALL)),
        ("str_replace", re.compile(
            r'<str_replace\s+path="([^"]*)">(.*?)<old>(.*?)</old>(.*?)<new>(.*?)</new>(.*?)</str_replace>',
            re.DOTALL
        )),
        # 关键修复：兼容有闭合 </done> 和无闭合（到字符串结尾）两种情况
        ("done", re.compile(r'<done>(.*?)(?:</done>|$)', re.DOTALL)),
    ]

    all_matches = []
    for tool_name, pattern in patterns:
        for m in pattern.finditer(response):
            all_matches.append((m.start(), m.end(), tool_name, m))

    all_matches.sort(key=lambda x: x[0])

    tools = []
    remaining_parts = []
    last_end = 0

    for start, end, tool_name, m in all_matches:
        if start > last_end:
            remaining_parts.append(response[last_end:start])

        '''
        if tool_name == "code_view":
            tools.append({"llm_tool": "code_view", "params": {"path": m.group(1)}})
        elif tool_name == "spec_view":
            tools.append({"llm_tool": "spec_view", "params": {"path": m.group(1)}})
        '''
        if tool_name == "file_view":
            tools.append({"llm_tool": "file_view", "params": {"path": m.group(1)}})
        elif tool_name == "create":
            content = m.group(2)
            if content.startswith('\n'):
                content = content[1:]
            if content.endswith('\n'):
                content = content[:-1]
            tools.append({"llm_tool": "create", "params": {"path": m.group(1), "content": content}})
        elif tool_name == "bash":
            tools.append({"llm_tool": "bash", "params": {"command": m.group(1).strip()}})
        elif tool_name == "str_replace":
            tools.append({
                "llm_tool": "str_replace",
                "params": {"path": m.group(1), "old": m.group(3), "new": m.group(5)}
            })
        elif tool_name == "done":
            tools.append({"llm_tool": "done", "params": {"message": m.group(1).strip()}})

        last_end = end

    if last_end < len(response):
        remaining_parts.append(response[last_end:])

    remaining = "".join(remaining_parts).strip()
    remaining = re.sub(r'\n{3,}', '\n\n', remaining)

    return remaining, tools


code_output_root = global_cfg.code_project.code_output_root
spec_root = global_cfg.spec.spec_root


def execute_code_tool(tool):
    """
    执行工具列表，返回 API 消息格式（供下一轮对话使用）。
    每个结果包装为 {"role": "user", "content": "[llm_tool] 结果..."}
    在系统提示词里，强制要求 LLM 输出绝对路径，所以 file_view(code_output_root, p["path"]) 中的 code_output_root，已经没有意义了
    """

    name = tool["llm_tool"]
    p = tool["params"]

    '''
    这里的根目录已经没意义了，因为已经要求 LLM 的回复，肯定是绝对路径
    如果 LLM 没有遵守指令，回复的是相对路径，出现错误，那就错吧
    '''
    if name == "file_view":
        result = file_view(spec_root, p["path"])
    elif name == "create":
        result = file_create(code_output_root, p["path"], p["content"])
    elif name == "str_replace":
        result = file_str_replace(code_output_root, p["path"], p["old"], p["new"])
    elif name == "bash":
        result = tool_bash(p["command"])
    else:
        result = "unknown llm_tool"

    # 返回 dict，不是 list
    return {
        "role": "user",
        "content": f"[{name}] 工具执行结果：{result}"
    }


def execute_tools(tools: list) -> list[dict]:
    """
    执行工具列表，返回 API 消息格式（供下一轮对话使用）。
    每个结果包装为 {"role": "user", "content": "[llm_tool] 结果..."}
    """
    results = []
    for t in tools:
        t_results = execute_code_tool(t)
        results.append(t_results)

    return results
