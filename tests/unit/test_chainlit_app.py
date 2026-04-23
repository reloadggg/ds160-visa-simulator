from pathlib import Path
from types import SimpleNamespace

import pytest

import chainlit as cl

from chainlit_app import _build_session_actions
from chainlit_app import _format_internal_report
from chainlit_app import _format_user_report
from chainlit_app import _send_report_actions
from chainlit_app import on_chat_start
from chainlit_app import on_message
from chainlit_app import _prompt_for_required_files
from chainlit_app import show_internal_report
from chainlit_app import show_user_report
from chainlit_app import _upload_message_elements
from chainlit_app import upload_requested_documents


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
            "outcome_label": "需补强关键证据",
            "summary": "当前最关键的证明点是 funding_proof，请优先补强。",
            "interview_status": "waiting_key_proof",
            "risk_level": "medium",
            "current_key_question": "你的留学资金主要由谁承担？",
            "current_key_proof": "funding_proof",
            "allowed_next_actions": ["upload_key_proof", "explain_missing_proof"],
            "missing_evidence": ["funding_proof"],
            "recommended_improvements": ["优先补充 funding_proof，再继续面谈。"],
        }
    )

    assert formatted == (
        "当前结论：需补强关键证据\n"
        "摘要：当前最关键的证明点是 funding_proof，请优先补强。\n"
        "当前状态：等待关键证明（中风险）\n"
        "当前关键问题：你的留学资金主要由谁承担？\n"
        "当前关键证明：funding_proof\n"
        "缺失材料：funding_proof\n"
        "建议动作：上传关键证明、先说明暂时缺少的原因\n"
        "建议：\n"
        "- 优先补充 funding_proof，再继续面谈。"
    )


def test_format_user_report_handles_missing_key_fields_stably() -> None:
    formatted = _format_user_report(
        {
            "outcome_label": "正式问答进行中",
            "summary": "当前已进入正式 interview 阶段，可继续回答后续问题。",
            "interview_status": "continue_interview",
            "risk_level": "none",
            "allowed_next_actions": ["continue_interview", "answer_question"],
            "missing_evidence": [],
            "recommended_improvements": ["继续回答后续问题，并保持叙事一致。"],
        }
    )

    assert formatted == (
        "当前结论：正式问答进行中\n"
        "摘要：当前已进入正式 interview 阶段，可继续回答后续问题。\n"
        "当前状态：继续问答（无明显风险）\n"
        "当前关键问题：暂无\n"
        "当前关键证明：暂无\n"
        "建议动作：继续面谈、继续回答当前问题\n"
        "建议：\n"
        "- 继续回答后续问题，并保持叙事一致。"
    )


def test_format_user_report_uses_chinese_fallback_for_unknown_enums() -> None:
    formatted = _format_user_report(
        {
            "outcome_label": "补件中",
            "summary": "系统需要继续等待用户补件。",
            "interview_status": "internal_unknown_status",
            "risk_level": "internal_unknown_risk",
            "allowed_next_actions": [
                "internal_unknown_action",
                "upload_key_proof",
            ],
        }
    )

    assert "internal_unknown_status" not in formatted
    assert "internal_unknown_risk" not in formatted
    assert "internal_unknown_action" not in formatted
    assert formatted == (
        "当前结论：补件中\n"
        "摘要：系统需要继续等待用户补件。\n"
        "当前状态：状态待确认（风险待确认）\n"
        "当前关键问题：暂无\n"
        "当前关键证明：暂无\n"
        "建议动作：请按当前指引继续操作、上传关键证明"
    )


def test_format_user_report_ignores_score_summary_details() -> None:
    formatted = _format_user_report(
        {
            "outcome_label": "继续问答",
            "summary": "当前仍以主线问答为主。",
            "interview_status": "continue_interview",
            "risk_level": "low",
            "allowed_next_actions": ["continue_interview"],
            "score_summary": {
                "category_fit": 0.25,
                "document_readiness": 0.5,
                "narrative_consistency": 0.75,
                "confidence": 0.6,
            },
        }
    )

    assert "score_summary" not in formatted
    assert "category_fit" not in formatted
    assert "document_readiness" not in formatted
    assert "narrative_consistency" not in formatted
    assert "confidence" not in formatted
    assert formatted == (
        "当前结论：继续问答\n"
        "摘要：当前仍以主线问答为主。\n"
        "当前状态：继续问答（低风险）\n"
        "当前关键问题：暂无\n"
        "当前关键证明：暂无\n"
        "建议动作：继续面谈"
    )


def test_format_internal_report_marks_debug_content() -> None:
    formatted = _format_internal_report({"session_id": "sess-1"})

    assert formatted == '内部报告（调试信息）\n{\n  "session_id": "sess-1"\n}'


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
async def test_prompt_for_required_files_without_upload_does_not_claim_received(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []

    class DummyAskFileMessage:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def send(self):
            return None

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    monkeypatch.setattr(cl, "AskFileMessage", DummyAskFileMessage)
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(cl.user_session, "get", lambda key, default=None: "sess-1")
    monkeypatch.setattr(cl.user_session, "set", lambda key, value: None)

    await _prompt_for_required_files(["passport_bio"])

    assert sent_messages == []


@pytest.mark.asyncio
async def test_upload_message_elements_pushes_browser_uploads_to_backend(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_path = tmp_path / "passport.png"
    upload_path.write_bytes(b"png-bytes")
    captured: list[tuple[str, str, bytes, str, str | None, str | None]] = []

    class DummyClient:
        async def upload_file(
            self,
            session_id: str,
            filename: str,
            raw_bytes: bytes,
            content_type: str,
            document_type: str | None = None,
            context_text: str | None = None,
        ) -> dict[str, str]:
            captured.append(
                (session_id, filename, raw_bytes, content_type, document_type, context_text)
            )
            return {"document_status": "uploaded"}

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: {
            "session_id": "sess-1",
            "pending_requested_documents": ["passport_bio", "ds160"],
            "required_initial_package": ["passport_bio", "ds160"],
        }.get(key, default),
    )
    monkeypatch.setattr(cl.user_session, "set", lambda key, value: None)

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
    assert captured == [
        ("sess-1", "passport.png", b"png-bytes", "image/png", None, None)
    ]


@pytest.mark.asyncio
async def test_upload_message_elements_uses_explicit_text_hint_when_user_says_what_document_is(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_path = tmp_path / "passport.png"
    upload_path.write_bytes(b"png-bytes")
    captured: list[tuple[str, str, bytes, str, str | None]] = []

    class DummyClient:
        async def upload_file(
            self,
            session_id: str,
            filename: str,
            raw_bytes: bytes,
            content_type: str,
            document_type: str | None = None,
            context_text: str | None = None,
        ) -> dict[str, str]:
            captured.append(
                (session_id, filename, raw_bytes, content_type, document_type, context_text)
            )
            return {"document_status": "uploaded"}

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: {
            "session_id": "sess-1",
            "pending_requested_documents": ["passport_bio", "ds160"],
            "required_initial_package": ["passport_bio", "ds160"],
        }.get(key, default),
    )
    monkeypatch.setattr(cl.user_session, "set", lambda key, value: None)

    count = await _upload_message_elements(
        SimpleNamespace(
            content="这是我的护照首页。",
            elements=[
                SimpleNamespace(
                    path=Path(upload_path),
                    name="passport.png",
                    mime="image/png",
                )
            ],
        )
    )

    assert count == 1
    assert captured == [
        (
            "sess-1",
            "passport.png",
            b"png-bytes",
            "image/png",
            None,
            "这是我的护照首页。",
        )
    ]


@pytest.mark.asyncio
async def test_upload_message_elements_prefers_main_flow_feedback_and_refreshes_session_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_path = tmp_path / "funding-proof.pdf"
    upload_path.write_bytes(b"pdf-bytes")
    sent_messages: list[str] = []
    session_state = {
        "session_id": "sess-1",
        "pending_requested_documents": ["funding_proof"],
        "required_initial_package": ["funding_proof", "ds160"],
        "last_gate_progress": None,
    }

    class DummyClient:
        async def upload_file(
            self,
            session_id: str,
            filename: str,
            raw_bytes: bytes,
            content_type: str,
            document_type: str | None = None,
            context_text: str | None = None,
        ) -> dict[str, object]:
            assert session_id == "sess-1"
            assert filename == "funding-proof.pdf"
            assert raw_bytes == b"pdf-bytes"
            assert content_type == "application/pdf"
            assert document_type is None
            assert context_text is None
            return {
                "document_assessment": {
                    "document_type": "funding_proof",
                    "main_flow_feedback": {
                        "status": "helpful",
                        "message": (
                            "这份材料对当前关键证明 funding_proof 有帮助。"
                            " 当前最关键的证明是 funding_proof，系统正在等待解析结果。"
                        ),
                    },
                },
                "requested_documents": [],
                "gate_progress": {
                    "overall_status": "waiting_for_parse",
                    "uploaded_count": 1,
                },
            }

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )
    monkeypatch.setattr(cl.user_session, "set", lambda key, value: session_state.__setitem__(key, value))

    count = await _upload_message_elements(
        SimpleNamespace(
            elements=[
                SimpleNamespace(
                    path=Path(upload_path),
                    name="funding-proof.pdf",
                    mime="application/pdf",
                )
            ]
        )
    )

    assert count == 1
    assert sent_messages == [
        "上传反馈：已帮助当前主线。\n"
        "这份材料对当前关键证明 funding_proof 有帮助。 当前最关键的证明是 funding_proof，系统正在等待解析结果。"
    ]
    assert session_state["pending_requested_documents"] == []
    assert session_state["last_gate_progress"] == {
        "overall_status": "waiting_for_parse",
        "uploaded_count": 1,
    }


@pytest.mark.asyncio
async def test_upload_message_elements_falls_back_to_feedback_message_when_main_flow_feedback_missing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_path = tmp_path / "passport.pdf"
    upload_path.write_bytes(b"passport-bytes")
    sent_messages: list[str] = []
    session_state = {
        "session_id": "sess-1",
        "pending_requested_documents": ["passport_bio"],
        "required_initial_package": ["passport_bio"],
        "last_gate_progress": None,
    }

    class DummyClient:
        async def upload_file(
            self,
            session_id: str,
            filename: str,
            raw_bytes: bytes,
            content_type: str,
            document_type: str | None = None,
            context_text: str | None = None,
        ) -> dict[str, object]:
            assert document_type is None
            assert context_text is None
            return {
                "document_assessment": {
                    "document_type": "passport_bio",
                    "feedback_message": "新版上传回执",
                },
                "requested_documents": [],
                "gate_progress": {"overall_status": "waiting_for_parse"},
            }

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )
    monkeypatch.setattr(
        cl.user_session,
        "set",
        lambda key, value: session_state.__setitem__(key, value),
    )

    count = await _upload_message_elements(
        SimpleNamespace(
            elements=[
                SimpleNamespace(
                    path=Path(upload_path),
                    name="passport.pdf",
                    mime="application/pdf",
                )
            ]
        )
    )

    assert count == 1
    assert sent_messages == ["新版上传回执"]
    assert session_state["pending_requested_documents"] == []
    assert session_state["last_gate_progress"] == {
        "overall_status": "waiting_for_parse",
    }


@pytest.mark.asyncio
async def test_prompt_for_required_files_uses_not_helpful_copy_and_response_pending_documents(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_path = tmp_path / "tourism-flyer.pdf"
    upload_path.write_bytes(b"tourism-flyer")
    sent_messages: list[str] = []
    session_state = {
        "session_id": "sess-1",
        "pending_requested_documents": ["funding_proof"],
        "last_gate_progress": None,
    }

    class DummyAskFileMessage:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def send(self):
            return [
                SimpleNamespace(
                    path=Path(upload_path),
                    name="tourism-flyer.pdf",
                    type="application/pdf",
                )
            ]

    class DummyClient:
        async def upload_file(
            self,
            session_id: str,
            filename: str,
            raw_bytes: bytes,
            content_type: str,
            document_type: str | None = None,
        ) -> dict[str, object]:
            assert document_type is None
            return {
                "document_assessment": {
                    "document_type": "funding_proof",
                    "main_flow_feedback": {
                        "status": "not_helpful",
                        "message": "这份材料对当前主线没有直接帮助。 当前最缺的关键证明是 funding_proof。",
                    },
                },
                "requested_documents": ["funding_proof"],
                "gate_progress": {
                    "overall_status": "pending_documents",
                    "uploaded_count": 0,
                },
            }

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(cl, "AskFileMessage", DummyAskFileMessage)
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )
    monkeypatch.setattr(cl.user_session, "set", lambda key, value: session_state.__setitem__(key, value))

    await _prompt_for_required_files(["funding_proof"])

    assert sent_messages == [
        "上传反馈：对当前主线没有直接帮助。\n"
        "这份材料对当前主线没有直接帮助。 当前最缺的关键证明是 funding_proof。",
        "材料已收到，你可以继续回答，我会结合材料继续追问。"
    ]
    assert session_state["pending_requested_documents"] == ["funding_proof"]
    assert session_state["last_gate_progress"] == {
        "overall_status": "pending_documents",
        "uploaded_count": 0,
    }


@pytest.mark.asyncio
async def test_upload_message_elements_refreshes_upload_options_between_multiple_files(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_upload_path = tmp_path / "passport.png"
    second_upload_path = tmp_path / "ds160.pdf"
    first_upload_path.write_bytes(b"passport-bytes")
    second_upload_path.write_bytes(b"ds160-bytes")
    session_state = {
        "session_id": "sess-1",
        "pending_requested_documents": ["passport_bio", "ds160"],
        "required_initial_package": ["passport_bio", "ds160"],
        "last_gate_progress": None,
    }
    captured_document_types: list[str | None] = []

    class DummyClient:
        async def upload_file(
            self,
            session_id: str,
            filename: str,
            raw_bytes: bytes,
            content_type: str,
            document_type: str | None = None,
            context_text: str | None = None,
        ) -> dict[str, object]:
            captured_document_types.append(document_type)
            if len(captured_document_types) == 1:
                return {
                    "requested_documents": ["ds160"],
                    "gate_progress": {"overall_status": "pending_documents"},
                }
            return {
                "requested_documents": [],
                "gate_progress": {"overall_status": "waiting_for_parse"},
            }

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )
    monkeypatch.setattr(
        cl.user_session,
        "set",
        lambda key, value: session_state.__setitem__(key, value),
    )

    count = await _upload_message_elements(
        SimpleNamespace(
            elements=[
                SimpleNamespace(
                    path=Path(first_upload_path),
                    name="passport.png",
                    mime="image/png",
                ),
                SimpleNamespace(
                    path=Path(second_upload_path),
                    name="ds160.pdf",
                    mime="application/pdf",
                ),
            ]
        )
    )

    assert count == 2
    assert captured_document_types == [None, None]


@pytest.mark.asyncio
async def test_upload_message_elements_allows_unspecified_type_without_pending_documents(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_path = tmp_path / "extra-proof.pdf"
    upload_path.write_bytes(b"extra-proof")
    captured_document_types: list[str | None] = []
    session_state = {
        "session_id": "sess-1",
        "pending_requested_documents": [],
        "required_initial_package": [],
        "last_gate_progress": None,
    }

    class DummyClient:
        async def upload_file(
            self,
            session_id: str,
            filename: str,
            raw_bytes: bytes,
            content_type: str,
            document_type: str | None = None,
            context_text: str | None = None,
        ) -> dict[str, object]:
            captured_document_types.append(document_type)
            return {
                "requested_documents": [],
                "gate_progress": {"overall_status": "waiting_for_parse"},
            }

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )
    monkeypatch.setattr(
        cl.user_session,
        "set",
        lambda key, value: session_state.__setitem__(key, value),
    )

    count = await _upload_message_elements(
        SimpleNamespace(
            elements=[
                SimpleNamespace(
                    path=Path(upload_path),
                    name="extra-proof.pdf",
                    mime="application/pdf",
                )
            ]
        )
    )

    assert count == 1
    assert captured_document_types == [None]


@pytest.mark.asyncio
async def test_upload_message_elements_allows_unspecified_type_when_pending_is_empty_but_required_package_exists(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_path = tmp_path / "extra-proof.pdf"
    upload_path.write_bytes(b"extra-proof")
    captured_document_types: list[str | None] = []
    session_state = {
        "session_id": "sess-1",
        "pending_requested_documents": [],
        "required_initial_package": ["passport_bio"],
        "last_gate_progress": None,
    }

    class DummyClient:
        async def upload_file(
            self,
            session_id: str,
            filename: str,
            raw_bytes: bytes,
            content_type: str,
            document_type: str | None = None,
            context_text: str | None = None,
        ) -> dict[str, object]:
            captured_document_types.append(document_type)
            return {
                "requested_documents": [],
                "gate_progress": {"overall_status": "waiting_for_parse"},
            }

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )
    monkeypatch.setattr(
        cl.user_session,
        "set",
        lambda key, value: session_state.__setitem__(key, value),
    )

    count = await _upload_message_elements(
        SimpleNamespace(
            elements=[
                SimpleNamespace(
                    path=Path(upload_path),
                    name="extra-proof.pdf",
                    mime="application/pdf",
                )
            ]
        )
    )

    assert count == 1
    assert captured_document_types == [None]


@pytest.mark.asyncio
async def test_upload_requested_documents_still_prompts_when_nothing_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompted_documents: list[list[str]] = []
    sent_messages: list[str] = []
    session_state = {
        "pending_requested_documents": [],
        "required_initial_package": [],
    }

    async def fake_prompt_for_required_files(requested_documents: list[str]) -> None:
        prompted_documents.append(requested_documents)

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    monkeypatch.setattr(
        "chainlit_app._prompt_for_required_files",
        fake_prompt_for_required_files,
    )
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )

    await upload_requested_documents(None)

    assert prompted_documents == [[]]
    assert sent_messages == []


@pytest.mark.asyncio
async def test_on_message_with_attachments_only_uses_updated_follow_up_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []

    async def fake_upload(_message) -> int:
        return 1

    async def fake_send_report_actions() -> None:
        return None

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    monkeypatch.setattr("chainlit_app._upload_message_elements", fake_upload)
    monkeypatch.setattr("chainlit_app._send_report_actions", fake_send_report_actions)
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: {"session_id": "sess-1"}.get(key, default),
    )

    await on_message(SimpleNamespace(content="   ", elements=[SimpleNamespace()]))

    assert sent_messages == ["材料已收到，你可以继续回答，我会结合材料继续追问。"]


@pytest.mark.asyncio
async def test_send_report_actions_uses_soft_gate_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_payloads: list[tuple[str, list[str]]] = []
    session_state = {
        "pending_requested_documents": ["funding_proof"],
    }

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]
            self.actions = kwargs["actions"]

        async def send(self):
            sent_payloads.append(
                (self.content, [action.name for action in self.actions])
            )

    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )

    await _send_report_actions()

    assert sent_payloads == [
        (
            "继续像真实面签一样直接回答即可。"
            "如果你手边有能支持当前说法的表格或证明，也可以随时上传；系统会先自行识别。"
            "支持 PDF/PNG/JPG/JPEG，单文件不超过 64MB。",
            [
                "upload_requested_documents",
                "show_user_report",
                "show_internal_report",
            ],
        )
    ]


@pytest.mark.asyncio
async def test_on_chat_start_mentions_can_answer_before_uploading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []
    saved_state: dict[str, object] = {}

    class DummyClient:
        async def create_session(self, declared_family: str) -> dict[str, str]:
            assert declared_family == "f1"
            return {"session_id": "sess-1"}

        async def get_required_package(self, session_id: str) -> dict[str, object]:
            assert session_id == "sess-1"
            return {"required_initial_package": ["passport_bio", "ds160"]}

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    class DummyAskActionMessage:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def send(self):
            return {"payload": {"declared_family": "f1"}}

    async def fake_send_report_actions() -> None:
        return None

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr("chainlit_app._send_report_actions", fake_send_report_actions)
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(cl, "AskActionMessage", DummyAskActionMessage)
    monkeypatch.setattr(
        cl.user_session,
        "set",
        lambda key, value: saved_state.__setitem__(key, value),
    )

    await on_chat_start()

    assert sent_messages == [
        "欢迎使用 DS-160 模拟器。请先选择签证家族。",
        (
            "已进入 F-1 模拟面谈。\n"
            "先按真实面签方式直接回答问题即可；如果后面需要具体材料，我会再提示你。\n"
            "如你手边已有表格或证明，也可以随时上传，系统会先自行识别。"
            "支持 PDF/PNG/JPG/JPEG，单文件不超过 64MB。"
        ),
    ]
    assert saved_state["session_id"] == "sess-1"
    assert saved_state["declared_family"] == "f1"
    assert saved_state["required_initial_package"] == ["passport_bio", "ds160"]
    assert saved_state["pending_requested_documents"] == ["passport_bio", "ds160"]


@pytest.mark.asyncio
async def test_on_message_need_more_evidence_shows_reply_then_lightweight_cta_without_auto_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []
    prompted_documents: list[list[str]] = []
    session_state = {
        "session_id": "sess-1",
        "pending_requested_documents": ["ds160"],
        "last_governor_decision": None,
    }

    class DummyClient:
        async def post_message(self, session_id: str, content: str) -> dict[str, object]:
            assert session_id == "sess-1"
            assert content == "我现在先解释资助安排。"
            return {
                "assistant_message": "好的，你可以先说明资助安排和资金来源。",
                "governor_decision": "need_more_evidence",
                "requested_documents": ["funding_proof"],
                "gate_progress": {"overall_status": "pending_documents"},
            }

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    async def fake_upload(_message) -> int:
        return 0

    async def fake_prompt_for_required_files(requested_documents: list[str]) -> None:
        prompted_documents.append(requested_documents)

    async def fake_send_report_actions() -> None:
        return None

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr("chainlit_app._upload_message_elements", fake_upload)
    monkeypatch.setattr(
        "chainlit_app._prompt_for_required_files",
        fake_prompt_for_required_files,
    )
    monkeypatch.setattr("chainlit_app._send_report_actions", fake_send_report_actions)
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )
    monkeypatch.setattr(
        cl.user_session,
        "set",
        lambda key, value: session_state.__setitem__(key, value),
    )

    await on_message(SimpleNamespace(content="我现在先解释资助安排。", elements=[]))

    assert prompted_documents == []
    assert sent_messages == [
        "好的，你可以先说明资助安排和资金来源。",
        "当前最缺 funding_proof，可现在上传，也可继续解释。",
    ]
    assert session_state["last_governor_decision"] == "need_more_evidence"
    assert session_state["pending_requested_documents"] == ["funding_proof"]


@pytest.mark.asyncio
async def test_on_message_need_more_evidence_repeats_lightweight_cta_when_gap_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []
    session_state = {
        "session_id": "sess-1",
        "pending_requested_documents": ["funding_proof"],
        "last_governor_decision": None,
    }

    class DummyClient:
        async def post_message(self, session_id: str, content: str) -> dict[str, object]:
            assert session_id == "sess-1"
            assert content == "我先继续解释资金细节。"
            return {
                "assistant_message": "好的，请继续说明资金细节。",
                "governor_decision": "need_more_evidence",
                "requested_documents": ["funding_proof"],
                "gate_progress": {"overall_status": "pending_documents"},
            }

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    async def fake_upload(_message) -> int:
        return 0

    async def fake_send_report_actions() -> None:
        return None

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr("chainlit_app._upload_message_elements", fake_upload)
    monkeypatch.setattr("chainlit_app._send_report_actions", fake_send_report_actions)
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )
    monkeypatch.setattr(
        cl.user_session,
        "set",
        lambda key, value: session_state.__setitem__(key, value),
    )

    await on_message(SimpleNamespace(content="我先继续解释资金细节。", elements=[]))

    assert sent_messages == [
        "好的，请继续说明资金细节。",
        "当前最缺 funding_proof，可现在上传，也可继续解释。",
    ]
    assert session_state["pending_requested_documents"] == ["funding_proof"]


@pytest.mark.asyncio
async def test_on_message_waiting_for_parse_does_not_send_lightweight_cta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []
    session_state = {
        "session_id": "sess-1",
        "pending_requested_documents": ["funding_proof"],
        "last_governor_decision": None,
    }

    class DummyClient:
        async def post_message(self, session_id: str, content: str) -> dict[str, object]:
            assert session_id == "sess-1"
            assert content == "我补充一下。"
            return {
                "assistant_message": "好的，系统正在等待解析刚上传的材料。",
                "governor_decision": "need_more_evidence",
                "requested_documents": ["funding_proof"],
                "gate_progress": {"overall_status": "waiting_for_parse"},
            }

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    async def fake_upload(_message) -> int:
        return 0

    async def fake_send_report_actions() -> None:
        return None

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr("chainlit_app._upload_message_elements", fake_upload)
    monkeypatch.setattr("chainlit_app._send_report_actions", fake_send_report_actions)
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: session_state.get(key, default),
    )
    monkeypatch.setattr(
        cl.user_session,
        "set",
        lambda key, value: session_state.__setitem__(key, value),
    )

    await on_message(SimpleNamespace(content="我补充一下。", elements=[]))

    assert sent_messages == ["好的，系统正在等待解析刚上传的材料。"]
    assert session_state["pending_requested_documents"] == ["funding_proof"]


@pytest.mark.asyncio
async def test_on_message_with_attachments_only_and_cancelled_type_selection_does_not_post_empty_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []
    post_message_calls: list[tuple[str, str]] = []

    class DummyClient:
        async def post_message(self, session_id: str, content: str) -> dict[str, object]:
            post_message_calls.append((session_id, content))
            return {
                "assistant_message": "不应到达这里",
                "governor_decision": "continue_interview",
                "requested_documents": [],
                "gate_progress": {},
            }

    class DummyMessage:
        def __init__(self, **kwargs):
            self.content = kwargs["content"]

        async def send(self):
            sent_messages.append(self.content)

    async def fake_upload(_message) -> int:
        return 0

    async def fake_send_report_actions() -> None:
        return None

    monkeypatch.setattr("chainlit_app._client", lambda: DummyClient())
    monkeypatch.setattr("chainlit_app._upload_message_elements", fake_upload)
    monkeypatch.setattr("chainlit_app._send_report_actions", fake_send_report_actions)
    monkeypatch.setattr(cl, "Message", DummyMessage)
    monkeypatch.setattr(
        cl.user_session,
        "get",
        lambda key, default=None: {"session_id": "sess-1"}.get(key, default),
    )

    await on_message(SimpleNamespace(content="   ", elements=[SimpleNamespace()]))

    assert post_message_calls == []
    assert sent_messages == []


@pytest.mark.asyncio
async def test_show_user_report_sends_formatted_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []

    class DummyClient:
        async def get_user_report(self, session_id: str) -> dict[str, object]:
            assert session_id == "sess-1"
            return {
                "outcome_label": "高风险待复核",
                "summary": "当前面谈已识别出高风险事项，需先完成复核。",
                "interview_status": "high_risk_review",
                "risk_level": "high",
                "current_key_question": "你和资助人最近一次见面是什么时候？",
                "allowed_next_actions": ["wait_for_review"],
                "missing_evidence": ["sponsor_relationship_proof"],
                "recommended_improvements": ["围绕高风险点补充解释或关键证明。"],
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
        "当前结论：高风险待复核\n"
        "摘要：当前面谈已识别出高风险事项，需先完成复核。\n"
        "当前状态：高风险复核（高风险）\n"
        "当前关键问题：你和资助人最近一次见面是什么时候？\n"
        "当前关键证明：暂无\n"
        "缺失材料：sponsor_relationship_proof\n"
        "建议动作：等待进一步复核\n"
        "建议：\n"
        "- 围绕高风险点补充解释或关键证明。"
    ]


@pytest.mark.asyncio
async def test_show_internal_report_marks_debug_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []

    class DummyClient:
        async def get_internal_report(self, session_id: str) -> dict[str, object]:
            assert session_id == "sess-1"
            return {
                "session_id": "sess-1",
                "runtime_trace": [],
                "runtime_view_state": {
                    "decision": "continue_interview",
                    "governor_decision": "continue_interview",
                    "current_key_question": "What is the purpose of your travel?",
                },
            }

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
        "内部报告（调试信息）\n"
        "最新运行时视图：\n"
        "- decision: continue_interview\n"
        "- governor_decision: continue_interview\n"
        "- current_key_question: What is the purpose of your travel?\n"
        "- current_key_proof: 暂无\n"
        "{\n"
        '  "runtime_trace": [],\n'
        '  "runtime_view_state": {\n'
        '    "current_key_question": "What is the purpose of your travel?",\n'
        '    "decision": "continue_interview",\n'
        '    "governor_decision": "continue_interview"\n'
        "  },\n"
        '  "session_id": "sess-1"\n'
        "}"
    ]
