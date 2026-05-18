"""
MemoryManager - 记忆模块统一调度器

架构概览：

                    ┌─────────────────────────┐
                    │     MemoryManager        │
                    │  (统一调度入口)           │
                    └───────────┬─────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            │                   │                   │
    ┌───────▼───────┐  ┌───────▼───────┐  ┌───────▼───────┐
    │ Working Memory│  │ShortTerm Memory│  │LongTerm Memory │
    │  (当前会话)    │  │(跨会话暂存)    │  │(压缩后持久)    │
    └───────┬───────┘  └───────┬───────┘  └───────┬───────┘
            │                   │                   │
            │              ┌────▼────┐              │
            │              │Compressor│             │
            │              │(短期→长期)│             │
            │              └─────────┘              │
            │                                       │
            └───────────────────┬───────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │     Retrieve Pipeline  │
                    │  ┌──────────────────┐  │
                    │  │ Embedding (粗筛)  │  │
                    │  └────────┬─────────┘  │
                    │           ▼            │
                    │  ┌──────────────────┐  │
                    │  │ LLM (精排/回退)  │  │
                    │  └──────────────────┘  │
                    └───────────────────────┘

核心能力：

1. **三层记忆存储**：
   - Working Memory：仅存活于当前会话，不持久化
   - Short-term Memory：跨会话暂存（最近 N 轮对话），自动压缩为 Long-term
   - Long-term Memory：压缩后的长期知识，持久化存储

2. **双路召回**：
   - Primary：Embedding 向量召回（粗筛 top-20）
   - Secondary：LLM 语义精排（从粗筛结果中筛选 top-5）
   - Fallback：LLM 直接召回（embedding 不可用时）

3. **自动压缩**：
   - 短期记忆超过阈值时，自动触发 LLM 压缩
   - 多条短期记忆合并为一条长期记忆（含 summary + tags）

4. **遗忘曲线**：
   - 基于艾宾浩斯遗忘曲线的简化版
   - 长期记忆按 last_access 计算衰减因子
   - 衰减过低的记忆自动归档（软删除）

5. **上下文注入**：
   - inject_context() 返回格式化的 Markdown 上下文
   - 自动计算 token 预算，截断低分记忆

与旧 memory_manager 的本质区别：
- 引入真正的语义召回（embedding + LLM），取代字符级 TF-IDF
- 遗忘使用艾宾浩斯曲线，不依赖简单的 access_count 正反馈
- 记忆结构包含 summary + tags，支持高效筛选
- 压缩时保留关键事实，丢弃冗余细节
"""

import time
import math
from typing import Optional, List, Dict, Callable, Union
from dataclasses import dataclass, field

from .memory_store import MemoryStore, MemoryEntry
from .embedding_retriever import (
    EmbeddingRetriever,
    EmbeddingGenerator,
    RetrievalResult,
)
from .llm_retriever import LLMRetriever


# ========== 配置结构 ==========

@dataclass
class MemoryConfig:
    """记忆模块配置"""
    # 存储路径
    long_term_path: str = "D:/AI/MyClaude/data/memory2_long_term.json"
    short_term_path: str = "D:/AI/MyClaude/data/memory2_short_term.json"

    # 容量限制
    max_short_term: int = 15          # 短期记忆上限（触发压缩）
    max_long_term: int = 200           # 长期记忆上限（触发遗忘）
    max_working_memories: int = 20     # 工作记忆上限

    # 召回配置
    embedding_top_k: int = 10          # embedding 粗筛 top-K
    llm_top_k: int = 5                 # LLM 精排后返回数
    similarity_threshold: float = 0.35 # 语义相似度最低阈值
    context_token_budget: int = 1500   # 注入上下文的 token 预算
    chars_per_token: float = 2.0       # 中文字符 → token 估算比例

    # 遗忘曲线参数（艾宾浩斯简化版）
    forgetting_slope: float = 0.5      # 遗忘斜率（越大忘得越快）
    forgetting_min: float = 0.01       # 最低保留权重
    decay_check_interval: int = 3600   # 衰减检查间隔（秒）

    # 压缩配置
    compress_trigger_count: int = 12   # 短期记忆超过此数时触发压缩
    max_compressed_per_batch: int = 6  # 每次最多压缩条数

    # 注入格式
    inject_header: str = (
        "[系统提醒] 以下是与当前任务相关的历史记忆，仅供参考！\n"
    )


# ========== 统一调度器 ==========

class MemoryManager:
    """
    记忆模块统一调度器。

    使用方式：
        mgr = MemoryManager(
            config=MemoryConfig(),
            llm_chat_fn=my_chat_function,
            embedding_api_key="sk-...",
        )

        # 记录一轮对话
        mgr.record_turn(user_input="写一个排序函数", llm_response="...")

        # 召回相关记忆
        memories = mgr.recall("怎么实现快速排序？")

        # 注入到 LLM 对话
        context = mgr.inject_context("怎么实现快速排序？")
        api_messages.append({"role": "user", "content": context})

        # 会话结束时压缩
        mgr.on_session_end()
    """

    def __init__(
        self,
        config: MemoryConfig,
        llm_chat_fn: Callable[[List[Dict]], str],
        embedding_generator: Optional[EmbeddingGenerator] = None,
    ):
        self.cfg = config

        # 三层存储
        self._long_store = MemoryStore(config.long_term_path)
        self._short_store = MemoryStore(config.short_term_path)
        self._working_memories: List[MemoryEntry] = []

        # LLM 对话函数（来自项目主模块）
        self._llm_chat = llm_chat_fn

        # Embedding 生成器（可能为 None，表示不启用向量召回）
        self._embed_gen = embedding_generator

        # 召回器（惰性初始化）
        self._embed_retriever: Optional[EmbeddingRetriever] = None
        self._llm_retriever: Optional[LLMRetriever] = None

        # 衰减计时
        self._last_decay_check: float = time.time()

    # ========== 初始化 ==========

    def _get_embed_retriever(self) -> EmbeddingRetriever:
        if self._embed_retriever is None and self._embed_gen is not None:
            self._embed_retriever = EmbeddingRetriever(
                store=self._long_store,
                embedding_generator=self._embed_gen,
                similarity_threshold=self.cfg.similarity_threshold,
                top_k=self.cfg.embedding_top_k,
            )
        return self._embed_retriever

    def _get_llm_retriever(self) -> LLMRetriever:
        if self._llm_retriever is None:
            self._llm_retriever = LLMRetriever(
                store=self._long_store,
                llm_chat_fn=self._llm_chat,
                top_k=self.cfg.llm_top_k,
            )
        return self._llm_retriever

    # ========== 记忆注入/记录 ==========

    def record_turn(
        self,
        user_input: str,
        llm_response: str = "",
        llm_reasoning: str = "",
        tool_calls: str = "",
        importance: float = 0.5,
    ):
        """
        记录一轮对话到工作记忆。

        Args:
            user_input: 用户输入
            llm_response: LLM 应答
            llm_reasoning: LLM 推理过程（被 strip 的 thinking）
            tool_calls: 工具调用摘要
            importance: 重要性 0~1
        """
        content_parts = [f"用户输入: {user_input}"]
        if llm_reasoning:
            content_parts.append(f"LLM推理过程: {llm_reasoning[:500]}")
        if tool_calls:
            content_parts.append(f"LLM工具调用: {tool_calls[:300]}")
        if llm_response:
            content_parts.append(f"LLM应答: {llm_response[:500]}")

        entry = MemoryEntry(
            content="\n".join(content_parts),
            importance=importance,
            memory_type="working",
            created_at=time.time(),
        )

        self._working_memories.append(entry)

        # 超出容量时，将旧的移入短期存储
        while len(self._working_memories) > self.cfg.max_working_memories:
            oldest = self._working_memories.pop(0)
            oldest.memory_type = "short_term"
            self._short_store.add(oldest)

        # 短期存储超出阈值，触发压缩
        if self._short_store.count() >= self.cfg.compress_trigger_count:
            self._compress_short_term()

    def on_session_end(self):
        """
        会话结束时调用：
        1. 将所有工作记忆移入短期存储
        2. 触发压缩
        3. 保存所有存储
        """
        # 工作记忆 → 短期
        for entry in self._working_memories:
            entry.memory_type = "short_term"
            self._short_store.add(entry)
        self._working_memories.clear()

        # 压缩
        while self._short_store.count() >= self.cfg.compress_trigger_count:
            self._compress_short_term()

        # 持久化
        self._short_store.save()
        self._long_store.save()

    # ========== 记忆召回 ==========

    def recall(
        self,
        query: str,
        top_k: Optional[int] = None,
        use_llm_rerank: bool = True,
    ) -> List[RetrievalResult]:
        """
        双路召回管道：Embedding 粗筛 → LLM 精排。

        Args:
            query: 查询文本
            top_k: 返回结果数
            use_llm_rerank: 是否使用 LLM 精排（关闭则只做 embedding 召回）

        Returns:
            按综合得分降序的 RetrievalResult 列表
        """
        top_k = top_k or self.cfg.llm_top_k

        # 1. 定期衰减检查
        self._check_decay()

        # 2. 尝试 embedding 召回
        embed_results: List[RetrievalResult] = []
        embed_retriever = self._get_embed_retriever()
        if embed_retriever is not None:
            try:
                embed_results = embed_retriever.retrieve(
                    query=query,
                    top_k=self.cfg.embedding_top_k,
                )
            except RuntimeError:
                embed_results = []

        # 3. 如果 embedding 无结果，退回到 LLM 直接召回
        if not embed_results or not use_llm_rerank:
            if use_llm_rerank:
                llm_retriever = self._get_llm_retriever()
                return llm_retriever.retrieve(query=query, top_k=top_k)
            else:
                return embed_results[:top_k]

        # 4. LLM 精排：将 embedding 粗筛结果作为候选
        candidates = [r.entry for r in embed_results if r.entry is not None]
        if not candidates:
            return []

        llm_retriever = self._get_llm_retriever()
        try:
            refined = llm_retriever.retrieve(
                query=query,
                top_k=top_k,
                prefiltered=candidates,
            )
            return refined
        except (NotImplementedError, RuntimeError):
            # LLM 不可用，直接用 embedding 结果
            # 补充 summary 以便注入时展示
            for r in embed_results:
                if r.entry and not r.entry.summary:
                    r.entry.summary = r.entry.content[:80].replace("\n", " ").strip()
            return embed_results[:top_k]

    # ========== 上下文注入 ==========

    def inject_context(
        self,
        query: str,
        token_budget: Optional[int] = None,
    ) -> str:
        """
        召回记忆并格式化为 Markdown 上下文，用于注入 LLM 对话。

        Args:
            query: 当前用户查询（用于记忆召回）
            token_budget: 注入 token 预算，默认使用配置值

        Returns:
            格式化好的 Markdown 字符串，或空字符串（无相关记忆时）
        """
        token_budget = token_budget or self.cfg.context_token_budget
        results = self.recall(query, top_k=self.cfg.embedding_top_k)

        if not results:
            return ""

        lines = [self.cfg.inject_header]
        char_count = len(self.cfg.inject_header)
        item_count = 0

        for r in results:
            if r.entry is None:
                continue

            # 构建记忆条目行
            ts_str = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(r.entry.created_at),
            )
            summary = r.entry.summary or r.entry.content[:100].replace("\n", " ").strip()
            content_preview = r.entry.content[:300].replace("\n", " ").strip()

            item_text = (
                f"- [id={r.memory_id}, ts={ts_str}] "
                f"{summary}\n"
                f"  内容: {content_preview}\n"
            )

            # Token 预算检查
            estimated_tokens = len(item_text) / self.cfg.chars_per_token
            if char_count + len(item_text) > token_budget * self.cfg.chars_per_token:
                break

            lines.append(item_text)
            char_count += len(item_text)
            item_count += 1

        if item_count == 0:
            return ""

        return "\n".join(lines)

    # ========== 压缩 ==========

    def _compress_short_term(self):
        """将多条短期记忆压缩为一条长期记忆。"""
        short_entries = self._short_store.get_all()
        if len(short_entries) < 2:
            return

        # 按创建时间排序，取最老的 N 条进行压缩
        short_entries.sort(key=lambda e: e.created_at)
        to_compress = short_entries[:self.cfg.max_compressed_per_batch]

        # 构建压缩 prompt
        combined = "\n\n---\n\n".join(
            f"[{time.strftime('%H:%M:%S', time.localtime(e.created_at))}] "
            f"{e.content[:400]}"
            for e in to_compress
        )

        prompt = (
            "你将收到多条对话记忆。请将它们压缩为一条**长期记忆**。\n\n"
            "压缩原则：\n"
            "1. 保留关键事实：用户偏好、重要决策、技术要点\n"
            "2. 合并同类信息：相似主题合并为一句话\n"
            "3. 丢弃冗余细节：LLM 的具体推理过程、工具调用细节不要\n"
            "4. 保持第三人称描述，如「用户喜欢用 Python 写代码」\n\n"
            f"原始记忆：\n{combined}\n\n"
            "返回 JSON 格式：\n"
            '{"summary": "不超过40字的摘要", '
            '"content": "压缩后的长期记忆内容", '
            '"tags": ["标签1", "标签2", "标签3"], '
            '"importance": 0.5}'
        )

        messages = [
            {"role": "system", "content": "你是记忆压缩专家，擅长提取关键信息并丢弃冗余。"},
            {"role": "user", "content": prompt},
        ]

        try:
            result = self._llm_chat(messages)
            json_str = self._extract_json(result)
            import json as _json
            data = _json.loads(json_str)

            # 创建长期记忆
            entry = MemoryEntry(
                content=data.get("content", combined[:500]),
                summary=data.get("summary", "")[:80],
                tags=data.get("tags", [])[:10],
                importance=float(data.get("importance", 0.5)),
                memory_type="long_term",
                created_at=time.time(),
            )
            self._long_store.add(entry)

            # 为压缩后的记忆生成 embedding
            if self._embed_gen is not None:
                try:
                    entry.embedding = self._embed_gen.embed(entry.content)
                except RuntimeError:
                    pass

            # 删除已压缩的短期记忆
            compressed_ids = [e.id for e in to_compress]
            self._short_store.delete_many(compressed_ids)

            # 保存
            self._long_store.save()
            self._short_store.save()

            # 通知 embedding retriever 重建索引
            if self._embed_retriever:
                self._embed_retriever.rebuild_index()

        except Exception as e:
            # 压缩失败不阻塞，短期记忆保留不变
            print(f"[MemoryManager] 压缩失败: {e}")

    # ========== 遗忘机制 ==========

    def _check_decay(self):
        """定期检查并衰减长期记忆。"""
        now = time.time()
        if now - self._last_decay_check < self.cfg.decay_check_interval:
            return
        self._last_decay_check = now

        entries = self._long_store.get_all()
        to_delete = []

        for entry in entries:
            # 遗忘曲线：R = e^(-λ * t)
            # 其中 t 是天数，λ = forgetting_slope / importance
            # importance 越高，忘得越慢
            days_since_access = (now - max(entry.last_access, entry.created_at)) / 86400
            effective_lambda = self.cfg.forgetting_slope / max(entry.importance, 0.01)
            retention = math.exp(-effective_lambda * days_since_access)

            # retention 低于阈值则标记删除
            if retention < self.cfg.forgetting_min and entry.access_count < 2:
                to_delete.append(entry.id)

            # 存储当前 retention 作为得分（仅供调试）
            entry.importance = min(entry.importance, retention)

        if to_delete:
            self._long_store.delete_many(to_delete)
            self._long_store.save()
            if self._embed_retriever:
                self._embed_retriever.rebuild_index()

    def _extract_json(self, text: str) -> str:
        """从 LLM 响应中提取 JSON 部分。"""
        text = text.strip()
        if text.startswith("{"):
            return text
        import re
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            return match.group(1)
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return text[start : end + 1]
        return text

    # ========== 存储存取 ==========

    def get_long_term_count(self) -> int:
        return self._long_store.count()

    def get_short_term_count(self) -> int:
        return self._short_store.count()

    def get_working_count(self) -> int:
        return len(self._working_memories)

    def clear_all(self):
        """清空所有记忆（危险操作）。"""
        self._working_memories.clear()
        self._short_store.clear()
        self._long_store.clear()
        self._short_store.save()
        self._long_store.save()

    def get_stats(self) -> Dict:
        """返回记忆模块统计信息。"""
        return {
            "working_count": len(self._working_memories),
            "short_term_count": self._short_store.count(),
            "long_term_count": self._long_store.count(),
            "last_decay_check": time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(self._last_decay_check),
            ),
            "has_embedding": self._embed_gen is not None,
        }