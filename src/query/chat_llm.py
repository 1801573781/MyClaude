from openai import OpenAI, APIConnectionError, RateLimitError, APIError

from src.utility.config_loader import global_cfg
import httpx
import types
import logging

logger = logging.getLogger(__name__)


def _to_dict(obj):
    """递归将 SimpleNamespace 转回 dict，供 OpenAI SDK 使用"""
    if isinstance(obj, types.SimpleNamespace):
        return {k: _to_dict(v) for k, v in obj.__dict__.items()}
    return obj


# 主模型配置：从 model_key.yaml 的 main_model 节读取 provider 名称
main_model_cfg = getattr(global_cfg, "main_model", None)
model_provider = getattr(main_model_cfg, "provider", "DeepSeek")


def _resolve_provider_cfg(provider_name):
    """根据 provider 名称从 model_key.yaml 顶层配置中查找对应连接参数。

    支持 main_model / simple_chat / memory.llm_re_ranking /
    memory.rerank_re_ranking 等节引用同一个 provider（如 "DeepSeek"），
    返回包含 api_key、base_url、model_name、extra_body 的 SimpleNamespace。
    """
    provider_cfg = getattr(global_cfg, provider_name, None)
    if provider_cfg is None:
        logger.warning(f"provider '{provider_name}' 未在 model_key.yaml 顶层定义")
    return provider_cfg


def _merge_extra_body(base_extra_body, override_extra_body):
    """合并 provider 顶层 extra_body 与专用节 extra_body。

    专用节（如 simple_chat / memory.llm_re_ranking）的配置优先，
    与 provider 顶层配置在 extra_body 层面冲突时以专用节为准。

    Args:
        base_extra_body: provider 顶层配置中的 extra_body（可能为 None）
        override_extra_body: 专用节配置中的 extra_body（可能为 None）

    Returns:
        合并后的 dict；两者皆空时返回 None
    """
    base = _to_dict(base_extra_body) if base_extra_body else {}
    override = _to_dict(override_extra_body) if override_extra_body else {}

    if not base and not override:
        return None

    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_extra_body(merged[key], value)
        else:
            merged[key] = value
    return merged


provider_cfg = _resolve_provider_cfg(model_provider)
api_key = getattr(provider_cfg, "api_key", None) if provider_cfg else None
base_url = getattr(provider_cfg, "base_url", None) if provider_cfg else None
model_name = getattr(provider_cfg, "model_name", None) if provider_cfg else None
extra_body = getattr(provider_cfg, 'extra_body', None)

# 检查当前 provider 是否限制 system 角色只能出现在消息列表首位
_restrict_system_at_start = False
_restricted_providers = []
if hasattr(global_cfg, 'api_constraints'):
    constraint_cfg = getattr(global_cfg.api_constraints, 'system_role_only_at_start', None)
    if constraint_cfg is not None:
        _restricted_providers = getattr(constraint_cfg, 'providers', [])
        if model_provider in _restricted_providers:
            _restrict_system_at_start = True

logger.info(f"Provider={model_provider}, system_role_only_at_start={_restrict_system_at_start}, restricted_providers={_restricted_providers}")


def _sanitize_messages_for_provider(messages):
    """根据 provider 限制清洗消息列表。

    部分 provider（如 MiniMax）限制 system 角色只能出现在消息列表首位。
    此函数将首条之后的 system 消息转换为 user 消息，确保 API 调用不报错。
    """
    if not _restrict_system_at_start:
        return messages

    result = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "system" and i > 0:
            result.append({"role": "user", "content": msg["content"]})
        else:
            result.append(msg)
    return result


client = OpenAI(
    api_key=api_key,
    base_url=base_url,
    http_client=httpx.Client(verify=False),
    max_retries=0,  # 禁用SDK自动重试，避免超时后3倍等待（记忆检索等场景重试无意义）
)

# 模块级上下文变量：供记忆系统等间接调用 simple_chat 时标注 query/turn
_context_query = ""
_context_turn = ""


def set_context(query: str = "", turn: str = ""):
    """设置当前 LLM 调用上下文，供记忆系统等间接调用时标记 token 统计。

    在记忆操作（提取/整理/进化/召回）开始前调用 set_context() 设置上下文，
    操作结束后调用 set_context() 清除（传空字符串）。
    """
    global _context_query, _context_turn
    _context_query = query
    _context_turn = turn


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
        # 根据 provider 限制清洗消息（如 MiniMax 要求 system 只能在首位）
        msg = _sanitize_messages_for_provider(msg)

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
            # 注意：不能 break！OpenAI 流式协议中 usage chunk 在 finish_reason
            # 之后才发送，break 会导致 usage 永远无法被捕获，token 统计丢失。
            # 流会在发送完 usage chunk 后自然结束。
            if choice.finish_reason is not None:
                if choice.finish_reason == "length":
                    # 长度截断，添加明确标记
                    full_content += "\n[ERROR: 输出被截断，max_tokens 不足]"
                    is_truncated = True
                elif choice.finish_reason != "stop":
                    # 非正常结束，给出原因提示（stop 是最健康的信号，不添加额外文字）
                    full_content += f"\n\n[流结束原因: {choice.finish_reason}]"

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


# ===== 单轮聊天（simple_chat）专用客户端 =====
_simple_chat_client = None
_simple_chat_model_name = ""
_simple_chat_extra_body = None


def _get_simple_chat_client():
    """获取单轮聊天（simple_chat）专用客户端。

    根据 model_key.yaml 中 simple_chat.provider 指定的配置创建独立客户端。
    provider 顶层 connection 与 simple_chat.extra_body 合并，simple_chat 配置优先。
    若 simple_chat 配置缺失或 provider 未定义，回退到主模型客户端。

    Returns:
        (client, model_name, extra_body) 元组
    """
    global _simple_chat_client, _simple_chat_model_name, _simple_chat_extra_body

    if _simple_chat_client is not None:
        return _simple_chat_client, _simple_chat_model_name, _simple_chat_extra_body

    simple_chat_cfg = getattr(global_cfg, "simple_chat", None)
    if simple_chat_cfg is None:
        logger.warning("配置中缺少 simple_chat 节，回退到主模型")
        _simple_chat_client = client
        _simple_chat_model_name = model_name
        _simple_chat_extra_body = _to_dict(extra_body) if extra_body else None
        return _simple_chat_client, _simple_chat_model_name, _simple_chat_extra_body

    simple_chat_provider = getattr(simple_chat_cfg, "provider", model_provider)
    provider_cfg = _resolve_provider_cfg(simple_chat_provider)
    if provider_cfg is None:
        logger.warning(
            f"simple_chat.provider={simple_chat_provider} 未在 model_key.yaml 中定义，"
            f"回退到主模型"
        )
        _simple_chat_client = client
        _simple_chat_model_name = model_name
        _simple_chat_extra_body = _to_dict(extra_body) if extra_body else None
        return _simple_chat_client, _simple_chat_model_name, _simple_chat_extra_body

    _simple_chat_model_name = getattr(provider_cfg, "model_name", model_name)
    _simple_chat_extra_body = _merge_extra_body(
        getattr(provider_cfg, "extra_body", None),
        getattr(simple_chat_cfg, "extra_body", None),
    )
    try:
        _simple_chat_client = OpenAI(
            api_key=getattr(provider_cfg, "api_key", api_key),
            base_url=getattr(provider_cfg, "base_url", base_url),
            http_client=httpx.Client(verify=False),
            max_retries=0,
        )
        logger.info(
            f"simple_chat 客户端已初始化: provider={simple_chat_provider}, "
            f"model={_simple_chat_model_name}, extra_body={_simple_chat_extra_body}"
        )
    except Exception as e:
        logger.error(f"simple_chat 客户端初始化失败: {e}，回退到主模型")
        _simple_chat_client = client
        _simple_chat_model_name = model_name
        _simple_chat_extra_body = _to_dict(extra_body) if extra_body else None

    return _simple_chat_client, _simple_chat_model_name, _simple_chat_extra_body


def simple_chat(prompt: str, temperature: float = 0.3, max_tokens: int = 1024,
                  query: str = "", turn: str = "", timeout: float = 120) -> str:
    """非流式单轮调用，供记忆系统（提取器/整理器/进化器及意图压缩）使用。

    接收纯文本 prompt，构建单条 user 消息发送给 LLM，返回纯文本响应。
    发生异常时返回空字符串，不抛出异常，确保记忆系统流程不被中断。

    模型与 extra_body 由 model_key.yaml 中 simple_chat 节配置：
    provider 递归引用顶层连接参数，thinking 模式按 simple_chat.extra_body 的配置生效
    （配置 disabled 就 disabled，配置 enabled 就 enabled）。

    Args:
        prompt: 完整的提示词文本
        temperature: 采样温度
        max_tokens: 最大输出 token 数
        query: 触发此次调用的 CLI 命令或上下文（用于 token 统计）。
               若未传入，则使用模块级上下文 _context_query。
        turn: 轮次标识（如 CLI_COMMAND、CLI_RESULT，用于 token 统计）。
              若未传入，则使用模块级上下文 _context_turn。
        timeout: HTTP 请求超时秒数，默认 180 秒。
                 httpx.Client 默认超时仅 5 秒，对 LLM 非流式调用远远不够。

    Returns:
        LLM 响应的纯文本内容；异常时返回空字符串
    """
    try:
        sc_client, sc_model, sc_extra = _get_simple_chat_client()
        messages = _sanitize_messages_for_provider([{"role": "user", "content": prompt}])
        api_kwargs = dict(
            model=sc_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
            timeout=timeout,
        )
        # thinking 模式由 simple_chat.extra_body 决定：
        # 配置 disabled 就 disabled，配置 enabled 就 enabled
        if sc_extra:
            api_kwargs["extra_body"] = sc_extra

        response = sc_client.chat.completions.create(**api_kwargs)
        choice = response.choices[0]
        content = choice.message.content or ""

        # 记录 token 统计：优先使用参数传入的 query/turn，否则使用模块级上下文
        if response.usage:
            from src.utility.token_statistics import record_token_usage
            effective_query = query if query else _context_query
            effective_turn = turn if turn else _context_turn
            record_token_usage(
                model_name=sc_model,
                prompt_tokens=response.usage.prompt_tokens or 0,
                cached_tokens=getattr(
                    getattr(response.usage, 'prompt_tokens_details', None),
                    'cached_tokens', 0
                ) or 0,
                completion_tokens=response.usage.completion_tokens or 0,
                query=effective_query,
                turn=effective_turn,
            )

        # 检查是否因 max_tokens 不足被截断
        if choice.finish_reason == "length":
            logger.warning(
                f"simple_chat 响应被截断 (finish_reason=length, max_tokens={max_tokens})，"
                f"返回内容可能不完整"
            )

        return content.strip()
    except Exception as e:
        logger.error(f"simple_chat 调用失败: {e}")
        return ""


# ===== LLM 精排模型（llm_re_ranking）专用调用 =====

# LLM 精排模型客户端（基于 memory.llm_re_ranking.provider 配置，惰性初始化）
_rerank_client = None
_rerank_model_name = ""
_rerank_extra_body = None


def _get_rerank_client():
    """获取 LLM 精排模型客户端。

    根据 model_key.yaml 中 memory.llm_re_ranking.provider 指定的配置创建独立客户端。
    provider 顶层 connection 与 llm_re_ranking.extra_body 合并，
    llm_re_ranking 配置优先。
    若 llm_re_ranking 配置缺失或 provider 未定义，回退到主模型客户端。

    Returns:
        (client, model_name, extra_body) 元组
    """
    global _rerank_client, _rerank_model_name, _rerank_extra_body

    if _rerank_client is not None:
        return _rerank_client, _rerank_model_name, _rerank_extra_body

    memory_cfg = getattr(global_cfg, "memory", None)
    rerank_cfg = getattr(memory_cfg, "llm_re_ranking", None) if memory_cfg else None
    if rerank_cfg is None:
        logger.warning("配置中缺少 memory.llm_re_ranking 节，精排回退到主模型")
        _rerank_client = client
        _rerank_model_name = model_name
        _rerank_extra_body = _to_dict(extra_body) if extra_body else None
        return _rerank_client, _rerank_model_name, _rerank_extra_body

    rerank_provider = getattr(rerank_cfg, "provider", model_provider)
    provider_cfg = _resolve_provider_cfg(rerank_provider)
    if provider_cfg is None:
        logger.warning(
            f"memory.llm_re_ranking.provider={rerank_provider} 未在 model_key.yaml 中定义，"
            f"精排回退到主模型"
        )
        _rerank_client = client
        _rerank_model_name = model_name
        _rerank_extra_body = _to_dict(extra_body) if extra_body else None
        return _rerank_client, _rerank_model_name, _rerank_extra_body

    _rerank_model_name = getattr(provider_cfg, "model_name", model_name)
    _rerank_extra_body = _merge_extra_body(
        getattr(provider_cfg, "extra_body", None),
        getattr(rerank_cfg, "extra_body", None),
    )
    try:
        _rerank_client = OpenAI(
            api_key=getattr(provider_cfg, "api_key", api_key),
            base_url=getattr(provider_cfg, "base_url", base_url),
            http_client=httpx.Client(verify=False),
            max_retries=0,
        )
        logger.info(
            f"LLM 精排模型已初始化: provider={rerank_provider}, "
            f"model={_rerank_model_name}, extra_body={_rerank_extra_body}"
        )
    except Exception as e:
        logger.error(f"LLM 精排客户端初始化失败: {e}，回退到主模型")
        _rerank_client = client
        _rerank_model_name = model_name
        _rerank_extra_body = _to_dict(extra_body) if extra_body else None

    return _rerank_client, _rerank_model_name, _rerank_extra_body


def rerank_simple_chat(prompt: str, temperature: float = 0.1,
                       max_tokens: int = 2048, timeout: float = 30) -> str:
    """精排专用非流式单轮调用。

    使用 model_key.yaml 中 re-ranking.provider 指定的模型进行 LLM 精排。
    若 re-ranking 配置缺失，降级使用主模型（simple_chat）。

    Args:
        prompt: 精排 Prompt
        temperature: 采样温度（精排默认 0.1，保证评分稳定）
        max_tokens: 最大输出 token 数
        timeout: HTTP 请求超时秒数

    Returns:
        LLM 响应的纯文本内容；异常时返回空字符串
    """
    try:
        rerank_client, rerank_model, rerank_extra = _get_rerank_client()
        messages = _sanitize_messages_for_provider(
            [{"role": "user", "content": prompt}]
        )
        api_kwargs = dict(
            model=rerank_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
            timeout=timeout,
        )
        # thinking 模式由 memory.llm_re_ranking.extra_body 决定：
        # 配置 disabled 就 disabled，配置 enabled 就 enabled
        if rerank_extra:
            api_kwargs["extra_body"] = rerank_extra

        response = rerank_client.chat.completions.create(**api_kwargs)

        # 记录 token 统计：精排模型消耗的 token 也需统计
        # 使用模块级上下文（由 query_loop 或 CLI 在调用前通过 set_context 设置）
        # turn 追加 -rerank 后缀以区分精排调用与普通调用
        if response.usage:
            from src.utility.token_statistics import record_token_usage
            effective_turn = f"{_context_turn}-rerank" if _context_turn else "rerank"
            record_token_usage(
                model_name=rerank_model,
                prompt_tokens=response.usage.prompt_tokens or 0,
                cached_tokens=getattr(
                    getattr(response.usage, 'prompt_tokens_details', None),
                    'cached_tokens', 0
                ) or 0,
                completion_tokens=response.usage.completion_tokens or 0,
                query=_context_query,
                turn=effective_turn,
            )

        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"精排调用失败: {e}")
        return ""


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
