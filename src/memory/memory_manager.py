import uuid
import time
import logging
from typing import List, Optional, Dict, Callable

from .memory_store import MemoryStore
from .memory_retrieval import MemoryRetrieval
from .memory_compressor import MemoryCompressor
from .memory_injector import MemoryInjector


logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Memory 模块的统一入口（Facade）。
    协调 MemoryStore / MemoryRetrieval / MemoryCompressor / MemoryInjector 四个子组件。
    所有方法均为同步（无 async/await）。
    """

    def __init__(self, config: Dict):
        """
        初始化 MemoryManager。

        参数:
            config: 完整配置字典（与 config.yaml 结构一致），
                    必须包含 config["memory"] 子字典。

        内部行为:
            1. 从 config["memory"] 提取各项阈值参数。
            2. 创建 MemoryStore 实例，加载持久化文件。
            3. 创建 MemoryRetrieval / MemoryCompressor / MemoryInjector 实例。
            4. 初始化空的工作记忆列表 self._working_memories: List[Dict]。
        """
        mem_cfg = config.get("memory", {})

        self._enabled = mem_cfg.get("enabled", True)
        storage_path = mem_cfg.get("storage_path", ".memdir")

        # 初始化子组件
        self._store = MemoryStore(storage_path)
        self._retrieval = MemoryRetrieval(
            similarity_threshold=mem_cfg.get("similarity_threshold", 0.15)
        )
        self._compressor = MemoryCompressor(
            short_term_max_entries=mem_cfg.get("short_term_max_entries", 50),
            short_term_max_tokens=mem_cfg.get("short_term_max_tokens", 8000),
            compress_batch_size=mem_cfg.get("compress_batch_size", 20),
            llm_call=None  # 由外部注入
        )
        self._injector = MemoryInjector(
            max_tokens=int(mem_cfg.get("working_memory_max_tokens", 2000))
        )

        self._long_term_max_inject = int(mem_cfg.get("long_term_max_inject", 5))
        self._working_memory_max_tokens = int(mem_cfg.get(
            "working_memory_max_tokens", 2000
        ))
        self._forget_older_than_days = mem_cfg.get("forget_older_than_days", 30)
        self._forget_importance_below = mem_cfg.get(
            "forget_importance_below", 0.2
        )

        # 工作记忆（不持久化）
        self._working_memories: List[Dict] = []

        logger.info(
            f"MemoryManager 初始化完成: "
            f"enabled={self._enabled}, "
            f"短={self._store.count('short')}条, "
            f"长={self._store.count('long')}条, "
            f"工作=0条"
        )

    # ========== CRUD ==========

    def add_memory(self,
                   content: str,
                   mem_type: str,
                   importance: float = 0.5,
                   tags: Optional[List[str]] = None,
                   metadata: Optional[Dict] = None) -> str:
        """
        添加一条记忆，返回记忆 ID（32 位十六进制字符串）。

        参数:
            content:    记忆文本内容（UTF-8）。
            mem_type:   "working" | "short" | "long"。
            importance: 0.0~1.0，默认 0.5。
            tags:       可选标签列表。
            metadata:   可选元数据字典（如 user_input / llm_reasoning 等结构化字段）。

        行为:
            - mem_type == "working" → 仅追加到 self._working_memories，不持久化。
            - mem_type == "short" | "long" → 创建条目并调用 MemoryStore.save() 持久化。

        返回:
            新创建记忆的 id 字符串。
        """
        now = int(time.time())
        mem_id = uuid.uuid4().hex

        if mem_type == "working":
            memory = {
                "id": mem_id,
                "content": content,
                "type": "working",
                "importance": max(0.0, min(1.0, float(importance))),
                "timestamp": now,
                "access_count": 0,
                "last_access": now,
                "tags": tags if isinstance(tags, list) else [],
                "metadata": metadata if isinstance(metadata, dict) else {}
            }
            self._working_memories.append(memory)
            self._enforce_working_token_limit()
            logger.debug(f"添加工作记忆: id={mem_id}")
            return mem_id

        # short / long：持久化
        memory = {
            "id": mem_id,
            "content": content,
            "type": mem_type,
            "importance": importance,
            "timestamp": now,
            "access_count": 0,
            "last_access": now,
            "tags": tags or [],
            "metadata": metadata if isinstance(metadata, dict) else {}
        }
        return self._store.add(memory)

    def get_memories(self,
                     query: str,
                     mem_type: Optional[str] = None,
                     limit: int = 5) -> List[Dict]:
        """
        根据查询文本检索最相关的记忆条目。

        参数:
            query:    查询文本（用于 TF‑IDF 向量化）。
            mem_type: 可选过滤类型，None 表示检索 short + long。
            limit:    最大返回条数。

        返回:
            按最终得分降序排列的记忆字典列表。
            命中条目的 access_count 和 last_access 同步更新并持久化。

        异常处理:
            向量化失败或记忆为空 → 返回 []，记录 logging.warning。
        """
        try:
            short_mems = []
            long_mems = []

            if mem_type is None:
                short_mems = self._store.get_all("short")
                long_mems = self._store.get_all("long")
            elif mem_type == "short":
                short_mems = self._store.get_all("short")
            elif mem_type == "long":
                long_mems = self._store.get_all("long")

            results = self._retrieval.search(
                query=query,
                long_memories=long_mems,
                short_memories=short_mems,
                working_memories=self._working_memories,
                limit=limit
            )

            # 持久化命中后更新的 access_count / last_access
            for r in results:
                if "id" in r and r.get("type") in ("short", "long"):
                    self._store.update(
                        r["id"],
                        access_count=r.get("access_count", 0),
                        last_access=r.get("last_access", int(time.time()))
                    )

            return results

        except Exception as e:
            logger.warning(f"检索失败: {e}")
            return []

    def get_all_memories(self,
                         mem_type: Optional[str] = None) -> List[Dict]:
        """
        返回所有指定类型的记忆（不修改 access_count）。

        参数:
            mem_type: None 返回全部，或指定 "short" / "long"。

        用途:
            主要用于测试验证和调试导出。
        """
        if mem_type == "working":
            return list(self._working_memories)

        return self._store.get_all(mem_type)

    def update_memory(self,
                      memory_id: str,
                      content: Optional[str] = None,
                      importance: Optional[float] = None,
                      tags: Optional[List[str]] = None) -> bool:
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
        kwargs = {}
        if content is not None:
            kwargs["content"] = content
        if importance is not None:
            kwargs["importance"] = max(0.0, min(1.0, float(importance)))
        if tags is not None:
            kwargs["tags"] = tags

        if kwargs:
            return self._store.update(memory_id, **kwargs)

        # 没有任何更新字段，仅检查是否存在
        return self._store.get(memory_id) is not None

    def delete_memory(self, memory_id: str) -> bool:
        """
        永久删除一条记忆。

        返回:
            True 表示删除成功，False 表示未找到指定 ID。
        """
        # 查工作记忆
        for i, mem in enumerate(self._working_memories):
            if mem["id"] == memory_id:
                del self._working_memories[i]
                logger.info(f"删除工作记忆: id={memory_id}")
                return True

        # 查持久化记忆
        return self._store.delete(memory_id)

    def clear_all_memories(self) -> int:
        """
        清除所有记忆（短期 + 长期 + 工作记忆），并删除所有备份文件。

        返回:
            被删除的记忆条目总数。
        """
        # 删除所有持久化记忆（含备份文件）
        all_mems = self._store.get_all()
        ids = [mem["id"] for mem in all_mems]
        persisted_count = self._store.delete_batch(ids)
        self._store.clear_all()

        # 清空工作记忆
        working_count = len(self._working_memories)
        self._working_memories.clear()

        total = persisted_count + working_count
        logger.info(f"清除所有记忆: {total} 条 (持久化={persisted_count}, 工作={working_count})，备份已删除")
        return total

    # ========== 记忆生命周期 ==========

    def compress_short_term(self) -> int:
        """
        当短期记忆超过阈值时，调用 LLM 将旧条目合并为长期记忆。

        返回:
            本次压缩新生成的长期记忆数量（0 表示未触发或无需压缩）。
        """
        short_mems = self._store.get_all("short")
        if not short_mems:
            return 0

        return self._compressor.compress(
            short_memories=short_mems,
            add_long_memory=self._add_long_from_compress,
            delete_memory=self._store.delete
        )

    def forget(self,
               older_than_days: int = 30,
               importance_below: float = 0.2) -> int:
        """
        根据时间与重要性自动遗忘长期记忆。

        详细算法见 spec 6.4 节。

        返回:
            被删除的记忆条目数量。
        """
        older_than_days = older_than_days or self._forget_older_than_days
        importance_below = importance_below or self._forget_importance_below

        now = time.time()
        deleted_count = 0
        to_delete = []

        for mem in self._store.get_all("long"):
            age_days = (now - mem.get("timestamp", 0)) / 86400

            # 保护 pinned 记忆
            if "pinned" in mem.get("tags", []):
                continue

            # 高价值记忆延缓删除
            if (mem.get("importance", 0) > 0.8
                    and mem.get("access_count", 0) > 10):
                grace_multiplier = 4
            else:
                grace_multiplier = 3

            if age_days >= older_than_days * grace_multiplier:
                to_delete.append(mem["id"])
            elif (age_days >= older_than_days
                  and mem.get("importance", 0) <= importance_below):
                to_delete.append(mem["id"])

        for mem_id in to_delete:
            self._store.delete(mem_id)
            deleted_count += 1

        if deleted_count > 0:
            self._retrieval.rebuild_index()
            logger.info(f"遗忘操作完成：删除 {deleted_count} 条长期记忆")

        return deleted_count

    def inject_context(self,
                       current_query: str = "",
                       max_tokens: int = 2000) -> tuple:
        """
        为当前 LLM 请求生成需要注入的上下文文本。

        详细格式见 spec 6.3 节。

        返回:
            (格式化的 Markdown 上下文字符串, 召回的记忆数量)。
            若 enabled=False 或工作记忆为空且无相关长期记忆，返回 ("", 0)。
        """
        if not self._enabled:
            return "", 0

        # 检索长期记忆 + 短期记忆
        long_mems = []
        if current_query:
            long_mems = self._retrieval.search(
                query=current_query,
                long_memories=self._store.get_all("long"),
                short_memories=self._store.get_all("short"),
                working_memories=[],
                limit=self._long_term_max_inject
            )

        # 格式化注入文本
        formatted = self._injector.format_context(
            working_memories=self._working_memories,
            long_memories=long_mems,
            max_tokens=max_tokens
        )

        # 统计召回的记忆数量
        memory_count = len(self._working_memories) + len(long_mems)
        return formatted, memory_count

    def clear_working_memory(self) -> None:
        """
        清空当前工作记忆。

        调用时机:
            新任务开始时由外部调用者（如 QueryLoop）调用。
        """
        count = len(self._working_memories)
        self._working_memories.clear()
        logger.info(f"清空工作记忆: {count} 条")

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
                "id": mem.get("id"),
                "content": mem.get("content", ""),
                "type": "short",
                "importance": mem.get("importance", 0.5),
                "timestamp": mem.get("timestamp", int(time.time())),
                "access_count": mem.get("access_count", 0),
                "last_access": mem.get("last_access", int(time.time())),
                "tags": mem.get("tags", []),
                "metadata": mem.get("metadata", {})
            })

        self._working_memories.clear()
        logger.info(f"工作记忆转移为短期记忆: {count} 条")
        return count

    # ========== LLM 回调注入 ==========

    def set_llm_call(self, llm_call: Callable) -> None:
        """
        注入 LLM 调用函数给 MemoryCompressor。

        参数:
            llm_call: 签名为 (messages, max_tokens, temperature) -> str | None。
        """
        self._compressor._llm_call = llm_call
        logger.info("LLM 回调已注入到 MemoryCompressor")

    # ========== 内部方法 ==========

    def _add_long_from_compress(self,
                                 content: str,
                                 importance: float,
                                 tags: List[str],
                                 metadata: Dict) -> str:
        """压缩器回调：创建长期记忆。"""
        now = int(time.time())
        memory = {
            "id": uuid.uuid4().hex,
            "content": content,
            "type": "long",
            "importance": importance,
            "timestamp": now,
            "access_count": 0,
            "last_access": now,
            "tags": tags,
            "metadata": metadata
        }
        return self._store.add(memory)

    def _enforce_working_token_limit(self) -> None:
        """
        当工作记忆总 token 数超过上限时，按 importance 升序删除末尾条目。
        """
        while True:
            total_text = " ".join(
                m.get("content", "") for m in self._working_memories
            )
            if MemoryInjector._estimate_tokens(total_text) <= \
                    self._working_memory_max_tokens:
                break
            if not self._working_memories:
                break

            # 按 importance 升序，删除最不重要的
            to_remove = min(
                self._working_memories,
                key=lambda m: m.get("importance", 0.5)
            )
            self._working_memories.remove(to_remove)
            logger.debug(
                f"工作记忆超限，移除条目: id={to_remove['id']}"
            )