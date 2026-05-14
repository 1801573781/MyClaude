import re

# 示例响应文本（包含符合正则的字符串）
response = """
现在开始按需求逐步修改。先修改 `chat_llm.py`，让 `stream_chat()` 返回三元组并收集 `reasoning_content`。

<str_replace path="D:/AI/MyClaude/src/query/chat_llm.py" summary="stream_chat 收集 reasoning_content 并返回三元组">
<old>def chat_with_retry(api_messages):
    '''调用 stream_chat，若因 max_tokens 不足被截断则自动翻倍重试
    返回 (content: str, is_truncated: bool, reasoning_content: str)'''

    initial_max_tokens = global_cfg.model_chat.initial_max_tokens
    max_retries = global_cfg.model_chat.max_retries
    max_tokens_limit = global_cfg.model_chat.max_tokens_limit

    max_tokens = initial_max_tokens

    for attempt in range(max_retries + 1):  # +1 包含首次请求
        ai_response, is_truncated = stream_chat(api_messages, max_tokens=max_tokens)

        # 成功：没有截断标记，直接返回
        if not is_truncated:
            return ai_response

        # 失败：被截断了，检查是否还能继续重试
        if attempt >= max_retries:
            return ai_response  # 返回带截断标记的结果，让上层决定怎么处理

        next_tokens = max_tokens * 2
        if next_tokens > max_tokens_limit:
            return ai_response

        max_tokens = next_tokens</old>
<new>def chat_with_retry(api_messages):
    '''调用 stream_chat，若因 max_tokens 不足被截断则自动翻倍重试
    返回 (content: str, is_truncated: bool, reasoning_content: str)'''

    initial_max_tokens = global_cfg.model_chat.initial_max_tokens
    max_retries = global_cfg.model_chat.max_retries
    max_tokens_limit = global_cfg.model_chat.max_tokens_limit

    max_tokens = initial_max_tokens

    for attempt in range(max_retries + 1):  # +1 包含首次请求
        ai_response, is_truncated, reasoning_content = stream_chat(api_messages, max_tokens=max_tokens)

        # 成功：没有截断标记，直接返回
        if not is_truncated:
            return ai_response, is_truncated, reasoning_content

        # 失败：被截断了，检查是否还能继续重试
        if attempt >= max_retries:
            return ai_response, is_truncated, reasoning_content

        next_tokens = max_tokens * 2
        if next_tokens > max_tokens_limit:
            return ai_response, is_truncated, reasoning_content

        max_tokens = next_tokens</new>
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

