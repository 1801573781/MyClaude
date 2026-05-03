from openai import OpenAI, APIConnectionError, RateLimitError, APIError
from openai import AsyncOpenAI

from utility.config_loader import global_cfg


client = OpenAI(
    api_key=global_cfg.model.api_key,  # minimax_api_key,
    base_url=global_cfg.model.base_url  # minimax_base_url
)


# 同步，非流式
def block_chat(msg, max_tokens=9000):
    response = client.chat.completions.create(
        model=global_cfg.model.model_name,  # "MiniMax-M2.7",
        messages=msg,
        max_tokens=max_tokens,
        temperature=global_cfg.model.temperature,  # 0.7,
        stream=False
    )

    return response.choices[0].message.content


# 同步，流式
def stream_chat(msg, max_tokens=9000):
    is_truncated = False

    try:
        stream = client.chat.completions.create(
            model=global_cfg.model.model_name,  # "MiniMax-M2.7",
            messages=msg,
            max_tokens=max_tokens,
            temperature=global_cfg.model.temperature,  # 0.7,
            stream=True
        )
    except APIError as e:
        # 内容安全拦截、账号异常等异常
        error_body = getattr(e, 'body', str(e))
        return f"[API_ERROR: {error_body}]", is_truncated
    except (APIConnectionError, RateLimitError) as e:
        return f"[API_ERROR: 网络/限流问题，{e}]", is_truncated

    full_content = ""

    for chunk in stream:
        choice = chunk.choices[0]

        # 处理 finish_reason（流结束标记）
        if choice.finish_reason == "length":
            # 明确标记因为长度被截断
            full_content += "\n[ERROR: 输出被截断，max_tokens 不足]"
            is_truncated = True
            break

        if choice.finish_reason is not None:
            # "stop" 就是 LLM 自己说"我说完了"，最健康的结束信号。但是，这个信息就不要打印出来了。
            if "stop" == choice.finish_reason:
                full_content += ""
            else:
                full_content += f"\n\n[流结束原因: {choice.finish_reason}]"

            break

        # 提取并打印内容增量
        if choice.delta.content:
            text = choice.delta.content
            full_content += text

    return full_content, is_truncated


async_client = AsyncOpenAI(
    api_key=global_cfg.model.api_key,  # minimax_api_key,
    base_url=global_cfg.model.base_url  # minimax_base_url
)


async def async_stream_chat(msg, max_tokens=9000):
    stream = await async_client.chat.completions.create(
        model=global_cfg.model.model_name,  # "MiniMax-M2.7",
        messages=msg,
        max_tokens=max_tokens,
        temperature=global_cfg.model.temperature,  # 0.7,
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


def chat_with_retry(api_messages):
    """调用 stream_chat，若因 max_tokens 不足被截断则自动翻倍重试"""

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

        max_tokens = next_tokens

