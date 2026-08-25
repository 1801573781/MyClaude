# -*- coding: utf-8 -*-
"""
读取 model_key.yaml，解析 embedding 配置，返回配置对象。
"""
import yaml
from pathlib import Path
from dataclasses import dataclass


@dataclass
class EmbeddingConfig:
    """Embedding 配置对象"""
    api_key: str
    base_url: str
    model_name: str
    dim: int
    batch_size: int


def load_embedding_config(config_path: str = None) -> EmbeddingConfig:
    """
    从 model_key.yaml 读取 embedding 配置。

    读取逻辑：
      1. 从 embedding 节读取 provider、dim、batch_size
      2. 用 provider 值作为 key，读取同文件中对应的 API 连接参数

    Args:
        config_path: model_key.yaml 的绝对路径，默认为 D:/AI/MyClaude/config/model_key.yaml

    Returns:
        EmbeddingConfig 配置对象
    """
    if config_path is None:
        config_path = "D:/AI/MyClaude/config/model_key.yaml"

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    memory_section = raw.get("memory", {}) or {}
    embedding_section = memory_section.get("embedding", {})
    provider = embedding_section.get("provider", "")

    # 用 provider 值作为 key 读取 API 连接参数
    provider_section = raw.get(provider, {})

    config = EmbeddingConfig(
        api_key=provider_section.get("api_key", ""),
        base_url=provider_section.get("base_url", ""),
        model_name=provider_section.get("model_name", ""),
        dim=int(embedding_section.get("dim", 3072)),
        batch_size=int(embedding_section.get("batch_size", 100)),
    )

    return config
