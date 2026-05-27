"""
使用 OpenAI SDK 和 Anthropic SDK 分别与 LLM 对话的测试函数。
依需求文档 d:/ai/tmp/4.txt 生成。
"""
from src.utility.config_loader import global_cfg
from openai import OpenAI


provider_cfg = getattr(global_cfg, global_cfg.model.provider)


def hello_with_openai() -> str:
    """
    使用 OpenAI API 向 LLM 发送 "hello" 并获取回复。
    配置参数参照 chat_llm.py 的加载方式。
    """

    client = OpenAI(
        api_key=provider_cfg.api_key,
        base_url=provider_cfg.base_url,
    )
    response = client.chat.completions.create(
        model=provider_cfg.model_name,
        messages=[{"role": "user", "content": "hello"}],
    )
    return response.choices[0].message.content


def hello_with_anthropic() -> str:
    """
    使用 Anthropic API 向 LLM 发送 "hello" 并获取回复。
    DeepSeek 提供 Anthropic 兼容接口，base_url 写死，api_key 引用配置文件。
    """
    from anthropic import Anthropic

    client = Anthropic(
        api_key=provider_cfg.api_key,
        base_url="https://api.deepseek.com/anthropic",
    )
    response = client.messages.create(
        model=provider_cfg.model_name,
        max_tokens=1024,
        messages=[{"role": "user", "content": "hello"}],
    )
    # response.content 可能包含 ThinkingBlock（无 .text），只提取 TextBlock
    text_parts = []
    for block in response.content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
    return "\n".join(text_parts)


if __name__ == "__main__":
    print()
    print(f"say hello to {provider_cfg.model_name} by OpenAI API")
    openai_txt = hello_with_openai()
    print()
    print(f"It's response is:")  # noqa: E231
    print()
    print(openai_txt)
    print()

    print(f"say hello to {provider_cfg.model_name} by Anthropic API")
    anthropic_txt = hello_with_anthropic()
    print()
    print(f"It's response is:")  # noqa: E231
    print()
    print(anthropic_txt)
    print()
