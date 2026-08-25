"""记忆召回模块。

召回策略：两阶段召回（向量粗排 + LLM 精排）+ 倒排索引搜索。

职责：
- 向量粗排：通过 FAISS 向量检索从全量记忆中筛选 top_k 候选
- LLM 精排：在候选集上用 LLM 评分，选出最终注入的记忆
- 读取 Layer 1（MEMORY.md）内容供注入
- 利用倒排索引搜索 Layer 1 数据
- 提供搜索接口供 CLI /mem search 命令调用
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .embedding.memory_retrieval import retrieve_by_vector

logger = logging.getLogger(__name__)

# Prompt 模板路径
_PROMPT_DIR = Path(__file__).parent / "prompts"

# ===== 策略描述映射 =====
_STRATEGY_DESC = {
    "coarse_only": "仅粗排",
    "coarse_llm": "粗排+LLM精排",
    "coarse_rerank": "粗排+Rerank精排",
    "llm_only": "仅LLM精排",
    "rerank_only": "仅Rerank精排",
}


@dataclass
class RetrievalStage:
    """单个召回阶段的记录"""
    stage_name: str          # 阶段名称：如 "向量粗排"、"LLM精排"、"Rerank精排"
    items: List[Dict[str, Any]]  # 本阶段召回的记忆条目列表
    count: int               # 本阶段召回条目数


@dataclass
class RetrievalResult:
    """记忆召回完整结果"""
    strategy: str            # 策略代号：如 "coarse_llm"
    strategy_desc: str       # 策略描述：如 "粗排+LLM精排"
    stages: List[RetrievalStage] = field(default_factory=list)
    final_items: List[Dict[str, Any]] = field(default_factory=list)
    final_count: int = 0

    def to_log_text(self) -> str:
        """生成记忆召回日志文本（MD格式）。

        每条记忆按以下格式输出：
          [j] 分数: xxx
          (id=xxx)
          - 内容
          (session=xxx)
        """
        lines = [f"策略: {self.strategy_desc} ({self.strategy})"]
        for i, stage in enumerate(self.stages, 1):
            lines.append(f"\n  ── [{i}] {stage.stage_name} ({stage.count}条) ──")
            lines.append("")  # 阶段标题后加空行
            for j, item in enumerate(stage.items, 1):
                score = item.get("score", "N/A")
                content = item.get("content", "")
                entry_id = item.get("id", "")
                session_id = item.get("session_id", "")
                if isinstance(score, float):
                    score_str = f"{score:.4f}"
                else:
                    score_str = str(score)
                lines.append(f"  [{j}] 分数: {score_str}")
                if entry_id:
                    lines.append(f"  (id={entry_id})")
                lines.append(f"  - {content}")
                if session_id:
                    lines.append(f"  (session={session_id})")
                if j < len(stage.items):
                    lines.append("")  # 多条记忆之间加空行
        lines.append(f"\n  ── 最终返回 ({self.final_count}条) ──")
        lines.append("")  # 阶段标题后加空行
        for j, item in enumerate(self.final_items, 1):
            score = item.get("score", "N/A")
            content = item.get("content", "")
            entry_id = item.get("id", "")
            session_id = item.get("session_id", "")
            if isinstance(score, float):
                score_str = f"{score:.4f}"
            else:
                score_str = str(score)
            lines.append(f"  [{j}] 分数: {score_str}")
            if entry_id:
                lines.append(f"  (id={entry_id})")
            lines.append(f"  - {content}")
            if session_id:
                lines.append(f"  (session={session_id})")
            if j < len(self.final_items):
                lines.append("")  # 多条记忆之间加空行
        return "\n".join(lines)


def _load_prompt(filename: str) -> str:
    """加载 Prompt 模板文件。"""
    try:
        return (_PROMPT_DIR / filename).read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(f"Prompt 模板未找到: {filename}")
        return ""


class MemoryRetriever:
    """记忆召回器。

    正常情况下召回方只读 Layer 1 + Layer 2。
    如果 LLM 发现索引层信息不够，可以主动搜索 Layer 0 原始数据。

    召回策略：
    - retrieve_for_query(): LLM 预检索，根据查询相关性筛选 Layer 1 条目
    - get_layer1_content(): 全量返回（向后兼容）
    - search(): 关键词搜索（CLI 手动调用）
    """

    def __init__(self, mem_config: Any, store: Any):
        """初始化召回器。

        Args:
            mem_config: memory_ex.yaml 配置对象
            store: MemoryStore 实例
        """
        self._store = store

        retrieval_config = mem_config.retrieval
        self._default_top_k = int(getattr(retrieval_config, "default_top_k", 5))
        self._max_top_k = int(getattr(retrieval_config, "max_top_k", 20))

        # 召回策略
        self._strategy = getattr(retrieval_config, "strategy", "coarse_llm")

        # 向量粗排参数（兼容旧版扁平配置和新版嵌套配置）
        vector_cfg = getattr(retrieval_config, "vector", None)
        if vector_cfg is not None:
            self._vector_top_k = int(getattr(vector_cfg, "top_k", 10))
            self._vector_score_threshold = float(getattr(vector_cfg, "score_threshold", 0.3))
        else:
            self._vector_top_k = int(getattr(retrieval_config, "vector_top_k", 10))
            self._vector_score_threshold = float(getattr(retrieval_config, "vector_score_threshold", 0.3))

        # LLM 精排参数（兼容旧版扁平配置和新版嵌套配置）
        llm_rerank_cfg = getattr(retrieval_config, "llm_rerank", None)
        if llm_rerank_cfg is not None:
            self._llm_max_results = int(getattr(llm_rerank_cfg, "max_results", 3))
            self._timeout = int(getattr(llm_rerank_cfg, "timeout", 30))
        else:
            self._llm_max_results = int(getattr(retrieval_config, "llm_max_results", 3))
            self._timeout = int(getattr(retrieval_config, "timeout", 30))

        # Rerank 精排参数
        rerank_cfg = getattr(retrieval_config, "rerank", None)
        if rerank_cfg is not None:
            self._rerank_max_results = int(getattr(rerank_cfg, "max_results", 3))
            self._rerank_timeout = int(getattr(rerank_cfg, "timeout", 30))
        else:
            self._rerank_max_results = 3
            self._rerank_timeout = 30

        # 降级策略
        self._fallback_to_full = bool(
            getattr(retrieval_config, "fallback_to_full", True)
        )

        injection_config = mem_config.injection
        self._max_injection_tokens = int(
            getattr(injection_config, "max_tokens", 2000)
        )

        # LLM 调用函数（延迟注入）
        self._llm_chat_fn = None

        # Rerank 客户端（延迟初始化）
        self._rerank_client = None

    def set_llm_chat_fn(self, fn):
        """注入 LLM 调用函数。"""
        self._llm_chat_fn = fn

    def _get_rerank_client(self):
        """延迟初始化 Rerank 客户端。"""
        if self._rerank_client is None:
            try:
                from .embedding.rerank_client import RerankClient
                from src.utility.config_loader import global_cfg
                memory_cfg = getattr(global_cfg, "memory", None)
                rerank_provider = getattr(memory_cfg, "rerank_re_ranking", None) if memory_cfg else None
                if rerank_provider is not None:
                    provider_name = getattr(rerank_provider, "provider", "GLM")
                else:
                    provider_name = "GLM"
                self._rerank_client = RerankClient(provider=provider_name)
            except Exception as e:
                logger.error(f"RerankClient 初始化失败: {e}")
                self._rerank_client = False  # 标记初始化失败，避免重复尝试
        return self._rerank_client if self._rerank_client is not False else None

    def retrieve_for_query(self, query: str, exclude_session_id: str = "") -> List[Dict[str, Any]]:
        """记忆召回（仅返回条目，不带分数）。

        根据配置的策略执行召回，返回最终选中的记忆条目列表。

        Args:
            query: 增强后的用户查询（可能含文件内容）
            exclude_session_id: 需要排除的 session_id（当前会话）

        Returns:
            筛选后的记忆条目列表，每个元素含 id, session_id, tags, content, raw_line。
            空列表表示无相关记忆或召回失败。
        """
        result = self.retrieve_for_query_with_scores(query, exclude_session_id)
        return result.final_items

    def retrieve_for_query_with_scores(
        self, query: str, exclude_session_id: str = ""
    ) -> RetrievalResult:
        """记忆召回（带完整阶段记录）。

        根据配置的策略执行召回，返回包含各阶段记录的 RetrievalResult 对象。

        支持的策略：
        - coarse_only: 仅向量粗排
        - coarse_llm: 向量粗排 + LLM精排
        - coarse_rerank: 向量粗排 + Rerank精排
        - llm_only: 仅LLM精排（全量记忆）
        - rerank_only: 仅Rerank精排（全量记忆）

        Args:
            query: 增强后的用户查询（可能含文件内容）
            exclude_session_id: 需要排除的 session_id（当前会话）

        Returns:
            RetrievalResult 对象，包含策略信息、各阶段记录和最终结果。
        """
        strategy = self._strategy
        strategy_desc = _STRATEGY_DESC.get(strategy, strategy)

        # 空结果对象
        empty_result = RetrievalResult(
            strategy=strategy,
            strategy_desc=strategy_desc,
        )

        layer1_content = self._store.read_layer1()
        if not layer1_content or not layer1_content.strip():
            return empty_result

        all_entries = self._parse_layer1_entries(layer1_content)
        if not all_entries:
            return empty_result

        # 显式过滤：排除当前 session 的记忆
        if exclude_session_id:
            entries = [
                e for e in all_entries
                if e.get("session_id", "") != exclude_session_id
            ]
            excluded_count = len(all_entries) - len(entries)
            if excluded_count > 0:
                logger.info(f"已过滤当前 session 记忆 {excluded_count} 条")
        else:
            entries = all_entries

        if not entries:
            return empty_result

        # 策略分发
        if strategy == "coarse_only":
            return self._retrieve_coarse_only(query, entries, strategy, strategy_desc)
        elif strategy == "coarse_llm":
            return self._retrieve_coarse_llm(query, entries, strategy, strategy_desc)
        elif strategy == "coarse_rerank":
            return self._retrieve_coarse_rerank(query, entries, strategy, strategy_desc)
        elif strategy == "llm_only":
            return self._retrieve_llm_only(query, entries, strategy, strategy_desc)
        elif strategy == "rerank_only":
            return self._retrieve_rerank_only(query, entries, strategy, strategy_desc)
        else:
            logger.warning(f"未知召回策略: {strategy}，降级为 coarse_llm")
            return self._retrieve_coarse_llm(query, entries, "coarse_llm", "粗排+LLM精排")

    # ===== 策略实现 =====

    def _retrieve_coarse_only(
        self, query: str, entries: List[Dict[str, Any]], strategy: str, strategy_desc: str
    ) -> RetrievalResult:
        """策略A：仅向量粗排。"""
        result = RetrievalResult(strategy=strategy, strategy_desc=strategy_desc)

        candidates = self._vector_coarse_rank(query, entries)
        if candidates is None:
            # 降级为全量
            logger.info("向量粗排降级：全量记忆返回")
            candidates = entries if self._fallback_to_full else []
        if not candidates:
            result.stages.append(RetrievalStage(
                stage_name="向量粗排",
                items=[],
                count=0,
            ))
            return result

        # 粗排结果带上向量分数（从 retrieve_by_vector 获取）
        scored_candidates = self._enrich_with_vector_scores(query, candidates)

        stage = RetrievalStage(
            stage_name="向量粗排",
            items=scored_candidates,
            count=len(scored_candidates),
        )
        result.stages.append(stage)
        result.final_items = scored_candidates
        result.final_count = len(scored_candidates)
        return result

    def _retrieve_coarse_llm(
        self, query: str, entries: List[Dict[str, Any]], strategy: str, strategy_desc: str
    ) -> RetrievalResult:
        """策略B：向量粗排 + LLM精排。"""
        result = RetrievalResult(strategy=strategy, strategy_desc=strategy_desc)

        if not self._llm_chat_fn:
            logger.info("LLM 调用函数未注入，跳过召回")
            result.stages.append(RetrievalStage(
                stage_name="向量粗排",
                items=[],
                count=0,
            ))
            result.stages.append(RetrievalStage(
                stage_name="LLM精排",
                items=[],
                count=0,
            ))
            return result

        # 阶段一：向量粗排
        candidates = self._vector_coarse_rank(query, entries)
        if candidates is None:
            # 降级为 llm_only
            logger.info("向量粗排降级：全量记忆送入 LLM 精排")
            return self._retrieve_llm_only(query, entries, strategy, strategy_desc)
        if not candidates:
            result.stages.append(RetrievalStage(
                stage_name="向量粗排",
                items=[],
                count=0,
            ))
            result.stages.append(RetrievalStage(
                stage_name="LLM精排",
                items=[],
                count=0,
            ))
            return result

        scored_candidates = self._enrich_with_vector_scores(query, candidates)
        result.stages.append(RetrievalStage(
            stage_name="向量粗排",
            items=scored_candidates,
            count=len(scored_candidates),
        ))

        # 阶段二：LLM 精排
        ranked = self._llm_fine_rank(query, candidates, entries)
        if not ranked:
            result.stages.append(RetrievalStage(
                stage_name="LLM精排",
                items=[],
                count=0,
            ))
            return result

        final_items = []
        for entry, score in ranked:
            item = dict(entry)
            item["score"] = score
            final_items.append(item)

        result.stages.append(RetrievalStage(
            stage_name="LLM精排",
            items=final_items,
            count=len(final_items),
        ))
        result.final_items = final_items
        result.final_count = len(final_items)
        return result

    def _retrieve_coarse_rerank(
        self, query: str, entries: List[Dict[str, Any]], strategy: str, strategy_desc: str
    ) -> RetrievalResult:
        """策略C：向量粗排 + Rerank精排。"""
        result = RetrievalResult(strategy=strategy, strategy_desc=strategy_desc)

        # 阶段一：向量粗排
        candidates = self._vector_coarse_rank(query, entries)
        if candidates is None:
            # 降级为 rerank_only
            logger.info("向量粗排降级：全量记忆送入 Rerank 精排")
            return self._retrieve_rerank_only(query, entries, strategy, strategy_desc)
        if not candidates:
            return result

        scored_candidates = self._enrich_with_vector_scores(query, candidates)
        result.stages.append(RetrievalStage(
            stage_name="向量粗排",
            items=scored_candidates,
            count=len(scored_candidates),
        ))

        # 阶段二：Rerank 精排
        rerank_client = self._get_rerank_client()
        if rerank_client is None:
            logger.error("Rerank客户端不可用，返回粗排结果")
            result.stages.append(RetrievalStage(
                stage_name="Rerank精排",
                items=[],
                count=0,
            ))
            result.final_items = scored_candidates
            result.final_count = len(scored_candidates)
            return result

        documents = [c.get("content", "") for c in candidates]
        rerank_results = rerank_client.rerank(
            query, documents, top_n=self._rerank_max_results, timeout=self._rerank_timeout
        )
        if not rerank_results:
            logger.info("Rerank精排无结果，返回粗排结果")
            result.stages.append(RetrievalStage(
                stage_name="Rerank精排",
                items=[],
                count=0,
            ))
            result.final_items = scored_candidates
            result.final_count = len(scored_candidates)
            return result

        final_items = []
        for r in rerank_results:
            idx = r.get("index", -1)
            if 0 <= idx < len(candidates):
                item = dict(candidates[idx])
                item["score"] = r.get("score", 0.0)
                final_items.append(item)

        result.stages.append(RetrievalStage(
            stage_name="Rerank精排",
            items=final_items,
            count=len(final_items),
        ))
        result.final_items = final_items
        result.final_count = len(final_items)
        return result

    def _retrieve_llm_only(
        self, query: str, entries: List[Dict[str, Any]], strategy: str, strategy_desc: str
    ) -> RetrievalResult:
        """策略D：仅LLM精排（全量记忆）。"""
        result = RetrievalResult(strategy=strategy, strategy_desc=strategy_desc)

        if not self._llm_chat_fn:
            logger.info("LLM 调用函数未注入，跳过召回")
            result.stages.append(RetrievalStage(
                stage_name="全量候选",
                items=[],
                count=0,
            ))
            result.stages.append(RetrievalStage(
                stage_name="LLM精排",
                items=[],
                count=0,
            ))
            return result

        # 全量候选
        result.stages.append(RetrievalStage(
            stage_name="全量候选",
            items=entries,
            count=len(entries),
        ))

        # LLM 精排
        ranked = self._llm_fine_rank(query, entries, entries)
        if not ranked:
            result.stages.append(RetrievalStage(
                stage_name="LLM精排",
                items=[],
                count=0,
            ))
            return result

        final_items = []
        for entry, score in ranked:
            item = dict(entry)
            item["score"] = score
            final_items.append(item)

        result.stages.append(RetrievalStage(
            stage_name="LLM精排",
            items=final_items,
            count=len(final_items),
        ))
        result.final_items = final_items
        result.final_count = len(final_items)
        return result

    def _retrieve_rerank_only(
        self, query: str, entries: List[Dict[str, Any]], strategy: str, strategy_desc: str
    ) -> RetrievalResult:
        """策略E：仅Rerank精排（全量记忆）。"""
        result = RetrievalResult(strategy=strategy, strategy_desc=strategy_desc)

        # 全量候选
        result.stages.append(RetrievalStage(
            stage_name="全量候选",
            items=entries,
            count=len(entries),
        ))

        # Rerank 精排
        rerank_client = self._get_rerank_client()
        if rerank_client is None:
            logger.error("Rerank客户端不可用，跳过召回")
            result.stages.append(RetrievalStage(
                stage_name="Rerank精排",
                items=[],
                count=0,
            ))
            return result

        documents = [e.get("content", "") for e in entries]
        rerank_results = rerank_client.rerank(
            query, documents, top_n=self._rerank_max_results, timeout=self._rerank_timeout
        )
        if not rerank_results:
            logger.info("Rerank精排无结果")
            result.stages.append(RetrievalStage(
                stage_name="Rerank精排",
                items=[],
                count=0,
            ))
            return result

        final_items = []
        for r in rerank_results:
            idx = r.get("index", -1)
            if 0 <= idx < len(entries):
                item = dict(entries[idx])
                item["score"] = r.get("score", 0.0)
                final_items.append(item)

        result.stages.append(RetrievalStage(
            stage_name="Rerank精排",
            items=final_items,
            count=len(final_items),
        ))
        result.final_items = final_items
        result.final_count = len(final_items)
        return result

    def _enrich_with_vector_scores(
        self, query: str, candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """为粗排候选条目附加向量分数。

        降级条目（带 _degraded 标记）已自带 score=0.0，跳过 API 调用，
        避免对同一超长查询文本重复调用必然失败的 embedding API。

        Args:
            query: 查询文本
            candidates: 粗排匹配到的条目列表

        Returns:
            带有 score 字段的条目列表
        """
        # 降级条目已带 score=0.0，跳过 API 调用（避免重复失败）
        if candidates and candidates[0].get("_degraded"):
            return candidates

        try:
            results = retrieve_by_vector(
                query,
                top_k=self._vector_top_k,
                score_threshold=self._vector_score_threshold,
            )
            score_map = {}
            if results:
                for r in results:
                    mem_id = r.get("id", "")
                    if mem_id:
                        score_map[mem_id] = r.get("score", 0.0)
        except Exception as e:
            print(f"⚠️ [记忆召回] 向量分数获取异常: {e}")
            score_map = {}

        enriched = []
        for c in candidates:
            item = dict(c)
            item["score"] = score_map.get(c.get("id", ""), 0.0)
            enriched.append(item)
        return enriched

    # ===== 原有方法保留 =====

    def _vector_coarse_rank(
        self, query: str, entries: List[Dict[str, Any]]
    ) -> Optional[List[Dict[str, Any]]]:
        """向量粗排阶段：通过 FAISS 向量检索筛选候选记忆。

        Args:
            query: 粗排输入文本（用户查询或查询+文件内容组合）
            entries: 全量 Layer 1 条目列表（用于降级和匹配）

        Returns:
            候选记忆条目列表（≤ vector_top_k 条）；
            None 表示降级为全量记忆（索引不存在或向量化失败）；
            空列表表示索引存在但无匹配结果。
        """
        try:
            results = retrieve_by_vector(
                query,
                top_k=self._vector_top_k,
                score_threshold=self._vector_score_threshold,
            )
        except Exception as e:
            logger.error(f"向量粗排异常: {e}，降级为全量记忆")
            print(f"⚠️ [记忆召回] 向量粗排失败: {e}，已降级为全量记忆送入精排")
            results = None

        # 索引不存在或向量化失败 → 降级为全量记忆
        if results is None:
            if self._fallback_to_full:
                logger.info("向量粗排降级：全量记忆送入 LLM 精排")
                # 降级条目标记 score=0.0，避免 _enrich_with_vector_scores 重复调 API
                degraded = []
                for e in entries:
                    item = dict(e)
                    item["score"] = 0.0
                    item["_degraded"] = True
                    degraded.append(item)
                return degraded
            else:
                logger.info("向量粗排降级已禁用，不召回")
                return []

        if not results:
            logger.info("向量粗排无匹配候选")
            return []

        # 用 ID 直接查表，将向量检索结果映射回结构化条目
        matched = self._match_vector_to_entries_by_id(results, entries)

        logger.info(f"向量粗排匹配 {len(matched)} 条候选记忆")
        return matched

    def _match_vector_to_entries_by_id(
        self,
        vector_results: List[Dict[str, Any]],
        entries: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """用记忆 ID 直接查表，将向量检索结果映射回结构化条目。

        替代旧的文本逐字比对方式，通过 ID 唯一标识一步到位，不会因文本变动而失配。

        Args:
            vector_results: 向量检索返回的结果列表（含 id 字段）
            entries: 全量结构化条目列表

        Returns:
            匹配到的条目列表（保持向量检索的排序顺序）
        """
        # 构建 id → entry 的查表字典
        id_to_entry = {}
        for entry in entries:
            entry_id = entry.get("id", "")
            if entry_id:
                id_to_entry[entry_id] = entry

        matched = []
        for vr in vector_results:
            mem_id = vr.get("id", "")
            if mem_id and mem_id in id_to_entry:
                matched.append(id_to_entry[mem_id])
            else:
                # ID 缺失或未命中（索引与 MEMORY.md 不同步），跳过该条
                logger.debug(f"向量粗排 ID 未命中: {mem_id}")

        return matched

    def _llm_fine_rank(
        self,
        query: str,
        candidates: Optional[List[Dict[str, Any]]],
        all_entries: List[Dict[str, Any]],
    ) -> List[Tuple[Dict[str, Any], int]]:
        """LLM 精排阶段：在候选集上用 LLM 评分选出最终记忆。

        Args:
            query: 精排输入文本（用户查询或查询+文件内容组合）
            candidates: 粗排返回的候选条目列表，None 或空列表表示无候选
            all_entries: 全量条目（仅用于日志统计）

        Returns:
            (条目 dict, 评分) 元组列表，按分数降序排列（≤ llm_max_results 条）。
        """
        if not candidates:
            logger.info("粗排无候选，跳过 LLM 精排")
            return []

        # 构建精排 Prompt
        prompt = self._build_retrieval_prompt(query, candidates)
        if not prompt:
            return []

        # 调用 LLM 精排
        try:
            response = self._call_llm_with_timeout(prompt, timeout=self._timeout)
            if response is None:
                logger.warning("LLM 精排超时，跳过召回")
                return []

            scored = self._parse_retrieval_response(response, len(candidates))

            if not scored:
                logger.info("LLM 精排无匹配，不召回")
                return []

            # 时间衰减：对陈旧的代码架构类记忆降分
            scored = self._apply_time_decay(candidates, scored)

            # 衰减后重新过滤 ≥8 分
            scored = [(idx, score) for idx, score in scored if score >= 8]
            if not scored:
                logger.info("时间衰减后无 ≥8 分记忆，不召回")
                return []

            # 最多返回 llm_max_results 条
            scored = scored[: self._llm_max_results]
            selected = [
                (candidates[idx], score)
                for idx, score in scored
                if idx < len(candidates)
            ]
            logger.info(
                f"LLM 精排命中 {len(selected)}/{len(candidates)} 条候选 "
                f"(全量 {len(all_entries)} 条)"
            )
            return selected

        except Exception as e:
            logger.error(f"LLM 精排失败: {e}，跳过召回")
            print(f"⚠️ [记忆召回] LLM 精排失败: {e}，跳过精排")
            return []

    def _parse_layer1_entries(self, layer1_content: str) -> List[Dict[str, Any]]:
        """解析 Layer 1 内容为结构化条目列表。

        Args:
            layer1_content: MEMORY.md 的原始内容

        Returns:
            条目列表，每个元素含 id, tags, content, raw_line
        """
        entries = []
        for line in layer1_content.split("\n"):
            line = line.strip()
            if not line.startswith("- "):
                continue

            # 解析格式: - [tag1][tag2] content (id=xxx) (session=yyy)
            raw_line = line

            # 提取 ID
            id_match = re.search(r"\(id=([^)]+)\)", line)
            entry_id = id_match.group(1) if id_match else ""

            # 提取 session_id
            session_match = re.search(r"\(session=([^)]+)\)", line)
            session_id = session_match.group(1) if session_match else ""

            # 提取标签
            tags = re.findall(r"\[([^\]]+)\]", line)
            # 过滤掉 id 标签
            tags = [t for t in tags if not t.startswith("id=")]

            # 提取内容（去掉前导 "- " 和所有 [tag] 和 (id=...) 和 (session=...)）
            content = re.sub(r"^\-\s+", "", line)
            content = re.sub(r"\[[^\]]+\]", "", content).strip()
            content = re.sub(r"\(id=[^)]+\)", "", content).strip()
            content = re.sub(r"\(session=[^)]+\)", "", content).strip()

            entries.append({
                "id": entry_id,
                "session_id": session_id,
                "tags": tags,
                "content": content,
                "raw_line": raw_line,
            })

        return entries

    def _build_retrieval_prompt(self, query: str, entries: List[Dict]) -> str:
        """构建预检索 Prompt。

        Args:
            query: 增强后的用户查询
            entries: Layer 1 条目列表

        Returns:
            完整的预检索 Prompt 字符串
        """
        prompt_template = _load_prompt("retrieval_prompt.txt")
        if not prompt_template:
            logger.warning("retrieval_prompt.txt 未找到，跳过 LLM 预检索")
            return ""

        # 构建记忆列表（带编号，方括号格式与提示词输出要求对齐）
        memory_lines = []
        for i, entry in enumerate(entries, 1):
            tags_str = "".join(f"[{t}]" for t in entry.get("tags", []))
            memory_lines.append(f"[{i}] {tags_str} {entry.get('content', '')}")

        memories_text = "\n".join(memory_lines)

        prompt = prompt_template.replace("{query}", query)
        prompt = prompt.replace("{memories}", memories_text)

        return prompt

    def _parse_retrieval_response(self, response: str, total: int) -> List[Tuple[int, int]]:
        """解析 LLM 预检索响应，支持含分析摘要的多行格式。

        响应可能包含结构化分析摘要（分析 + 评估 + SCORED/NONE），
        SCORED 或 NONE 通常在最后一行，需用 search 而非 match 匹配。

        注意：本方法仅解析和过滤 ≥8 分的条目，不做数量截取。
        最终截取由 _llm_fine_rank() 在时间衰减后统一执行，
        避免过早截取导致时间衰减后可用候选不足。

        Args:
            response: LLM 响应文本
            total: 总条目数（用于边界检查）

        Returns:
            (0-based 索引, 分数) 元组列表，按分数降序排列。
            包含所有 ≥8 分的条目，不截取数量。
        """
        if not response:
            return []

        response = response.strip()

        # 检查 NONE（可能在最后一行，前面有分析摘要）
        last_line = response.split("\n")[-1].strip()
        if last_line.upper().startswith("NONE"):
            return []

        # 匹配 SCORED: 1:9, 3:8, 5:7（可能出现在分析摘要之后的任意位置）
        scored_match = re.search(r"SCORED:\s*([\d:,\s]+)", response, re.IGNORECASE)
        if scored_match:
            scored_str = scored_match.group(1)
            scored_entries = []
            for part in scored_str.split(","):
                part = part.strip()
                if ":" in part:
                    num_str, score_str = part.split(":", 1)
                    try:
                        num = int(num_str.strip())
                        score = int(score_str.strip())
                        if 1 <= num <= total and score >= 8:
                            scored_entries.append((num - 1, score))
                    except ValueError:
                        continue

            # 按分数降序排序，不截取数量（由 _llm_fine_rank 在时间衰减后截取）
            scored_entries.sort(key=lambda x: x[1], reverse=True)
            return scored_entries

        # 兼容旧格式 RELATED: 1,3,5（无分数信息，默认 8 分）
        related_match = re.search(r"RELATED:\s*([\d,\s]+)", response, re.IGNORECASE)
        if related_match:
            numbers_str = related_match.group(1)
            numbers = [int(n.strip()) for n in numbers_str.split(",") if n.strip().isdigit()]
            indices = [(n - 1, 8) for n in numbers if 1 <= n <= total]
            return indices

        logger.warning(f"无法解析预检索响应: {response[:100]}")
        return []

    def _apply_time_decay(
        self, entries: List[Dict[str, Any]], scored: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        """对 LLM 打分结果应用时间衰减。

        仅对 [代码架构] 标签的记忆应用衰减：
        - created_at 超过 30 天且 access_count == 0 → 分数 -1
        - created_at 超过 60 天且 access_count == 0 → 分数 -2

        其他标签（caution、架构决策、性能基准等）不受时间影响，
        因为它们描述的是通用规则或长期约束，不随代码变更而过时。

        Args:
            entries: Layer 1 解析后的条目列表
            scored: (索引, 分数) 元组列表

        Returns:
            调整后的 (索引, 分数) 元组列表，按分数降序排列
        """
        DECAY_TAGS = {"代码架构"}
        now = datetime.now()
        adjusted = []

        for idx, score in scored:
            entry = entries[idx] if idx < len(entries) else None
            if not entry:
                adjusted.append((idx, score))
                continue

            tags = set(entry.get("tags", []))
            if not tags & DECAY_TAGS:
                adjusted.append((idx, score))
                continue

            entry_id = entry.get("id", "")
            if not entry_id:
                adjusted.append((idx, score))
                continue

            meta = self._store.get_metadata_entry(entry_id)
            if not meta:
                adjusted.append((idx, score))
                continue

            # 被访问过的记忆不衰减（说明仍然有用）
            access_count = meta.get("access_count", 0)
            if access_count > 0:
                adjusted.append((idx, score))
                continue

            created_at_str = meta.get("created_at", "")
            try:
                created_at = datetime.fromisoformat(created_at_str)
                age_days = (now - created_at).days

                if age_days >= 60:
                    score = max(1, score - 2)
                    logger.info(
                        f"时间衰减：记忆 {entry_id} 年龄 {age_days}天，分数 -2 → {score}"
                    )
                elif age_days >= 30:
                    score = max(1, score - 1)
                    logger.info(
                        f"时间衰减：记忆 {entry_id} 年龄 {age_days}天，分数 -1 → {score}"
                    )
            except (ValueError, TypeError):
                pass

            adjusted.append((idx, score))

        # 重新按分数降序排序
        adjusted.sort(key=lambda x: x[1], reverse=True)
        return adjusted

    def _call_llm_with_timeout(self, prompt: str, timeout: int = 30) -> Optional[str]:
        """调用 LLM，带超时保护。

        精排阶段使用 re-ranking.provider 指定的模型（rerank_simple_chat），
        若精排模型不可用则回退到注入的 LLM 函数。
        超时由 httpx 在 HTTP 层处理，确保超时后连接被正确关闭，
        避免线程泄漏和僵尸连接。

        精排输出仅需分析摘要 + 逐条评估 + SCORED 行，
        2048 tokens 足够（10 条候选 × ~150 token/条 + 分析 ~300 token）。
        过大的 max_tokens 会导致 LLM 生成冗长分析，严重拖慢召回速度。

        Args:
            prompt: 完整 Prompt
            timeout: 超时秒数

        Returns:
            LLM 响应文本，None 表示超时
        """
        import time

        try:
            start = time.time()
            # 使用 LLM 精排模型（memory.llm_re_ranking.provider）
            from src.query import chat_llm
            rerank_fn = getattr(chat_llm, "rerank_simple_chat", None)
            if rerank_fn is None:
                raise AttributeError("chat_llm.rerank_simple_chat 不存在")
            response = rerank_fn(
                prompt,
                temperature=0.1,
                max_tokens=20480,
                timeout=float(timeout),
            )
            elapsed = time.time() - start

            if not response:
                if elapsed >= timeout * 0.9:
                    logger.warning(f"LLM 预检索疑似超时（耗时 {elapsed:.1f}s，阈值 {timeout}s）")
                    print(f"⚠️ [记忆召回] LLM 精排疑似超时（{elapsed:.1f}s），跳过精排")
                else:
                    logger.warning(f"LLM 预检索返回空响应（耗时 {elapsed:.1f}s）")
                    print(f"⚠️ [记忆召回] LLM 精排返回空响应，跳过精排")
                return None

            logger.info(f"LLM 预检索成功（耗时 {elapsed:.1f}s）")
            return response
        except Exception as e:
            logger.error(f"LLM 预检索调用异常: {e}")
            print(f"⚠️ [记忆召回] LLM 精排调用异常: {e}")
            return None

    def get_layer1_content(self) -> str:
        """读取 Layer 1（MEMORY.md）内容（全量，向后兼容）。

        新的召回主路径是 retrieve_for_query()，此方法保留供回退和调试使用。

        Returns:
            Layer 1 的 Markdown 内容，空字符串表示无内容
        """
        return self._store.read_layer1()

    def get_layer1_stats(self) -> Dict[str, int]:
        """获取 Layer 1 的行数和 token 估算。"""
        return self._store.get_layer1_stats()

    def search(self, query: str, top_k: int = None, **filters) -> List[Dict]:
        """搜索 Layer 1。

        供 CLI /mem search 命令调用。仅搜索 Layer 1（MEMORY.md）。

        利用倒排索引进行快速定位，再回退到内容匹配。

        Args:
            query: 搜索关键词
            top_k: 返回的最大条目数
            **filters: 过滤条件（如 tags=["数据库"]）

        Returns:
            匹配的记忆条目列表
        """
        if top_k is None:
            top_k = self._default_top_k
        top_k = min(top_k, self._max_top_k)

        # 提取搜索关键词
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        # 1. 通过倒排索引查找匹配的条目 ID
        matched_ids = self._store.search_inverted_index(keywords)

        # 2. 从 Layer 1 读取并匹配
        layer1_content = self._store.read_layer1()
        if not layer1_content:
            return []

        all_entries = self._parse_layer1_entries(layer1_content)
        id_set = set(matched_ids)

        results = []
        for entry in all_entries:
            entry_id = entry.get("id", "")
            content = entry.get("content", "")
            tags = entry.get("tags", [])

            # 倒排索引命中或内容匹配
            if entry_id in id_set or any(kw.lower() in content.lower() for kw in keywords):
                if self._matches_filters(entry, filters):
                    results.append(entry)

            if len(results) >= top_k:
                break

        return results[:top_k]

    def search_layer0_by_keywords(self, keywords: List[str]) -> List[Dict]:
        """通过关键词搜索（已废弃，保留仅为接口兼容）。

        Args:
            keywords: 关键词列表

        Returns:
            空列表
        """
        return []

    # ===== 辅助方法 =====

    def _extract_keywords(self, query: str) -> List[str]:
        """从查询文本中提取关键词。

        简化实现：按空格分词，过滤停用词和过短词。
        """
        if not query:
            return []

        # 中文按字符分割，英文按空格分词
        # 简化：直接按空格和标点分词
        raw_words = re.split(r"[\s,，。、；;：:！!？?()（）\[\]]+", query)
        keywords = [w.strip() for w in raw_words if w.strip() and len(w.strip()) >= 2]

        # 停用词过滤
        stop_words = {"的", "了", "是", "在", "我", "你", "他", "她", "它", "这", "那"}
        keywords = [w for w in keywords if w.lower() not in stop_words]

        return keywords

    def _matches_filters(self, entry: Dict, filters: Dict) -> bool:
        """检查条目是否匹配过滤条件。"""
        for key, value in filters.items():
            if key == "tags":
                entry_tags = set(entry.get("tags", []))
                if isinstance(value, list):
                    if not set(value).intersection(entry_tags):
                        return False
                elif value not in entry_tags:
                    return False
            elif key == "status":
                if entry.get("status") != value:
                    return False
            elif key == "session_id":
                if entry.get("session_id") != value:
                    return False
            elif key == "query_id":
                if entry.get("query_id") != value:
                    return False

        return True


