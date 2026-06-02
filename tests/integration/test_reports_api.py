from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import DocumentRecord, SessionRecord
from app.db.session import get_db
from app.domain.case_memory import (
    CaseClaim,
    CaseConflict,
    DocumentTypeCandidate,
    EvidenceCard,
    MaterialUnderstandingResult,
    ProofPoint,
)
from app.domain.runtime import build_initial_gate_status
from app.services.interview_review_service import InterviewReviewService
from app.repositories.session_turn_repo import SessionTurnRepository
from app.main import app


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reports-api.sqlite3'}",
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


def test_user_report_returns_summary_shape(client: TestClient) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert response.status_code == 200
    assert "outcome_label" in response.json()
    assert "interview_status" in response.json()
    assert "interview_result" in response.json()


def test_session_export_returns_json_without_document_bytes(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.profile_json = {"funding": {"primary_source": "parents"}}
        db.add(
            DocumentRecord(
                document_id="doc-export-1",
                session_id=session_id,
                filename="bank-statement.png",
                status="parsed",
                raw_bytes=b"binary-image-content",
                raw_text="Material understanding sponsor bank balance text",
                artifact_json={
                    "source_type": "image",
                    "document_type": "funding_proof",
                    "expected_findings": [
                        {"kind": "cross_document_conflict"}
                    ],
                    "synthetic_bundle_id": "dbg-bundle-export",
                    "debug_bundle_scenario": "funding_shortfall_bundle",
                    "scenario_label": "资金缺口调试包",
                    "metadata": {
                        "expected_findings": "oracle should not export",
                        "debug_material_bundle": True,
                    },
                    "document_assessment": {
                        "document_type": "funding_proof",
                        "supported_claims": ["parents sponsor tuition"],
                    },
                },
            )
        )
        db.add(record)
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/reports/export")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "ds160.session_export.v1"
    assert payload["session"]["session_id"] == session_id
    assert payload["documents"] == [
        {
            "document_id": "doc-export-1",
            "filename": "bank-statement.png",
            "status": "parsed",
            "extracted_text": "Material understanding sponsor bank balance text",
            "artifact": {
                "source_type": "image",
                "document_type": "funding_proof",
                "metadata": {
                    "debug_material_bundle": True,
                },
                "document_assessment": {
                    "document_type": "funding_proof",
                    "supported_claims": ["parents sponsor tuition"],
                },
            },
        }
    ]
    assert "raw_bytes" not in str(payload)
    serialized = str(payload)
    assert "expected_findings" not in serialized
    assert "cross_document_conflict" not in serialized
    assert "dbg-bundle-export" not in serialized
    assert "funding_shortfall_bundle" not in serialized
    assert "资金缺口调试包" not in serialized
    assert "oracle should not export" not in serialized
    assert "binary-image-content" not in str(payload)


def test_interview_review_context_uses_public_case_board_and_redacted_artifacts(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    material_result = MaterialUnderstandingResult(
        evidence_cards=[
            EvidenceCard(
                evidence_id="ev-school",
                source_type="uploaded_file",
                document_id="doc-review-context",
                excerpt="School Name: Example University",
                claim_refs=["claim-school"],
                confidence=0.93,
                metadata={
                    "expected_findings": "hidden oracle",
                    "debug_material_bundle": True,
                },
            )
        ],
        extracted_claims=[
            CaseClaim(
                claim_id="claim-school",
                field_path="/education/school_name",
                value="Example University",
                status="documented",
                supporting_evidence_ids=["ev-school"],
                confidence=0.93,
            )
        ],
        confidence=0.93,
    )

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.phase_state = "interview"
        db.add(
            DocumentRecord(
                document_id="doc-review-context",
                session_id=session_id,
                filename="i20.png",
                status="parsed",
                raw_text="School Name: Example University",
                artifact_json={
                    "document_type": "i20",
                    "expected_findings": [{"kind": "school_mismatch"}],
                    "synthetic_bundle_id": "dbg-review-context",
                    "debug_bundle_scenario": "school_mismatch_bundle",
                    "metadata": {
                        "expected_findings": "hidden oracle",
                        "debug_material_bundle": True,
                    },
                    "material_understanding_result": material_result.model_dump(
                        mode="json"
                    ),
                },
            )
        )
        db.add(record)
        db.commit()

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        context = InterviewReviewService(db)._build_review_context(record)

    assert context["user_report"]["case_board"]["claims"][0]["claim_id"] == (
        "claim-school"
    )
    assert context["internal_report"]["case_board"]["claims"][0]["claim_id"] == (
        "claim-school"
    )
    assert context["documents"][0]["artifact"]["metadata"] == {
        "debug_material_bundle": True,
    }
    serialized = str(context)
    assert "expected_findings" not in serialized
    assert "school_mismatch" not in serialized
    assert "dbg-review-context" not in serialized
    assert "school_mismatch_bundle" not in serialized
    assert "hidden oracle" not in serialized


def test_reports_api_returns_advisory_report_and_internal_histories(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        gate_status = build_initial_gate_status(
            declared_family="f1",
            scenario_key="parent_sponsored",
            required_documents=["funding_proof"],
        )
        gate_status["status"] = "waiting_for_parse"
        record.phase_state = "gate_review"
        record.current_governor_decision = "need_more_evidence"
        record.profile_json = {"funding": {"primary_source": "parents"}}
        record.gate_status_json = gate_status
        record.runtime_trace_json = [
            {"node_name": "resolve_evidence", "summary": "documented_refs=0"}
        ]
        record.score_history_json = [
            {
                "scoring_stage": "gate_review",
                "category_fit": 0,
                "document_readiness": 40,
                "narrative_consistency": 0,
                "confidence": 0,
                "missing_evidence": ["funding_proof"],
                "risk_flags": [],
                "summary": "missing=1 risk_flags=0",
            }
        ]
        record.governor_history_json = [
            {
                "decision": "need_more_evidence",
                "summary": "decision=need_more_evidence",
            }
        ]
        db.add(record)
        db.commit()

    user_response = client.get(f"/v1/sessions/{session_id}/reports/user")
    internal_response = client.get(f"/v1/sessions/{session_id}/reports/internal")

    assert user_response.status_code == 200
    assert user_response.json()["interview_status"] == "verify_key_issue"
    assert user_response.json()["outcome_label"] == "需核验关键问题"

    assert internal_response.status_code == 200
    internal_payload = internal_response.json()
    assert internal_payload["runtime_trace"] == [
        {"node_name": "resolve_evidence", "summary": "documented_refs=0"}
    ]
    assert internal_payload["score_history"] == [
        {
            "scoring_stage": "gate_review",
            "category_fit": 0,
            "document_readiness": 40,
            "narrative_consistency": 0,
            "confidence": 0,
            "missing_evidence": ["funding_proof"],
            "risk_flags": [],
            "summary": "missing=1 risk_flags=0",
        }
    ]
    assert internal_payload["governor_history"] == [
        {
            "decision": "need_more_evidence",
            "summary": "decision=need_more_evidence",
        }
    ]
    assert internal_payload["runtime_ledger"]["session_id"] == session_id
    assert internal_payload["runtime_ledger"]["turns"] == []
    assert [event["event_type"] for event in internal_payload["runtime_ledger"]["events"]] == [
        "trace",
        "scorer",
        "boundary",
    ]
    assert internal_payload["runtime_ledger"]["events"][0]["event_id"].startswith(
        "session-orphan:trace:"
    )


def test_reports_api_returns_interview_copy(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        gate_status = build_initial_gate_status(
            declared_family="f1",
            scenario_key="parent_sponsored",
            required_documents=["funding_proof"],
        )
        gate_status["status"] = "ready_for_interview"
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        record.profile_json = {"funding": {"primary_source": "self"}}
        record.gate_status_json = gate_status
        record.runtime_trace_json = [
            {"node_name": "build_next_action", "summary": "requested_documents=0"}
        ]
        record.score_history_json = [
            {
                "scoring_stage": "interview_turn",
                "category_fit": 78,
                "document_readiness": 82,
                "narrative_consistency": 75,
                "confidence": 80,
                "missing_evidence": [],
                "risk_flags": [],
                "summary": "missing=0 risk_flags=0",
            }
        ]
        record.governor_history_json = [
            {
                "decision": "continue_interview",
                "summary": "decision=continue_interview",
            }
        ]
        db.add(record)
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert response.status_code == 200
    payload = response.json()
    assert payload["interview_status"] == "continue_interview"
    assert payload["outcome_label"] == "正式问答进行中"
    assert payload["summary"] == "当前已进入正式 interview 阶段，可继续回答后续问题。"
    assert payload["recommended_improvements"] == [
        "继续回答后续问题，并保持叙事一致。",
    ]


def test_reports_api_keeps_interviewer_focus_when_gate_review_state_outpaces_runtime_view(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        gate_status = build_initial_gate_status(
            declared_family="f1",
            scenario_key="gate-primary-report",
            required_documents=["ds160", "funding_proof"],
        )
        gate_status["status"] = "waiting_for_parse"
        gate_status["required_documents"][0]["status"] = "missing"
        gate_status["required_documents"][1]["status"] = "uploaded"
        gate_status["required_documents"][1]["is_uploaded"] = True
        gate_status["required_documents"][1]["is_parsed"] = False
        gate_status["required_documents"][1]["meets_minimum_fields"] = False
        record.phase_state = "gate_review"
        record.current_governor_decision = "need_more_evidence"
        record.profile_json = {"funding": {"primary_source": "self"}}
        record.gate_status_json = gate_status
        record.interviewer_state_json = {
            "public_status": "waiting_key_proof",
            "current_key_proof": "funding_proof",
            "requested_documents": ["funding_proof"],
            "allowed_next_actions": ["upload_key_proof", "explain_missing_proof"],
        }
        record.current_focus_json = {
            "owner": "interviewer_runtime_service",
            "kind": "required_document",
            "document_type": "funding_proof",
        }
        db.add(record)
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert response.status_code == 200
    payload = response.json()
    assert payload["interview_status"] == "waiting_key_proof"
    assert payload["current_key_proof"] == "funding_proof"
    assert payload["missing_evidence"] == ["funding_proof"]
    assert payload["recommended_improvements"] == [
        "围绕 funding_proof 说明事实来源；如果有材料，可作为补强证据上传。"
    ]
    assert "待证明点" not in payload["summary"]
    assert "上传对应证据" not in payload["summary"]


def test_reports_api_projects_case_memory_from_material_understanding(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    material_result = MaterialUnderstandingResult(
        document_type_candidates=[
            DocumentTypeCandidate(document_type="i20", confidence=0.92)
        ],
        evidence_cards=[
            EvidenceCard(
                evidence_id="ev-school",
                source_type="uploaded_file",
                document_id="doc-case-memory",
                excerpt="School Name: Example University",
                claim_refs=["claim-school"],
                confidence=0.93,
            ),
            EvidenceCard(
                evidence_id="ev-parent-bank",
                source_type="uploaded_file",
                document_id="doc-case-memory",
                excerpt="Parent sponsor balance",
                claim_refs=["claim-funding"],
                confidence=0.86,
            ),
        ],
        extracted_claims=[
            CaseClaim(
                claim_id="claim-school",
                field_path="/education/school_name",
                value="Example University",
                status="documented",
                supporting_evidence_ids=["ev-school"],
                confidence=0.93,
            ),
            CaseClaim(
                claim_id="claim-funding",
                field_path="/funding/primary_source",
                value="self",
                status="contradicted",
                conflicting_evidence_ids=["ev-parent-bank"],
                confidence=0.86,
            ),
        ],
        proof_points=[
            ProofPoint(
                proof_point_id="proof-funding-source",
                visa_family="f1",
                question="Who will pay for your study?",
                status="partial",
                why_it_matters="Funding source must be credible.",
            )
        ],
        conflicts=[
            CaseConflict(
                conflict_id="conflict-funding-source",
                claim_ids=["claim-funding"],
                evidence_ids=["ev-parent-bank"],
                summary="用户说自费，但材料显示父母资助。",
                severity="high",
                suggested_followup="请澄清资金来源。",
            )
        ],
        confidence=0.88,
    )

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        db.add(
            DocumentRecord(
                document_id="doc-case-memory",
                session_id=session_id,
                filename="i20.png",
                status="parsed",
                raw_bytes=b"image",
                raw_text="",
                artifact_json={
                    "material_understanding_result": material_result.model_dump(
                        mode="json"
                    ),
                    "case_board_delta": {
                        "latest_material": {
                            "document_id": "doc-case-memory",
                            "filename": "i20.png",
                            "understanding_status": "completed",
                        }
                    },
                },
            )
        )
        db.add(record)
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert response.status_code == 200
    payload = response.json()
    assert payload["case_board"]["claims"][0]["claim_id"] == "claim-funding"
    assert "用户说自费，但材料显示父母资助。" in payload["risk_points"]
    assert payload["risk_level"] == "high"
    assert payload["missing_evidence"][0] == "proof-funding-source"
    assert "请澄清资金来源。" in payload["recommended_improvements"]


def test_user_report_can_derive_runtime_view_state_from_ledger_when_state_is_empty(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        record.profile_json = {"funding": {"primary_source": "self"}}
        record.interviewer_state_json = {}
        record.current_focus_json = {}
        record.runtime_trace_json = [
            {
                "node_name": "turn_decision",
                "prompt_pack_id": "ds160.interviewer",
                "prompt_version": "v2",
                "provider": "openai",
                "model": "gpt-5.4",
                "metadata": {"reasoning_effort": "high"},
                "turn_decision": "continue_interview",
            }
        ]
        record.score_history_json = [
            {
                "scoring_stage": "interview_turn",
                "category_fit": 78,
                "document_readiness": 82,
                "narrative_consistency": 75,
                "confidence": 80,
                "missing_evidence": [],
                "risk_flags": [],
                "summary": "missing=0 risk_flags=0",
            }
        ]
        record.governor_history_json = [
            {
                "decision": "continue_interview",
                "summary": "decision=continue_interview",
            }
        ]
        repo = SessionTurnRepository(db)
        repo.append_user_turn(
            session_id=session_id,
            content="I want to study computer science.",
            source="user_message",
            commit=False,
        )
        repo.append_assistant_turn(
            session_id=session_id,
            content="What is the purpose of your travel?",
            source="interviewer_runtime_service",
            metadata_json={
                "turn_record": {
                    "decision": "continue_interview",
                    "requested_documents": [],
                    "focus": {
                        "kind": "interview_question",
                        "question": "What is the purpose of your travel?",
                    },
                    "advisory_summary": {
                        "risk_codes": [],
                        "missing_evidence": [],
                        "risk_level": "none",
                    },
                }
            },
            commit=False,
        )
        db.add(record)
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/reports/user")
    internal_response = client.get(f"/v1/sessions/{session_id}/reports/internal")

    assert response.status_code == 200
    payload = response.json()
    assert payload["interview_status"] == "continue_interview"
    assert payload["interview_result"] == "in_progress"
    assert payload["current_key_question"] == "What is the purpose of your travel?"
    assert payload["allowed_next_actions"] == [
        "answer_question",
        "continue_interview",
    ]
    assert payload["prompt_trace"] == {
        "prompt_pack_id": "ds160.interviewer",
        "prompt_version": "v2",
        "provider": "openai",
        "model": "gpt-5.4",
        "reasoning_effort": "high",
    }

    assert internal_response.status_code == 200
    assert internal_response.json()["runtime_view_state"]["current_key_question"] == (
        "What is the purpose of your travel?"
    )
    assert internal_response.json()["runtime_view_state"]["source_turn_content"] == (
        "What is the purpose of your travel?"
    )


def test_reports_api_projects_passed_result_from_final_assistant_closure(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        record.profile_json = {"funding": {"primary_source": "parents"}}
        record.interviewer_state_json = {}
        record.current_focus_json = {}
        record.score_history_json = [
            {
                "scoring_stage": "interview_turn",
                "category_fit": 90,
                "document_readiness": 88,
                "narrative_consistency": 92,
                "confidence": 86,
                "missing_evidence": [],
                "risk_flags": [],
                "summary": "missing=0 risk_flags=0",
            }
        ]
        record.governor_history_json = [
            {
                "decision": "continue_interview",
                "summary": "decision=continue_interview",
            }
        ]
        repo = SessionTurnRepository(db)
        repo.append_user_turn(
            session_id=session_id,
            content="My parents will sponsor my study, and I plan to return to China after graduation.",
            source="user_message",
            commit=False,
        )
        repo.append_assistant_turn(
            session_id=session_id,
            content=(
                "All right, Mr. Lee. Your study plan, funding, and intention "
                "to return to China are clear; that will be all for now."
            ),
            source="interviewer_runtime_service",
            metadata_json={
                "turn_record": {
                    "decision": "continue_interview",
                    "requested_documents": [],
                    "focus": {},
                    "advisory_summary": {
                        "risk_codes": [],
                        "missing_evidence": [],
                        "risk_level": "none",
                    },
                }
            },
            commit=False,
        )
        db.add(record)
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert response.status_code == 200
    payload = response.json()
    assert payload["interview_status"] == "continue_interview"
    assert payload["interview_result"] == "passed"
    assert payload["interview_result_label"] == "本轮模拟通过"
    assert payload["outcome_label"] == "本轮模拟通过"
    assert payload["risk_level"] == "none"
    assert payload["missing_evidence"] == []


def test_reports_api_distinguishes_high_risk_review_from_simulated_refusal(
    client: TestClient,
    db_session_factory,
) -> None:
    high_risk_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    high_risk_session_id = high_risk_resp.json()["session_id"]
    refusal_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    refusal_session_id = refusal_resp.json()["session_id"]

    with db_session_factory() as db:
        high_risk_record = db.get(SessionRecord, high_risk_session_id)
        refusal_record = db.get(SessionRecord, refusal_session_id)
        assert high_risk_record is not None
        assert refusal_record is not None

        for record, decision in (
            (high_risk_record, "high_risk_review"),
            (refusal_record, "simulated_refusal"),
        ):
            record.phase_state = "interview"
            record.current_governor_decision = decision
            record.profile_json = {"funding": {"primary_source": "self"}}
            db.add(record)
        db.commit()

    high_risk_response = client.get(f"/v1/sessions/{high_risk_session_id}/reports/user")
    refusal_response = client.get(f"/v1/sessions/{refusal_session_id}/reports/user")

    assert high_risk_response.status_code == 200
    assert refusal_response.status_code == 200
    assert high_risk_response.json()["interview_status"] == "high_risk_review"
    assert high_risk_response.json()["interview_result"] == "not_passed"
    assert high_risk_response.json()["interview_result_label"] == "未通过：高风险待复核"
    assert high_risk_response.json()["outcome_label"] == "高风险待复核"
    assert refusal_response.json()["interview_status"] == "simulated_refusal"
    assert refusal_response.json()["interview_result"] == "refused"
    assert refusal_response.json()["interview_result_label"] == "模拟拒签"
    assert refusal_response.json()["outcome_label"] == "模拟拒签结果"


def test_generate_interview_review_returns_fallback_report_without_model_config(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.phase_state = "interview"
        record.current_governor_decision = "simulated_refusal"
        record.interviewer_state_json = {
            "public_status": "simulated_refusal",
            "current_key_question": "Who will pay for your first year?",
            "risk_points": ["第一年资金证明不足"],
            "recommended_improvements": ["补齐覆盖 I-20 第一年度费用的资金证明。"],
        }
        db.add(
            DocumentRecord(
                document_id="doc-review-1",
                session_id=session_id,
                filename="i20.png",
                status="parsed",
                raw_text="I-20 estimated expenses: 56000 USD",
                artifact_json={"document_type": "i20"},
            )
        )
        db.add(record)
        db.commit()

    response = client.post(f"/v1/sessions/{session_id}/reports/review")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "ds160.interview_review.v1"
    assert payload["source"] == "fallback"
    assert payload["report"]["outcome"] == "模拟拒签复盘"
    assert payload["basis"]["document_count"] == 1
