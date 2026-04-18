from __future__ import annotations

import os
from typing import Any

import httpx


class ChainlitBackendClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url or os.getenv(
            "CHAINLIT_BACKEND_BASE_URL", "http://127.0.0.1:8000"
        )
        self._client = client

    async def create_session(self, declared_family: str) -> dict[str, Any]:
        async with self._use_client() as client:
            response = await client.post(
                "/v1/sessions",
                json={"declared_family": declared_family},
            )
            response.raise_for_status()
            return response.json()

    async def get_required_package(self, session_id: str) -> dict[str, Any]:
        async with self._use_client() as client:
            response = await client.get(f"/v1/sessions/{session_id}/required-package")
            response.raise_for_status()
            return response.json()

    async def post_message(self, session_id: str, content: str) -> dict[str, Any]:
        async with self._use_client() as client:
            response = await client.post(
                f"/v1/sessions/{session_id}/messages",
                json={"role": "user", "content": content},
            )
            response.raise_for_status()
            return response.json()

    async def upload_file(
        self,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        async with self._use_client() as client:
            response = await client.post(
                f"/v1/sessions/{session_id}/files",
                files={"file": (filename, raw_bytes, content_type)},
            )
            response.raise_for_status()
            return response.json()

    async def get_user_report(self, session_id: str) -> dict[str, Any]:
        async with self._use_client() as client:
            response = await client.get(f"/v1/sessions/{session_id}/reports/user")
            response.raise_for_status()
            return response.json()

    async def get_internal_report(self, session_id: str) -> dict[str, Any]:
        async with self._use_client() as client:
            response = await client.get(f"/v1/sessions/{session_id}/reports/internal")
            response.raise_for_status()
            return response.json()

    def _use_client(self):
        if self._client is not None:
            return _BorrowedAsyncClient(self._client)
        return httpx.AsyncClient(base_url=self.base_url, timeout=30.0)


class _BorrowedAsyncClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None
