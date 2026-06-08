from src.utility.config_loader import global_cfg
from src.utility.file_tool import file_view, file_create, file_str_replace
from src.llm_tool.cmd_bash import tool_bash

import re


def _quick_balance_check(text: str) -> int:
    """快速检查文本的结构平衡性。

    返回正数表示内容可能不完整（有未闭合的括号/引号）。
    用于判断一个候选闭标签是否在内容内部（应跳过）还是真正的闭标签。

    检查：
    - 括号/花括号/方括号的平衡
    - 字符串字面量是否闭合（包括三引号）
    - 若仍在字符串内，判定内容不完整
    """
    bracket_balance = 0
    in_string = False
    string_char = None  # '"', "'", '"""', "'''"
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == '\\':
                i += 2
                continue
            if string_char in ('"""', "'''"):
                if text[i:i + 3] == string_char:
                    in_string = False
                    i += 3
                    continue
            elif ch == string_char:
                in_string = False
                i += 1
                continue
            i += 1
            continue
        # 检查三引号开标签
        if text[i:i + 3] in ('"""', "'''"):
            in_string = True
            string_char = text[i:i + 3]
            i += 3
            continue
        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            i += 1
            continue
        if ch in ('{', '[', '('):
            bracket_balance += 1
        elif ch in ('}', ']', ')'):
            bracket_balance -= 1
        i += 1
    if in_string:
        return bracket_balance + 1  # 未闭合字符串，内容不完整
    return bracket_balance


def _find_container_end(response: str, content_start: int,
                        open_tag_prefix: str, close_tag: str) -> int:
    """嵌套感知的容器闭合标签查找器（字符串感知版）。

    从 content_start 位置开始逐字符扫描，跟踪 JSON 字符串状态和
    同名标签的嵌套深度，找到与当前开标签匹配的闭标签位置。

    核心改进：不再使用 str.find() 盲目搜索，而是逐字符扫描并
    跟踪是否处于字符串字面量内部。当发现疑似开/闭标签前缀时，
    若当前位置处于字符串内，则跳过不处理，彻底消除 JSON 内容中
    出现同名标签关键字导致的误判。
    """
    depth = 1
    pos = content_start
    in_string = False
    string_char = None  # '"', "'", '"""', "'''"
    open_len = len(open_tag_prefix)
    close_len = len(close_tag)

    while pos < len(response) and depth > 0:
        if in_string:
            ch = response[pos]
            if ch == '\\':
                pos += 2  # 跳过转义字符及被转义字符
                continue
            if string_char in ('"""', "'''"):
                if response[pos:pos + 3] == string_char:
                    in_string = False
                    string_char = None
                    pos += 3
                    continue
            elif ch == string_char:
                in_string = False
                string_char = None
                pos += 1
                continue
            pos += 1
            continue

        # 检查三引号开标签（""" 或 '''）
        if pos + 3 <= len(response) and response[pos:pos + 3] in ('"""', "'''"):
            in_string = True
            string_char = response[pos:pos + 3]
            pos += 3
            continue

        ch = response[pos]
        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            pos += 1
            continue

        # 检查开标签前缀（仅当不在字符串内时生效）
        if (pos + open_len <= len(response)
                and response[pos:pos + open_len] == open_tag_prefix):
            after = pos + open_len
            if after < len(response) and response[after] in (' ', '>', '/', '\n', '\t', '\r'):
                depth += 1
                pos = after
                continue

        # 检查闭标签（仅当不在字符串内时生效）
        if (pos + close_len <= len(response)
                and response[pos:pos + close_len] == close_tag):
            depth -= 1
            if depth == 0:
                return pos + close_len
            pos += close_len
            continue

        pos += 1

    return -1


def _extract_subtag_content(block: str, tag_name: str):
    """从块文本中提取子标签内容，支持嵌套感知。

    例如从 str_replace 块中提取 old 和 new 子标签的内容。
    当子标签内容中包含同名的闭标签时，仍能正确提取。

    返回 (content, end_pos, found)。
    - found=True 表示成功找到了闭合标签
    - found=False 表示未找到闭合标签（content 为补偿提取的内容）
    - 完全未找到开标签时返回 (None, -1, False)
    """
    open_tag = f'<{tag_name}>'
    close_tag_str = f'</{tag_name}>'
    open_prefix = f'<{tag_name}'

    start = block.find(open_tag)
    if start == -1:
        return None, -1, False

    content_start = start + len(open_tag)
    end_pos = _find_container_end(block, content_start, open_prefix, close_tag_str)

    if end_pos == -1:
        # 未闭合的子标签，取到块末尾
        return block[content_start:], len(block), False

    content = block[content_start:end_pos - len(close_tag_str)]
    return content, end_pos, True


def _parse_str_replace_block(block: str):
    """解析 str_replace 块，提取 old/new 子标签，并清理标签泄露。

    核心策略：
    1. 优先使用嵌套感知的 _extract_subtag_content 提取子标签
    2. 失败时回退到简单正则（兼容 LLM 输出的各种畸形情况）
    3. 利用 found 标志检测标签泄露（old 闭合但 new 未闭合）
    4. 从 new_content 末尾剥离泄露的 </old> / </new> / </str_replace>

    Returns:
        dict 或 None（解析失败时）
    """
    # 1. 从块开头提取外层 path/summary
    open_match = re.match(
        r'<str_replace\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>',
        block
    )
    if not open_match:
        return None
    path = open_match.group(1)
    summary = open_match.group(2) or ""

    # 2. 提取 old 子标签（嵌套感知优先）
    old_content, old_end, old_found = _extract_subtag_content(block, "old")
    if old_content is None:
        return None

    # 3. 提取 new 子标签：嵌套感知 → 简单正则 → 末尾兜底
    new_content, new_end, new_found = _extract_subtag_content(block, "new")
    if new_content is None:
        new_match = re.search(r'<new>(.*?)</new>', block, re.DOTALL)
        if new_match:
            new_content = new_match.group(1)
            new_found = True
        else:
            new_start = block.find('<new>')
            if new_start != -1:
                new_content = block[new_start + len('<new>'):]
                new_found = False
            else:
                return None

    # 4. 标签泄露清理：old 正常闭合但 new 未闭合时
    if old_found and not new_found:
        leaked_tags = ["</old>", "</new>", "</str_replace>"]
        changed = True
        while changed:
            changed = False
            for tag in leaked_tags:
                if new_content.endswith(tag):
                    new_content = new_content[:-len(tag)]
                    changed = True
                    break
            if not changed and new_content:
                stripped = new_content.rstrip()
                if stripped != new_content:
                    new_content = stripped
                    changed = True

    # 5. 清理子标签内容首尾空白（保留内部格式）
    if old_content.startswith('\n'):
        old_content = old_content[1:]
    if old_content.endswith('\n'):
        old_content = old_content[:-1]
    if new_content.startswith('\n'):
        new_content = new_content[1:]
    if new_content.endswith('\n'):
        new_content = new_content[:-1]

    return {
        "llm_tool": "str_replace",
        "params": {
            "path": path,
            "old": old_content,
            "new": new_content,
            "summary": summary,
        }
    }


def parse_tools(response: str, reasoning_content: str = ""):
    """
    按顺序解析 AI 响应中的 XML 工具调用。
    返回: (剩余普通文本, 工具列表)

    所有容器工具（create / str_replace / bash / done）均使用嵌套感知解析器，
    正确处理内容中包含同名闭标签的情况。
    非容器工具（file_view / use_skill）为自闭合标签，使用正则匹配。

    如果主响应中未解析到任何工具，且 reasoning_content 非空，
    则对 reasoning_content 使用宽松匹配兜底（容忍单引号、无闭合 done 等畸形容器标签）。
    """
    remaining, tools = _parse_tools_strict(response)

    # 兜底：主响应无工具，且提供了 reasoning_content
    if not tools and reasoning_content:
        remaining, tools = _parse_tools_loose(reasoning_content)

    return remaining, tools


def _parse_tools_strict(response: str):
    """严格嵌套感知解析（原 parse_tools 的主解析逻辑）。"""
    all_matches = []

    # === 非容器工具：正则匹配（自闭合标签，不存在嵌套问题） ===
    non_container_patterns = [
        ("file_view", re.compile(r'<file_view\s+path="([^"]*)"[^>]*/>')),
        ("use_skill", re.compile(r'<use_skill\s+name="([^"]*)"\s*/>')),
    ]
    for tool_name, pattern in non_container_patterns:
        for m in pattern.finditer(response):
            all_matches.append((m.start(), m.end(), tool_name, m))

    # === 容器工具：嵌套感知解析 ===
    container_tool_names = ["create", "str_replace", "bash", "done"]

    container_open_patterns = {
        "create": re.compile(r'<create\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>'),
        "str_replace": re.compile(r'<str_replace\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>'),
        "bash": re.compile(r'<bash>'),
        "done": re.compile(r'<done>'),
    }

    for tool_name in container_tool_names:
        open_pattern = container_open_patterns[tool_name]
        close_tag = f'</{tool_name}>'
        open_prefix = f'<{tool_name}'

        for m in open_pattern.finditer(response):
            content_start = m.end()
            end_pos = _find_container_end(response, content_start, open_prefix, close_tag)

            if end_pos == -1:
                content = response[content_start:]
                match_end = len(response)
                is_unclosed = True
            else:
                content = response[content_start:end_pos - len(close_tag)]
                match_end = end_pos
                is_unclosed = False

            all_matches.append((m.start(), match_end, tool_name, {
                "match": m,
                "content": content,
                "is_unclosed": is_unclosed,
            }))

    # 按位置排序
    all_matches.sort(key=lambda x: x[0])

    # 识别容器块范围
    container_ranges = []
    for start, end, tool_name, _m in all_matches:
        if tool_name in container_tool_names:
            container_ranges.append((start, end))

    def _is_inside_container(pos: int) -> bool:
        for cs, ce in container_ranges:
            if cs < pos < ce:
                return True
        return False

    return _build_result(response, all_matches, container_tool_names, _is_inside_container)


def _build_result(response: str, all_matches: list, container_tool_names: list,
                  _is_inside_container):
    """将匹配结果构建为 (remaining_text, tools_list)。"""
    tools = []
    remaining_parts = []
    last_end = 0

    for start, end, tool_name, m in all_matches:
        if _is_inside_container(start):
            continue

        if start > last_end:
            remaining_parts.append(response[last_end:start])

        if tool_name == "file_view":
            params = {"path": m.group(1)}
            limit_match = re.search(r'limit="(\d+)"', m.group(0))
            offset_match = re.search(r'offset="(\d+)"', m.group(0))
            if limit_match:
                params["limit"] = int(limit_match.group(1))
            if offset_match:
                params["offset"] = int(offset_match.group(1))
            tools.append({"llm_tool": "file_view", "params": params})

        elif tool_name == "create":
            info = m
            content = info["content"]
            if content.startswith('\n'):
                content = content[1:]
            if content.endswith('\n'):
                content = content[:-1]
            tools.append({
                "llm_tool": "create",
                "params": {
                    "path": info["match"].group(1),
                    "content": content,
                    "summary": info["match"].group(2) or "",
                    "_is_unclosed": info["is_unclosed"],
                }
            })

        elif tool_name == "str_replace":
            info = m
            if info["is_unclosed"]:
                block = info["match"].group(0) + info["content"]
            else:
                block = info["match"].group(0) + info["content"] + f'</{tool_name}>'
            tool = _parse_str_replace_block(block)
            if tool:
                if info["is_unclosed"]:
                    tool["params"]["_is_unclosed"] = True
                tools.append(tool)

        elif tool_name == "bash":
            info = m
            content = info["content"].strip()
            tools.append({"llm_tool": "bash", "params": {"command": content, "_is_unclosed": info["is_unclosed"]}})

        elif tool_name == "done":
            info = m
            content = info["content"].strip()
            tools.append({"llm_tool": "done", "params": {"message": content, "_is_unclosed": info["is_unclosed"]}})

        elif tool_name == "use_skill":
            tools.append({"llm_tool": "use_skill", "params": {"name": m.group(1)}})

        last_end = end

    if last_end < len(response):
        remaining_parts.append(response[last_end:])

    remaining = "".join(remaining_parts).strip()
    remaining = re.sub(r'\n{3,}', '\n\n', remaining)

    return remaining, tools


def _parse_tools_loose(text: str):
    """
    对 reasoning_content 进行宽松工具标签匹配。
    容忍单引号、无闭合 done/容器标签等 LLM 思考过程中产生的畸形格式。

    支持的标签（宽松版）：
    - <create path='...' summary='...'/>  自闭合或容器两种形态
    - <file_view path='...'/>
    - <done>...</done> 或裸 <done>（无闭合）
    - <bash>...</bash>
    - <str_replace path='...' summary='...'>...</str_replace>
    - <use_skill name='...'/>
    """
    tools = []

    # 1. 自闭合 file_view（单引号或双引号）
    for m in re.finditer(r"<file_view\s+path=['\"]([^'\"]*)['\"][^>]*/>", text):
        params = {"path": m.group(1)}
        limit_match = re.search(r'limit=["\'](\d+)["\']', m.group(0))
        offset_match = re.search(r'offset=["\'](\d+)["\']', m.group(0))
        if limit_match:
            params["limit"] = int(limit_match.group(1))
        if offset_match:
            params["offset"] = int(offset_match.group(1))
        tools.append({"llm_tool": "file_view", "params": params})

    # 2. 自闭合 create（单引号或双引号）
    for m in re.finditer(r"<create\s+path=['\"]([^'\"]*)['\"][^>]*/>", text):
        summary_match = re.search(r"summary=['\"]([^'\"]*)['\"]", m.group(0))
        tools.append({
            "llm_tool": "create",
            "params": {
                "path": m.group(1),
                "content": "",
                "summary": summary_match.group(1) if summary_match else "",
            }
        })

    # 3. done（可无闭合）
    for m in re.finditer(r"<done>(.*?)(?:</done>|$)", text):
        content = m.group(1).strip()
        tools.append({"llm_tool": "done", "params": {"message": content}})

    # 4. bash
    for m in re.finditer(r"<bash>(.*?)</bash>", text, re.DOTALL):
        tools.append({"llm_tool": "bash", "params": {"command": m.group(1).strip()}})

    # 5. use_skill（单引号或双引号）
    for m in re.finditer(r"<use_skill\s+name=['\"]([^'\"]*)['\"][^>]*/>", text):
        tools.append({"llm_tool": "use_skill", "params": {"name": m.group(1)}})

    # 6. str_replace 容器（单引号或双引号 path / summary）
    for m in re.finditer(
        r"<str_replace\s+path=['\"]([^'\"]*)['\"](?:\s+summary=['\"]([^'\"]*)['\"])?\s*>(.*?)</str_replace>",
        text, re.DOTALL
    ):
        path = m.group(1)
        summary = m.group(2) or ""
        body = m.group(3)

        # 尝试提取 old/new 子标签
        old_content = ""
        new_content = ""
        old_match = re.search(r"<old>(.*?)</old>", body, re.DOTALL)
        new_match = re.search(r"<new>(.*?)</new>", body, re.DOTALL)
        if old_match:
            old_content = old_match.group(1)
        if new_match:
            new_content = new_match.group(1)

        tools.append({
            "llm_tool": "str_replace",
            "params": {
                "path": path,
                "old": old_content,
                "new": new_content,
                "summary": summary,
            }
        })

    return "", tools


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
        if result and not result.startswith("错误") and not result.startswith("[BLOCKED]") and not result.startswith(
                "[ERROR]"):
            lines = result.split("\n")
            if len(lines) > 30 and all(line.startswith("[DIR]") or line.startswith("[FILE]") for line in lines):
                result = "\n".join(
                    lines[:30]) + f"\n...（共 {len(lines)} 项，已截断前 30 项。请使用更精确的路径或 limit 参数缩小范围）"

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
