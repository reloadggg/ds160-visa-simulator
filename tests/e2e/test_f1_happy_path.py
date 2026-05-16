import json
from collections.abc import Generator
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from types import SimpleNamespace

from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def disable_runtime_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


@pytest.fixture()
def client(tmp_path) -> Generator[TestClient, None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'f1-happy-path.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def install_stub_build_question_action(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_build_question_action(
        self,
        session_id,
        profile,
        score,
        governor_decision,
        trace_entries,
        recent_turns=None,
    ):
        del self, session_id, profile, governor_decision, trace_entries, recent_turns
        requested_documents = list(score.missing_evidence[:1])
        if requested_documents:
            return SimpleNamespace(
                assistant_message=f"Please upload {requested_documents[0]}.",
                requested_documents=requested_documents,
                decision_hint="need_more_evidence",
            )
        return SimpleNamespace(
            assistant_message="What is the purpose of your travel?",
            requested_documents=[],
            decision_hint="continue_interview",
        )

    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        fake_build_question_action,
    )


def test_f1_happy_path_fixture_produces_expected_user_report(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_stub_build_question_action(monkeypatch)
    fixture_dir = Path("fixtures/f1/f1_parent_sponsored_consistent_01")
    case_payload = json.loads((fixture_dir / "case.json").read_text())
    expected_score = json.loads((fixture_dir / "expected_score.json").read_text())
    expected_governor = json.loads((fixture_dir / "expected_governor.json").read_text())
    expected_profile = json.loads((fixture_dir / "expected_profile.json").read_text())
    expected_internal_report = json.loads(
        (fixture_dir / "expected_internal_report.json").read_text(),
    )

    session_resp = client.post(
        "/v1/sessions",
        json={"declared_family": case_payload["visa_family"]},
    )
    session_id = session_resp.json()["session_id"]

    message_resp = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )
    report_resp = client.get(f"/v1/sessions/{session_id}/reports/user")
    internal_resp = client.get(f"/v1/sessions/{session_id}/reports/internal")

    assert message_resp.status_code == 200
    assert report_resp.status_code == 200
    assert internal_resp.status_code == 200
    assert message_resp.json()["score_summary"] == {}
    for document_type in expected_score["missing_evidence"]:
        assert document_type in message_resp.json()["requested_documents"]
    assert message_resp.json()["governor_decision"] == expected_governor["decision"]
    assert report_resp.json()["interview_status"] == "waiting_key_proof"
    assert report_resp.json()["outcome_label"] == "需补强关键证据"
    assert (
        internal_resp.json()["profile_snapshot"]["funding"]
        == expected_profile["funding"]
    )
    assert internal_resp.json()["policy_pack_trace"].get("prompt_pack_id") in {
        expected_internal_report["policy_pack_trace"].get("prompt_pack_id"),
        expected_internal_report["policy_pack_trace"].get("policy_pack_id"),
        "ds160.interviewer",
    }
