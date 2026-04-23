import httpx
from fastapi import FastAPI
import pytest

from app.ui.chainlit_client import ChainlitBackendClient


@pytest.mark.asyncio
async def test_chainlit_client_posts_to_session_message_endpoint() -> None:
    captured: dict[str, object] = {}
    expected_response = {
        "assistant_message": "ok",
        "governor_decision": "need_more_evidence",
        "requested_documents": ["funding_proof"],
        "gate_progress": {"overall_status": "pending_documents"},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(200, json=expected_response)

    client = ChainlitBackendClient(
        base_url="http://testserver",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://testserver",
        ),
    )

    response = await client.post_message("sess-1", "hello")

    assert response == expected_response
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/sessions/sess-1/messages"
    assert '"content":"hello"' in captured["body"]


@pytest.mark.asyncio
async def test_chainlit_client_uploads_file_to_files_endpoint() -> None:
    captured: dict[str, object] = {}
    expected_response = {
        "main_flow_feedback": {
            "status": "helpful",
            "message": "这份材料对当前关键证明有帮助。",
        },
        "feedback_message": "旧版上传回执",
        "requested_documents": [],
        "gate_progress": {"overall_status": "waiting_for_parse"},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["content_type"] = request.headers["content-type"]
        return httpx.Response(202, json=expected_response)

    client = ChainlitBackendClient(
        base_url="http://testserver",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://testserver",
        ),
    )

    response = await client.upload_file(
        "sess-1",
        "funding_proof.pdf",
        b"%PDF-1.7",
        "application/pdf",
        context_text="这是资金证明",
    )

    assert response == expected_response
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/sessions/sess-1/files"
    assert "multipart/form-data" in str(captured["content_type"])


@pytest.mark.asyncio
async def test_chainlit_client_can_send_document_type_hint_context_to_backend() -> None:
    captured: dict[str, bytes] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(202, json={})

    client = ChainlitBackendClient(
        base_url="http://testserver",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://testserver",
        ),
    )

    await client.upload_file(
        "sess-1",
        "passport.png",
        b"png-bytes",
        "image/png",
        context_text="这是我的护照首页",
    )

    assert b'name="context_text"' in captured["body"]
    assert "这是我的护照首页".encode("utf-8") in captured["body"]


@pytest.mark.asyncio
async def test_chainlit_client_rejects_unsupported_upload_type_before_request() -> None:
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    client = ChainlitBackendClient(
        base_url="http://testserver",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://testserver",
        ),
    )

    with pytest.raises(
        ValueError,
        match="Only PDF and PNG/JPG/JPEG images are supported",
    ):
        await client.upload_file(
            "sess-1",
            "funding_proof.txt",
            b"bank statement",
            "text/plain",
        )

    assert called is False


@pytest.mark.asyncio
async def test_chainlit_client_can_use_local_asgi_app_without_base_url(
) -> None:
    app = FastAPI()

    @app.post("/v1/sessions")
    async def create_session() -> dict[str, str]:
        return {"session_id": "sess-local"}

    client = ChainlitBackendClient(app=app)

    response = await client.create_session("f1")

    assert response == {"session_id": "sess-local"}
