from openai import OpenAI, APIConnectionError, RateLimitError, APIError

from src.utility.config_loader import global_cfg
import httpx
import types


def _to_dict(obj):
    """递归将 SimpleNamespace 转回 dict，供 OpenAI SDK 使用"""
    if isinstance(obj, types.SimpleNamespace):
        return {k: _to_dict(v) for k, v in obj.__dict__.items()}
    return obj


model_provider = global_cfg.model.provider
provider_cfg = getattr(global_cfg, model_provider)
api_key = provider_cfg.api_key
base_url = provider_cfg.base_url
model_name = provider_cfg.model_name
extra_body = getattr(provider_cfg, 'extra_body', None)

client = OpenAI(
    api_key=api_key,
    base_url=base_url,
    http_client=httpx.Client(verify=False),
)


def chat_with_retry(api_messages):
    """调用 stream_chat，若因 max_tokens 不足被截断则自动翻倍重试
    返回 (content: str, is_truncated: bool, reasoning_content: str, usage: dict|None)"""

    initial_max_tokens = global_cfg.model_chat.initial_max_tokens
    max_retries = global_cfg.model_chat.max_retries
    max_tokens_limit = global_cfg.model_chat.max_tokens_limit

    max_tokens = initial_max_tokens
    accumulated_usage = None

    for attempt in range(max_retries + 1):  # +1 包含首次请求
        ai_response, is_truncated, reasoning, usage = stream_chat(api_messages, max_tokens=max_tokens)

        # 累加 usage（重试时每次 API 调用都单独计费）
        if usage:
            if accumulated_usage is None:
                accumulated_usage = dict(usage)
            else:
                accumulated_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                accumulated_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                accumulated_usage["cached_tokens"] += usage.get("cached_tokens", 0)

        # 成功：没有截断标记，直接返回
        if not is_truncated:
            return ai_response, is_truncated, reasoning, accumulated_usage

        # 失败：被截断了，检查是否还能继续重试
        if attempt >= max_retries:
            return ai_response, is_truncated, reasoning, accumulated_usage

        next_tokens = max_tokens * 2
        if next_tokens > max_tokens_limit:
            return ai_response, is_truncated, reasoning, accumulated_usage

        max_tokens = next_tokens


# 同步，流式
def stream_chat(msg, max_tokens=global_cfg.model_chat.initial_max_tokens):
    """
    流式调用聊天接口，返回 (完整内容, 是否因长度截断, 推理内容, usage字典|None)。
    usage 字典键：prompt_tokens, completion_tokens, cached_tokens（缓存命中数，可能为0或不存在）
    """
    is_truncated = False
    full_content = ""
    reasoning_content = ""
    usage = None

    try:
        # 构建 API 调用参数，仅在 extra_body 非空时传入
        api_kwargs = dict(
            model=model_name,
            messages=msg,
            max_tokens=max_tokens,
            temperature=global_cfg.model_chat.temperature,
            stream=True,
            stream_options={"include_usage": True},
        )
        if extra_body:
            api_kwargs["extra_body"] = _to_dict(extra_body)

        stream = client.chat.completions.create(**api_kwargs)

        # 整个流式循环也放在异常保护中
        for chunk in stream:
            # 处理 usage chunk（OpenAI 流式，当 include_usage=True 时，
            # 最后一个 chunk 可能 choices 为空，但 chunk.usage 存在）
            if hasattr(chunk, 'usage') and chunk.usage:
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                    "cached_tokens": getattr(chunk.usage, 'prompt_tokens_details', None) and
                                     getattr(chunk.usage.prompt_tokens_details, 'cached_tokens', 0) or 0,  # noqa E131
                }
                continue  # 这个 chunk 通常不含 choices，继续循环（也可能同时也有 choices）

            # 处理正常 chunk（有 choices）
            if not chunk.choices:
                continue

            choice = chunk.choices[0]

            # ① 收集推理内容（如果提供商支持，安全访问避免 AttributeError）
            delta = choice.delta
            rc = getattr(delta, 'reasoning_content', None) or getattr(delta, 'reasoning', None)
            if rc:
                reasoning_content += rc

            # ② 收集内容（防止最后一个块同时带有内容和 finish_reason）
            if getattr(delta, 'content', None):
                full_content += delta.content

            # ③ 再处理结束原因
            if choice.finish_reason is not None:
                if choice.finish_reason == "length":
                    # 长度截断，添加明确标记
                    full_content += "\n[ERROR: 输出被截断，max_tokens 不足]"
                    is_truncated = True
                elif choice.finish_reason != "stop":
                    # 非正常结束，给出原因提示（stop 是最健康的信号，不添加额外文字）
                    full_content += f"\n\n[流结束原因: {choice.finish_reason}]"
                # 无论哪种结束原因，都要跳出循环
                break

    except APIError as e:
        # 内容安全拦截、账号异常等
        error_body = getattr(e, 'body', str(e))
        return f"[API_ERROR: {error_body}]", is_truncated, "", None
    except (APIConnectionError, RateLimitError) as e:
        return f"[API_ERROR: 网络/限流问题，{e}]", is_truncated, "", None
    except Exception as e:
        # 兜底异常（例如流读取过程中意外中断）
        return f"[API_ERROR: 流式读取异常，{e}]", is_truncated, "", None

    return full_content, is_truncated, reasoning_content, usage


"""
# 同步，非流式
def block_chat(msg, max_tokens=9000):
    response = client.chat.completions.create(
        model=model_name,  # "MiniMax-M2.7",
        messages=msg,
        max_tokens=max_tokens,
        temperature=global_cfg.model_chat.temperature,  
        stream=False
    )

    return response.choices[0].message.content
"""

"""
async_client = AsyncOpenAI(
    api_key=api_key,  # minimax_api_key,
    base_url=base_url  # minimax_base_url
)


# 异步，流式
async def async_stream_chat(msg, max_tokens=9000):
    stream = await async_client.chat.completions.create(
        model=model_name,  # "MiniMax-M2.7",
        messages=msg,
        max_tokens=max_tokens,
        temperature=global_cfg.model_chat.temperature,  
        stream=True
    )

    async for chunk in stream:
        choice = chunk.choices[0]

        # 处理 finish_reason（流结束标记）

        if choice.finish_reason == "length":
            # 明确标记因为长度被截断
            yield f"\n[ERROR: 输出被截断，max_tokens 不足]"
            break

        if choice.finish_reason is not None:
            # print(f"\n\n[流结束原因: {choice.finish_reason}]")
            break

        # 提取并打印内容增量
        if choice.delta.content:
            text = choice.delta.content
            yield text
"""
