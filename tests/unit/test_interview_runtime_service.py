from app.agents.schemas import InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState
from app.services.interview_runtime_service import InterviewRuntimeService


def test_run_turn_records_runtime_trace_and_histories(monkeypatch) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-1")
    record = SessionRecord(
        session_id="sess-1",
        declared_family="f1",
        profile_json=profile.model_dump(mode="json"),
        runtime_trace_json=[{"node_name": "existing", "summary": "old"}],
        score_history_json=[
            {
                "scoring_stage": "previous_turn",
                "category_fit": 10,
                "document_readiness": 20,
                "narrative_consistency": 30,
                "confidence": 40,
                "missing_evidence": ["passport_bio"],
                "risk_flags": [],
                "summary": "old score",
            }
        ],
        governor_history_json=[
            {"decision": "continue_interview", "summary": "old governor"}
        ],
    )
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    score.category_fit = 61
    score.document_readiness = 42
    score.narrative_consistency = 77
    score.confidence = 68
    score.missing_evidence = ["funding_proof"]
    score.risk_flags = [
        RiskFlag(
            code="supporting_evidence_missing",
            severity="medium",
            status="supported",
            evidence_refs=[],
        )
    ]

    def fake_apply_message(updated_profile, message_text: str):
        assert message_text == "My parents will pay for my studies."
        updated_profile.funding["primary_source"] = "parents"
        return updated_profile

    monkeypatch.setattr(service.extractor, "apply_message", fake_apply_message)
    monkeypatch.setattr(service.consistency, "evaluate", lambda profile: [])
    monkeypatch.setattr(service.scoring, "propose", lambda profile, findings, scoring_stage: score)
    monkeypatch.setattr(service.session_repo, "save", lambda updated_record: updated_record)
    monkeypatch.setattr(
        service.governor,
        "decide",
        lambda profile, score_state, early_term_candidate: {
            "decision": "need_more_evidence",
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": ["funding_proof"],
        },
    )
    monkeypatch.setattr(
        service,
        "_question_action",
        lambda session_id, profile, score_state, governor_decision: InterviewNextAction(
            assistant_message="Please upload funding proof.",
            requested_documents=["funding_proof"],
            decision_hint="need_more_evidence",
        ),
    )

    response = service.run_turn(
        record,
        "My parents will pay for my studies.",
    )

    assert response == {
        "assistant_message": "Please upload funding proof.",
        "governor_decision": "need_more_evidence",
        "score_summary": {
            "category_fit": 61,
            "document_readiness": 42,
            "narrative_consistency": 77,
            "confidence": 68,
        },
        "requested_documents": ["funding_proof"],
    }
    assert [entry["node_name"] for entry in record.runtime_trace_json] == [
        "existing",
        "receive_input",
        "extract_claims",
        "resolve_evidence",
        "consistency_check",
        "score_case",
        "governor_decide",
        "build_next_action",
    ]
    assert record.runtime_trace_json[-7:] == [
        {"node_name": "receive_input", "summary": "user_message_received"},
        {"node_name": "extract_claims", "summary": "profile_version=2"},
        {"node_name": "resolve_evidence", "summary": "documented_refs=0"},
        {"node_name": "consistency_check", "summary": "findings=0"},
        {"node_name": "score_case", "summary": "missing=1 risk_flags=1"},
        {"node_name": "governor_decide", "summary": "decision=need_more_evidence"},
        {"node_name": "build_next_action", "summary": "requested_documents=1"},
    ]
    assert record.score_history_json[-1] == {
        "scoring_stage": "interview_turn",
        "category_fit": 61,
        "document_readiness": 42,
        "narrative_consistency": 77,
        "confidence": 68,
        "missing_evidence": ["funding_proof"],
        "risk_flags": [
            {
                "code": "supporting_evidence_missing",
                "severity": "medium",
                "status": "supported",
                "evidence_refs": [],
            }
        ],
        "summary": "missing=1 risk_flags=1",
    }
    assert record.governor_history_json[-1] == {
        "decision": "need_more_evidence",
        "summary": "decision=need_more_evidence",
    }
