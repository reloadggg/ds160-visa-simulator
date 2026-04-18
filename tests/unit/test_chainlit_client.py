import httpx
import pytest

from app.ui.chainlit_client import ChainlitBackendClient


@pytest.mark.asyncio
async def test_chainlit_client_posts_to_session_message_endpoint() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "assistant_message": "ok",
                "governor_decision": "need_more_evidence",
                "score_summary": {
                    "category_fit": 0,
                    "document_readiness": 0,
                    "narrative_consistency": 0,
                    "confidence": 0,
                },
                "requested_documents": ["funding_proof"],
            },
        )

    client = ChainlitBackendClient(
        base_url="http://testserver",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://testserver",
        ),
    )

    response = await client.post_message("sess-1", "hello")

    assert response["assistant_message"] == "ok"
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/sessions/sess-1/messages"
    assert '"content":"hello"' in captured["body"]


@pytest.mark.asyncio
async def test_chainlit_client_uploads_file_to_files_endpoint() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["content_type"] = request.headers["content-type"]
        return httpx.Response(
            202,
            json={
                "document_id": "doc-1",
                "document_status": "uploaded",
                "job_id": "job-1",
                "job_status": "queued",
            },
        )

    client = ChainlitBackendClient(
        base_url="http://testserver",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://testserver",
        ),
    )

    response = await client.upload_file(
        "sess-1",
        "funding_proof.txt",
        b"bank statement",
        "text/plain",
    )

    assert response["document_status"] == "uploaded"
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/sessions/sess-1/files"
    assert "multipart/form-data" in str(captured["content_type"])
