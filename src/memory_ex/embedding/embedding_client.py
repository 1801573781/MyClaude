# -*- coding: utf-8 -*-
"""
封装 GLM embedding API 调用，支持批量向量化。
"""
import requests

from .embedding_config import EmbeddingConfig


class EmbeddingClient:
    """GLM embedding API 客户端，支持批量向量化。"""

    def __init__(self, config: EmbeddingConfig):
        self.config = config

    # GLM Embedding API 单次请求 input 数组最大条数
    MAX_API_BATCH = 64

    def get_embeddings(self, texts: list) -> list:
        """
        批量调用 GLM embedding API，返回向量列表（与输入顺序一致）。

        当输入超过 MAX_API_BATCH 条时，自动拆分为多个子请求，
        合并结果后按原始顺序返回。

        Args:
            texts: 文本列表

        Returns:
            向量列表，每个向量是 float 列表，与 texts 顺序一致
        """
        all_embeddings = []
        for i in range(0, len(texts), self.MAX_API_BATCH):
            chunk = texts[i:i + self.MAX_API_BATCH]
            chunk_embeddings = self._call_api(chunk)
            all_embeddings.extend(chunk_embeddings)
        return all_embeddings

    def _call_api(self, texts: list) -> list:
        """单次 API 调用（内部方法，texts 不得超过 MAX_API_BATCH 条）。"""
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.model_name,
            "input": texts,
            "dimensions": self.config.dim,
        }

        resp = requests.post(
            self.config.base_url,
            headers=headers,
            json=payload,
            timeout=120,
        )
        if resp.status_code != 200:
            try:
                error_detail = resp.json()
            except Exception:
                error_detail = resp.text
            raise RuntimeError(
                f"Embedding API 调用失败 (HTTP {resp.status_code}): {error_detail}"
            )

        data = resp.json()
        # 按 index 排序保证顺序
        embeddings = [
            item["embedding"]
            for item in sorted(data["data"], key=lambda x: x["index"])
        ]
        return embeddings

    def get_single_embedding(self, text: str) -> list:
        """
        获取单条文本的向量。

        Args:
            text: 文本字符串

        Returns:
            向量列表
        """
        return self.get_embeddings([text])[0]
