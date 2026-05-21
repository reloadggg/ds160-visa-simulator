from __future__ import annotations

from typing import Any

import httpx

from app.core.settings import settings


class SiliconFlowRerankClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.siliconflow_base_url).rstrip("/")
        self.api_key = api_key or settings.siliconflow_api_key
        self.model = model or settings.siliconflow_rerank_model
        self.timeout_seconds = timeout_seconds or settings.openai_timeout_seconds

    def rerank(
        self,
        *,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        normalized_documents = [document for document in documents if document.strip()]
        if not normalized_documents:
            return []
        if not self.api_key:
            raise ValueError("SILICONFLOW_API_KEY is required for rerank")

        response = httpx.post(
            f"{self.base_url}/rerank",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "query": query,
                "documents": normalized_documents,
                "top_n": top_n,
                "return_documents": False,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return [
            (int(item["index"]), float(item["relevance_score"]))
            for item in body.get("results", [])
        ]
