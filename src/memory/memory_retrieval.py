"""
MemoryRetrieval - 基于 TF-IDF + 余弦相似度的轻量检索

不依赖 sklearn，纯 Python 实现。
支持工作记忆的 Jaccard 相似度匹配。
"""

import logging
import math
import re
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# 英文停用词
_EN_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "in", "on",
    "to", "for", "and", "or", "it", "this", "that", "with", "as", "at",
    "by", "from"
}

# 中文停用词
_ZH_STOP_WORDS = {
    "的", "了", "和", "是", "在", "不", "我", "有", "也", "就",
    "都", "要", "会", "可以", "这个"
}


class MemoryRetrieval:
    """
    TF-IDF + 余弦相似度检索器。

    特性:
        - 纯 Python 实现 TF-IDF 向量化
        - 支持中英文分词与停用词过滤
        - 记忆数量 > 500 时限制词表大小
        - 最终得分结合 importance 与 access_count 加成
        - 工作记忆使用 Jaccard 相似度单独处理
    """

    def __init__(self, similarity_threshold: float = 0.15):
        """
        参数:
            similarity_threshold: 检索相似度最低阈值（默认 0.15）。
        """
        self._similarity_threshold = similarity_threshold
        self._vocabulary: Dict[str, int] = {}  # word → index
        self._idf: List[float] = []  # IDF 值列表
        self._vectors: Dict[str, List[float]] = {}  # memory_id → TF-IDF vector

    # ========== 公共接口 ==========

    def retrieve(
        self,
        query: str,
        memories: List[Dict],
        working_memories: List[Dict],
        limit: int = 5,
        on_access_hit: callable = None,
    ) -> List[Dict]:
        """
        根据查询文本检索最相关的记忆条目。

        参数:
            query:             查询文本。
            memories:          长期 + 短期记忆列表（不含 work）。
            working_memories:  工作记忆列表。
            limit:             最大返回条数。
            on_access_hit:     命中回调，签名: on_access_hit(memory_id: str)。

        返回:
            按最终得分降序排列的记忆字典列表（每个字典额外包含 "score" 键）。
            若向量化失败或记忆为空，返回 []。
        """
        if not memories and not working_memories:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            logger.warning("查询分词后为空（可能全是停用词），返回空列表")
            return []

        # 1. 处理工作记忆（Jaccard 相似度）
        results = self._retrieve_working(query, working_memories)

        # 2. 处理长期/短期记忆（TF-IDF + 余弦相似度）
        if memories:
            self.rebuild_index(memories)
            long_results = self._retrieve_tfidf(query, query_tokens, memories, on_access_hit)
            results.extend(long_results)

        # 3. 按得分降序排列并截取
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:limit]

    def rebuild_index(self, memories: List[Dict]) -> None:
        """
        重建 TF-IDF 索引（通常在 forget 后调用）。

        参数:
            memories: 长期 + 短期记忆列表。
        """
        if not memories:
            self._vocabulary = {}
            self._idf = []
            self._vectors = {}
            return

        # 对所有记忆分词
        all_tokens = [self._tokenize(mem["content"]) for mem in memories]

        # 构建词表
        self._build_vocabulary(all_tokens)

        # 计算 IDF
        N = len(memories)
        self._idf = self._compute_idf(all_tokens, N)

        # 计算每条记忆的 TF-IDF 向量
        self._vectors = {}
        for mem, tokens in zip(memories, all_tokens):
            self._vectors[mem["id"]] = self._compute_tfidf_vector(tokens)

        logger.debug(f"已重建 TF-IDF 索引：词表大小 {len(self._vocabulary)}，记忆数 {N}")

    # ========== 分词 ==========

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """
        小写化 → 按非字母数字字符分割（保留中文字符）→ 去停用词。

        中文按单字切分，英文按空格/标点分割。
        """
        text = text.lower()

        # 分离中文和非中文
        # 按非字母数字非中文字符分割
        parts = re.split(r'[^a-z0-9\u4e00-\u9fff]+', text)

        tokens = []
        for part in parts:
            if not part:
                continue
            # 检查是否包含中文
            if re.search(r'[\u4e00-\u9fff]', part):
                # 中文部分：按单字切分
                for char in part:
                    if '\u4e00' <= char <= '\u9fff':
                        if char not in _ZH_STOP_WORDS:
                            tokens.append(char)
                    else:
                        # 英文/数字混合在中文块中
                        if char not in _EN_STOP_WORDS:
                            tokens.append(char)
            else:
                # 纯英文/数字
                if part not in _EN_STOP_WORDS:
                    tokens.append(part)

        return tokens

    # ========== 词汇表构建 ==========

    def _build_vocabulary(self, all_tokens: List[List[str]]) -> None:
        """
        构建词表（word → index 映射）。

        优化：记忆数量 > 500 时，限制词表大小为前 2000 个最高文档频率的词。
        """
        # 统计文档频率
        df = {}
        for tokens in all_tokens:
            seen = set()
            for token in tokens:
                if token not in seen:
                    df[token] = df.get(token, 0) + 1
                    seen.add(token)

        N = len(all_tokens)
        max_vocab = 2000 if N > 500 else None

        if max_vocab and len(df) > max_vocab:
            # 按文档频率降序截断
            sorted_words = sorted(df.items(), key=lambda x: x[1], reverse=True)
            selected_words = sorted_words[:max_vocab]
            self._vocabulary = {word: idx for idx, (word, _) in enumerate(selected_words)}
            logger.info(f"词表已截断至 {max_vocab} 个词（共 {len(df)} 个唯一词）")
        else:
            sorted_words = sorted(df.keys())
            self._vocabulary = {word: idx for idx, word in enumerate(sorted_words)}

    # ========== IDF 计算 ==========

    def _compute_idf(self, all_tokens: List[List[str]], N: int) -> List[float]:
        """
        计算 IDF 向量。

        idf(w) = log((N + 1) / (df(w) + 1)) + 1
        """
        vocab_size = len(self._vocabulary)
        idf = [0.0] * vocab_size

        # 统计每个词的文档频率
        df = [0] * vocab_size
        for tokens in all_tokens:
            seen = set()
            for token in tokens:
                idx = self._vocabulary.get(token)
                if idx is not None and token not in seen:
                    df[idx] += 1
                    seen.add(token)

        for idx in range(vocab_size):
            idf[idx] = math.log((N + 1) / (df[idx] + 1)) + 1

        return idf

    # ========== TF-IDF 向量 ==========

    def _compute_tfidf_vector(self, tokens: List[str]) -> List[float]:
        """
        计算单条记忆的 TF-IDF 向量。
        """
        vocab_size = len(self._vocabulary)
        vector = [0.0] * vocab_size

        if not tokens:
            return vector

        # TF
        tf = {}
        total = len(tokens)
        for token in tokens:
            if token in self._vocabulary:
                tf[token] = tf.get(token, 0) + 1

        # TF-IDF
        for token, count in tf.items():
            idx = self._vocabulary[token]
            if idx < len(self._idf):
                tf_val = count / total
                vector[idx] = tf_val * self._idf[idx]

        return vector

    # ========== 检索 ==========

    def _retrieve_tfidf(
        self,
        query: str,
        query_tokens: List[str],
        memories: List[Dict],
        on_access_hit: callable,
    ) -> List[Dict]:
        """
        TF-IDF + 余弦相似度检索长期/短期记忆。
        """
        # 查询向量
        query_vec = self._compute_tfidf_vector(query_tokens)

        results = []
        for mem in memories:
            mem_id = mem["id"]
            mem_vec = self._vectors.get(mem_id, [])
            if not mem_vec:
                continue

            cosine = self._cosine_similarity(query_vec, mem_vec)

            if cosine < self._similarity_threshold:
                continue

            # 最终得分 = cosine * importance 加成 * access_count 加成
            importance = mem.get("importance", 0.5)
            access_count = mem.get("access_count", 0)
            final_score = cosine * (0.7 + 0.3 * importance) * (1 + math.log(access_count + 1) / 10)

            results.append({
                **mem,
                "score": round(final_score, 4),
            })

            # 命中回调
            if on_access_hit:
                on_access_hit(mem_id)

        return results

    def _retrieve_working(
        self,
        query: str,
        working_memories: List[Dict],
    ) -> List[Dict]:
        """
        工作记忆使用 Jaccard 相似度检索。
        """
        query_tokens = set(self._tokenize(query))
        results = []

        for mem in working_memories:
            content_tokens = set(self._tokenize(mem["content"]))
            if not query_tokens and not content_tokens:
                continue

            intersection = len(query_tokens & content_tokens)
            union = len(query_tokens | content_tokens)

            if union == 0:
                continue

            jaccard = intersection / union

            if jaccard > self._similarity_threshold:
                results.append({
                    **mem,
                    "score": round(jaccard, 4),
                })

        return results

    # ========== 相似度计算 ==========

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        """
        余弦相似度：cos_sim(a, b) = (a · b) / (||a|| * ||b|| + 1e-8)
        """
        if len(a) != len(b):
            return 0.0

        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0

        for x, y in zip(a, b):
            dot += x * y
            norm_a += x * x
            norm_b += y * y

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return dot / (math.sqrt(norm_a) * math.sqrt(norm_b) + 1e-8)

    @property
    def similarity_threshold(self) -> float:
        return self._similarity_threshold