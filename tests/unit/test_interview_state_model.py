from types import SimpleNamespace

from app.agents.schemas import InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState
from app.services.report_service import ReportService
from app.services.interviewer_runtime_service import InterviewerRuntimeService


def _build_score(
    *,
    risk_code: str | None = None,
    missing_evidence: list[str] | None = None,
    severity: str = "medium",
    status: str = "supported",
) -> ScoreState:
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    score.category_fit = 70
    score.document_readiness = 55
    score.narrative_consistency = 72
    score.confidence = 60
    score.missing_evidence = list(missing_evidence or [])
    if risk_code is not None:
        score.risk_flags = [
            RiskFlag(
                code=risk_code,
                severity=severity,
                status=status,
                evidence_refs=["msg:last_user_turn"] if severity == "high" else [],
            )
        ]
    return score


def _build_record(session_id: str) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        declared_family="f1",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
    )


def _run_turn(
    monkeypatch,
    *,
    decision: str,
    action: InterviewNextAction,
    score: ScoreState,
    session_id: str = "sess-1",
) -> SessionRecord:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal(f"profile-{session_id}")
    record = _build_record(session_id)

    monkeypatch.setattr(
        service.session_turn_repo,
        "list_session_turns",
        lambda current_session_id: [object()],
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns=None: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
        ),
    )
    monkeypatch.setattr(
        service,
        "_decide_governor",
        lambda current_record, current_profile, current_score, trace_entries, findings=None: {
            "decision": decision,
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": list(action.requested_documents),
        },
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        lambda *args, **kwargs: action,
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )
    monkeypatch.setattr(service.session_repo, "save", lambda current_record: current_record)
    monkeypatch.setattr(service.session_turn_repo, "append_user_turn", lambda **kwargs: None)
    monkeypatch.setattr(
        service.session_turn_repo,
        "append_assistant_turn",
        lambda **kwargs: None,
    )

    service.run_turn(record, "user message")
    return record


def test_interviewer_state_persists_key_question_and_next_actions(monkeypatch) -> None:
    record = _run_turn(
        monkeypatch,
        decision="continue_interview",
        action=InterviewNextAction(
            assistant_message="你为什么选择这所学校？",
            requested_documents=[],
            decision_hint="continue_interview",
        ),
        score=_build_score(),
    )

    assert record.interviewer_state_json["status"] == "continue_interview"
    assert record.interviewer_state_json["current_key_question"] == "你为什么选择这所学校？"
    assert record.interviewer_state_json["current_key_proof"] is None
    assert record.interviewer_state_json["risk_level"] == "none"
    assert record.interviewer_state_json["allowed_next_actions"] == [
        "answer_question",
        "continue_interview",
    ]


def test_interviewer_state_persists_key_proof_when_waiting_for_document(monkeypatch) -> None:
    record = _run_turn(
        monkeypatch,
        decision="need_more_evidence",
        action=InterviewNextAction(
            assistant_message="请补充你父母的资金证明。",
            requested_documents=["parent_funding_proof"],
            decision_hint="need_more_evidence",
        ),
        score=_build_score(missing_evidence=["parent_funding_proof"]),
        session_id="sess-proof",
    )

    assert record.interviewer_state_json["status"] == "waiting_key_proof"
    assert record.interviewer_state_json["current_key_question"] is None
    assert record.interviewer_state_json["current_key_proof"] == "parent_funding_proof"
    assert record.interviewer_state_json["allowed_next_actions"] == [
        "upload_key_proof",
        "explain_missing_proof",
    ]


def test_high_risk_review_and_simulated_refusal_are_distinct_states(monkeypatch) -> None:
    high_risk_record = _run_turn(
        monkeypatch,
        decision="high_risk_review",
        action=InterviewNextAction(
            assistant_message="你的资金来源存在冲突，需要进一步核验。",
            requested_documents=[],
            decision_hint="high_risk_review",
        ),
        score=_build_score(risk_code="funding_conflict", severity="high", status="confirmed"),
        session_id="sess-high-risk",
    )
    refusal_record = _run_turn(
        monkeypatch,
        decision="simulated_refusal",
        action=InterviewNextAction(
            assistant_message="当前记录已形成模拟拒签结论。",
            requested_documents=[],
            decision_hint="simulated_refusal",
        ),
        score=_build_score(risk_code="fraud_admission", severity="high", status="confirmed"),
        session_id="sess-refusal",
    )

    assert high_risk_record.interviewer_state_json["status"] == "high_risk_review"
    assert high_risk_record.interviewer_state_json["risk_level"] == "high"
    assert refusal_record.interviewer_state_json["status"] == "simulated_refusal"
    assert refusal_record.interviewer_state_json["risk_level"] == "high"
    assert high_risk_record.interviewer_state_json["status"] != refusal_record.interviewer_state_json["status"]


def test_user_report_exposes_simple_status_for_high_risk_and_refusal() -> None:
    service = ReportService()

    high_risk_payload = service.user_report(
        session_id="sess-high-risk",
        visa_family="f1",
        governor_decision="high_risk_review",
        profile_json={"funding": {"primary_source": "self"}},
        phase_state="interview",
        interviewer_state_json={
            "status": "high_risk_review",
            "public_status": "high_risk_review",
            "risk_level": "high",
            "current_key_question": None,
            "current_key_proof": "bank_statement",
        },
    )
    refusal_payload = service.user_report(
        session_id="sess-refusal",
        visa_family="f1",
        governor_decision="simulated_refusal",
        profile_json={"funding": {"primary_source": "self"}},
        phase_state="interview",
        interviewer_state_json={
            "status": "simulated_refusal",
            "public_status": "simulated_refusal",
            "risk_level": "high",
            "current_key_question": None,
            "current_key_proof": None,
        },
    )

    assert high_risk_payload["interview_status"] == "high_risk_review"
    assert high_risk_payload["outcome_label"] == "高风险待复核"
    assert refusal_payload["interview_status"] == "simulated_refusal"
    assert refusal_payload["outcome_label"] == "模拟拒签结果"


def test_user_report_prefers_requested_documents_from_state_snapshot() -> None:
    service = ReportService()

    payload = service.user_report(
        session_id="sess-proof-priority",
        visa_family="f1",
        governor_decision="need_more_evidence",
        profile_json={"funding": {"primary_source": "parents"}},
        phase_state="interview",
        interviewer_state_json={
            "status": "waiting_key_proof",
            "public_status": "waiting_key_proof",
            "requested_documents": ["bank_statement", "sponsor_letter"],
            "current_key_proof": "bank_statement",
        },
    )

    assert payload["interview_status"] == "waiting_key_proof"
    assert payload["missing_evidence"] == ["bank_statement", "sponsor_letter"]
    assert payload["current_key_proof"] == "bank_statement"


def test_user_report_route_correction_falls_back_to_verify_key_issue() -> None:
    service = ReportService()

    payload = service.user_report(
        session_id="sess-route",
        visa_family="f1",
        governor_decision="route_correction",
        profile_json={"funding": {"primary_source": "self"}},
        phase_state="interview",
        interviewer_state_json={},
    )

    assert payload["interview_status"] == "verify_key_issue"
    assert payload["risk_level"] == "medium"
    assert payload["outcome_label"] == "需核验关键问题"
