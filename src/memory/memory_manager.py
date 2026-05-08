"""
MemoryManager - 分层记忆系统统一入口（Facade）

协调 MemoryStore / MemoryRetrieval / MemoryCompressor / MemoryInjector 四个子组件。
提供简单的高层 API，所有方法均为同步。
"""

import logging
import time
import uuid
from typing import Dict, List, Optional

from src.memory.memory_store import MemoryStore
from src.memory.memory_retrieval import MemoryRetrieval
from src.memory.memory_compressor import MemoryCompressor
from src.memory.memory_injector import MemoryInjector

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Memory 模块的统一入口（Facade）。

    协调四个子组件：
        - MemoryStore:      JSON 持久化存储
        - MemoryRetrieval:  TF-IDF + 余弦相似度检索
        - MemoryCompressor: LLM 驱动的短期记忆压缩
        - MemoryInjector:   上下文格式化与注入

    所有方法均为同步（无 async/await）。
    """

    def __init__(self, config: Dict):
        """
        初始化 MemoryManager。

        参数:
            config: 完整配置字典（与 config.yaml 结构一致），
                    必须包含 config["memory"] 子字典。
        """
        mem_cfg = config.get("memory", {})

        self._enabled = mem_cfg.get("enabled", True)
        self._storage_path = mem_cfg.get("storage_path", ".memdir")
        self._short_term_max_entries = mem_cfg.get("short_term_max_entries", 50)
        self._short_term_max_tokens = mem_cfg.get("short_term_max_tokens", 8000)
        self._long_term_max_inject = mem_cfg.get("long_term_max_inject", 5)
        self._working_memory_max_tokens = mem_cfg.get("working_memory_max_tokens", 2000)
        self._similarity_threshold = mem_cfg.get("similarity_threshold", 0.15)
        self._compress_batch_size = mem_cfg.get("compress_batch_size", 20)
        self._compress_llm_model = mem_cfg.get("compress_llm_model", "DeepSeek")
        self._forget_older_than_days = mem_cfg.get("forget_older_than_days", 30)
        self._forget_importance_below = mem_cfg.get("forget_importance_below", 0.2)

        # 子组件
        self._store = MemoryStore(self._storage_path)
        self._retrieval = MemoryRetrieval(self._similarity_threshold)
        self._compressor = MemoryCompressor(
            short_term_max_entries=self._short_term_max_entries,
            short_term_max_tokens=self._short_term_max_tokens,
            compress_batch_size=self._compress_batch_size,
            llm_call=self._llm_call_wrapper,
        )
        self._injector = MemoryInjector(max_tokens=2000)  # inject_context 使用独立的 max_tokens

        # 工作记忆（仅内存，不持久化）
        self._working_memories: List[Dict] = []

    # ========== CRUD ==========

    def add_memory(
        self,
        content: str,
        mem_type: str,
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
    ) -> str:
        """
        添加一条记忆，返回记忆 ID（32 位十六进制字符串）。

        参数:
            content:    记忆文本内容（UTF-8）。
            mem_type:   "working" | "short" | "long"。
            importance: 0.0~1.0，默认 0.5。
            tags:       可选标签列表。

        行为:
            - mem_type == "working" → 仅追加到 self._working_memories，不持久化。
            - mem_type == "short" | "long" → 创建条目并持久化。

        返回:
            新创建记忆的 id 字符串。
        """
        memory_id = uuid.uuid4().hex
        now = int(time.time())
        tags = tags or []

        # 校验 importance
        importance = max(0.0, min(1.0, float(importance)))

        item = {
            "id": memory_id,
            "content": content,
            "type": mem_type,
            "importance": importance,
            "timestamp": now,
            "access_count": 0,
            "last_access": now,
            "tags": tags,
            "metadata": {},
        }

        if mem_type == "working":
            self._working_memories.append(item)
            self._enforce_working_token_limit()
            logger.info(f"添加工作记忆: {memory_id[:8]}...")
        elif mem_type in ("short", "long"):
            self._store.add(item)
            logger.info(f"添加 {mem_type} 记忆: {memory_id[:8]}...")
        else:
            raise ValueError(f"无效的 mem_type: {mem_type}，必须是 'working' / 'short' / 'long'")

        return memory_id

    def get_memories(
        self,
        query: str,
        mem_type: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """
        根据查询文本检索最相关的记忆条目。

        参数:
            query:    查询文本（用于 TF-IDF 向量化）。
            mem_type: 可选过滤类型，None 表示检索 short + long。
            limit:    最大返回条数。

        返回:
            按最终得分降序排列的记忆字典列表。
            命中条目的 access_count 和 last_access 同步更新并持久化。
        """
        if mem_type is None:
            search_memories = self._store.get_by_types(["short", "long"])
        elif mem_type in ("short", "long"):
            search_memories = self._store.get_all(mem_type)
        else:
            # 不支持检索 working 类型
            search_memories = []

        if not search_memories and not self._working_memories:
            return []

        try:
            results = self._retrieval.retrieve(
                query=query,
                memories=search_memories,
                working_memories=self._working_memories,
                limit=limit,
                on_access_hit=self._on_access_hit,
            )
        except Exception as e:
            logger.warning(f"检索失败: {e}")
            return []

        return results

    def get_all_memories(self, mem_type: Optional[str] = None) -> List[Dict]:
        """
        返回所有指定类型的记忆（不修改 access_count）。

        参数:
            mem_type: None 返回全部（含 working），或指定 "short" / "long" / "working"。

        用途:
            主要用于测试验证和调试导出。
        """
        if mem_type is None:
            result = self._store.get_all()
            result.extend(self._working_memories)
            return result
        elif mem_type == "working":
            return list(self._working_memories)
        else:
            return self._store.get_all(mem_type)

    def update_memory(
        self,
        memory_id: str,
        content: Optional[str] = None,
        importance: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ) -> bool:
        """
        更新已有记忆的部分字段。

        参数:
            memory_id:  目标记忆 ID。
            content:    新内容（None 表示不修改）。
            importance: 新重要性（None 表示不修改）。
            tags:       新标签列表（None 表示不修改）。

        返回:
            True 表示更新成功，False 表示未找到指定 ID。

        注意:
            不能修改 type 字段（类型一旦确定不可更改）。
        """
        # 先查工作记忆
        for mem in self._working_memories:
            if mem["id"] == memory_id:
                if content is not None:
                    mem["content"] = content
                if importance is not None:
                    mem["importance"] = max(0.0, min(1.0, float(importance)))
                if tags is not None:
                    mem["tags"] = tags
                return True

        # 再查持久化记忆
        mem = self._store.get_by_id(memory_id)
        if mem is None:
            return False

        updates = {}
        if content is not None:
            updates["content"] = content
        if importance is not None:
            updates["importance"] = max(0.0, min(1.0, float(importance)))
        if tags is not None:
            updates["tags"] = tags

        return self._store.update(memory_id, updates)

    def delete_memory(self, memory_id: str) -> bool:
        """
        永久删除一条记忆。

        返回:
            True 表示删除成功，False 表示未找到指定 ID。
        """
        # 检查工作记忆
        for i, mem in enumerate(self._working_memories):
            if mem["id"] == memory_id:
                self._working_memories.pop(i)
                return True

        # 检查持久化记忆
        return self._store.delete(memory_id)

    # ========== 记忆生命周期 ==========

    def compress_short_term(self) -> int:
        """
        当短期记忆超过阈值时，调用 LLM 将旧条目合并为长期记忆。

        返回:
            本次压缩新生成的长期记忆数量（0 表示未触发或无需压缩）。
        """
        short_mems = self._store.get_all("short")
        if not self._compressor.should_compress(short_mems):
            return 0

        logger.info(f"触发压缩：短期记忆 {len(short_mems)} 条，"
                     f"token 约 {MemoryCompressor.estimate_tokens(str(short_mems)[:100])}...")

        return self._compressor.compress(
            short_term_memories=short_mems,
            get_all_short=lambda: self._store.get_all("short"),
            delete_memory=self._store.delete,
            add_long_memory=lambda content, importance, tags, metadata: (
                self._store.add({
                    "id": uuid.uuid4().hex,
                    "content": content,
                    "type": "long",
                    "importance": importance,
                    "timestamp": int(time.time()),
                    "access_count": 0,
                    "last_access": int(time.time()),
                    "tags": tags,
                    "metadata": metadata,
                })
            ),
            model=self._compress_llm_model,
        )

    def forget(
        self,
        older_than_days: int = 30,
        importance_below: float = 0.2,
    ) -> int:
        """
        根据时间与重要性自动遗忘长期记忆。

        详细算法见 memory_spec.md 6.4 节。

        返回:
            被删除的记忆条目数量。
        """
        now = time.time()
        deleted_count = 0
        to_delete = []

        for mem in self._store.get_all("long"):
            age_days = (now - mem.get("timestamp", now)) / 86400

            # 保护 pinned 记忆
            if "pinned" in mem.get("tags", []):
                continue

            # 高价值记忆延缓删除（importance > 0.8 且 access_count > 10）
            grace_multiplier = 4 if (
                mem.get("importance", 0) > 0.8
                and mem.get("access_count", 0) > 10
            ) else 3

            if age_days >= older_than_days * grace_multiplier:
                to_delete.append(mem["id"])
            elif (
                age_days >= older_than_days
                and mem.get("importance", 0.5) <= importance_below
            ):
                to_delete.append(mem["id"])

        for mem_id in to_delete:
            self._store.delete(mem_id)
            deleted_count += 1

        if deleted_count > 0:
            # 重建 TF-IDF 索引
            self._retrieval.rebuild_index(self._store.get_by_types(["short", "long"]))
            logger.info(f"遗忘操作完成：删除 {deleted_count} 条长期记忆")

        return deleted_count

    def inject_context(
        self,
        current_query: str,
        max_tokens: int = 2000,
    ) -> str:
        """
        为当前 LLM 请求生成需要注入的上下文文本。

        参数:
            current_query: 当前查询文本。
            max_tokens:    注入上下文的最大 token 数。

        返回:
            格式化的 Markdown 上下文字符串。
            若 enabled=False 或工作记忆为空且无相关长期记忆，返回空字符串 ""。
        """
        if not self._enabled:
            return ""

        # 检索相关长期记忆
        long_results = self.get_memories(
            query=current_query,
            mem_type=None,
            limit=self._long_term_max_inject,
        )

        # 格式化
        return self._injector.format_context(
            working_memories=self._working_memories,
            long_term_results=long_results,
        )

    def clear_working_memory(self) -> None:
        """
        清空当前工作记忆。

        调用时机:
            新任务开始时由外部调用者（如 QueryLoop）调用。
        """
        count = len(self._working_memories)
        self._working_memories.clear()
        logger.info(f"已清空 {count} 条工作记忆")

    def persist_working_to_short(self) -> int:
        """
        将当前工作记忆中所有条目转移为短期记忆（持久化），并清空工作记忆。

        返回:
            转移的条目数量。

        注意:
            转移后的条目 type 变为 "short"，importance 保留原值。
        """
        count = len(self._working_memories)
        for mem in self._working_memories:
            self._store.add({
                **mem,
                "type": "short",
            })
        self._working_memories.clear()
        logger.info(f"已将 {count} 条工作记忆转移为短期记忆")
        return count

    # ========== 内部方法 ==========

    def _on_access_hit(self, memory_id: str) -> None:
        """
        检索命中回调：更新 access_count 和 last_access。
        """
        now = int(time.time())
        mem = self._store.get_by_id(memory_id)
        if mem is not None:
            self._store.update(memory_id, {
                "access_count": mem.get("access_count", 0) + 1,
                "last_access": now,
            })

    def _enforce_working_token_limit(self) -> None:
        """
        当工作记忆总 token 数超过上限时，按 importance 升序删除末尾条目。
        """
        if not self._working_memories:
            return

        total = MemoryCompressor.estimate_tokens(
            " ".join(m["content"] for m in self._working_memories)
        )

        while total > self._working_memory_max_tokens and self._working_memories:
            # 删除 importance 最低且最旧的条目
            self._working_memories.sort(key=lambda m: (m.get("importance", 0.5), m.get("timestamp", 0)))
            removed = self._working_memories.pop(0)
            logger.debug(f"工作记忆超限，删除: {removed['id'][:8]}...")
            total = MemoryCompressor.estimate_tokens(
                " ".join(m["content"] for m in self._working_memories)
            )

    def _llm_call_wrapper(
        self,
        model: str,
        messages: List[Dict],
        max_tokens: int = 512,
        temperature: float = 0.3,
    ):
        """
        LLM 调用包装器，供 MemoryCompressor 使用。

        尝试从 src.query.chat_llm 导入 _chat_with_retry，
        若不可用则抛出异常（需由调用方注入）。
        """
        try:
            from src.query.chat_llm import _chat_with_retry
            return _chat_with_retry(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except ImportError as e:
            logger.error(f"无法导入 chat_llm: {e}")
            raise