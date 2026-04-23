from types import SimpleNamespace

from app.agents.schemas import InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState
from app.domain.runtime import RuntimeTraceEntry
from app.services.interviewer_turn_projector_service import (
    InterviewerTurnProjectorService,
)


def _build_score(
    *,
    risk_codes: list[str] | None = None,
    missing_evidence: list[str] | None = None,
) -> ScoreState:
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    score.category_fit = 61
    score.document_readiness = 42
    score.narrative_consistency = 77
    score.confidence = 68
    score.missing_evidence = list(missing_evidence or [])
    score.risk_flags = [
        RiskFlag(
            code=code,
            severity="high" if code != "supporting_evidence_missing" else "medium",
            status="confirmed" if code != "supporting_evidence_missing" else "supported",
            evidence_refs=["msg:last_user_turn"] if code != "supporting_evidence_missing" else [],
        )
        for code in (risk_codes or [])
    ]
    return score


def _build_record(session_id: str) -> SessionRecord:
    profile = ApplicantProfile.minimal(f"profile-{session_id}")
    profile.profile_version = 2
    return SessionRecord(
        session_id=session_id,
        declared_family="f1",
        profile_json=profile.model_dump(mode="json"),
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
    )


def test_project_builds_projection_for_continue_interview() -> None:
    projector = InterviewerTurnProjectorService()
    score = _build_score(risk_codes=["supporting_evidence_missing"])
    record = _build_record("sess-projector-1")

    projection = projector.project(
        record=record,
        message_text="I will study computer science.",
        action=InterviewNextAction(
            assistant_message="What is the purpose of your travel?",
            requested_documents=[],
            decision_hint="continue_interview",
        ),
        score=score,
        governor_decision="continue_interview",
        governor_requested_documents=[],
        trace_entries=[
            RuntimeTraceEntry(node_name="receive_input"),
            RuntimeTraceEntry(
                node_name="turn_decision",
                prompt_pack_id="ds160.interviewer",
                prompt_version="v2",
                provider="openai",
                model="gpt-5.4",
                metadata={"reasoning_effort": "high"},
            ),
        ],
        history_turn_count=0,
        history_turns=[SimpleNamespace(role="user", turn_id="turn-user-1")],
    )

    assert projection.response["assistant_message"] == "What is the purpose of your travel?"
    assert projection.response["requested_documents"] == []
    assert projection.response["prompt_trace"] == {
        "prompt_pack_id": "ds160.interviewer",
        "prompt_version": "v2",
        "provider": "openai",
        "model": "gpt-5.4",
        "reasoning_effort": "high",
    }
    assert projection.current_focus == {
        "owner": "interviewer_runtime_service",
        "kind": "interview_question",
        "question": "What is the purpose of your travel?",
    }
    assert projection.phase_state == "interview"
    assert projection.interviewer_state["status"] == "verify_key_issue"
    assert projection.interviewer_state["current_risk_code"] == "supporting_evidence_missing"
    assert projection.turn_record == {
        "turn_id": "turn-user-1",
        "session_id": "sess-projector-1",
        "user_turn_id": "turn-user-1",
        "user_input": "I will study computer science.",
        "decision": "continue_interview",
        "assistant_message": "What is the purpose of your travel?",
        "requested_documents": [],
        "focus": {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "What is the purpose of your travel?",
        },
        "trace_refs": ["receive_input", "turn_decision"],
        "artifacts": [],
        "advisory_summary": {
            "risk_codes": ["supporting_evidence_missing"],
            "missing_evidence": [],
            "risk_level": "medium",
        },
    }


def test_project_uses_governor_requested_document_as_need_more_evidence_fallback() -> None:
    projector = InterviewerTurnProjectorService()
    score = _build_score(missing_evidence=["funding_proof"])
    record = _build_record("sess-projector-2")

    projection = projector.project(
        record=record,
        message_text="I can upload it later.",
        action=InterviewNextAction(
            assistant_message="Please upload your passport bio page.",
            requested_documents=[],
            decision_hint="need_more_evidence",
        ),
        score=score,
        governor_decision="need_more_evidence",
        governor_requested_documents=["passport_bio"],
        trace_entries=[RuntimeTraceEntry(node_name="turn_decision")],
        history_turn_count=1,
        history_turns=[SimpleNamespace(role="user", turn_id="turn-user-2")],
    )

    assert projection.response["requested_documents"] == ["passport_bio"]
    assert projection.current_focus == {
        "owner": "interviewer_runtime_service",
        "kind": "required_document",
        "document_type": "passport_bio",
    }
    assert projection.interviewer_state["requested_documents"] == ["passport_bio"]
    assert projection.turn_record["artifacts"] == [
        {
            "kind": "requested_document",
            "document_type": "passport_bio",
        }
    ]


def test_project_uses_public_refusal_message_for_response_and_focus_reason() -> None:
    projector = InterviewerTurnProjectorService()
    score = _build_score(risk_codes=["fraud_admission"])
    record = _build_record("sess-projector-3")

    projection = projector.project(
        record=record,
        message_text="I changed the document myself.",
        action=InterviewNextAction(
            assistant_message="internal refusal draft",
            requested_documents=[],
            decision_hint="simulated_refusal",
        ),
        score=score,
        governor_decision="simulated_refusal",
        governor_requested_documents=[],
        trace_entries=[RuntimeTraceEntry(node_name="turn_decision")],
        history_turn_count=2,
        history_turns=[SimpleNamespace(role="user", turn_id="turn-user-3")],
    )

    assert projection.response["assistant_message"] == (
        "当前记录已确认存在虚假陈述或伪造材料，系统给出模拟拒签结果，"
        "本次会话到此结束。"
    )
    assert projection.current_focus == {
        "owner": "interviewer_runtime_service",
        "kind": "refusal",
        "risk_code": "fraud_admission",
        "reason": (
            "当前记录已确认存在虚假陈述或伪造材料，系统给出模拟拒签结果，"
            "本次会话到此结束。"
        ),
    }
    assert projection.phase_state == "session_closed"
    assert projection.interviewer_state["status"] == "simulated_refusal"
    assert projection.interviewer_state["next_action"] == "review_refusal_result"
    assert projection.turn_record["assistant_message"] == (
        "当前记录已确认存在虚假陈述或伪造材料，系统给出模拟拒签结果，"
        "本次会话到此结束。"
    )


def test_project_appends_capability_artifacts_from_trace_metadata() -> None:
    projector = InterviewerTurnProjectorService()
    score = _build_score(missing_evidence=["funding_proof"])
    record = _build_record("sess-projector-capability")

    projection = projector.project(
        record=record,
        message_text="I can upload it later.",
        action=InterviewNextAction(
            assistant_message="Please upload your funding proof.",
            requested_documents=["funding_proof"],
            decision_hint="need_more_evidence",
        ),
        score=score,
        governor_decision="need_more_evidence",
        governor_requested_documents=["funding_proof"],
        trace_entries=[
            RuntimeTraceEntry(node_name="decide_capability"),
            RuntimeTraceEntry(
                node_name="resolve_capability",
                metadata={
                    "artifacts": [
                        {
                            "kind": "capability",
                            "capability_name": "document_assessment",
                            "status": "completed",
                            "feedback_status": "helpful",
                        }
                    ]
                },
            ),
            RuntimeTraceEntry(node_name="turn_decision"),
        ],
        history_turn_count=1,
        history_turns=[SimpleNamespace(role="user", turn_id="turn-user-capability")],
    )

    assert projection.turn_record["artifacts"] == [
        {
            "kind": "requested_document",
            "document_type": "funding_proof",
        },
        {
            "kind": "capability",
            "capability_name": "document_assessment",
            "status": "completed",
            "feedback_status": "helpful",
        },
    ]
