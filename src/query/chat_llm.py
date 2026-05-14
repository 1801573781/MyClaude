from openai import OpenAI, APIConnectionError, RateLimitError, APIError
from openai import AsyncOpenAI

from utility.config_loader import global_cfg
import httpx


model_provider = global_cfg.model.provider
api_key = ""
base_url = ""
model_name = ""

if "DeepSeek" == model_provider:
    api_key = global_cfg.DeepSeek.api_key
    base_url = global_cfg.DeepSeek.base_url
    model_name = global_cfg.DeepSeek.model_name
else:
    api_key = global_cfg.MiniMax.api_key
    base_url = global_cfg.MiniMax.base_url
    model_name = global_cfg.MiniMax.model_name


client = OpenAI(
    api_key=api_key,
    base_url=base_url,
    http_client=httpx.Client(verify=False),
)


def chat_with_retry(api_messages):
    """调用 stream_chat，若因 max_tokens 不足被截断则自动翻倍重试
    返回 (content: str, is_truncated: bool, reasoning_content: str)"""

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


# 同步，流式
def stream_chat(msg, max_tokens=global_cfg.model_chat.initial_max_tokens):
    """
    流式调用聊天接口，返回完整内容和是否因长度截断。
    """
    is_truncated = False
    full_content = ""

    try:
        stream = client.chat.completions.create(
            model=model_name,
            messages=msg,
            max_tokens=max_tokens,
            temperature=global_cfg.model_chat.temperature,
            stream=True
        )

        # 整个流式循环也放在异常保护中
        for chunk in stream:
            choice = chunk.choices[0]

            # ① 优先收集内容（防止最后一个块同时带有内容和 finish_reason）
            if choice.delta.content:
                full_content += choice.delta.content

            # ② 再处理结束原因
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
        return f"[API_ERROR: {error_body}]", is_truncated
    except (APIConnectionError, RateLimitError) as e:
        return f"[API_ERROR: 网络/限流问题，{e}]", is_truncated
    except Exception as e:
        # 兜底异常（例如流读取过程中意外中断）
        return f"[API_ERROR: 流式读取异常，{e}]", is_truncated

    return full_content, is_truncated


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

