from pathlib import Path
from types import SimpleNamespace

import pytest

import chainlit as cl

from chainlit_app import _build_session_actions
from chainlit_app import _format_internal_report
from chainlit_app import _format_user_report
from chainlit_app import _prompt_for_required_files
from chainlit_app import show_internal_report
from chainlit_app import show_user_report
from chainlit_app import _upload_message_elements


def test_build_session_actions_includes_upload_when_documents_pending() -> None:
    actions = _build_session_actions(["passport_bio", "ds160"])

    assert [action.name for action in actions] == [
        "upload_requested_documents",
        "show_user_report",
        "show_internal_report",
    ]


def test_build_session_actions_hides_upload_when_nothing_pending() -> None:
    actions = _build_session_actions([])

    assert [action.name for action in actions] == [
        "upload_requested_documents",
        "show_user_report",
        "show_internal_report",
    ]


def test_format_user_report_returns_readable_summary() -> None:
    formatted = _format_user_report(
        {
            "outcome_label": "补件审核中",
            "summary": "当前处于材料门控阶段。材料已提交，仍在解析中，暂不能进入正式 interview。",
            "missing_evidence": ["funding_proof"],
            "recommended_improvements": ["等待解析完成后再继续。"],
        }
    )

    assert formatted == (
        "当前结论：补件审核中\n"
        "摘要：当前处于材料门控阶段。材料已提交，仍在解析中，暂不能进入正式 interview。\n"
        "缺失材料：funding_proof\n"
        "建议：\n"
        "- 等待解析完成后再继续。"
    )


def test_format_internal_report_marks_debug_content() -> None:
    formatted = _format_internal_report({"session_id": "sess-1"})

    assert formatted == "内部报告（调试信息）\n{'session_id': 'sess-1'}"


@pytest.mark.asyncio
async def test_prompt_for_required_files_limits_accept_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DummyAskFileMessage:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def send(self):
            return None

    class DummyMessage:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def send(self):
            return None

    monkeypatch.setattr(cl, "AskFileMessage", DummyAskFileMessage)
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(cl.user_session, "get", lambda key, default=None: "sess-1")
    monkeypatch.setattr(cl.user_session, "set", lambda key, value: None)

    await _prompt_for_required_files(["passport_bio"])

    assert captured["accept"] == [
        "application/pdf",
        "image/png",
        "image/jpeg",
    ]
    assert captured["max_size_mb"] == 64


@pytest.mark.asyncio
async def test_upload_message_elements_pushes_browser_uploads_to_backend(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_path = tmp_path / "passport.png"
    upload_path.write_bytes(b"png-bytes")
    captured: list[tuple[str, str, bytes, str]] = []

    class DummyClient:
        async def upload_file(
            self,
            session_id: str,
            filename: str,
            raw_bytes: bytes,
            content_type: str,
            document_type: str | None = None,
        ) -> dict[str, str]:
            captured.append((session_id, filename, raw_bytes, content_type, document_type))
            return {"document_status": "uploaded"}

    class DummyAskActionMessage:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def send(self):
            return {"payload": {"document_type": "passport_bio"}}

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(cl, "AskActionMessage", DummyAskActionMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: {
            "session_id": "sess-1",
            "pending_requested_documents": ["passport_bio", "ds160"],
            "required_initial_package": ["passport_bio", "ds160"],
        }.get(key, default),
    )

    count = await _upload_message_elements(
        SimpleNamespace(
            elements=[
                SimpleNamespace(
                    path=Path(upload_path),
                    name="passport.png",
                    mime="image/png",
                )
            ]
        )
    )

    assert count == 1
    assert captured == [("sess-1", "passport.png", b"png-bytes", "image/png", "passport_bio")]


@pytest.mark.asyncio
async def test_show_user_report_sends_formatted_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []

    class DummyClient:
        async def get_user_report(self, session_id: str) -> dict[str, object]:
            assert session_id == "sess-1"
            return {
                "outcome_label": "补件审核中",
                "summary": "当前处于材料门控阶段。材料已提交，仍在解析中，暂不能进入正式 interview。",
                "missing_evidence": ["funding_proof"],
                "recommended_improvements": ["等待解析完成后再继续。"],
            }

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(cl.user_session, "get", lambda key, default=None: "sess-1")

    await show_user_report(None)

    assert sent_messages == [
        "当前结论：补件审核中\n"
        "摘要：当前处于材料门控阶段。材料已提交，仍在解析中，暂不能进入正式 interview。\n"
        "缺失材料：funding_proof\n"
        "建议：\n"
        "- 等待解析完成后再继续。"
    ]


@pytest.mark.asyncio
async def test_show_internal_report_marks_debug_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []

    class DummyClient:
        async def get_internal_report(self, session_id: str) -> dict[str, object]:
            assert session_id == "sess-1"
            return {"session_id": "sess-1", "runtime_trace": []}

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(cl.user_session, "get", lambda key, default=None: "sess-1")

    await show_internal_report(None)

    assert sent_messages == [
        "内部报告（调试信息）\n{'session_id': 'sess-1', 'runtime_trace': []}"
    ]
