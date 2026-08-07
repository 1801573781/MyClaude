# -*- coding: utf-8 -*-
"""
业务编排：加载索引 → 向量化 query → FAISS 检索 → 格式化输出。
"""
import logging
import os
from typing import Any, Dict, List, Optional

from .embedding_config import load_embedding_config
from .embedding_client import EmbeddingClient
from .faiss_store import FaissStore
from .memory_embedding import parse_memory_entries

logger = logging.getLogger(__name__)

# 文件路径常量
MEMORY_MD_PATH = "D:/AI/MyClaude/memory_storage/memory_ex/memory/MEMORY.md"
INDEX_PATH = "D:/AI/MyClaude/memory_storage/memory_ex/memory/memory.index"

# 模块级缓存，避免重复加载
_loaded_store: FaissStore = None


def _get_store() -> FaissStore:
    """
    获取已加载的 FaissStore 实例。

    如果 memory.index 尚未加载到内存，则从磁盘加载，
    同时从 MEMORY.md 重建文本映射（chunks）。

    Returns:
        FaissStore 实例
    """
    global _loaded_store

    if _loaded_store is not None:
        return _loaded_store

    if not os.path.exists(INDEX_PATH):
        raise FileNotFoundError(
            f"索引文件不存在: {INDEX_PATH}\n请先执行 /mem emb 生成向量索引。"
        )

    config = load_embedding_config()
    store = FaissStore(dim=config.dim)
    store.load(INDEX_PATH)

    # FAISS 的 .index 文件只存储向量，不存储文本和 ID。
    # 从 MEMORY.md 重建文本映射（chunks），确保向量 ID 与文本顺序一致。
    # IDs 已在 load() 中从 sidecar JSON 加载；若旧版无 sidecar，则从 MEMORY.md 重建。
    texts, ids = parse_memory_entries(MEMORY_MD_PATH)
    store.chunks = texts
    if not store.ids:
        store.ids = ids

    _loaded_store = store
    return _loaded_store


def retrieve_by_vector(
    query: str,
    top_k: int = 10,
    score_threshold: float = 0.0,
) -> Optional[List[Dict[str, Any]]]:
    """
    可复用的向量粗排检索方法。

    执行流程：
    1. 检查索引文件是否存在，不存在则返回 None（由调用方决定降级策略）
    2. 加载索引（带缓存）
    3. 将 query 向量化
    4. 用 FAISS 检索 top_k 个最相似结果
    5. 过滤低于 score_threshold 的结果

    Args:
        query: 检索文本
        top_k: 返回的候选条数上限
        score_threshold: 向量相似度最低阈值，低于此分的结果被过滤

    Returns:
        候选记忆列表，每条含 text 和 score 字段；
        None 表示索引文件不存在（调用方应降级为全量 LLM 召回）；
        空列表表示索引存在但无匹配结果。
    """
    # 1. 检查索引文件
    if not os.path.exists(INDEX_PATH):
        logger.warning(f"索引文件不存在: {INDEX_PATH}，向量粗排降级")
        return None

    # 2. 加载索引（带缓存）
    try:
        store = _get_store()
    except FileNotFoundError:
        return None

    # 3. 向量化 query
    config = load_embedding_config()
    client = EmbeddingClient(config)
    query_vector = client.get_single_embedding(query)

    # 4. FAISS 检索
    results = store.search(query_vector, k=top_k)

    # 5. 过滤低于阈值的结果
    if score_threshold > 0:
        results = [r for r in results if r["score"] >= score_threshold]

    logger.info(f"向量粗排完成: {len(results)} 条候选 (top_k={top_k}, threshold={score_threshold})")
    return results


def run_retrieval(query: str, top_k: int = None):
    """
    执行向量召回的完整流程（CLI /mem emb rt 命令调用）：

    1. 调用 retrieve_by_vector 获取向量粗排结果
    2. 在终端打印召回结果

    Args:
        query: 用户输入的检索信息
        top_k: 返回的候选条数上限，None 时从配置文件读取 vector_top_k
    """
    # 从配置文件读取向量粗排参数
    if top_k is None:
        try:
            from src.utility.config_loader import global_cfg
            retrieval_cfg = global_cfg.memory_ex.retrieval
            top_k = getattr(retrieval_cfg, 'vector_top_k', 10)
            score_threshold = getattr(retrieval_cfg, 'vector_score_threshold', 0.0)
        except Exception:
            top_k = 10
            score_threshold = 0.0
    else:
        score_threshold = 0.0

    # 1. 向量粗排
    results = retrieve_by_vector(query, top_k=top_k, score_threshold=score_threshold)

    if results is None:
        return "索引文件不存在，请先执行 /mem emb 生成向量索引。"

    actual_count = len(results)

    # 2. 构建召回结果文本（由调用方负责终端打印）
    import re
    separator = '=' * 50

    lines = [
        separator,
        f"  向量召回测试: {actual_count} 条 (top_k={top_k}, 查询: {query[:50]})",
        separator,
    ]

    for i, r in enumerate(results):
        if i > 0:
            lines.append("")
        score = r['score']
        rid = r.get("id", "")
        # Strip trailing (id=xxx) from text to avoid duplicate display
        text = re.sub(r'\s*\(id=[^)]+\)\s*$', '', r['text'])

        lines.append(f"  [{i + 1}] 分数: {score:.4f}")
        if rid:
            lines.append(f"  (id={rid})")
        lines.append(f"  {text}")

    if actual_count < top_k:
        lines.append(f"注意：期望召回 {top_k} 条，实际召回 {actual_count} 条（索引中记忆总数不足或相似度较低）。")

    lines.append(separator)

    return "\n".join(lines)


def reset_store():
    """重置模块级缓存，下次调用时重新加载索引。"""
    global _loaded_store
    _loaded_store = None
