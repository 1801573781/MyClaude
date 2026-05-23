
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

import hashlib
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

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

## 关键规则：无实质内容查询 —— 最高优先级
如果当前查询属于以下任一类别，则**所有记忆的评分均不得超过 0.30**，因为这类查询不可能与任何历史编程记忆相关：

- **问候/告别**：如 "hello"、"hi"、"你好"、"再见"、"bye"
- **确认/回应**：如 "好的"、"ok"、"嗯"、"知道了"、"收到"、"谢谢"
- **日期/时间/天气询问**：如 "今天星期几"、"现在几点"、"今天下雨了吗"、"明天多少度"
- **纯闲聊/无技术意图**：不包含文件名、函数名、代码片段、路径、技术名词、任务描述的普通对话
- **无实质语义**：纯标点、emoji、单字确认语

只有查询明确提及具体技术实体（文件名、函数名、路径、技术栈名词、报错信息、代码片段）或表达明确编码/开发任务意图时，才允许给出 0.30 以上的评分。

## 综合评分
综合以上 3 个维度（主题相关性、任务连续性、实体重叠），给出最终的相关性评分（0~1），精确到小数点后两位。

## 输出格式（严格遵守）
你必须输出一个 JSON 数组，每个元素对应一条记忆，格式如下：
```json
[
  {
    "memory_index": "记忆ID",
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
        default_top_k: int = 5,
        max_top_k: int = 20,
        min_relevance: float = 0.50,
        cache_ttl_seconds: int = 60,
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
            default_top_k: 默认返回条数
            max_top_k: 最大允许返回条数
            min_relevance: 最低相关性阈值，低于此值的记忆将被过滤
            cache_ttl_seconds: LLM 评分缓存 TTL（秒）
        """
        self._llm_chat_fn = llm_chat_fn
        self._system_prompt = system_prompt or _RETRIEVAL_SYSTEM_PROMPT
        self._max_candidates_per_batch = max_candidates_per_batch
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._score_field = score_field
        self._default_top_k = default_top_k
        self._max_top_k = max_top_k
        self._min_relevance = min_relevance
        self._cache_ttl_seconds = cache_ttl_seconds

        # LLM 评分缓存：{cache_key: (scores_dict, expiry_timestamp)}
        self._score_cache: Dict[str, Tuple[Dict[str, float], float]] = {}

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
        """执行 LLM 召回检索。所有评分完全由 LLM 判断，不依赖任何代码层面的算法。

        Args:
            query: 用户查询文本
            store: MemoryStore 实例（用于候选召回和分数回写）
            context: 对话上下文文本（最近 N 轮）
            top_k: 返回条数
            role_filter: 按 role 过滤
            tag_filter: 按 tag 过滤
            time_window_days: 时间窗口过滤（天）

        Returns:
            list[dict]，每项含 id、content、score、llm_score、reason、timestamp 等字段
        """
        if top_k is None:
            top_k = self._default_top_k
        top_k = min(top_k, self._max_top_k)

        # 1. 候选召回（阶梯式预过滤）
        candidates = self._get_candidates(store, tag_filter, role_filter, time_window_days)

        if not candidates:
            logger.debug("MemoryRetriever.search: 候选集为空")
            return []

        candidate_ids = [c["id"] for c in candidates]
        logger.info(f"MemoryRetriever.search: query='{query[:50]}...', 候选 {len(candidates)} 条")

        if self._llm_chat_fn is None:
            logger.warning("MemoryRetriever: LLM 函数未注入，无法评分")
            return []

        # 2. LLM 评分（带缓存）
        all_scores: Dict[str, float] = {}
        all_reasons: Dict[str, str] = {}
        cache_key = self._compute_cache_key(query, candidate_ids)
        cached = self._get_cached_scores(cache_key)
        if cached:
            all_scores = cached
            logger.info("MemoryRetriever.search: 命中 LLM 评分缓存")
        else:
            # 冷启动检测：所有 last_score 均为 None → 提高温度
            is_cold = all(c.get("last_score") is None for c in candidates)
            effective_temp = 0.3 if is_cold else self._temperature
            if is_cold:
                logger.info("MemoryRetriever.search: 检测到冷启动，使用 temperature=0.3")

            # LLM 精排（分批处理）
            batches = self._split_into_batches(candidates)

            for batch_idx, batch in enumerate(batches):
                try:
                    batch_scores, batch_reasons = self._score_batch(query, context, batch, temperature=effective_temp)
                    all_scores.update(batch_scores)
                    all_reasons.update(batch_reasons)
                except Exception as e:
                    logger.error(f"MemoryRetriever: 批次 {batch_idx} LLM 评分失败: {e}")
                    for item in batch:
                        all_scores[item["id"]] = 0.0

            # 多批次交叉校准
            if len(batches) > 1:
                all_scores = self._cross_batch_normalize(batches, all_scores, query, context,
                                                          temperature=effective_temp)

            # 写入缓存
            self._set_cached_scores(cache_key, all_scores)

        # 3. 分数回写
        self._writeback_scores(store, all_scores)

        # 4. 构建结果：score 直接使用 LLM 评分
        scored = self._compute_final_scores(candidates, all_scores, all_reasons)

        # 5. 按最低相关性阈值过滤 + 排序 + 截断
        min_relevance = getattr(self, "_min_relevance", 0.50)
        filtered = [s for s in scored if s.get("llm_score", 0) >= min_relevance]
        filtered.sort(key=lambda x: x["score"], reverse=True)

        if not filtered:
            logger.info(f"MemoryRetriever.search: 所有 {len(scored)} 条记忆均低于阈值 {min_relevance}，返回空列表")
            return []

        results = filtered[:top_k]

        if results:
            logger.info(
                f"MemoryRetriever.search: 候选 {len(scored)} 条，过滤后 {len(filtered)} 条，"
                f"返回 {len(results)} 条，最高分 {results[0]['score']:.2f}"
            )
        else:
            logger.info("MemoryRetriever.search: 无结果")
        return results

    # ------------------------------------------------------------------ #
    #  阶梯式预过滤
    # ------------------------------------------------------------------ #

    def _get_candidates(
        self,
        store: Any,
        tag_filter: Optional[List[str]],
        role_filter: Optional[str],
        time_window_days: int,
    ) -> List[Dict[str, Any]]:
        """阶梯式预过滤：时间窗口 → 重要性 → last_score 逐步削减候选量。

        确保送入 LLM 评分的候选不超过 max_candidates_per_batch * 2。
        """
        max_ideal = self._max_candidates_per_batch * 2

        # Step 1: 时间窗口预过滤
        candidates = store.query(
            tag_filter=tag_filter,
            time_window_days=time_window_days or 30,
            role_filter=role_filter,
            exclude_compressed=True,
            limit=max_ideal + 50,
        )

        if len(candidates) <= max_ideal:
            return candidates

        # Step 2: 按重要性粗筛（删除 importance < 0.3 的低重要性条目）
        important = [c for c in candidates if c.get("importance", 0.5) >= 0.3]
        if len(important) <= max_ideal:
            return important

        # Step 3: 按 last_score 粗筛（优先保留有评分且评分高的）
        scored = [c for c in important if c.get("last_score") is not None]
        unscored = [c for c in important if c.get("last_score") is None]
        scored.sort(key=lambda x: x.get("last_score", 0), reverse=True)

        result = scored[:max_ideal] + unscored
        return result[:max_ideal]

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
    #  多批次交叉校准
    # ------------------------------------------------------------------ #

    def _cross_batch_normalize(
        self,
        batches: List[List[Dict[str, Any]]],
        all_scores: Dict[str, float],
        query: str,
        context: str,
        temperature: float = 0.1,
    ) -> Dict[str, float]:
        """多批次交叉校准：取每批 top-3 汇总送 LLM 二次评分。

        Args:
            batches: 各批次候选列表
            all_scores: 原始评分 {id: score}
            query: 查询文本
            context: 上下文

        Returns:
            校准后的评分字典
        """
        # 收集每批 top-3
        top_ids = []
        for batch in batches:
            sorted_batch = sorted(batch, key=lambda x: all_scores.get(x["id"], 0), reverse=True)
            top_ids.extend([m["id"] for m in sorted_batch[:3]])

        # 去重
        top_ids = list(dict.fromkeys(top_ids))

        if not top_ids or not self._llm_chat_fn:
            return all_scores

        # 获取 top-3 记忆完整内容
        top_memories = []
        for batch in batches:
            for m in batch:
                if m["id"] in top_ids:
                    top_memories.append(m)
                    break

        if not top_memories:
            return all_scores

        # 构造校准 Prompt
        memory_texts = []
        for i, item in enumerate(top_memories):
            ts = item.get("timestamp", "未知")[:19]
            content = item.get("content", "")[:400]
            memory_texts.append(f"[{i}] id={item['id']}\n    时间: {ts}\n    内容: {content}")

        memory_list = "\n\n".join(memory_texts)

        calibrate_prompt = (
            f"请对以下 {len(top_memories)} 条高优先级记忆进行交叉校准评分。\n\n"
            f"当前查询: {query}\n\n"
            f"对话上下文: {context or '（无）'}\n\n"
            f"{memory_list}\n\n"
            f"请输出 JSON 数组，格式同前，校准所有记忆的相对分数。"
        )

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": calibrate_prompt},
        ]

        try:
            response = self._llm_chat_fn(messages, temperature=temperature, max_tokens=self._max_tokens)
            if response:
                cal_scores, _ = self._parse_scores(response, top_memories)
                # 更新评分
                for mid, score in cal_scores.items():
                    all_scores[mid] = score
                logger.debug(f"MemoryRetriever: 交叉校准 {len(cal_scores)} 条")
        except Exception as e:
            logger.warning(f"MemoryRetriever._cross_batch_normalize: 校准失败 ({e})")

        return all_scores

    # ------------------------------------------------------------------ #
    #  LLM 评分缓存
    # ------------------------------------------------------------------ #

    def _get_cached_scores(self, cache_key: str) -> Optional[Dict[str, float]]:
        """获取缓存的评分。过期则删除并返回 None。"""
        if cache_key in self._score_cache:
            scores, expiry = self._score_cache[cache_key]
            if time.time() < expiry:
                return dict(scores)
            else:
                del self._score_cache[cache_key]
        return None

    def _set_cached_scores(self, cache_key: str, scores: Dict[str, float]) -> None:
        """写入缓存。"""
        expiry = time.time() + self._cache_ttl_seconds
        self._score_cache[cache_key] = (dict(scores), expiry)
        # 限制缓存大小
        if len(self._score_cache) > 100:
            oldest = min(self._score_cache, key=lambda k: self._score_cache[k][1])
            del self._score_cache[oldest]

    # ------------------------------------------------------------------ #
    #  LLM 评分
    # ------------------------------------------------------------------ #

    def _score_batch(
        self,
        query: str,
        context: str,
        batch: List[Dict[str, Any]],
        temperature: float = None,
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """对一个批次的候选记忆调用 LLM 评分。

        Returns:
            ({memory_id: score}, {memory_id: reason}) 映射
        """
        if temperature is None:
            temperature = self._temperature

        # 构造记忆列表文本
        memory_texts = []
        for i, item in enumerate(batch):
            ts = item.get("timestamp", "未知")[:19]
            role = item.get("role", "unknown")
            content = item.get("content", "")[:400]
            tags = ", ".join(item.get("tags", []))
            memory_texts.append(
                f"[{i}] id={item['id']}\n"
                f"    角色: {role}\n"
                f"    时间: {ts}\n"
                f"    标签: {tags}\n"
                f"    内容: {content}"
            )

        memory_list = "\n\n".join(memory_texts)

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

        response = self._llm_chat_fn(messages, temperature=temperature, max_tokens=self._max_tokens)
        if not response:
            logger.warning("MemoryRetriever._score_batch: LLM 返回空响应")
            return {}, {}

        return self._parse_scores(response, batch)

    # ------------------------------------------------------------------ #
    #  解析 LLM 响应
    # ------------------------------------------------------------------ #

    def _parse_scores(
        self,
        response: str,
        batch: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """解析 LLM 的结构化评分响应。

        期望格式：JSON 数组，每项含 memory_index、score 和可选的 reason。
        容错处理：提取 JSON 代码块、兼容 markdown 包裹、修复常见格式问题、
        逐条正则回退。

        Returns:
            ({memory_id: score}, {memory_id: reason})
        """
        scores: Dict[str, float] = {}
        reasons: Dict[str, str] = {}

        # 1. 尝试提取 JSON 代码块
        json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = response

        # 2. 尝试直接解析
        parsed = self._try_parse_json_array(json_str)

        # 3. 如果失败，尝试截取 JSON 数组片段再解析
        if parsed is None:
            logger.warning("MemoryRetriever: JSON 解析失败，尝试修复")
            # 找到最外层 [ ... ]
            start = json_str.find("[")
            end = json_str.rfind("]")
            if start != -1 and end != -1 and end > start:
                trimmed = json_str[start:end + 1]
                parsed = self._try_parse_json_array(trimmed)

        if not isinstance(parsed, list) or len(parsed) == 0:
            # 逐条正则回退
            logger.warning("MemoryRetriever: JSON 解析失败，使用逐条正则回退")
            fallback_scores = self._fallback_parse_scores(response, batch)
            return fallback_scores, {}

        # 建立序号→UUID映射，兼容LLM返回序号或UUID两种情况
        seq_to_uuid = {str(i): item["id"] for i, item in enumerate(batch)}

        for item in parsed:
            if not isinstance(item, dict):
                continue
            mid = item.get("memory_index")
            score = item.get(self._score_field, item.get("score"))
            reason = item.get("reason", "")
            if mid is not None and score is not None:
                try:
                    # 若LLM返回的是序号（纯数字），映射为实际UUID
                    actual_id = seq_to_uuid.get(str(mid), str(mid))
                    scores[actual_id] = float(score)
                    if reason:
                        reasons[actual_id] = str(reason)[:100]
                except (ValueError, TypeError):
                    pass

        logger.debug(f"MemoryRetriever._parse_scores: 解析到 {len(scores)} 个评分")
        return scores, reasons

    @staticmethod
    def _try_parse_json_array(text: str) -> Optional[List[Any]]:
        """尝试解析 JSON 数组，自动修复常见 LLM 输出问题。

        修复项：
        - 尾逗号（trailing comma）
        - 无引号的简单键名
        - 前导点数字（.85 → 0.85）
        """
        if not text or not text.strip():
            return None

        cleaned = text.strip()

        # 尝试 1：直接解析
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # 尝试 2：修复尾逗号（最常见的 LLM JSON 错误）
        try:
            fixed = re.sub(r",\s*([]}])", r"\1", cleaned)
            result = json.loads(fixed)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # 尝试 3：修复前导点数字
        try:
            # .85 → 0.85（但只替换值位置的点，不替换键名中的点）
            fixed = re.sub(r':\s*\.(\d+)', r': 0.\1', cleaned)
            result = json.loads(fixed)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # 尝试 4：组合修复（尾逗号 + 前导点数字）
        try:
            fixed = re.sub(r",\s*([]}])", r"\1", cleaned)
            fixed = re.sub(r':\s*\.(\d+)', r': 0.\1', fixed)
            result = json.loads(fixed)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        return None

    def _fallback_parse_scores(
        self,
        response: str,
        batch: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        """逐条正则回退解析评分（JSON 完全无法解析时使用）。"""
        scores: Dict[str, float] = {}

        # 先尝试逐对象提取：匹配一对 { ... } 内的 memory_index 和 score
        # 使用 re.DOTALL 支持跨行匹配
        obj_pattern = r'\{\s*"memory_index"\s*:\s*"(\S+?)".*?"(?:score|{})"\s*:\s*([\d.]+)\s*[,\}]'
        for match in re.finditer(
            obj_pattern.format(re.escape(self._score_field)),
            response,
            re.DOTALL | re.IGNORECASE,
        ):
            mid = match.group(1)
            try:
                score = float(match.group(2))
                scores[mid] = score
            except ValueError:
                pass

        if not scores:
            # 回退方案：分别匹配 memory_index 和 score，按顺序配对
            ids = re.findall(r'"memory_index"\s*:\s*"(\S+?)"', response)
            scores_raw = re.findall(
                r'"(?:score|{})"\s*:\s*([\d.]+)'.format(re.escape(self._score_field)),
                response,
            )
            for i, mid in enumerate(ids):
                if i < len(scores_raw):
                    try:
                        scores[mid] = float(scores_raw[i])
                    except ValueError:
                        pass

        # 如果仍然为空，记录原始响应片段方便调试
        if not scores:
            logger.warning(
                f"MemoryRetriever._fallback_parse_scores: 回退解析失败，"
                f"无法从响应中提取评分。响应前200字符: {response[:200]}"
            )

        logger.debug(f"MemoryRetriever._fallback_parse_scores: 回退解析到 {len(scores)} 个评分")
        return scores

    # ------------------------------------------------------------------ #
    #  综合打分
    # ------------------------------------------------------------------ #

    def _compute_final_scores(
        self,
        candidates: List[Dict[str, Any]],
        llm_scores: Dict[str, float],
        reasons: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """构建结果：score 直接等于 LLM 评分，不做任何代码层面的加权。

        Args:
            candidates: 候选记忆列表
            llm_scores: LLM 返回的 {id: score} 映射
            reasons: LLM 返回的 {id: reason} 映射（可选）

        Returns:
            带 score 和 llm_score 的记忆列表
        """
        results = []

        for item in candidates:
            mid = item["id"]
            llm_score = llm_scores.get(mid, 0.0)

            result = dict(item)
            result["score"] = round(llm_score, 4)
            result["llm_score"] = round(llm_score, 4)
            if reasons:
                result["reason"] = reasons.get(mid, "")
            results.append(result)

        return results

    def _compute_cache_key(self, query: str, candidate_ids: List[str]) -> str:
        """生成 LLM 评分缓存键（基于 query + candidate ID 集合哈希）。"""
        raw = query + "|" + ",".join(sorted(candidate_ids))
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

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