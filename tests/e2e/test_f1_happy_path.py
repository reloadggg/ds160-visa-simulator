import json
from collections.abc import Generator
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.core import settings as settings_module
from app.db.session import get_db
from app.main import app
from app.services.native_interviewer_runtime_service import NativeInterviewerOutput


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


def install_native_interviewer_turn_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the canonical native interviewer writer while keeping this fixture test deterministic."""

    monkeypatch.setattr(settings_module.settings, "agent_runtime", "native_interviewer")

    def fake_run_turn(self, record, message_text: str, *, user_turn=None):
        case_state = self._build_case_state(record)
        advisory_context = self._build_advisory_context(case_state)
        missing_evidence = self._missing_evidence_documents(advisory_context)
        has_funding_evidence = any(
            item.get("evidence_type") == "funding_proof"
            or item.get("field_path") == "/funding/primary_source"
            for item in case_state.get("evidence_items", [])
            if isinstance(item, dict)
        )
        if has_funding_evidence:
            missing_evidence = [
                item for item in missing_evidence if item != "funding_proof"
            ]
        decision = "need_more_evidence" if missing_evidence else "continue_interview"
        requested_documents = missing_evidence[:1] if decision == "need_more_evidence" else []
        response = self._build_response(
            record=record,
            message_text=message_text,
            case_state=case_state,
            output=NativeInterviewerOutput(
                assistant_message=(
                    "Please provide the key supporting document for this point."
                    if requested_documents
                    else "What is the purpose of your travel?"
                ),
                decision=decision,
                requested_documents=requested_documents,
            ),
            run_id="native-f1-fixture-stub-run",
            quality={"status": "passed", "attempts": []},
            user_turn_id=getattr(user_turn, "turn_id", None),
        )
        response["remaining_required_documents"] = list(missing_evidence)
        response["turn_decision"]["remaining_required_documents"] = list(missing_evidence)
        response["advisory_context"]["missing_evidence"] = list(missing_evidence)
        response["runtime_view_state"]["remaining_required_documents"] = list(missing_evidence)
        response["runtime_view_state"]["advisory_context"] = response["advisory_context"]
        if not missing_evidence:
            response["advisory_context"]["risk_codes"] = [
                code
                for code in response["advisory_context"].get("risk_codes", [])
                if code != "supporting_evidence_missing"
            ]
            response["advisory_context"]["risk_level"] = "none"
            response["runtime_view_state"]["risk_level"] = "none"
            response["runtime_view_state"]["public_status"] = "continue_interview"
            response["turn_record"]["advisory_summary"]["missing_evidence"] = []
            response["turn_record"]["advisory_summary"]["risk_codes"] = response[
                "advisory_context"
            ]["risk_codes"]
            response["turn_record"]["advisory_summary"]["risk_level"] = "none"
        return response

    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_turn",
        fake_run_turn,
    )


def test_f1_happy_path_fixture_produces_expected_user_report(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_native_interviewer_turn_stub(monkeypatch)
    fixture_dir = Path("fixtures/f1/f1_parent_sponsored_consistent_01")
    case_payload = json.loads((fixture_dir / "case.json").read_text())
    expected_governor = json.loads((fixture_dir / "expected_governor.json").read_text())
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
    assert set(message_resp.json()["score_summary"]) == {
        "category_fit",
        "document_readiness",
        "narrative_consistency",
        "confidence",
    }
    assert message_resp.json()["requested_documents"] == ["funding_proof"]
    assert message_resp.json()["remaining_required_documents"] == ["funding_proof"]
    assert message_resp.json()["runtime_execution"]["runtime_role"] == "canonical"
    assert message_resp.json()["runtime_execution"]["canonical"] is True
    assert message_resp.json()["gate_progress"]["overall_status"] == "pending_documents"
    assert report_resp.json()["missing_evidence"] == ["funding_proof"]
    assert [
        item["document_type"] for item in message_resp.json()["gate_progress"]["documents"]
    ] == ["ds160", "passport_bio", "i20"]
    assert message_resp.json()["governor_decision"] == expected_governor["decision"]
    assert report_resp.json()["interview_status"] == "waiting_key_proof"
    assert report_resp.json()["outcome_label"] == "需核验关键事实"
    assert internal_resp.json()["interviewer_state"]["owner"] == "native_interviewer_runtime"
    assert internal_resp.json()["interviewer_state"]["runtime_execution"]["runtime_role"] == (
        "canonical"
    )
    assert internal_resp.json()["policy_pack_trace"].get("prompt_pack_id") in {
        expected_internal_report["policy_pack_trace"].get("prompt_pack_id"),
        expected_internal_report["policy_pack_trace"].get("policy_pack_id"),
        "ds160.interviewer",
        "ds160.native_interviewer",
    }
