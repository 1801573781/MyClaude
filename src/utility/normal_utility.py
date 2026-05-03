import re


def strip_thinking(text: str) -> str:
    """过滤掉 LLM 的思考过程"""
    # 匹配 <think>...</think> 或  思考过程
    patterns = [
        r'<think>.*?</think>',
        # 仅当"思考过程"作为独立行开头时才匹配，最多向下吞 20 行，防止无限误伤
        r'(?:^|\n)\s*思考过程[：:]\s*(?:[^\n]*\n){0,20}?(?=\n\s*<|$)',
    ]
    for p in patterns:
        text = re.sub(p, '', text, flags=re.DOTALL)

    # 不去除首尾空行，直接return
    return text
    # return text.strip()



