"""
memory_2 LLM 召回检索器

核心理念：不依赖 Embedding 和向量索引，直接将记忆条目以原始文本形式
交给 LLM，由 LLM 判断每条记忆与当前查询的相关性并输出结构化评分。

流程：
1. 查询构造：拼接用户 query + 最近对话上下文
2. 候选召回：从 MemoryStore 预过滤（标签、时间窗口、角色）
3. LLM 精排：候选记忆列表 + 查询 → LLM → 结构化评分（JSON）
4. 结果排序：按 LLM 评分降序，取 top_k
5. 分数回写：将 LLM 评分回写到记忆条目的 last_score
"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# LLM 精排系统提示词
_RETRIEVAL_SYSTEM_PROMPT = """你是一个记忆相关性评分助手。你需要根据用户当前的查询，判断每条记忆的相关性并给出评分。

## 评分维度
1. **主题相关性**（0~1）：记忆内容与当前查询的话题是否相关。
   - 完全无关 → 0.0
   - 涉及相同领域/话题 → 0.5~0.7
   - 直接回答或直接相关 → 0.8~1.0

2. **任务连续性**（0~1）：记忆是否属于同一任务/会话的延续。
   - 不同任务 → 0.0
   - 同一会话的相邻轮次 → 0.6~0.8
   - 同一任务的直接延续 → 0.9~1.0

3. **实体重叠**（0~1）：记忆中出现的关键实体（文件名、函数名、类名、路径、变量名）是否出现在当前查询中。
   - 无重叠 → 0.0
   - 部分重叠 → 0.4~0.6
   - 高度重叠 → 0.8~1.0

4. **时间新近度**（0~1）：记忆的时间戳越新，得分越高。
   - 超过 30 天 → 0.0~0.2
   - 7 天内 → 0.5~0.7
   - 今天/昨天 → 0.8~1.0

## 综合评分
综合以上 4 个维度，给出最终的相关性评分（0~1），精确到小数点后两位。

## 输出格式（严格遵守）
你必须输出一个 JSON 数组，每个元素对应一条记忆，格式如下：
```json
[
  {
    "id": "记忆ID",
    "score": 0.85,
    "reason": "简短判断理由（20字以内）"
  }
]
```

不要输出任何 JSON 数组以外的内容。不要加前缀、后缀或解释。"""

# 用户查询 Prompt 模板
_RETRIEVAL_USER_PROMPT_TEMPLATE = """## 当前查询
{query}

## 对话上下文（最近几轮）
{context}

## 候选记忆列表
以下共有 {count} 条候选记忆，请为每条记忆评分。

{memory_list}"""


class MemoryRetriever:
    """LLM 召回检索器。

    职责：
    - 候选集预过滤（通过 MemoryStore.query()）
    - 构造 LLM 评分 Prompt
    - 解析 LLM 结构化评分输出
    - 分批处理超量候选集
    - 综合打分（LLM 评分 + 时间 + 重要性）
    """

    def __init__(
        self,
        config: Any = None,
        llm_chat_fn: Optional[Callable[..., str]] = None,
        system_prompt: Optional[str] = None,
        max_candidates_per_batch: int = 50,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        score_field: str = "relevance",
        llm_score_weight: float = 0.7,
        recency_weight: float = 0.2,
        importance_weight: float = 0.1,
        default_top_k: int = 5,
        max_top_k: int = 20,
        forgetting_strategy: str = "exponential",
        half_life_hours: float = 72.0,
    ):
        """
        Args:
            config: memory_2 配置节
            llm_chat_fn: LLM 对话函数，签名 (messages: list[dict], temperature, max_tokens) -> str
            system_prompt: 自定义系统提示词（覆盖默认）
            max_candidates_per_batch: 单批最多候选记忆数
            temperature: LLM 评分温度
            max_tokens: LLM 响应最大 token 数
            score_field: 解析响应时的评分字段名
            llm_score_weight: LLM 评分权重
            recency_weight: 时间新近度权重
            importance_weight: 重要性权重
            default_top_k: 默认返回条数
            max_top_k: 最大允许返回条数
            forgetting_strategy: 遗忘策略
            half_life_hours: 半衰期（小时）
        """
        self._llm_chat_fn = llm_chat_fn
        self._system_prompt = system_prompt or _RETRIEVAL_SYSTEM_PROMPT
        self._max_candidates_per_batch = max_candidates_per_batch
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._score_field = score_field
        self._llm_score_weight = llm_score_weight
        self._recency_weight = recency_weight
        self._importance_weight = importance_weight
        self._default_top_k = default_top_k
        self._max_top_k = max_top_k
        self._forgetting_strategy = forgetting_strategy
        self._half_life_seconds = half_life_hours * 3600

    def set_llm_chat_fn(self, chat_fn: Callable[..., str]) -> None:
        """注入 LLM 对话函数。"""
        self._llm_chat_fn = chat_fn

    # ------------------------------------------------------------------ #
    #  检索主流程
    # ------------------------------------------------------------------ #

    def search(
        self,
        query: str,
        store: Any,  # MemoryStore 实例
        context: str = "",
        top_k: int = None,
        role_filter: Optional[str] = None,
        tag_filter: Optional[List[str]] = None,
        time_window_days: int = 0,
    ) -> List[Dict[str, Any]]:
        """执行 LLM 召回检索。

        Args:
            query: 用户查询文本
            store: MemoryStore 实例（用于候选召回和分数回写）
            context: 对话上下文文本（最近 N 轮）
            top_k: 返回条数
            role_filter: 按 role 过滤
            tag_filter: 按 tag 过滤
            time_window_days: 时间窗口过滤（天）

        Returns:
            list[dict]，每项含 id、content、score、timestamp 等字段
        """
        if top_k is None:
            top_k = self._default_top_k
        top_k = min(top_k, self._max_top_k)

        if self._llm_chat_fn is None:
            logger.warning("MemoryRetriever: LLM 函数未注入，返回空列表")
            return []

        # 1. 候选召回（预过滤）
        candidates = store.query(
            tag_filter=tag_filter,
            time_window_days=time_window_days,
            role_filter=role_filter,
            exclude_compressed=True,
            limit=self._max_candidates_per_batch * 3,  # 预过滤多拿一些，LLM 评分时再截断
        )

        if not candidates:
            logger.debug("MemoryRetriever.search: 候选集为空")
            return []

        logger.info(f"MemoryRetriever.search: query='{query[:50]}...', 候选 {len(candidates)} 条")

        # 2. LLM 精排（分批处理）
        all_scores: Dict[str, float] = {}
        batches = self._split_into_batches(candidates)

        for batch_idx, batch in enumerate(batches):
            try:
                batch_scores = self._score_batch(query, context, batch)
                all_scores.update(batch_scores)
            except Exception as e:
                logger.error(f"MemoryRetriever: 批次 {batch_idx} LLM 评分失败: {e}")
                # 失败批次使用默认分数
                for item in batch:
                    all_scores[item["id"]] = 0.0

        # 3. 综合打分
        scored = self._compute_final_scores(candidates, all_scores)

        # 4. 分数回写
        self._writeback_scores(store, all_scores)

        # 5. 排序 + 截断
        scored.sort(key=lambda x: x["score"], reverse=True)
        results = scored[:top_k]

        logger.info(f"MemoryRetriever.search: 返回 {len(results)} 条，最高分 {results[0]['score']:.2f}" if results else "MemoryRetriever.search: 无结果")
        return results

    # ------------------------------------------------------------------ #
    #  分批处理
    # ------------------------------------------------------------------ #

    def _split_into_batches(self, candidates: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """将候选集分成多批。"""
        if len(candidates) <= self._max_candidates_per_batch:
            return [candidates]

        batches = []
        for i in range(0, len(candidates), self._max_candidates_per_batch):
            batches.append(candidates[i: i + self._max_candidates_per_batch])
        logger.debug(f"MemoryRetriever: 分割为 {len(batches)} 批")
        return batches

    # ------------------------------------------------------------------ #
    #  LLM 评分
    # ------------------------------------------------------------------ #

    def _score_batch(
        self,
        query: str,
        context: str,
        batch: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        """对一个批次的候选记忆调用 LLM 评分。

        Returns:
            {memory_id: score} 映射
        """
        # 构造记忆列表文本
        memory_texts = []
        for i, item in enumerate(batch):
            ts = item.get("timestamp", "未知")[:19]
            role = item.get("role", "unknown")
            content = item.get("content", "")[:400]  # 截断长文本
            tags = ", ".join(item.get("tags", []))
            memory_texts.append(
                f"[{i}] id={item['id']}\n"
                f"    角色: {role}\n"
                f"    时间: {ts}\n"
                f"    标签: {tags}\n"
                f"    内容: {content}"
            )

        memory_list = "\n\n".join(memory_texts)

        # 构造 Prompt
        user_prompt = _RETRIEVAL_USER_PROMPT_TEMPLATE.format(
            query=query,
            context=context or "（无上下文）",
            count=len(batch),
            memory_list=memory_list,
        )

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # 调用 LLM
        response = self._llm_chat_fn(messages, temperature=self._temperature, max_tokens=self._max_tokens)
        if not response:
            logger.warning("MemoryRetriever._score_batch: LLM 返回空响应")
            return {}

        # 解析响应
        return self._parse_scores(response, batch)

    # ------------------------------------------------------------------ #
    #  解析 LLM 响应
    # ------------------------------------------------------------------ #

    def _parse_scores(self, response: str, batch: List[Dict[str, Any]]) -> Dict[str, float]:
        """解析 LLM 的结构化评分响应。

        期望格式：JSON 数组，每项含 id 和 score。
        容错处理：提取 JSON 代码块、兼容 markdown 包裹。
        """
        scores: Dict[str, float] = {}

        # 1. 尝试提取 JSON 代码块
        json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接解析整个响应
            json_str = response

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            # 尝试修复常见格式问题
            logger.warning("MemoryRetriever: JSON 解析失败，尝试修复")
            try:
                # 去除首尾无关字符
                trimmed = json_str.strip()
                if not trimmed.startswith("["):
                    trimmed = "[" + trimmed.split("[", 1)[-1] if "[" in trimmed else ""
                if trimmed and not trimmed.endswith("]"):
                    trimmed = trimmed.rsplit("]", 1)[0] + "]" if "]" in trimmed else ""
                parsed = json.loads(trimmed)
            except json.JSONDecodeError:
                logger.error(f"MemoryRetriever: 无法解析 LLM 响应: {response[:200]}")
                return {}

        if not isinstance(parsed, list):
            logger.warning("MemoryRetriever: LLM 响应不是数组格式")
            return {}

        for item in parsed:
            if not isinstance(item, dict):
                continue
            mid = item.get("id")
            score = item.get(self._score_field, item.get("score"))
            if mid and score is not None:
                try:
                    scores[str(mid)] = float(score)
                except (ValueError, TypeError):
                    pass

        logger.debug(f"MemoryRetriever._parse_scores: 解析到 {len(scores)} 个评分")
        return scores

    # ------------------------------------------------------------------ #
    #  综合打分
    # ------------------------------------------------------------------ #

    def _compute_final_scores(
        self,
        candidates: List[Dict[str, Any]],
        llm_scores: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """综合 LLM 评分 + 时间衰减 + 重要性。

        Args:
            candidates: 候选记忆列表
            llm_scores: LLM 返回的 {id: score} 映射

        Returns:
            带 final_score 的记忆列表
        """
        import math
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        results = []

        for item in candidates:
            mid = item["id"]
            llm_score = llm_scores.get(mid, 0.0)

            # 时间衰减
            recency = self._compute_recency(item.get("timestamp"), now)

            # 重要性
            importance = item.get("importance", 0.5)

            # 综合
            final = (
                self._llm_score_weight * llm_score
                + self._recency_weight * recency
                + self._importance_weight * importance
            )

            result = dict(item)
            result["score"] = round(final, 4)
            result["llm_score"] = round(llm_score, 4)
            results.append(result)

        return results

    def _compute_recency(self, timestamp: Optional[str], now) -> float:
        """计算时间新近度得分（0~1）。"""
        if timestamp is None:
            return 0.0
        try:
            ts = datetime.fromisoformat(timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_seconds = (now - ts).total_seconds()
            if age_seconds < 0:
                return 1.0
        except (ValueError, TypeError):
            return 0.0

        if self._forgetting_strategy == "exponential":
            if self._half_life_seconds <= 0:
                return 1.0
            return math.pow(2, -age_seconds / self._half_life_seconds)
        elif self._forgetting_strategy == "linear":
            decay_days = 7 * 86400
            if age_seconds >= decay_days:
                return 0.0
            return 1.0 - age_seconds / decay_days
        else:
            return 1.0

    # ------------------------------------------------------------------ #
    #  分数回写
    # ------------------------------------------------------------------ #

    def _writeback_scores(self, store: Any, scores: Dict[str, float]) -> None:
        """将 LLM 评分回写到 MemoryStore。"""
        if not scores:
            return
        try:
            store.batch_update_scores(scores)
        except Exception as e:
            logger.error(f"MemoryRetriever._writeback_scores: 回写失败 {e}")

    # ------------------------------------------------------------------ #
    #  配置属性
    # ------------------------------------------------------------------ #

    @property
    def default_top_k(self) -> int:
        return self._default_top_k

    @property
    def max_top_k(self) -> int:
        return self._max_top_k