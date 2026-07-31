import hashlib
import math
import re
from abc import ABC, abstractmethod

import httpx

from ..config import Settings, get_settings


class EmbeddingProvider(ABC):
    dimensions: int

    @abstractmethod
    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]


class HashingEmbedder(EmbeddingProvider):
    """Deterministic zero-download fallback for demos and tests."""

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        normalized = text.lower()
        words = re.findall(r"[\w\u4e00-\u9fff]+", normalized)
        features = words + [normalized[i : i + 3] for i in range(max(0, len(normalized) - 2))]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]


class OpenAIEmbedder(EmbeddingProvider):
    def __init__(self, settings: Settings) -> None:
        self.dimensions = settings.embedding_dimensions
        self.model = settings.embedding_model
        self.base_url = (settings.embedding_base_url or settings.openai_base_url).rstrip("/")
        self.api_key = settings.embedding_api_key or settings.openai_api_key

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise RuntimeError("使用 OpenAI Embedding 时必须配置 EMBEDDING_API_KEY 或 OPENAI_API_KEY")
        payload = {"model": self.model, "input": texts, "dimensions": self.dimensions}
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in data]


class OllamaEmbedder(EmbeddingProvider):
    def __init__(self, settings: Settings) -> None:
        self.dimensions = settings.embedding_dimensions
        self.model = settings.embedding_model
        self.base_url = (settings.embedding_base_url or "http://localhost:11434").rstrip("/")

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": texts})
            response.raise_for_status()
        vectors = response.json()["embeddings"]
        if vectors and len(vectors[0]) != self.dimensions:
            raise RuntimeError(
                f"Ollama 返回 {len(vectors[0])} 维向量，但数据库配置为 {self.dimensions} 维"
            )
        return vectors


def get_embedder(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    provider = settings.embedding_provider.lower()
    if provider == "openai":
        return OpenAIEmbedder(settings)
    if provider == "ollama":
        return OllamaEmbedder(settings)
    if provider != "hashing":
        raise ValueError(f"未知 Embedding 提供商：{provider}")
    return HashingEmbedder(settings.embedding_dimensions)
