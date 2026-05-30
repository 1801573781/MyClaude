from src.utility.config_loader import global_cfg
from src.utility.file_tool import file_view, file_create, file_str_replace
from src.llm_tool.cmd_bash import tool_bash

import re


def parse_tools(response: str):
    """
    按顺序解析 AI 响应中的 XML 工具调用。
    返回: (剩余普通文本, 工具列表)
    """
    patterns = [
        ("file_view", re.compile(r'<file_view\s+path="([^"]*)"[^>]*/>')),
        ("create", re.compile(r'<create\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>(.*?)</create>', re.DOTALL)),
        ("bash", re.compile(r'<bash>(.*?)</bash>', re.DOTALL)),
        # 改：str_replace 改用“块提取”正则，整块交给辅助函数处理
        ("str_replace", re.compile(r'<str_replace\b.*?</str_replace>', re.DOTALL)),
        ("use_skill", re.compile(r'<use_skill\s+name="([^"]*)"\s*/>')),
        ("done", re.compile(r'<done>(.*?)(?:</done>|$)', re.DOTALL)),
    ]

    all_matches = []
    for tool_name, pattern in patterns:
        for m in pattern.finditer(response):
            all_matches.append((m.start(), m.end(), tool_name, m))

    all_matches.sort(key=lambda x: x[0])

    # 识别容器块（create / str_replace），其内容中的 XML 标签是文档正文，不是工具调用
    container_ranges = []
    for start, end, tool_name, _m in all_matches:
        if tool_name in ("create", "str_replace"):
            container_ranges.append((start, end))

    def _is_inside_container(pos: int) -> bool:
        for cs, ce in container_ranges:
            if cs < pos < ce:
                return True
        return False

    tools = []
    remaining_parts = []
    last_end = 0

    for start, end, tool_name, m in all_matches:
        # 忽略嵌套在 create / str_replace 块内的"工具调用"
        if tool_name not in ("create", "str_replace") and _is_inside_container(start):
            continue

        if start > last_end:
            remaining_parts.append(response[last_end:start])

        if tool_name == "file_view":
            params = {"path": m.group(1)}
            # 提取可选的 limit（最多行数）和 offset（起始行，1-based）
            limit_match = re.search(r'limit="(\d+)"', m.group(0))
            offset_match = re.search(r'offset="(\d+)"', m.group(0))
            if limit_match:
                params["limit"] = int(limit_match.group(1))
            if offset_match:
                params["offset"] = int(offset_match.group(1))
            tools.append({"llm_tool": "file_view", "params": params})

        elif tool_name == "create":
            summary = m.group(2) or ""
            content = m.group(3)
            if content.startswith('\n'):
                content = content[1:]
            if content.endswith('\n'):
                content = content[:-1]
            tools.append({
                "llm_tool": "create",
                "params": {"path": m.group(1), "content": content, "summary": summary}
            })

        elif tool_name == "str_replace":
            # 调用容错解析器处理整个块
            tool = _parse_str_replace_block(m.group(0))
            if tool:
                tools.append(tool)

        elif tool_name == "bash":
            tools.append({"llm_tool": "bash", "params": {"command": m.group(1).strip()}})

        elif tool_name == "use_skill":
            tools.append({"llm_tool": "use_skill", "params": {"name": m.group(1)}})

        elif tool_name == "done":
            tools.append({"llm_tool": "done", "params": {"message": m.group(1).strip()}})

        last_end = end

    if last_end < len(response):
        remaining_parts.append(response[last_end:])

    remaining = "".join(remaining_parts).strip()
    remaining = re.sub(r'\n{3,}', '\n\n', remaining)

    return remaining, tools


def _parse_str_replace_block(block: str):
    """
    容错解析单个 <str_replace> 块。
    即使 <new> 错误地以 </old> 结束，也能正确提取 new 内容。
    """
    # 1. 提取 path 和 summary 属性
    header = re.search(r'<str_replace\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>', block)
    if not header:
        return None
    path = header.group(1)
    summary = header.group(2) or ""

    # 2. 提取 <old> 内容（正常情况）
    old_match = re.search(r'<old>(.*?)</old>', block, re.DOTALL)
    old = old_match.group(1) if old_match else ""

    # 3. 提取 <new> 内容，优先用 </new>，找不到则容忍 </old> 或 </str_replace>
    new_match = re.search(r'<new>(.*?)</new>', block, re.DOTALL)
    if new_match:
        new_content = new_match.group(1)
    else:
        # 容错：<new> 到 </old> 或 </str_replace>
        new_start = block.find('<new>')
        if new_start != -1:
            new_start += len('<new>')
            # 尝试找 </new>, </old>, </str_replace>
            end_idx = -1
            for end_tag in ['</new>', '</old>', '</str_replace>']:
                idx = block.find(end_tag, new_start)
                if idx != -1:
                    end_idx = idx
                    break
            if end_idx != -1:
                new_content = block[new_start:end_idx]
            else:
                new_content = block[new_start:]  # 极端情况：完全没闭合
        else:
            return None  # 连 <new> 都没有，解析失败

    return {
        "llm_tool": "str_replace",
        "params": {
            "path": path,
            "summary": summary,
            "old": old,
            "new": new_content
        }
    }


code_output_root = global_cfg.base_path.code_output_root
spec_root = global_cfg.base_path.spec_root


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
        # file_view 内部已经做了 _is_invalid_path 检测，这里再做一层目录截断
        raw_path = p.get("path", "")
        result = file_view(spec_root, raw_path,
                           limit=p.get("limit"),
                           offset=p.get("offset"))
        # 如果返回的是目录列表且行数过多，截断并附加警告，防止 LLM 因巨量上下文产生幻觉
        if result and not result.startswith("错误") and not result.startswith("[BLOCKED]") and not result.startswith("[ERROR]"):
            lines = result.split("\n")
            if len(lines) > 30 and all(line.startswith("[DIR]") or line.startswith("[FILE]") for line in lines):
                result = "\n".join(lines[:30]) + f"\n...（共 {len(lines)} 项，已截断前 30 项。请使用更精确的路径或 limit 参数缩小范围）"

    elif name == "create":
        # 写入文件
        result_detail = file_create(code_output_root, p["path"], p["content"])

        # 提取summary
        summary = p.get("summary", "")
        if summary and summary.strip():
            if len(summary) > 50:
                summary = summary[:47] + "..."
            result = f"文件已创建：{p['path']}，摘要：{summary}"
        else:
            import re
            match = re.search(r'\((\d+) 字符\)', result_detail)
            size = match.group(1) if match else str(len(p["content"]))
            result = f"已创建 {p['path']}（{size} 字符）"

    elif name == "str_replace":
        result_detail = file_str_replace(code_output_root, p["path"], p["old"], p["new"])
        summary = p.get("summary", "")
        if summary and summary.strip():
            if len(summary) > 50:
                summary = summary[:47] + "..."
            result = f"文件已修改：{p['path']}，摘要：{summary}"
        else:
            if result_detail.startswith("已修改"):
                old_len = len(p["old"])
                new_len = len(p["new"])
                result = f"文件已修改：{p['path']}，替换了 1 处（{old_len} → {new_len} 字符）"
            else:
                result = result_detail

    elif name == "bash":
        result = tool_bash(p["command"])

    elif name == "use_skill":
        skill_name = p["name"]
        # 动态导入避免循环依赖
        from src.utility.skill_loader import get_skill_loader
        loader = get_skill_loader()
        full_content = loader.load_full_skill(skill_name)
        if full_content is not None:
            result = f"已激活技能 '{skill_name}'，完整指令如下：\n\n{full_content}"
        else:
            # 获取可用技能名称列表
            available = [s["name"] for s in loader.get_metadata()]
            result = (
                f"[CRITICAL ERROR] 技能 '{skill_name}' 不存在或无法加载。\n"
                f"可用的技能列表：{available}\n"
                f"你必须立即输出 <done> 并报告错误，禁止继续执行任何其他工具。"
            )

    else:
        result = "unknown llm_tool"

    # 返回 dict，不是 list
    if name == "bash":
        tool_result = {
            "role": "user",
            "content": f"[{name}] 工具执行结果：\n{result}"
        }
    else:
        tool_result = {
            "role": "user",
            "content": f"[{name}] 工具执行结果：{result}"
        }

    return tool_result


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
