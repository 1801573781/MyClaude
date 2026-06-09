"""
LLM 评判模块

调用 LLM 比对测试用例的实际输出与期望行为，判定 PASS / FAIL。
"""
from __future__ import annotations

import json
import logging
import os

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
- 如果无法确定（输出被截断、模糊不清），输出 INCONCLUSIVE。

## 输出格式（严格 JSON）
{{"verdict": "PASS" | "FAIL" | "INCONCLUSIVE", "reason": "简短理由"}}
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
            return {"pass": verdict == TestStatus.PASS, "reason": response[:200]}
        except (ConnectionError, TimeoutError, OSError) as exc:
            logger.exception("Judge LLM call failed, defaulting to INCONCLUSIVE")
            return {"pass": False, "reason": str(exc)}

    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        """调用评判 LLM"""
        if not self._api_key:
            logger.warning("No judge API key configured, using heuristic fallback")
            return '{"verdict": "INCONCLUSIVE", "reason": "no judge API key"}'

        try:
            from openai import OpenAI
        except ImportError:
            logger.warning("openai not installed, using heuristic fallback")
            return '{"verdict": "INCONCLUSIVE", "reason": "openai not installed"}'

        client_kwargs = {"api_key": self._api_key}
        if self._base_url:
            client_kwargs["base_url"] = self._base_url

        client = OpenAI(**client_kwargs)
        resp = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": "你是测试评判专家，输出严格JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        content = resp.choices[0].message.content
        if not content:
            logger.warning("Judge LLM returned empty content, using fallback")
            return '{"verdict": "INCONCLUSIVE", "reason": "LLM returned empty response"}'
        return content

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
