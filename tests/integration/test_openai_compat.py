from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from types import SimpleNamespace

from app.db.base import Base
from app.core import settings as settings_module
from app.db.models import SessionRecord, SessionTurnRecord
from app.db.session import get_db
from app.main import app
from app.services.native_interviewer_runtime_service import NativeInterviewerOutput
from app.services.runtime_errors import ModelRuntimeError, ModelUnavailableError


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'openai-compat.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session_factory) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def install_native_interviewer_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    assistant_message: str = "你提到父母会资助。请具体说明他们的资金来源和这笔钱如何覆盖第一年费用？",
) -> None:
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService._build_runtime",
        lambda self, declared_family: {
            "provider": "openai_compatible",
            "model": "gpt-5.4",
            "reasoning_effort": "high",
        },
    )
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.OpenAIAgentsInterviewerRunner.run",
        lambda self, **kwargs: NativeInterviewerOutput(
            assistant_message=assistant_message,
            decision="continue_interview",
        ),
    )


def test_chat_completions_maps_to_domain_flow(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: SimpleNamespace(
            assistant_message="Please explain your funding plan.",
            requested_documents=[],
            decision_hint="continue_interview",
        ),
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "My parents will pay for my studies."}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    choice = payload["choices"][0]["message"]
    assert choice["role"] == "assistant"
    assert choice["content"]
    assert {
        "session_id",
        "phase_state",
        "context_mode",
        "governor_decision",
        "requested_documents",
        "remaining_required_documents",
        "turn_decision",
        "document_review",
        "prompt_trace",
        "runtime_view_state",
    }.issubset(set(payload["metadata"]))
    assert payload["metadata"]["session_id"].startswith("sess-")
    assert payload["metadata"]["phase_state"] == "interview"
    assert payload["metadata"]["context_mode"] == "new_session"
    assert isinstance(payload["metadata"]["runtime_view_state"], dict)
    assert payload["metadata"]["runtime_view_state"]["decision"]
    assert payload["metadata"]["runtime_view_state"].get("prompt_trace", {}) == payload["metadata"][
        "prompt_trace"
    ]


def test_chat_completions_graph_shadow_keeps_metadata_contract(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_shadow")
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: SimpleNamespace(
            assistant_message="Please explain your funding plan.",
            requested_documents=[],
            decision_hint="continue_interview",
        ),
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "My parents will pay."}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    metadata = payload["metadata"]
    assert "graph_shadow" not in metadata
    assert metadata["turn_decision"]["decision"] == "continue_interview"
    assert isinstance(metadata["prompt_trace"], dict)
    assert isinstance(metadata["runtime_view_state"], dict)

    with db_session_factory() as db:
        assistant_turns = db.scalars(
            select(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == metadata["session_id"],
                SessionTurnRecord.role == "assistant",
            )
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert len(assistant_turns) == 1
    assert assistant_turns[0].metadata_json["graph_shadow"]["status"] == "completed"


def test_chat_completions_graph_mode_keeps_metadata_contract(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    install_native_interviewer_stub(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "My parents will pay."}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    metadata = payload["metadata"]
    assert payload["choices"][0]["message"]["content"] == (
        "你提到父母会资助。请具体说明他们的资金来源和这笔钱如何覆盖第一年费用？"
    )
    assert metadata["selected_public_runtime"] == "native_interviewer"
    assert metadata["native_run_id"].startswith("native-run-")
    assert metadata["turn_decision"]["decision"] == "continue_interview"
    assert metadata["turn_decision"]["assistant_message_author"] == "native_interviewer"
    assert metadata["prompt_trace"]["prompt_pack_id"] == "ds160.native_interviewer"
    assert metadata["prompt_trace"]["native_run_id"] == metadata["native_run_id"]
    assert metadata["runtime_view_state"]["source_turn_id"]
    assert metadata["runtime_view_state"]["prompt_trace"]["native_run_id"]

    with db_session_factory() as db:
        assistant_turn = db.scalar(
            select(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == metadata["session_id"],
                SessionTurnRecord.role == "assistant",
            )
            .order_by(SessionTurnRecord.turn_index)
        )

    assert assistant_turn is not None
    assert assistant_turn.source == "native_interviewer_runtime"
    assert assistant_turn.metadata_json["selected_public_runtime"] == "native_interviewer"
    assert assistant_turn.metadata_json["native_run_id"] == metadata["native_run_id"]


def test_chat_completions_uses_same_runtime_gate_initialization(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.handle_user_turn",
        lambda self, session_id, message_text, **kwargs: {
            "assistant_message": "handled",
            "governor_decision": "continue_interview",
            "score_summary": {
                "category_fit": 60,
                "document_readiness": 50,
                "narrative_consistency": 55,
                "confidence": 58,
            },
            "requested_documents": [],
            "turn_decision": {},
            "prompt_trace": {},
        },
    )
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "I am funded by my institution."}],
            "metadata": {"declared_family": "j1"},
        },
    )

    assert response.status_code == 200
    session_id = response.json()["metadata"]["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    assert record.gate_status_json["scenario_key"] == "institution_funded"
    assert [doc["document_type"] for doc in record.gate_status_json["required_documents"]] == [
        "ds160",
        "passport_bio",
        "ds2019",
        "funding_proof",
    ]


def test_chat_completions_reuses_existing_session_when_metadata_session_id_present(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    handled_session_ids: list[str] = []

    def fake_handle_user_turn(
        self,
        session_id: str,
        message_text: str,
        **kwargs,
    ) -> dict:
        handled_session_ids.append(session_id)
        return {
            "assistant_message": f"handled: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {
                "category_fit": 60,
                "document_readiness": 50,
                "narrative_consistency": 55,
                "confidence": 58,
            },
            "requested_documents": [],
            "gate_progress": {
                "overall_status": "ready_for_interview",
                "ready_count": 0,
                "uploaded_count": 0,
                "missing_count": 0,
                "documents": [],
            },
        }

    monkeypatch.setattr(
        "app.services.message_service.MessageService.handle_user_turn",
        fake_handle_user_turn,
    )

    first_response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "First turn"}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert first_response.status_code == 200
    first_session_id = first_response.json()["metadata"]["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, first_session_id)
        assert record is not None
        record.phase_state = "interview"
        db.add(record)
        db.commit()

    second_response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {"role": "assistant", "content": "Previous reply"},
                {"role": "user", "content": "Second turn"},
            ],
            "metadata": {
                "session_id": first_session_id,
                "declared_family": "j1",
            },
        },
    )

    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert handled_session_ids == [first_session_id, first_session_id]
    assert second_payload["choices"][0]["message"]["content"] == "handled: Second turn"
    metadata = second_payload["metadata"]
    assert metadata["session_id"] == first_session_id
    assert metadata["phase_state"] == "interview"
    assert metadata["context_mode"] == "existing_session"
    assert metadata["governor_decision"] == "continue_interview"
    assert metadata["requested_documents"] == []
    assert metadata["remaining_required_documents"] == []
    assert metadata["document_review"] == {}
    assert metadata["prompt_trace"] == {}
    assert isinstance(metadata["turn_decision"], dict)
    assert isinstance(metadata["runtime_view_state"], dict)

    with db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))
        assert session_count == 1


def test_chat_completions_imports_full_prior_messages_before_latest_user(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    observed_turns_before_runtime: list[tuple[str, str]] = []

    def fake_handle_user_turn(
        self,
        session_id: str,
        message_text: str,
        **kwargs,
    ) -> dict:
        with db_session_factory() as db:
            observed_turns_before_runtime.extend(
                (
                    turn.role,
                    turn.content,
                )
                for turn in db.scalars(
                    select(SessionTurnRecord)
                    .where(SessionTurnRecord.session_id == session_id)
                    .order_by(SessionTurnRecord.turn_index)
                ).all()
            )
        return {
            "assistant_message": f"handled: {message_text}",
            "governor_decision": "continue_interview",
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {},
            "document_review": {},
            "prompt_trace": {},
            "runtime_view_state": {},
        }

    monkeypatch.setattr(
        "app.services.message_service.MessageService.handle_user_turn",
        fake_handle_user_turn,
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {"role": "system", "content": "internal instruction"},
                {"role": "user", "content": "I will attend Example University."},
                {"role": "assistant", "content": "What will you study?"},
                {"role": "user", "content": "Data science."},
            ],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    session_id = response.json()["metadata"]["session_id"]
    assert observed_turns_before_runtime == [
        ("user", "I will attend Example University."),
        ("assistant", "What will you study?"),
    ]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [(turn.role, turn.content) for turn in turns] == [
        ("user", "I will attend Example University."),
        ("assistant", "What will you study?"),
    ]
    assert turns[0].source == "chat_completions_import"
    assert turns[1].source == "chat_completions_import"


def test_chat_completions_derives_idempotency_key_without_explicit_metadata_key(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        return {
            "assistant_message": f"handled #{run_count}: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {"run_count": run_count},
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

    first_response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "Repeatable text"}],
            "metadata": {"declared_family": "f1"},
        },
    )
    session_id = first_response.json()["metadata"]["session_id"]
    second_response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "Repeatable text"}],
            "metadata": {"session_id": session_id},
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert run_count == 1
    assert second_response.json()["choices"][0]["message"]["content"] == (
        first_response.json()["choices"][0]["message"]["content"]
    )

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert turns[0].client_message_id is not None


def test_chat_completions_supports_http_idempotency_key_for_new_session_replay(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        return {
            "assistant_message": f"handled once: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {"run_count": run_count},
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )
    request_body = {
        "model": "visa-simulator-v1",
        "messages": [{"role": "user", "content": "New session retried request"}],
        "metadata": {"declared_family": "f1"},
    }

    first_response = client.post(
        "/v1/chat/completions",
        json=request_body,
        headers={"Idempotency-Key": "compat-new-session-retry"},
    )
    second_response = client.post(
        "/v1/chat/completions",
        json=request_body,
        headers={"Idempotency-Key": "compat-new-session-retry"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert run_count == 1
    assert second_response.json()["metadata"]["session_id"] == (
        first_response.json()["metadata"]["session_id"]
    )
    assert second_response.json()["metadata"]["context_mode"] == "idempotency_replay"

    with db_session_factory() as db:
        turns = db.scalars(select(SessionTurnRecord)).all()

    assert [turn.role for turn in turns] == ["user", "assistant"]


def test_chat_completions_honors_explicit_metadata_client_message_id(
    client: TestClient,
    monkeypatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        return {
            "assistant_message": f"handled once: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {"run_count": run_count},
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

    first_response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "My parents will pay."}],
            "metadata": {
                "declared_family": "f1",
                "client_message_id": "compat-client-repeat-1",
            },
        },
    )
    session_id = first_response.json()["metadata"]["session_id"]
    second_response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "My parents will pay."}],
            "metadata": {
                "session_id": session_id,
                "client_message_id": "compat-client-repeat-1",
            },
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert run_count == 1
    assert second_response.json()["choices"][0]["message"]["content"] == (
        first_response.json()["choices"][0]["message"]["content"]
    )


def test_chat_completions_imported_history_enters_case_and_interview_memory(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "handled",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {},
        },
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {"role": "assistant", "content": "毕业后你准备做什么工作？"},
                {"role": "user", "content": "我毕业后会回国做数据分析师。"},
                {"role": "assistant", "content": "Who will pay your tuition?"},
                {"role": "user", "content": "My parents will pay for my studies."},
                {"role": "user", "content": "Continue."},
            ],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    session_id = response.json()["metadata"]["session_id"]

    with db_session_factory() as db:
        imported_users = db.scalars(
            select(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == session_id,
                SessionTurnRecord.role == "user",
                SessionTurnRecord.source == "chat_completions_import",
            )
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert imported_users[0].metadata_json["interview_memory"]["topic"] == (
        "post_study_plan"
    )
    assert imported_users[1].metadata_json["interview_memory"]["topic"] == "funding"
    assert imported_users[1].metadata_json["case_memory_claims"][0]["field_path"] == (
        "/funding/primary_source"
    )
    assert imported_users[1].client_message_id is None


def test_chat_completions_imported_repeated_text_preserves_ordinal_history(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.handle_user_turn",
        lambda self, session_id, message_text, **kwargs: {
            "assistant_message": "handled",
            "governor_decision": "continue_interview",
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {},
            "document_review": {},
            "prompt_trace": {},
            "runtime_view_state": {},
        },
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {"role": "assistant", "content": "Please confirm."},
                {"role": "user", "content": "Yes."},
                {"role": "assistant", "content": "Please confirm."},
                {"role": "user", "content": "Yes."},
                {"role": "user", "content": "Continue."},
            ],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    session_id = response.json()["metadata"]["session_id"]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [(turn.role, turn.content) for turn in turns] == [
        ("assistant", "Please confirm."),
        ("user", "Yes."),
        ("assistant", "Please confirm."),
        ("user", "Yes."),
    ]
    assert all(turn.client_message_id is None for turn in turns if turn.role == "user")


def test_chat_completions_normalizes_oversized_client_message_id(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "handled",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {},
        },
    )
    long_key = "x" * 512

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "Use long key."}],
            "metadata": {
                "declared_family": "f1",
                "client_message_id": long_key,
            },
        },
    )

    assert response.status_code == 200
    session_id = response.json()["metadata"]["session_id"]

    with db_session_factory() as db:
        user_turn = db.scalar(
            select(SessionTurnRecord).where(
                SessionTurnRecord.session_id == session_id,
                SessionTurnRecord.role == "user",
            )
        )

    assert user_turn is not None
    assert user_turn.client_message_id is not None
    assert len(user_turn.client_message_id) <= 128
    assert user_turn.client_message_id.startswith("clientmsg:")


def test_chat_completions_returns_404_for_unknown_metadata_session_id(
    client: TestClient,
    db_session_factory,
) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "Resume missing session"}],
            "metadata": {"session_id": "sess-missing"},
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found: sess-missing"

    with db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))
        assert session_count == 0


def test_chat_completions_returns_503_when_message_runtime_lacks_model_config(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.handle_user_turn",
        lambda self, session_id, message_text, **kwargs: (_ for _ in ()).throw(
            ModelUnavailableError(
                detail="当前后端未配置可用的对话模型，无法生成面签问答。请检查 OPENAI_API_KEY, OPENAI_BASE_URL。"
            )
        ),
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "Resume the interview."}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_chat_completions_preserves_model_runtime_status_code(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.handle_user_turn",
        lambda self, session_id, message_text, **kwargs: (_ for _ in ()).throw(
            ModelRuntimeError(
                detail="当前对话模型认证失败，API Key 可能已失效或被禁用。",
                status_code=401,
                provider="openai_compatible",
                model="gpt-5.4",
                upstream_code="API_KEY_DISABLED",
            )
        ),
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "Resume the interview."}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 401
    assert "认证失败" in response.json()["detail"]


def test_chat_completions_rejects_empty_messages(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 422


def test_chat_completions_rejects_missing_user_message_without_session(
    client: TestClient,
    db_session_factory,
) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "system", "content": "hi"}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "at least one user message is required"

    with db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))
        assert session_count == 0


def test_chat_completions_rejects_unsupported_family_without_session(
    client: TestClient,
    db_session_factory,
) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "My parents will pay for my studies."}],
            "metadata": {"declared_family": "zzz"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "unsupported declared_family: zzz"

    with db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))
        assert session_count == 0
