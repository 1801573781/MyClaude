"""
LLMRetriever - 基于 LLM 语义理解能力的记忆召回器

核心思路：

1. **粗筛阶段**：利用记忆的 summary 和 tags 构建候选池
   （summary 在记忆创建时由 LLM 生成，tags 是关键词列表）

2. **LLM 精确筛选**：将候选记忆的 summary + 截断 content 发送给 LLM，
   让 LLM 直接判断哪些与当前查询真正相关。

3. **JSON 结构化输出**：LLM 返回标准 JSON，包含每条候选记忆的
   相关性分数（0~10）和简短理由。

4. **分页处理**：当候选记忆超出 token 预算时，分批次处理，
   每批取 top-M 进入下一轮，类似 MMR 的 cascade 思想。

5. **结果合并**：多批次结果按 LLM 打分合并排序，去重后返回 top-K。

与 embedding_retriever 的对比：
- **优势**：真正理解语义，不会因表面词汇不重叠而漏掉相关记忆
- **劣势**：token 消耗大，延迟高（需一次 LLM 往返）
- **最佳实践**：作为第二级精排，在 embedding 粗筛后使用
"""

import json
import time
from typing import List, Optional, Callable, Dict, Tuple
from dataclasses import dataclass, field

from .memory_store import MemoryEntry, MemoryStore
from .embedding_retriever import RetrievalResult


# ========== LLM 交互协议 ==========

RECALL_SYSTEM_PROMPT = """你是一个记忆相关性判断专家。你的任务是：
给定用户的当前查询（Query）和一组候选记忆的摘要，判断哪些记忆与查询相关。

判定标准：
- 主题相关：记忆内容与查询讨论同一话题
- 因果相关：记忆内容是查询的前置条件或后续结果
- 用户偏好：记忆记录了用户的偏好、习惯、要求
- 技术相关：记忆涉及的技术栈、代码模块与查询一致

返回 JSON 格式：
{
  "relevant": [
    {"id": "记忆ID", "score": 8, "reason": "简短的关联理由（<=20字）"}
  ],
  "irrelevant": ["不相关的记忆ID"]
}

注意：
- score 范围 0~10，越高越相关
- 只返回 JSON，不要有其他文字
- 不确定是否相关时，宁可标记为 irrelevant
- 最多标记 top-10 个相关记忆"""


def build_recall_user_prompt(query: str, candidates: List[Dict]) -> str:
    """构建发给 LLM 的候选记忆列表 prompt。"""
    lines = [
        f"## 用户当前查询\n{query}\n",
        "## 候选记忆",
    ]
    for i, c in enumerate(candidates, 1):
        # 摘要 + 前 80 字内容预览
        summary = c.get("summary", "") or c.get("content", "")[:80]
        content_preview = c.get("content", "")[:120]
        tags = c.get("tags", [])
        tag_str = ", ".join(tags[:8]) if tags else "无"

        lines.append(
            f"### 记忆 {i}\n"
            f"- ID: {c['id']}\n"
            f"- 标签: {tag_str}\n"
            f"- 摘要: {summary}\n"
            f"- 内容预览: {content_preview}\n"
        )

    return "\n".join(lines)


# ========== LLM 召回器 ==========

class LLMRetriever:
    """
    基于 LLM 的语义记忆召回器。

    使用方式：
        retriever = LLMRetriever(
            store=memory_store,
            llm_chat_fn=my_chat_function,  # 接收 List[Dict] 返回 str
        )
        results = retriever.retrieve("用户最近的代码风格偏好", top_k=5)

    Args:
        store: MemoryStore 实例
        llm_chat_fn: LLM 对话函数，签名为 fn(messages: List[Dict]) -> str
        max_candidates_per_batch: 每批最多发送给 LLM 的候选数
        min_score: LLM 返回 score 的最低阈值
        top_k: 最终返回的记忆数
    """

    def __init__(
        self,
        store: MemoryStore,
        llm_chat_fn: Optional[Callable[[List[Dict]], str]] = None,
        max_candidates_per_batch: int = 30,
        min_score: float = 3.0,
        top_k: int = 5,
    ):
        self._store = store
        self._llm_chat = llm_chat_fn
        self._max_candidates = max_candidates_per_batch
        self._min_score = min_score
        self._top_k = top_k

        # 如果没有传入 llm_chat_fn，使用占位（实际使用时必须注入）
        if self._llm_chat is None:
            self._llm_chat = self._placeholder_chat

    # ========== 公开接口 ==========

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        memory_type: Optional[str] = None,
        prefiltered: Optional[List[MemoryEntry]] = None,
    ) -> List[RetrievalResult]:
        """
        使用 LLM 判断记忆相关性并召回。

        Args:
            query: 用户当前查询
            top_k: 返回结果数
            memory_type: 记忆类型筛选
            prefiltered: 预筛选的候选列表（来自 embedding 粗筛结果）

        Returns:
            按 LLM 打分降序排列的 RetrievalResult 列表
        """
        self._store._ensure_loaded()
        top_k = top_k or self._top_k

        # 1. 获取候选记忆
        if prefiltered is not None:
            candidates = prefiltered
        elif memory_type:
            candidates = self._store.get_by_type(memory_type)
        else:
            candidates = self._store.get_all()

        if not candidates:
            return []

        # 2. 如果候选数少，直接全部发给 LLM
        if len(candidates) <= self._max_candidates:
            return self._recall_batch(query, candidates, top_k)

        # 3. 候选太多，按重要性 + 时间新近度预排序后分批
        candidates.sort(
            key=lambda e: (
                e.importance * 0.6
                + min((time.time() - e.created_at) / 86400, 30) * 0.4 / 30
            ) * 0.5
            + e.access_count * 0.01,
            reverse=True,
        )

        # 分批处理
        all_results: List[RetrievalResult] = []
        seen_ids = set()

        for batch_start in range(0, len(candidates), self._max_candidates):
            batch = candidates[batch_start : batch_start + self._max_candidates]
            batch_results = self._recall_batch(query, batch, top_k * 2)

            for r in batch_results:
                if r.memory_id not in seen_ids:
                    seen_ids.add(r.memory_id)
                    all_results.append(r)

            # 如果已收集足够高质量的结果，提前终止
            if len([r for r in all_results if r.score >= 6.0]) >= top_k:
                break

        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:top_k]

    def summarize_memory(self, entry: MemoryEntry) -> str:
        """
        调用 LLM 为一条记忆生成摘要和标签。
        在记忆创建时调用，结果写入 entry.summary 和 entry.tags。
        """
        prompt = (
            "请为以下对话记忆生成摘要和标签。\n\n"
            f"记忆内容：\n{entry.content[:2000]}\n\n"  # 截断到 2000 字
            "返回 JSON 格式：\n"
            '{"summary": "不超过50字的摘要", "tags": ["标签1", "标签2", "标签3"]}\n'
            "只返回 JSON，不要有其他文字。"
        )

        messages = [
            {"role": "system", "content": "你是一个精确的记忆摘要生成器。"},
            {"role": "user", "content": prompt},
        ]

        try:
            result = self._llm_chat(messages)
            summary_str = self._extract_json(result)
            data = json.loads(summary_str)
            entry.summary = data.get("summary", "")[:80]
            entry.tags = data.get("tags", [])[:10]
            return entry.summary
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            # LLM 返回格式异常，使用简单截断
            entry.summary = entry.content[:100].replace("\n", " ").strip()
            entry.tags = []
            return entry.summary

    # ========== 内部方法 ==========

    def _recall_batch(
        self,
        query: str,
        candidates: List[MemoryEntry],
        top_k: int,
    ) -> List[RetrievalResult]:
        """对一批候选记忆执行 LLM 召回。"""
        cand_dicts = [
            {
                "id": e.id,
                "summary": e.summary,
                "content": e.content,
                "tags": e.tags,
            }
            for e in candidates
        ]

        user_prompt = build_recall_user_prompt(query, cand_dicts)
        messages = [
            {"role": "system", "content": RECALL_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            llm_response = self._llm_chat(messages)
            recall_data = self._parse_recall_response(llm_response, cand_dicts)

            # 构建结果
            results = []
            id_to_entry = {e.id: e for e in candidates}

            for rel in recall_data.get("relevant", []):
                mem_id = rel.get("id", "")
                score = min(max(float(rel.get("score", 0)), 0), 10)
                if score < self._min_score:
                    continue

                entry = id_to_entry.get(mem_id)
                if entry:
                    results.append(RetrievalResult(
                        memory_id=mem_id,
                        content=entry.content,
                        score=score / 10.0,  # 归一化到 [0, 1]
                        semantic_score=score / 10.0,
                        importance=entry.importance,
                        entry=entry,
                    ))

                    # 更新访问计数
                    entry.access_count = min(entry.access_count + 0.5, 100.0)
                    entry.last_access = time.time()

            results.sort(key=lambda r: r.score, reverse=True)
            return results[:top_k]

        except (json.JSONDecodeError, KeyError, TypeError):
            # LLM 返回解析失败，降级为简单关键词匹配
            return self._fallback_retrieve(query, candidates, top_k)

    def _parse_recall_response(self, response: str, candidates: List[Dict]) -> Dict:
        """解析 LLM 的召回响应 JSON。"""
        json_str = self._extract_json(response)
        return json.loads(json_str)

    def _extract_json(self, text: str) -> str:
        """从 LLM 响应中提取 JSON 部分。"""
        # 尝试直接解析
        text = text.strip()
        if text.startswith("{"):
            return text

        # 尝试提取 ```json ... ``` 块
        import re
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            return match.group(1)

        # 尝试提取第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return text[start : end + 1]

        return text

    def _fallback_retrieve(
        self,
        query: str,
        candidates: List[MemoryEntry],
        top_k: int,
    ) -> List[RetrievalResult]:
        """
        LLM 召回失败时的降级策略：
        使用 query 与 summary 的简单字符串匹配。
        """
        query_lower = query.lower()
        results = []

        for entry in candidates:
            content_lower = (entry.summary + " " + entry.content[:200]).lower()
            # 简单共现计数
            query_terms = set(query_lower.split())
            score = sum(1 for t in query_terms if t in content_lower) / max(len(query_terms), 1)
            if score > 0.1:
                results.append(RetrievalResult(
                    memory_id=entry.id,
                    content=entry.content,
                    score=score,
                    semantic_score=score,
                    entry=entry,
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def _placeholder_chat(self, messages: List[Dict]) -> str:
        """占位 LLM 函数，实际使用时会被替换。"""
        raise NotImplementedError(
            "LLMRetriever 需要注入 llm_chat_fn。"
            "请从 query 模块传入真正的 chat 函数。"
        )