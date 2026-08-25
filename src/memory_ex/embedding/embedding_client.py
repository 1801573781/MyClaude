# -*- coding: utf-8 -*-
"""
封装 GLM embedding API 调用，支持批量向量化。
"""
import logging
import requests

from .embedding_config import EmbeddingConfig

logger = logging.getLogger(__name__)


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

    # 单条文本向量化的最大输入字符数（约 8000 tokens）
    # 超长文本（如完整 session log）会导致 Embedding API 报错，需截断
    MAX_INPUT_CHARS = 16000

    def get_single_embedding(self, text: str) -> list:
        """
        获取单条文本的向量。

        对超长文本进行截断，避免 Embedding API 报错。
        截断长度为 MAX_INPUT_CHARS 字符，在保证不触发 API 限制的同时
        尽可能保留更多语义信息。

        Args:
            text: 文本字符串

        Returns:
            向量列表
        """
        if len(text) > self.MAX_INPUT_CHARS:
            logger.warning(
                f"Embedding 输入文本过长（{len(text)} 字符），"
                f"截断为前 {self.MAX_INPUT_CHARS} 字符"
            )
            text = text[:self.MAX_INPUT_CHARS]
        return self.get_embeddings([text])[0]
