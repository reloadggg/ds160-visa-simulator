from types import SimpleNamespace

import pytest

from app.agents.schemas import InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState
from app.domain.runtime import RuntimeTraceEntry
from app.services.interviewer_runtime_service import InterviewerRuntimeService


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


def test_run_turn_persists_interviewer_owned_focus_and_state(monkeypatch) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-1")
    profile.profile_version = 2
    score = _build_score(risk_codes=["supporting_evidence_missing"])
    record = SessionRecord(
        session_id="sess-1",
        declared_family="f1",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
    )
    saved_records: list[str] = []
    user_turns: list[tuple[str, str, str, dict]] = []
    assistant_turns: list[tuple[str, str, str, dict]] = []
    events: list[str] = []

    monkeypatch.setattr(service.session_turn_repo, "list_session_turns", lambda session_id: [])
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[
                RuntimeTraceEntry(
                    node_name="receive_input",
                    summary="user_message_received",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        service,
        "_decide_governor",
        lambda current_record, current_profile, current_score, trace_entries, findings=None: {
            "decision": "continue_interview",
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": [],
        },
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        lambda session_id, current_profile, current_score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="What is the purpose of your travel?",
            requested_documents=[],
            decision_hint="continue_interview",
        ),
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )
    monkeypatch.setattr(
        service.session_repo,
        "save",
        lambda current_record: events.append("save")
        or saved_records.append(current_record.session_id)
        or current_record,
    )
    monkeypatch.setattr(
        service.session_turn_repo,
        "append_user_turn",
        lambda **kwargs: events.append("user_turn")
        or user_turns.append(
            (
                kwargs["session_id"],
                kwargs["content"],
                kwargs["source"],
                kwargs["metadata_json"],
            )
        ),
    )
    monkeypatch.setattr(
        service.session_turn_repo,
        "append_assistant_turn",
        lambda **kwargs: events.append("assistant_turn")
        or assistant_turns.append(
            (
                kwargs["session_id"],
                kwargs["content"],
                kwargs["source"],
                kwargs["metadata_json"],
            )
        ),
    )

    response = service.run_turn(record, "I will study computer science.")

    assert response == {
        "assistant_message": "What is the purpose of your travel?",
        "governor_decision": "continue_interview",
        "score_summary": {},
        "requested_documents": [],
    }
    assert record.profile_json == profile.model_dump(mode="json")
    assert record.current_focus_json == {
        "owner": "interviewer_runtime_service",
        "kind": "interview_question",
        "question": "What is the purpose of your travel?",
    }
    assert record.phase_state == "interview"
    assert record.interviewer_state_json == {
        "owner": "interviewer_runtime_service",
        "status": "verify_key_issue",
        "public_status": "verify_key_issue",
        "decision": "continue_interview",
        "governor_decision": "continue_interview",
        "next_action": "answer_question",
        "decision_hint": "continue_interview",
        "current_key_question": "What is the purpose of your travel?",
        "current_key_proof": None,
        "current_risk_code": "supporting_evidence_missing",
        "risk_level": "medium",
        "allowed_next_actions": [
            "answer_question",
            "clarify_key_issue",
        ],
        "requested_documents": [],
        "risk_codes": ["supporting_evidence_missing"],
        "history_turn_count": 0,
    }
    assert saved_records == []
    assert events == []
    assert user_turns == []
    assert assistant_turns == []


def test_run_turn_keeps_focus_under_single_owner_for_each_next_action(
    monkeypatch,
) -> None:
    cases = [
        (
            "continue_interview",
            _build_score(risk_codes=["supporting_evidence_missing"]),
            InterviewNextAction(
                assistant_message="What is the purpose of your travel?",
                requested_documents=[],
                decision_hint="continue_interview",
            ),
            "interview",
            {
                "owner": "interviewer_runtime_service",
                "kind": "interview_question",
                "question": "What is the purpose of your travel?",
            },
            ["supporting_evidence_missing"],
        ),
        (
            "need_more_evidence",
            _build_score(missing_evidence=["funding_proof", "passport_bio"]),
            InterviewNextAction(
                assistant_message="Please upload funding proof.",
                requested_documents=["funding_proof"],
                decision_hint="need_more_evidence",
            ),
            "gate_review",
            {
                "owner": "interviewer_runtime_service",
                "kind": "required_document",
                "document_type": "funding_proof",
            },
            [],
        ),
        (
            "high_risk_review",
            _build_score(risk_codes=["record_conflict"]),
            InterviewNextAction(
                assistant_message="This case needs additional review.",
                requested_documents=[],
                decision_hint="high_risk_review",
            ),
            "interview",
            {
                "owner": "interviewer_runtime_service",
                "kind": "risk_review",
                "risk_code": "record_conflict",
            },
            ["record_conflict"],
        ),
        (
            "simulated_refusal",
            _build_score(risk_codes=["fraud_admission"]),
            InterviewNextAction(
                assistant_message="This simulated case results in refusal.",
                requested_documents=[],
                decision_hint="simulated_refusal",
            ),
            "session_closed",
            {
                "owner": "interviewer_runtime_service",
                "kind": "refusal",
                "risk_code": "fraud_admission",
                "reason": "当前记录已确认存在虚假陈述或伪造材料，系统给出模拟拒签结果，本次会话到此结束。",
            },
            ["fraud_admission"],
        ),
    ]

    for index, (
        decision,
        score,
        action,
        expected_phase_state,
        expected_focus,
        expected_risk_codes,
    ) in enumerate(
        cases,
        start=1,
    ):
        service = InterviewerRuntimeService(db=object())
        profile = ApplicantProfile.minimal(f"profile-sess-{index}")
        profile.profile_version = 2
        record = SessionRecord(
            session_id=f"sess-{index}",
            declared_family="f1",
            profile_json={},
            runtime_trace_json=[],
            score_history_json=[],
            governor_history_json=[],
            interviewer_state_json={},
            current_focus_json={},
        )
        monkeypatch.setattr(
            service.session_turn_repo,
            "list_session_turns",
            lambda session_id: [object()],
        )
        monkeypatch.setattr(
            service.interview_runtime,
            "analyze_turn",
            lambda current_record, message_text, recent_turns, current_profile=profile, current_score=score: SimpleNamespace(
                profile=current_profile,
                score=current_score,
                trace_entries=[],
            ),
        )
        monkeypatch.setattr(
            service,
            "_decide_governor",
            lambda current_record, current_profile, current_score, trace_entries, current_decision=decision, findings=None: {
                "decision": current_decision,
                "blocked_actions": [],
                "rationale_refs": [],
                "requested_documents": list(current_score.missing_evidence),
            },
        )
        monkeypatch.setattr(
            service.interview_runtime,
            "build_question_action",
            lambda session_id, current_profile, current_score, governor_decision, trace_entries, recent_turns=None, current_action=action: current_action,
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

        response = service.run_turn(record, f"message-{index}")

        assert response["governor_decision"] == decision
        assert record.phase_state == expected_phase_state
        assert record.current_focus_json == expected_focus
        assert record.interviewer_state_json == {
            "owner": "interviewer_runtime_service",
            "status": (
                "verify_key_issue"
                if decision == "continue_interview"
                else "waiting_key_proof"
                if decision == "need_more_evidence"
                else decision
            ),
            "public_status": (
                "verify_key_issue"
                if decision == "continue_interview"
                else "waiting_key_proof"
                if decision == "need_more_evidence"
                else decision
            ),
            "decision": decision,
            "governor_decision": decision,
            "next_action": (
                "answer_question"
                if decision == "continue_interview"
                else "upload_key_proof"
                if decision == "need_more_evidence"
                else "wait_for_review"
                if decision == "high_risk_review"
                else "review_refusal_result"
            ),
            "decision_hint": decision,
            "current_key_question": (
                "What is the purpose of your travel?"
                if decision == "continue_interview"
                else None
            ),
            "current_key_proof": (
                "funding_proof"
                if decision == "need_more_evidence"
                else None
            ),
            "current_risk_code": expected_risk_codes[0] if expected_risk_codes else None,
            "risk_level": (
                "medium"
                if decision == "continue_interview"
                else "none"
                if decision == "need_more_evidence"
                else "high"
            ),
            "allowed_next_actions": (
                ["answer_question", "clarify_key_issue"]
                if decision == "continue_interview"
                else ["upload_key_proof", "explain_missing_proof"]
                if decision == "need_more_evidence"
                else ["wait_for_review"]
                if decision == "high_risk_review"
                else ["review_refusal_result"]
            ),
            "requested_documents": list(action.requested_documents),
            "risk_codes": expected_risk_codes,
            "history_turn_count": 0,
        }


def test_run_turn_rejects_legacy_analysis_fallback(monkeypatch) -> None:
    service = InterviewerRuntimeService(db=object())
    record = SessionRecord(
        session_id="sess-legacy",
        declared_family="f1",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
    )

    monkeypatch.setattr(service.session_turn_repo, "list_session_turns", lambda session_id: [])
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            trace_entries=[],
            current_governor_decision="need_more_evidence",
            response={
                "assistant_message": "Please upload funding proof.",
                "governor_decision": "need_more_evidence",
                "score_summary": {
                    "category_fit": 0,
                    "document_readiness": 0,
                    "narrative_consistency": 0,
                    "confidence": 0,
                },
                "requested_documents": ["funding_proof"],
            },
            interviewer_state={"risk_codes": ["legacy_risk"]},
        ),
    )

    with pytest.raises(ValueError, match="profile and score"):
        service.run_turn(record, "legacy message")


def test_run_turn_leaves_turn_persistence_to_message_service(monkeypatch) -> None:
    rollbacks: list[str] = []
    service = InterviewerRuntimeService(
        db=SimpleNamespace(rollback=lambda: rollbacks.append("rollback"))
    )
    profile = ApplicantProfile.minimal("profile-sess-1")
    score = _build_score()
    record = SessionRecord(
        session_id="sess-fail",
        declared_family="f1",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
    )
    events: list[str] = []

    monkeypatch.setattr(service.session_turn_repo, "list_session_turns", lambda session_id: [])
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
        ),
    )
    monkeypatch.setattr(
        service,
        "_decide_governor",
        lambda current_record, current_profile, current_score, trace_entries, findings=None: {
            "decision": "continue_interview",
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": [],
        },
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        lambda session_id, current_profile, current_score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="What is the purpose of your travel?",
            requested_documents=[],
            decision_hint="continue_interview",
        ),
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )
    monkeypatch.setattr(
        service.session_repo,
        "save",
        lambda current_record: events.append("save") or current_record,
    )
    monkeypatch.setattr(
        service.session_turn_repo,
        "append_user_turn",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("run_turn 不应直接写 user turn")),
    )
    monkeypatch.setattr(
        service.session_turn_repo,
        "append_assistant_turn",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("run_turn 不应直接写 assistant turn")),
    )

    response = service.run_turn(record, "failing message")

    assert response["assistant_message"] == "What is the purpose of your travel?"
    assert rollbacks == []
    assert events == []


def test_is_evasive_answer_uses_question_topic_instead_of_fixed_prompt_text() -> None:
    service = InterviewerRuntimeService(db=object())

    assert service._is_evasive_answer(
        "Who is funding your education?",
        "My program is computer science.",
    )
    assert not service._is_evasive_answer(
        "Who is funding your education?",
        "My parents are funding my education.",
    )


def test_is_evasive_answer_does_not_misclassify_generic_education_question_as_funding() -> None:
    service = InterviewerRuntimeService(db=object())

    assert not service._is_evasive_answer(
        "Tell me about your education history.",
        "I studied computer science at Tsinghua University.",
    )


def test_run_turn_selects_requested_document_in_owner_when_action_keeps_it_empty(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-owner-doc")
    score = _build_score(missing_evidence=["funding_proof"])
    record = SessionRecord(
        session_id="sess-owner-doc",
        declared_family="f1",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
    )

    monkeypatch.setattr(service.session_turn_repo, "list_session_turns", lambda session_id: [])
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
        ),
    )
    monkeypatch.setattr(
        service,
        "_decide_governor",
        lambda current_record, current_profile, current_score, trace_entries, findings=None: {
            "decision": "need_more_evidence",
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": list(current_score.missing_evidence),
        },
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        lambda session_id, current_profile, current_score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="Please provide the key supporting document for this point.",
            requested_documents=[],
            decision_hint="need_more_evidence",
        ),
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )
    monkeypatch.setattr(service.session_repo, "save", lambda current_record: current_record)
    monkeypatch.setattr(service.session_turn_repo, "append_user_turn", lambda **kwargs: None)
    monkeypatch.setattr(service.session_turn_repo, "append_assistant_turn", lambda **kwargs: None)

    response = service.run_turn(record, "I will upload it later.")

    assert response["assistant_message"] == "Please upload funding proof."
    assert response["requested_documents"] == ["funding_proof"]
    assert record.current_focus_json == {
        "owner": "interviewer_runtime_service",
        "kind": "required_document",
        "document_type": "funding_proof",
    }


def test_run_turn_uses_governor_requested_document_when_action_and_score_are_empty(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-governor-doc")
    score = _build_score()
    record = SessionRecord(
        session_id="sess-governor-doc",
        declared_family="f1",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
    )

    monkeypatch.setattr(service.session_turn_repo, "list_session_turns", lambda session_id: [])
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
        ),
    )
    monkeypatch.setattr(
        service,
        "_decide_governor",
        lambda current_record, current_profile, current_score, trace_entries, findings=None: {
            "decision": "need_more_evidence",
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": ["passport_bio"],
        },
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        lambda session_id, current_profile, current_score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="Please provide the key supporting document for this point.",
            requested_documents=[],
            decision_hint="need_more_evidence",
        ),
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )
    monkeypatch.setattr(service.session_repo, "save", lambda current_record: current_record)
    monkeypatch.setattr(service.session_turn_repo, "append_user_turn", lambda **kwargs: None)
    monkeypatch.setattr(service.session_turn_repo, "append_assistant_turn", lambda **kwargs: None)

    response = service.run_turn(record, "I can upload it next.")

    assert response["assistant_message"] == "Please upload passport bio page."
    assert response["requested_documents"] == ["passport_bio"]
    assert response["score_summary"] == {}
    assert record.current_focus_json == {
        "owner": "interviewer_runtime_service",
        "kind": "required_document",
        "document_type": "passport_bio",
    }


def test_decide_governor_only_routes_explicit_high_risk_review_signals(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-high-risk")
    score = _build_score(risk_codes=["custom_high_signal"])
    trace_entries: list[RuntimeTraceEntry] = []
    record = SessionRecord(
        session_id="sess-high-risk",
        declared_family="f1",
    )

    monkeypatch.setattr(
        service.governor,
        "decide",
        lambda current_profile, current_score, early_term_candidate: {
            "decision": "continue_interview",
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": [],
        },
    )

    governor = service._decide_governor(record, profile, score, trace_entries)

    assert governor["decision"] == "continue_interview"
    assert trace_entries[-1].summary == "decision=continue_interview"


def test_decide_governor_routes_record_conflict_to_high_risk_review(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-record-conflict")
    score = _build_score(risk_codes=["record_conflict"])
    trace_entries: list[RuntimeTraceEntry] = []
    record = SessionRecord(
        session_id="sess-record-conflict",
        declared_family="f1",
    )

    monkeypatch.setattr(
        service.governor,
        "decide",
        lambda current_profile, current_score, early_term_candidate: {
            "decision": "continue_interview",
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": [],
        },
    )

    governor = service._decide_governor(record, profile, score, trace_entries)

    assert governor == {
        "decision": "high_risk_review",
        "blocked_actions": ["high_risk_review_signal"],
        "rationale_refs": ["msg:last_user_turn"],
        "requested_documents": [],
    }


def test_decide_governor_does_not_allow_score_only_redline_to_trigger_refusal() -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-score-only-redline")
    score = _build_score(risk_codes=["hard_conflict"])
    trace_entries: list[RuntimeTraceEntry] = []
    record = SessionRecord(
        session_id="sess-score-only-redline",
        declared_family="f1",
    )

    governor = service._decide_governor(
        record,
        profile,
        score,
        trace_entries,
        findings=[],
    )

    assert governor["decision"] == "continue_interview"


def test_decide_governor_allows_refusal_only_when_owner_findings_confirm_redline(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-owner-redline")
    score = _build_score(risk_codes=["hard_conflict"])
    trace_entries: list[RuntimeTraceEntry] = []
    record = SessionRecord(
        session_id="sess-owner-redline",
        declared_family="f1",
    )

    governor = service._decide_governor(
        record,
        profile,
        score,
        trace_entries,
        findings=[
            {
                "finding_type": "hard_conflict",
                "severity": "high",
                "status": "confirmed",
                "evidence_refs": ["msg:last_user_turn"],
            }
        ],
    )

    assert governor["decision"] == "simulated_refusal"


def test_run_turn_keeps_prior_focus_document_when_only_focus_can_name_the_key_proof(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-focus-doc")
    score = _build_score()
    record = SessionRecord(
        session_id="sess-focus-doc",
        declared_family="f1",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={
            "owner": "interviewer_runtime_service",
            "kind": "required_document",
            "document_type": "funding_proof",
        },
    )

    monkeypatch.setattr(service.session_turn_repo, "list_session_turns", lambda session_id: [])
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
        ),
    )
    monkeypatch.setattr(
        service,
        "_decide_governor",
        lambda current_record, current_profile, current_score, trace_entries, findings=None: {
            "decision": "need_more_evidence",
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": [],
        },
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        lambda session_id, current_profile, current_score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="Please provide the key supporting document for this point.",
            requested_documents=[],
            decision_hint="need_more_evidence",
        ),
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )
    monkeypatch.setattr(service.session_repo, "save", lambda current_record: current_record)
    monkeypatch.setattr(service.session_turn_repo, "append_user_turn", lambda **kwargs: None)
    monkeypatch.setattr(service.session_turn_repo, "append_assistant_turn", lambda **kwargs: None)

    response = service.run_turn(record, "I still need more time.")

    assert response["assistant_message"] == "Please upload funding proof."
    assert response["requested_documents"] == ["funding_proof"]
    assert record.current_focus_json == {
        "owner": "interviewer_runtime_service",
        "kind": "required_document",
        "document_type": "funding_proof",
    }
