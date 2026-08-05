# -*- coding: utf-8 -*-
"""
封装 FAISS 索引的创建、保存、加载、检索操作。
"""
import faiss
import json
import numpy as np
from pathlib import Path


class FaissStore:
    """FAISS 索引管理器，负责向量索引的创建、持久化与检索。"""

    def __init__(self, dim: int):
        """
        Args:
            dim: 向量维度
        """
        self.dim = dim
        self.index = faiss.IndexFlatIP(dim)
        self.chunks = []  # 存放与索引顺序一致的文本片段
        self.ids = []     # 存放与索引顺序一致的记忆 ID

    def add_vectors(self, vectors: list, texts: list, ids: list = None):
        """
        批量添加向量与对应文本、ID 到索引。

        Args:
            vectors: 向量列表
            texts: 文本列表，与 vectors 顺序一致
            ids: 记忆 ID 列表，与 vectors 顺序一致；None 时自动生成占位 ID
        """
        matrix = np.array(vectors, dtype=np.float32)
        faiss.normalize_L2(matrix)  # L2 归一化后内积等价于余弦相似度
        self.index.add(matrix)
        self.chunks.extend(texts)
        if ids:
            self.ids.extend(ids)
        else:
            start = len(self.ids)
            self.ids.extend([str(start + i) for i in range(len(texts))])

    def save(self, index_path: str):
        """
        将 FAISS 索引及 ID 映射保存到磁盘。

        Args:
            index_path: 索引文件路径
        """
        faiss.write_index(self.index, index_path)
        # 保存 ID 映射到 sidecar JSON 文件
        ids_path = index_path + ".ids.json"
        with open(ids_path, "w", encoding="utf-8") as f:
            json.dump(self.ids, f, ensure_ascii=False)

    def load(self, index_path: str):
        """
        从磁盘加载 FAISS 索引及 ID 映射。

        注意：FAISS 的 .index 文件只存储向量，不存储文本和 ID。
        本方法加载向量索引和 ID 映射（sidecar JSON），文本映射需由调用方维护。

        Args:
            index_path: 索引文件路径
        """
        self.index = faiss.read_index(index_path)
        # 从 sidecar JSON 加载 ID 映射
        ids_path = Path(index_path + ".ids.json")
        if ids_path.exists():
            with open(ids_path, "r", encoding="utf-8") as f:
                self.ids = json.load(f)
        else:
            self.ids = []  # 旧版索引无 ID 文件，由调用方从 MEMORY.md 重建

    def search(self, query_vector: list, k: int = 10) -> list:
        """
        检索与 query_vector 最相似的 Top-K 向量。

        Args:
            query_vector: 查询向量
            k: 返回结果数

        Returns:
            结果列表，每项为 {"score": float, "text": str, "idx": int}
        """
        qmatrix = np.array([query_vector], dtype=np.float32)
        faiss.normalize_L2(qmatrix)

        scores, ids = self.index.search(qmatrix, k)
        results = []
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0:  # FAISS 无结果时返回 -1
                continue
            results.append({
                "score": float(score),
                "text": self.chunks[idx] if idx < len(self.chunks) else "",
                "idx": int(idx),
                "id": self.ids[idx] if idx < len(self.ids) else "",
            })
        return results

    @property
    def ntotal(self) -> int:
        """索引中向量总数"""
        return self.index.ntotal
