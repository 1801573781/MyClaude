# -*- coding: utf-8 -*-
"""
业务编排：读取 MEMORY.md → 调用 client 向量化 → 调用 store 存索引。
"""
import math
import time

from .embedding_config import load_embedding_config
from .embedding_client import EmbeddingClient
from .faiss_store import FaissStore


# 文件路径常量
MEMORY_MD_PATH = "D:/AI/MyClaude/memory_storage/memory_ex/memory/MEMORY.md"
INDEX_PATH = "D:/AI/MyClaude/memory_storage/memory_ex/memory/memory.index"

# 耗时估算基准：每批调用 GLM embedding API 约 2 秒（embedding API 远快于 LLM 提取）
SECONDS_PER_BATCH = 2


def parse_memory_entries(memory_path: str = MEMORY_MD_PATH) -> tuple:
    """
    读取 MEMORY.md，按行解析记忆条目，提取文本和 ID。

    每行一条记忆，以 '- ' 开头的行为一条记忆。
    ID 从行尾的 (id=xxx) 模式中提取；无 ID 的行自动生成占位 ID。

    Args:
        memory_path: MEMORY.md 文件路径

    Returns:
        (texts, ids) 元组：
        - texts: 记忆文本列表（原始行）
        - ids: 记忆 ID 列表，与 texts 顺序一致
    """
    import re

    texts = []
    ids = []
    with open(memory_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            stripped = line.strip()
            if stripped.startswith("- "):
                texts.append(stripped)
                # 从行尾提取 (id=xxx) 模式
                id_match = re.search(r"\(id=([^)]+)\)", stripped)
                if id_match:
                    ids.append(id_match.group(1))
                else:
                    ids.append(f"_auto_{i}")  # 无 ID 时用行号生成占位 ID
    return texts, ids


def format_estimated_time(seconds: int) -> str:
    """
    将秒数格式化为"约X分Y秒"的中文描述。

    Args:
        seconds: 总秒数

    Returns:
        格式化后的时间描述字符串
    """
    if seconds < 60:
        return f"约{seconds}秒"
    minutes = seconds // 60
    remaining = seconds % 60
    if remaining == 0:
        return f"约{minutes}分"
    return f"约{minutes}分{remaining}秒"


def run_embedding():
    """
    执行记忆向量化的完整流程：

    1. 读取 model_key.yaml，获取 embedding 配置
    2. 读取 MEMORY.md，解析记忆条目
    3. 打印任务概述（预扫描模式）
    4. 按 batch_size 分批向量化
    5. 构造 FAISS 索引并写入 memory.index
    6. 打印执行摘要
    """
    # 1. 读取配置
    config = load_embedding_config()

    # 2. 读取 MEMORY.md，解析记忆条目（含 ID）
    texts, ids = parse_memory_entries()

    if not texts:
        print("MEMORY.md 中未找到记忆条目，无需向量化。")
        return

    # 3. 打印任务概述
    batch_size = config.batch_size
    total_batches = math.ceil(len(texts) / batch_size)
    estimated_seconds = total_batches * SECONDS_PER_BATCH
    print("开始执行记忆向量化...")
    print(f"  待向量化记忆: {len(texts)} 条")
    print(f"  预计耗时: {format_estimated_time(estimated_seconds)}")
    print()

    # 4. 分批向量化
    client = EmbeddingClient(config)
    store = FaissStore(dim=config.dim)

    start_time = time.time()
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i + batch_size]
        batch_ids = ids[i:i + batch_size]
        batch_num = i // batch_size + 1
        print(f"  正在向量化第 {batch_num}/{total_batches} 批（{len(batch_texts)} 条）...")

        vectors = client.get_embeddings(batch_texts)
        store.add_vectors(vectors, batch_texts, batch_ids)

    # 5. 保存索引
    store.save(INDEX_PATH)
    elapsed = time.time() - start_time

    # 6. 打印执行摘要
    print()
    print("记忆向量化完成")
    print(f"  总记忆条数: {len(texts)} 条")
    print(f"  批次数: {total_batches}")
    print(f"  向量维度: {config.dim}")
    print(f"  索引条目数: {store.ntotal}")
    print(f"  实际耗时: {format_estimated_time(int(elapsed))}")
    print(f"  索引文件: {INDEX_PATH}")

    return (
        f"记忆向量化完成:\n"
        f"  总记忆条数: {len(texts)} 条\n"
        f"  批次数: {total_batches}\n"
        f"  向量维度: {config.dim}\n"
        f"  索引条目数: {store.ntotal}\n"
        f"  实际耗时: {format_estimated_time(int(elapsed))}\n"
        f"  索引文件: {INDEX_PATH}"
    )
