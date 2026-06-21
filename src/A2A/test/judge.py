"""
LLM 评判模块

调用 LLM 比对测试用例的实际输出与期望行为，判定 PASS / FAIL。
"""
from __future__ import annotations

import json
import logging
import time

from src.utility.config_loader import global_cfg
from .models import TestStatus

logger = logging.getLogger(__name__)

# 评判提示词模板
_JUDGE_PROMPT = """你是一个测试评判助手。根据以下信息，判断测试用例是否通过。

## 测试用例描述
{description}

## 期望行为
{expected}

## MyClaude 实际输出
{actual}

## 评判规则
- 如果实际输出体现了期望行为，输出 PASS（即使输出格式不完全一致）。
- 如果实际输出明显不满足期望行为，输出 FAIL。
- 如果无法确定（输出被截断、模糊不清、信息不足），输出 INCONCLUSIVE。
- **重要**：即使信息有限，也必须输出一个最佳判断的 JSON，不要返回空响应。

## 输出格式（严格 JSON，不要输出任何其他文字）
{{"verdict": "PASS" | "FAIL" | "INCONCLUSIVE", "reason": "简短理由（中文，≤50字）"}}
"""


model_provider = global_cfg.model.provider
provider_cfg = getattr(global_cfg, model_provider)
api_key = provider_cfg.api_key
base_url = provider_cfg.base_url
model_name = provider_cfg.model_name
extra_body = getattr(provider_cfg, 'extra_body', None)


class LLMJudge:
    """基于 LLM 的测试用例评判器"""

    def __init__(self):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model_name

    # ------------------------------------------------------------------

    def evaluate(self,
                 expected: str,
                 actual_output: str,
                 context: str = "",
                 check_type: str = "general") -> dict:
        """评判一次测试的结果，返回 {"pass": bool, "reason": str}"""
        prompt = _JUDGE_PROMPT.format(
            description=context or "N/A",
            expected=expected,
            actual=actual_output[:2000],  # 截断防止 token 超限
        )
        # 根据检查类型追加评判提示
        check_hints = {
            "file_created": "重点检查：是否创建了文件。",
            "file_modified": "重点检查：是否修改了已有文件。",
            "tool_chain": "重点检查：是否使用了正确的 XML 工具链（如 <create>/<done>）。",
            "log_generated": "重点检查：是否生成了日志文件。",
            "startup": "重点检查：服务是否正常启动。",
            "memory_aware": "重点检查：是否体现了对上下文的记忆。",
            "skill_triggered": "重点检查：是否触发了 Skill。",
            "path_safety": "重点检查：是否正确处理了路径安全。",
        }
        if check_type in check_hints:
            prompt += f"\n## 补充评判指导\n{check_hints[check_type]}"

        try:
            response = self._call_llm(prompt)
            verdict = self._parse_verdict(response)
            # 从 response JSON 中提取 reason 字段，而非整段原始响应
            reason = ""
            try:
                start = response.find("{")
                end = response.rfind("}") + 1
                if 0 <= start < end:
                    data = json.loads(response[start:end])
                    reason = data.get("reason", "")[:200]
            except (json.JSONDecodeError, ValueError):
                reason = response[:200]
            return {
                "pass": verdict == TestStatus.PASS,
                "verdict": verdict,
                "reason": reason,
            }
        except (ConnectionError, TimeoutError, OSError) as exc:
            logger.exception("Judge LLM call failed, defaulting to INCONCLUSIVE")
            return {
                "pass": False,
                "verdict": TestStatus.INCONCLUSIVE,
                "reason": str(exc),
            }

    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        """调用评判 LLM，最多重试 3 次对付空响应。"""
        if not self._api_key:
            logger.warning("No judge API key configured, using heuristic fallback")
            return '{"verdict": "INCONCLUSIVE", "reason": "no judge API key"}'

        try:
            from openai import OpenAI, APIError, APIConnectionError
        except ImportError:
            logger.warning("openai not installed, using heuristic fallback")
            return '{"verdict": "INCONCLUSIVE", "reason": "openai not installed"}'

        client_kwargs = {"api_key": self._api_key}
        if self._base_url:
            client_kwargs["base_url"] = self._base_url

        client = OpenAI(**client_kwargs)

        max_retries = 3
        base_max_tokens = 8192

        for attempt in range(1, max_retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": "你是测试评判专家。无论信息多少都必须输出严格JSON，不要返回空响应。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    max_tokens=base_max_tokens * attempt,
                )
                content = resp.choices[0].message.content
                if content and content.strip():
                    # 检查是否为有效 JSON
                    if self._is_parsable_verdict(content):
                        return content
                    else:
                        logger.warning(
                            "Judge LLM 返回非 JSON 内容 (attempt %d/%d): %.120s",
                            attempt, max_retries, content
                        )
                else:
                    logger.warning(
                        "Judge LLM returned empty content (attempt %d/%d)",
                        attempt, max_retries
                    )
            except (APIConnectionError, APIError) as exc:
                logger.warning("Judge LLM API error (attempt %d/%d): %s", attempt, max_retries, exc)
            except Exception as exc:
                logger.warning("Judge LLM unexpected error (attempt %d/%d): %s", attempt, max_retries, exc)

            if attempt < max_retries:
                time.sleep(0.5 * attempt)

        return '{"verdict": "INCONCLUSIVE", "reason": "LLM 多次返回空或无效响应"}'

    @staticmethod
    def _is_parsable_verdict(raw: str) -> bool:
        """检查字符串是否包含可解析的 verdict JSON。"""
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if 0 <= start < end:
                data = json.loads(raw[start:end])
                if "verdict" in data:
                    return True
        except (json.JSONDecodeError, ValueError):
            pass
        return False

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_verdict(raw: str) -> TestStatus:
        """解析 LLM 返回的评判结果"""
        try:
            # 尝试提取 JSON
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if 0 <= start < end:
                data = json.loads(raw[start:end])
                v = data.get("verdict", "INCONCLUSIVE").upper()
                return TestStatus(v)
        except (json.JSONDecodeError, ValueError):
            pass

        # 启发式回退
        upper = raw.upper()
        if "PASS" in upper and "FAIL" not in upper:
            return TestStatus.PASS
        elif "FAIL" in upper:
            return TestStatus.FAIL
        else:
            return TestStatus.INCONCLUSIVE
