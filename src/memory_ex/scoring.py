"""记忆条目重要性评分模型。

对应设计文档第三章 3.5 节。

评分公式：
    score = 0.20 × recency
          + 0.25 × relevance
          + 0.20 × user_explicit
          + 0.10 × cross_module
          + 0.15 × access_frequency
          - 0.40 × code_absorbed

评分范围 0.0 ~ 1.0（code_absorbed 减分后可能略低于 0）。
"""

import math
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


class ImportanceScorer:
    """重要性评分器。

    根据记忆的元数据与内容特征，计算一个 0~1 的分数，用于整理阶段判断
    条目是留在 Layer 1、降级到 Layer 2、还是淘汰。

    所有权重从 memory_ex 配置读取，支持自定义调参。
    """

    def __init__(self, scoring_config: Any):
        """初始化评分器。

        Args:
            scoring_config: memory_ex.yaml 中 scoring 节对应的配置对象
        """
        self._recency_weight = float(getattr(scoring_config, "recency_weight", 0.20))
        self._relevance_weight = float(getattr(scoring_config, "relevance_weight", 0.25))
        self._user_explicit_weight = float(getattr(scoring_config, "user_explicit_weight", 0.20))
        self._cross_module_weight = float(getattr(scoring_config, "cross_module_weight", 0.10))
        self._access_frequency_weight = float(
            getattr(scoring_config, "access_frequency_weight", 0.15)
        )
        self._code_absorbed_penalty = float(getattr(scoring_config, "code_absorbed_penalty", 0.40))
        self._recency_halflife_days = float(getattr(scoring_config, "recency_halflife_days", 7))
        self._access_frequency_max = int(getattr(scoring_config, "access_frequency_max", 10))

    def score(self, entry: Dict, metadata_entry: Optional[Dict] = None) -> float:
        """计算单条记忆的重要性分数。

        Args:
            entry: Layer 0 JSONL 中的条目（含 content/tags 等字段）
            metadata_entry: metadata.json 中对应的元数据条目。
                如果为 None，则从 entry 的 metadata 字段中读取。

        Returns:
            重要性分数，范围约 -0.4 ~ 1.0
        """
        meta = metadata_entry or entry.get("metadata", {})

        recency = self._calc_recency(meta)
        relevance = self._calc_relevance(entry)
        user_explicit = self._calc_user_explicit(entry, meta)
        cross_module = self._calc_cross_module(entry)
        access_frequency = self._calc_access_frequency(meta)
        code_absorbed = self._calc_code_absorbed(entry, meta)

        score = (
            self._recency_weight * recency
            + self._relevance_weight * relevance
            + self._user_explicit_weight * user_explicit
            + self._cross_module_weight * cross_module
            + self._access_frequency_weight * access_frequency
            - self._code_absorbed_penalty * code_absorbed
        )

        # 限制在 [0, 1] 区间
        return max(0.0, min(1.0, score))

    def _calc_recency(self, meta: Dict) -> float:
        """时间近度：半衰期约 7 天。

        7 天内得满分 1.0，之后指数衰减。
        """
        last_accessed_str = meta.get("last_accessed") or meta.get("created_at")
        if not last_accessed_str:
            return 0.0

        try:
            last_accessed = datetime.fromisoformat(last_accessed_str)
        except (ValueError, TypeError):
            return 0.0

        now = datetime.now()
        days_elapsed = (now - last_accessed).total_seconds() / 86400.0
        if days_elapsed <= 0:
            return 1.0

        # 指数衰减：半衰期 = recency_halflife_days
        halflife = self._recency_halflife_days
        if halflife <= 0:
            return 1.0

        # 半衰期公式：score = 0.5^(days / halflife)
        return 0.5 ** (days_elapsed / halflife)

    def _calc_relevance(self, entry: Dict) -> float:
        """与当前项目的关联度。

        简化判定：如果条目有标签（tags 非空），视为与项目有关联，给中等分数。
        如果标签中包含项目核心关键词（路径、配置、架构等），给高分。
        """
        tags = entry.get("tags", [])
        if not tags:
            return 0.2

        # 项目核心关键词（可扩展）
        core_keywords = {"架构", "路径", "配置", "数据库", "API", "工具", "红线", "规范"}
        core_match_count = sum(1 for tag in tags if any(kw in tag for kw in core_keywords))

        if core_match_count >= 2:
            return 1.0
        elif core_match_count == 1:
            return 0.8
        else:
            return 0.5

    def _calc_user_explicit(self, entry: Dict, meta: Dict) -> float:
        """用户是否明确要求记住。

        判定：content 中包含"记住"、"别忘了"、"注意"等关键词，
        或 metadata 中有 user_explicit=True 标记。
        """
        if meta.get("user_explicit"):
            return 1.0

        content = entry.get("content", "")
        explicit_keywords = {"记住", "别忘了", "请注意", "必须记住", "重要"}
        if any(kw in content for kw in explicit_keywords):
            return 1.0

        return 0.0

    def _calc_cross_module(self, entry: Dict) -> float:
        """是否跨模块影响。

        判定：tags 中包含 2 个及以上不同主题，或 content 中提及多个文件路径。
        """
        tags = entry.get("tags", [])
        if len(tags) >= 2:
            return 1.0

        content = entry.get("content", "")
        # 粗略统计 content 中出现的 .py / .yaml / .md 文件引用数
        file_refs = 0
        for ext in (".py", ".yaml", ".yml", ".md", ".json"):
            file_refs += content.count(ext)

        if file_refs >= 2:
            return 1.0
        elif file_refs == 1:
            return 0.5
        return 0.0

    def _calc_access_frequency(self, meta: Dict) -> float:
        """召回频次，归一化到 0~1。

        被召回 10 次及以上得满分 1.0。
        """
        access_count = int(meta.get("access_count", 0))
        max_count = self._access_frequency_max
        if max_count <= 0:
            return 0.0
        return min(1.0, access_count / max_count)

    def _calc_code_absorbed(self, entry: Dict, meta: Dict) -> float:
        """是否已被代码固化（强减分项）。

        判定：metadata 中 code_absorbed 标记为 1/True。
        """
        if meta.get("code_absorbed") in (1, True, "1", "true", "True"):
            return 1.0
        return 0.0

    def get_score_tier(self, score: float) -> str:
        """根据分数返回处置层级。

        Args:
            score: 重要性分数

        Returns:
            "keep" (≥0.6): 必须留在 Layer 1
            "normal" (0.3~0.6): 正常留 Layer 1，空间紧张时可降级
            "demote_candidate" (0.1~0.3): 降级候选
            "evict_candidate" (<0.1): 淘汰候选
        """
        if score >= 0.6:
            return "keep"
        elif score >= 0.3:
            return "normal"
        elif score >= 0.1:
            return "demote_candidate"
        else:
            return "evict_candidate"
