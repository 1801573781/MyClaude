from src.utility.config_loader import global_cfg
from src.utility.file_tool import file_view, file_create, file_str_replace
from src.llm_tool.cmd_bash import tool_bash

import re

# ======================== XML 标签集中管理 ========================
# 所有 XML 工具标签（用于标签泄露清理和识别）。
# 新增工具时必须同步更新这三个常量，其他地方全部引用它们。
_ALL_XML_TAGS = {"create", "str_replace", "bash", "done", "file_view", "excel_view", "use_skill", "old", "new", "AskUserQuestion"}

# 容器标签（需要闭合标签的，如 <create>...</create>）
_CONTAINER_TAGS = {"create", "str_replace", "bash", "done"}

# 自闭合标签（如 <file_view path="..."/>）
_SELF_CLOSING_TAGS = {"file_view", "excel_view", "use_skill", "AskUserQuestion"}


def _final_clean_xml_tags(content: str) -> str:
    """最终安全网：移除内容中独占一行的 XML 工具标签残留。

    改进：只清除独占一行的标签（行首到行尾只有标签和空白），
    避免破坏文档内容中内嵌在表格、段落、代码块中的合法标签文本。
    LLM 输出的工具标签通常独占一行，而文档内容中的标签通常内嵌在文本中。
    """
    if not content:
        return content

    # 构建匹配所有标签的正则，只匹配独占一行的标签
    tags_or = '|'.join(_ALL_XML_TAGS)
    cleaned = re.sub(
        r'^[ \t]*</?(?:' + tags_or + r')(?:\s[^>]*)?/?>[ \t]*$',
        '',
        content,
        flags=re.MULTILINE
    )
    return cleaned


def _find_container_end(response: str, content_start: int,
                        open_tag_prefix: str, close_tag: str) -> int:
    """嵌套感知的容器闭合标签查找器（字符串 + Markdown 感知版）。

    从 content_start 位置开始逐字符扫描，跟踪以下状态：
    1. JSON 字符串状态（单双引号、三引号）
    2. Markdown 代码块状态（三反引号，可跨行）
    3. Markdown 行内代码状态（单反引号，不跨行）
    4. 同名标签的嵌套深度

    当处于字符串、代码块或行内代码中时，跳过所有标签匹配，
    彻底消除内容中出现同名标签关键字导致的误判。
    """
    depth = 1
    pos = content_start
    in_string = False
    string_char = None  # '"', "'", '"""', "'''"
    in_code_block = False   # Markdown 代码块（可跨行）
    in_inline_code = False  # Markdown 行内代码（不跨行）
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

        # === Markdown 代码块检测（三反引号，可跨行）===
        if pos + 3 <= len(response) and response[pos:pos + 3] == '```':
            in_code_block = not in_code_block
            pos += 3
            if in_code_block:
                # 代码块开头：跳过语言标记（如 python）
                while pos < len(response) and response[pos] not in '\n\r':
                    pos += 1
            continue

        # === Markdown 行内代码检测（单反引号，不在代码块内时）===
        if not in_code_block and response[pos] == '`':
            in_inline_code = not in_inline_code
            pos += 1
            continue

        # 行内代码遇到换行自动结束
        if in_inline_code and response[pos] in '\n\r':
            in_inline_code = False
            pos += 1
            continue

        # 在代码块或行内代码中，跳过所有标签匹配
        if in_code_block or in_inline_code:
            pos += 1
            continue

        # 检查三引号开标签（""" 或 '''）  # noqa
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

        # 检查开标签前缀（仅当不在字符串/代码块/行内代码内时生效）
        if (pos + open_len <= len(response)
                and response[pos:pos + open_len] == open_tag_prefix):
            after = pos + open_len
            if after < len(response) and response[after] in (' ', '>', '/', '\n', '\t', '\r'):
                depth += 1
                pos = after
                continue

        # 检查闭标签（仅当不在字符串/代码块/行内代码内时生效）
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
    3. 无条件清理：无论提取结果如何，最后都使用 _final_clean_xml_tags 清理

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
        return {
            "error": "str_replace 工具缺少 <old> 开标签，请检查 XML 格式。",
            "params": {"path": path, "summary": summary}
        }
    if not old_found:
        return {
            "error": "str_replace 工具中 old 子标签未闭合（缺少 /old 闭合标签），请补全后重新生成。",
            "params": {"path": path, "summary": summary}
        }

    # 3. 提取 new 子标签：嵌套感知 → 简单正则 → 末尾兜底
    new_content, new_end, new_found = _extract_subtag_content(block, "new")
    if new_content is None:
        new_match = re.search(r'<new>(.*?)</new>', block, re.DOTALL)
        if new_match:
            new_content = new_match.group(1)
            # new_found = True
        else:
            new_start = block.find('<new>')
            if new_start != -1:
                new_content = block[new_start + len('<new>'):]
                # new_found = False
            else:
                return None

    # 4. 标签泄露清理：无条件清理 old/new 中的 XML 标签残留
    # 使用 _ALL_XML_TAGS 动态构建清理列表，确保所有标签都被处理
    leaked_tags = [f'</{tag}>' for tag in _ALL_XML_TAGS]
    leaked_tags.extend([f'<{tag}>' for tag in _ALL_XML_TAGS])

    def _strip_leaked_tags(content: str) -> str:
        """递归剥离 content 末尾的泄露 XML 标签，并用正则做最终清理。"""
        if not content:
            return content
        changed = True
        while changed:
            changed = False
            for tag in leaked_tags:
                if content.endswith(tag):
                    content = content[:-len(tag)]
                    changed = True
                    break
            if not changed and content:
                stripped = content.rstrip()
                if stripped != content:
                    content = stripped
                    changed = True
        # 最终安全网：使用正则彻底清理所有 XML 标签残留
        return _final_clean_xml_tags(content)

    old_content = _strip_leaked_tags(old_content)
    new_content = _strip_leaked_tags(new_content)

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

    # 兜底1：严格解析无工具，对同一 response 尝试宽松解析（处理畸形标签）
    if not tools and response.strip():
        loose_rem, loose_tools = _parse_tools_loose(response)
        # 宽松解析找到了工具才采纳其结果；否则保留严格解析的 remaining（防止文本丢失）
        if loose_tools:
            remaining, tools = loose_rem, loose_tools

    # 兜底2：仍无工具，且额外提供了 reasoning_content
    if not tools and reasoning_content:
        loose_rem, loose_tools = _parse_tools_loose(reasoning_content)
        if loose_tools:
            remaining, tools = loose_rem, loose_tools

    return remaining, tools


def _find_str_replace_end(response: str, content_start: int, all_open_positions: list) -> tuple:
    """通过子标签 <old> 和 <new> 确定 str_replace 的闭合位置。

    避免 <old>/<new> 内容中的 </str_replace> 字符串导致外层
    _find_container_end 提前关闭。

    策略：
    1. 先用嵌套感知提取 <old>...</old> 子标签
    2. 再用嵌套感知提取 <new>...</new> 子标签
    3. 在 </new> 之后查找 </str_replace> 闭合标签

    Returns:
        (end_pos, is_unclosed)
        - end_pos: </str_replace> 之后的绝对位置（或软边界）
        - is_unclosed: 是否未闭合
    """
    close_tag = '</str_replace>'

    # 找到软边界（下一个工具的开标签或响应末尾）
    soft_boundary = len(response)
    for op in all_open_positions:
        if op > content_start:
            soft_boundary = op
            break

    search_region = response[content_start:soft_boundary]

    # 1. 查找 <old>...</old> 子标签
    old_content, old_end_rel, old_found = _extract_subtag_content(search_region, "old")

    if old_content is None or not old_found:
        # <old> 未找到或未闭合，回退到通用方法
        end_pos = _find_container_end(response, content_start, '<str_replace', close_tag)
        if end_pos == -1:
            return soft_boundary, True
        return end_pos, False

    # 2. 查找 <new>...</new> 子标签（在 </old> 之后）
    new_search_region = search_region[old_end_rel:]
    new_content, new_end_rel, new_found = _extract_subtag_content(new_search_region, "new")

    if new_content is None or not new_found:
        # 尝试简单正则兜底
        new_match = re.search(r'<new>(.*?)</new>', new_search_region, re.DOTALL)
        if new_match:
            new_end_rel = new_match.end()
        else:
            # 回退到通用方法
            end_pos = _find_container_end(response, content_start, '<str_replace', close_tag)
            if end_pos == -1:
                return soft_boundary, True
            return end_pos, False

    # 3. 在 new 子标签之后查找 str_replace 闭合标签
    after_new_abs = content_start + old_end_rel + new_end_rel
    close_pos = response.find(close_tag, after_new_abs)
    if close_pos == -1:
        return soft_boundary, True
    return close_pos + len(close_tag), False


def _find_markdown_code_ranges(text: str) -> list:
    """识别 Markdown 代码块和行内代码的范围，返回 (start, end) 列表。

    这些范围内的内容不应被解析为工具标签。
    与 _find_container_end 中的 Markdown 感知逻辑保持一致。
    """
    ranges = []
    pos = 0
    in_code_block = False
    code_block_start = 0
    in_inline_code = False
    inline_code_start = 0

    while pos < len(text):
        # 代码块检测（三反引号，可跨行）
        if pos + 3 <= len(text) and text[pos:pos + 3] == '```':
            if in_code_block:
                ranges.append((code_block_start, pos + 3))
                in_code_block = False
            else:
                if in_inline_code:
                    # 行内代码遇到三反引号，先结束行内代码
                    ranges.append((inline_code_start, pos))
                    in_inline_code = False
                in_code_block = True
                code_block_start = pos
            pos += 3
            continue

        # 行内代码检测（单反引号，不在代码块内时）
        if not in_code_block and text[pos] == '`':
            if in_inline_code:
                ranges.append((inline_code_start, pos + 1))
                in_inline_code = False
            else:
                in_inline_code = True
                inline_code_start = pos
            pos += 1
            continue

        # 行内代码遇到换行自动结束
        if in_inline_code and text[pos] in '\n\r':
            ranges.append((inline_code_start, pos))
            in_inline_code = False
            pos += 1
            continue

        pos += 1

    # 未闭合的代码块/行内代码，取到末尾
    if in_code_block:
        ranges.append((code_block_start, len(text)))
    if in_inline_code:
        ranges.append((inline_code_start, len(text)))

    return ranges


def _is_position_in_code(pos: int, code_ranges: list) -> bool:
    """检查位置是否在 Markdown 代码范围内。"""
    for start, end in code_ranges:
        if start <= pos < end:
            return True
    return False


def _parse_tools_strict(response: str):
    """严格模式解析 AI 响应中的 XML 工具调用。"""
    # 获取 Markdown 代码范围，用于过滤代码区域内的标签匹配
    code_ranges = _find_markdown_code_ranges(response)

    all_matches = []

    # === 非容器工具（自闭合标签）：正则匹配 ===
    non_container_patterns = {
        "file_view": re.compile(r'<file_view\s+path="([^"]*)"[^>]*/>'),
        "excel_view": re.compile(r'<excel_view\s+path="([^"]*)"[^>]*/>'),
        "use_skill": re.compile(r'<use_skill\s+name="([^"]*)"\s*/>'),
        "AskUserQuestion": re.compile(r'<AskUserQuestion\s+question="([^"]*)"(?:\s+choices="([^"]*)")?\s*/?>'),
    }
    for tool_name, pattern in non_container_patterns.items():
        for m in pattern.finditer(response):
            if _is_position_in_code(m.start(), code_ranges):
                continue
            all_matches.append((m.start(), m.end(), tool_name, m))

    # === 容器工具：嵌套感知解析 ===
    container_open_patterns = {
        "create": re.compile(r'<create\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>'),
        "str_replace": re.compile(r'<str_replace\s+path="([^"]*)"(?:\s+summary="([^"]*)")?\s*>'),
        "bash": re.compile(r'<bash>'),
        "done": re.compile(r'<done>'),
        "todowrite": re.compile(r'<todowrite>'),
    }

    # 收集所有工具的开标签位置（用于未闭合时的软边界）
    all_open_positions = []
    for _, pattern in non_container_patterns.items():
        for m in pattern.finditer(response):
            if not _is_position_in_code(m.start(), code_ranges):
                all_open_positions.append(m.start())
    for tool_name in _CONTAINER_TAGS:
        if tool_name in container_open_patterns:
            for m in container_open_patterns[tool_name].finditer(response):
                if not _is_position_in_code(m.start(), code_ranges):
                    all_open_positions.append(m.start())
    all_open_positions.sort()

    for tool_name in _CONTAINER_TAGS:
        open_pattern = container_open_patterns.get(tool_name)
        if open_pattern is None:
            continue
        close_tag = f'</{tool_name}>'
        open_prefix = f'<{tool_name}'

        for m in open_pattern.finditer(response):
            if _is_position_in_code(m.start(), code_ranges):
                continue
            content_start = m.end()

            if tool_name == "str_replace":
                # 特殊处理：通过 <old>/<new> 子标签定位闭合，避免内容中的
                # 闭标签字符串导致提前关闭
                end_pos, is_unclosed = _find_str_replace_end(
                    response, content_start, all_open_positions
                )
            else:
                end_pos = _find_container_end(response, content_start, open_prefix, close_tag)
                is_unclosed = (end_pos == -1)

            if is_unclosed:
                soft_boundary = len(response)
                for op in all_open_positions:
                    if op > content_start:
                        soft_boundary = op
                        break
                content = response[content_start:soft_boundary]
                match_end = soft_boundary
            else:
                content = response[content_start:end_pos - len(close_tag)]
                match_end = end_pos

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
        if tool_name in _CONTAINER_TAGS:
            container_ranges.append((start, end))

    def _is_inside_container(pos: int) -> bool:
        for cs, ce in container_ranges:
            if cs < pos < ce:
                return True
        return False

    return _build_result(response, all_matches, _is_inside_container)


def _build_result(response: str, all_matches: list, _is_inside_container):
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

        elif tool_name == "excel_view":
            params = {"path": m.group(1)}
            # 可选属性
            sheet_match = re.search(r'sheet="([^"]*)"', m.group(0))
            start_row_match = re.search(r'start_row="(\d+)"', m.group(0))
            end_row_match = re.search(r'end_row="(\d+)"', m.group(0))
            start_col_match = re.search(r'start_col="(\d+)"', m.group(0))
            end_col_match = re.search(r'end_col="(\d+)"', m.group(0))
            if sheet_match:
                params["sheet"] = sheet_match.group(1)
            if start_row_match:
                params["start_row"] = int(start_row_match.group(1))
            if end_row_match:
                params["end_row"] = int(end_row_match.group(1))
            if start_col_match:
                params["start_col"] = int(start_col_match.group(1))
            if end_col_match:
                params["end_col"] = int(end_col_match.group(1))
            tools.append({"llm_tool": "excel_view", "params": params})

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
                if "error" in tool:
                    # old/new 标签匹配失败：转为 error_tool，后续由 execute_code_tool 返回报错提示给 LLM
                    tools.append({
                        "llm_tool": "str_replace",
                        "params": {
                            "path": tool["params"]["path"],
                            "summary": tool["params"].get("summary", ""),
                            "_error": tool["error"]
                        }
                    })
                else:
                    if info["is_unclosed"]:
                        tool["params"]["_is_unclosed"] = True
                    tools.append(tool)

        elif tool_name == "bash":
            info = m
            content = info["content"].strip()
            tools.append({"llm_tool": "bash", "params": {"command": content, "_is_unclosed": info["is_unclosed"]}})

        elif tool_name == "todowrite":
            info = m
            content = info["content"]
            tools.append({"llm_tool": "todowrite", "params": {"content": content, "_is_unclosed": info["is_unclosed"]}})

        elif tool_name == "done":
            info = m
            content = info["content"].strip()
            tools.append({"llm_tool": "done", "params": {"message": content, "_is_unclosed": info["is_unclosed"]}})

        elif tool_name == "use_skill":
            tools.append({"llm_tool": "use_skill", "params": {"name": m.group(1)}})

        elif tool_name == "AskUserQuestion":
            question = m.group(1)
            choices_raw = m.group(2)
            choices = choices_raw.split(",") if choices_raw else None
            tools.append({"llm_tool": "AskUserQuestion", "params": {"question": question, "choices": choices}})

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

    # 2. 自闭合 excel_view（单引号或双引号）
    for m in re.finditer(r"<excel_view\s+path=['\"]([^'\"]*)['\"][^>]*/>", text):
        params = {"path": m.group(1)}
        sheet_match = re.search(r"sheet=['\"]([^'\"]*)['\"]", m.group(0))
        start_row_match = re.search(r'start_row=["\'](\d+)["\']', m.group(0))
        end_row_match = re.search(r'end_row=["\'](\d+)["\']', m.group(0))
        start_col_match = re.search(r'start_col=["\'](\d+)["\']', m.group(0))
        end_col_match = re.search(r'end_col=["\'](\d+)["\']', m.group(0))
        if sheet_match:
            params["sheet"] = sheet_match.group(1)
        if start_row_match:
            params["start_row"] = int(start_row_match.group(1))
        if end_row_match:
            params["end_row"] = int(end_row_match.group(1))
        if start_col_match:
            params["start_col"] = int(start_col_match.group(1))
        if end_col_match:
            params["end_col"] = int(end_col_match.group(1))
        tools.append({"llm_tool": "excel_view", "params": params})

    # 3. 自闭合 create（单引号或双引号）
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
            old_content = _final_clean_xml_tags(old_match.group(1))
        if new_match:
            new_content = _final_clean_xml_tags(new_match.group(1))

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
        # file_view 内部已经做了 _is_invalid_path 检测，这里再做一层目录截断和大文件截断
        raw_path = p.get("path", "")
        result = file_view(spec_root, raw_path,
                           limit=p.get("limit"),
                           offset=p.get("offset"))
        # 如果返回的是目录列表且行数过多，截断并附加警告，防止 LLM 因巨量上下文产生幻觉
        if result and not result.startswith("错误") and not result.startswith("[BLOCKED]") and not result.startswith(
                "[ERROR]"):
            lines = result.split("\n")
            # 目录列表截断
            if len(lines) > 30 and all(line.startswith("[DIR]") or line.startswith("[FILE]") for line in lines):
                result = "\n".join(
                    lines[:30]) + f"\n...（共 {len(lines)} 项，已截断前 30 项。请使用更精确的路径或 limit 参数缩小范围）"
            # 文件内容截断：超过 1000 行时截断，防止大文件耗尽 token
            elif len(lines) > 1000:
                result = "\n".join(
                    lines[:1000]) + f"\n...（文件共 {len(lines)} 行，已截断前 1000 行。请使用 limit/offset 参数分段读取剩余内容）"

    elif name == "create":
        # 写入前最终清理：确保内容中不含任何 XML 工具标签泄露
        cleaned_content = _final_clean_xml_tags(p["content"])
        if cleaned_content != p["content"]:
            p["content"] = cleaned_content
            tool["params"]["content"] = cleaned_content
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
        # 标签匹配错误：直接返回报错提示，不执行文件替换
        if p.get("_error"):
            error_msg = p["_error"]
            path = p.get("path", "未知路径")
            result = (
                f"[ERROR] str_replace 解析失败：[path] = {path}, [error] = {error_msg}\n"
                f"请修正 XML 格式后重新输出 str_replace 工具调用。"
            )
        else:
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

    elif name == "excel_view":
        from src.utility.excel_view import view_excel
        file_path = p.get("path", "")
        sheet_name = p.get("sheet", "")
        start_row = p.get("start_row")
        end_row = p.get("end_row")
        start_col = p.get("start_col")
        end_col = p.get("end_col")

        excel_output = view_excel(
            file_path=file_path,
            sheet_name=sheet_name,
            start_row=start_row,
            end_row=end_row,
            start_col=start_col,
            end_col=end_col,
        )

        # 错误/空表直接返回
        if excel_output.startswith("[错误]") or excel_output.startswith("[空表]") or excel_output.startswith("[范围无效]") or excel_output.startswith("[空范围]"):
            result = excel_output
        else:
            lines = excel_output.split("\n")
            data_lines = lines[2:] if len(lines) > 2 else []
            row_count = len(data_lines)
            if lines:
                col_count = lines[0].count("|") - 1
            else:
                col_count = 0
            range_info = []
            if start_row or end_row or start_col or end_col:
                range_info.append(f"范围: 行{start_row or 1}~{end_row or '末'} 列{start_col or 1}~{end_col or '末'}")
            range_str = f" ({'; '.join(range_info)})" if range_info else ""
            sheet_info = f"工作表: {sheet_name}" if sheet_name else "工作表: 第1个"
            summary = f"Excel 文件已读取：{file_path}，{sheet_info}，共 {row_count} 行 × {col_count} 列{range_str}"
            result = f"{summary}\n\n{excel_output}"

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

    elif name == "bash":
        command = p.get("command", "")
        result = tool_bash(command)

    elif name == "AskUserQuestion":
        from src.tools.ask_user_question import ask_user_question
        question = p.get("question", "")
        choices_raw = p.get("choices")
        if isinstance(choices_raw, str):
            choices = choices_raw.split(",")
        elif isinstance(choices_raw, list):
            choices = choices_raw if choices_raw else None
        else:
            choices = None
        result = ask_user_question(question=question, choices=choices)

    else:
        result = "unknown llm_tool"

    # AskUserQuestion 已经返回标准 dict 格式，直接透传
    if name == "AskUserQuestion" and isinstance(result, dict):
        return result

    # 返回 dict，不是 list
    if name == "bash":
        tool_result = {
            "role": "system",
            "content": f"[{name}] 工具执行结果：\n{result}"
        }
    else:
        tool_result = {
            "role": "system",
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
