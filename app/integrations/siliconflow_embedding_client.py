from __future__ import annotations

from typing import Any

import httpx

from app.core.settings import settings


class SiliconFlowEmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.siliconflow_base_url).rstrip("/")
        self.api_key = api_key or settings.siliconflow_api_key
        self.model = model or settings.siliconflow_embedding_model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds or settings.openai_timeout_seconds

    def embed(self, texts: list[str]) -> list[list[float]]:
        normalized_texts = [text for text in texts if text.strip()]
        if not normalized_texts:
            return []
        if not self.api_key:
            raise ValueError("SILICONFLOW_API_KEY is required for embeddings")

        payload: dict[str, Any] = {
            "model": self.model,
            "input": normalized_texts,
            "encoding_format": "float",
        }
        if self.dimensions is not None and self._supports_dimensions():
            payload["dimensions"] = self.dimensions

        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        data = sorted(body.get("data", []), key=lambda item: item.get("index", 0))
        return [list(item["embedding"]) for item in data]

    def _supports_dimensions(self) -> bool:
        return self.model.startswith("Qwen/Qwen3-Embedding-")
