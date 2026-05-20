"""memory6 LLM 精排检索器 —— 对 Embedding 召回候选进行精确相关性评分

从 src/memory2/llm_retriever.py 重新实现，保持行为语义一致。
支持摘要标签粗筛、分批评分，防止 token 溢出。
"""

import logging
import re
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LLMRetriever:
    """调用 LLM 对候选记忆批量重评分。"""

    def __init__(self, config: Dict):
        self._cfg = config
        self._model = config.get("rater_model", "gpt-3.5-turbo")
        self._enabled = config.get("enable_llm_rater", True)
        self._batch_size = int(config.get("rater_batch_size", 10))

        self._client = None
        try:
            from openai import OpenAI
            api_key = config.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
            base_url = config.get("base_url")
            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = OpenAI(**kwargs)
        except ImportError:
            logger.warning("LLMRetriever: openai 未安装，LLM 评分不可用")
        except Exception as e:
            logger.warning(f"LLMRetriever: 初始化失败: {e}")

    def rate(self,
             query: str,
             candidates: List[Dict],
             tags: Optional[List[str]] = None) -> List[Tuple[str, float]]:
        """对候选记忆进行 LLM 精排。

        Args:
            query: 查询文本。
            candidates: 候选记忆列表，每条需含 id、content 字段。
            tags: 可选标签过滤器（只评分含这些标签的候选，其他保留原始分数）。

        Returns:
            (id, score) 列表，按分数降序。
        """
        if not candidates or not self._client or not self._enabled:
            return [(c["id"], c.get("score", 0)) for c in candidates]

        # 摘要标签粗筛
        filtered, passthrough = self._tag_filter(candidates, tags or [])

        rated = {}
        # 分批评分
        for i in range(0, len(filtered), self._batch_size):
            batch = filtered[i:i + self._batch_size]
            batch_scores = self._rate_batch(query, batch)
            rated.update(batch_scores)

        # 合并结果
        results = []
        for c in candidates:
            cid = c["id"]
            if cid in rated:
                results.append((cid, rated[cid]))
            elif cid in passthrough:
                results.append((cid, c.get("score", 0)))
            else:
                results.append((cid, c.get("score", 0)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def _tag_filter(self,
                    candidates: List[Dict],
                    tags: List[str]) -> Tuple[List[Dict], Dict[str, float]]:
        """按标签粗筛：不含指定标签的候选直接保留原始分数。"""
        if not tags:
            return candidates, {}

        filtered = []
        passthrough = {}
        for c in candidates:
            mem_tags = set(c.get("tags", []))
            if any(t in mem_tags for t in tags):
                filtered.append(c)
            else:
                passthrough[c["id"]] = c.get("score", 0)
        return filtered, passthrough

    def _rate_batch(self, query: str, batch: List[Dict]) -> Dict[str, float]:
        """对一批候选进行评分，返回 {id: score}。"""
        items_text = "\n".join(
            f"[{i}] {c['content'][:200]}"
            for i, c in enumerate(batch)
        )
        prompt = (
            f"查询：{query}\n\n"
            f"候选记忆：\n{items_text}\n\n"
            f"请对每条候选记忆与查询的相关性打分（0-10 的整数），"
            f"格式：序号:分数，每行一条。只输出评分，不要解释。"
        )

        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
            )
            answer = resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"LLMRetriever 评分调用失败: {e}")
            return {c["id"]: c.get("score", 0) for c in batch}

        scores: Dict[int, float] = {}
        for line in answer.strip().split("\n"):
            match = re.match(r"(\d+)\s*[:：]\s*(\d+)", line.strip())
            if match:
                idx = int(match.group(1))
                s = float(match.group(2)) / 10.0
                scores[idx] = s

        result = {}
        for i, c in enumerate(batch):
            result[c["id"]] = scores.get(i, c.get("score", 0))
        return result