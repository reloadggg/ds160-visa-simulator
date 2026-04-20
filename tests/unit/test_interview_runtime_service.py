from app.agents.schemas import InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState
from app.domain.runtime import RuntimeTraceEntry
from app.services.interview_runtime_service import InterviewRuntimeService


def test_analyze_turn_returns_helper_analysis_only(monkeypatch) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-1")
    record = SessionRecord(
        session_id="sess-1",
        declared_family="f1",
        profile_json=profile.model_dump(mode="json"),
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

    def fake_apply_message(updated_profile, message_text: str, recent_turns=None):
        assert message_text == "My parents will pay for my studies."
        updated_profile.funding["primary_source"] = "parents"
        return updated_profile

    monkeypatch.setattr(service.extractor, "apply_message", fake_apply_message)
    monkeypatch.setattr(service.consistency, "evaluate", lambda current_profile: [])
    monkeypatch.setattr(
        service.scoring,
        "propose",
        lambda current_profile, findings, scoring_stage: score,
    )

    analysis = service.analyze_turn(record, "My parents will pay for my studies.")

    assert analysis.profile.funding["primary_source"] == "parents"
    assert analysis.score is score
    assert [entry.node_name for entry in analysis.trace_entries] == [
        "receive_input",
        "extract_claims",
        "resolve_evidence",
        "consistency_check",
        "score_case",
    ]
    assert [entry.summary for entry in analysis.trace_entries] == [
        "user_message_received",
        "profile_version=2",
        "documented_refs=0",
        "findings=0",
        "missing=1 risk_flags=1",
    ]


def test_build_question_action_appends_trace(monkeypatch) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-1")
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    score.missing_evidence = ["funding_proof"]
    trace_entries = []

    monkeypatch.setattr(
        service,
        "_question_action",
        lambda session_id, current_profile, current_score, governor_decision, recent_turns=None: (
            InterviewNextAction(
                assistant_message="Please upload funding proof.",
                requested_documents=["funding_proof"],
                decision_hint="need_more_evidence",
            ),
            RuntimeTraceEntry(
                node_name="turn_decision",
                summary="decision=need_more_evidence",
            ),
        ),
    )

    action = service.build_question_action(
        "sess-1",
        profile,
        score,
        "need_more_evidence",
        trace_entries,
    )

    assert action == InterviewNextAction(
        assistant_message="Please upload funding proof.",
        requested_documents=["funding_proof"],
        decision_hint="need_more_evidence",
    )
    assert [entry.model_dump(mode="json") for entry in trace_entries] == [
        {
            "node_name": "turn_decision",
            "summary": "decision=need_more_evidence",
            "prompt_pack_id": None,
            "prompt_version": None,
            "provider": None,
            "model": None,
            "tool_calls": [],
            "turn_decision": None,
            "fallback_used": False,
            "retry_count": 0,
            "metadata": {},
        }
    ]


def test_fallback_continue_interview_recovers_to_document_request_when_evidence_is_missing() -> None:
    service = InterviewRuntimeService(db=object())
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    score.missing_evidence = ["funding_proof"]

    action = service._fallback_question_action(
        "continue_interview",
        score,
        recent_turns=None,
    )

    assert action == InterviewNextAction(
        assistant_message="Please provide the key supporting document for this point.",
        requested_documents=["funding_proof"],
        decision_hint="need_more_evidence",
    )


def test_fallback_need_more_evidence_uses_single_document_focus() -> None:
    service = InterviewRuntimeService(db=object())
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    score.missing_evidence = ["funding_proof"]

    action = service._fallback_question_action(
        "need_more_evidence",
        score,
        recent_turns=None,
    )

    assert action == InterviewNextAction(
        assistant_message="Please provide the key supporting document for this point.",
        requested_documents=["funding_proof"],
        decision_hint="need_more_evidence",
    )
