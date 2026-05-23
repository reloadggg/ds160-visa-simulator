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

    assert response["assistant_message"] == "What is the purpose of your travel?"
    assert response["governor_decision"] == "continue_interview"
    assert response["score_summary"] == {}
    assert response["requested_documents"] == []
    assert response["turn_decision"]["decision"] == "continue_interview"
    assert response["advisory_context"]["risk_codes"] == ["supporting_evidence_missing"]
    assert response["prompt_trace"] == {}
    assert response["turn_record"] == {
        "turn_id": "sess-1:pending-turn",
        "session_id": "sess-1",
        "user_input": "I will study computer science.",
        "decision": "continue_interview",
        "assistant_message": "What is the purpose of your travel?",
        "requested_documents": [],
        "remaining_required_documents": [],
        "focus": {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "What is the purpose of your travel?",
        },
        "trace_refs": ["receive_input"],
        "artifacts": [],
        "advisory_summary": {
            "risk_codes": ["supporting_evidence_missing"],
            "missing_evidence": [],
            "risk_level": "medium",
        },
        "document_review": {},
    }
    assert record.profile_json == profile.model_dump(mode="json")
    assert record.current_focus_json == {
        "owner": "interviewer_runtime_service",
        "kind": "interview_question",
        "question": "What is the purpose of your travel?",
    }
    assert record.phase_state == "interview"
    assert record.interviewer_state_json | {
        "advisory_context": record.interviewer_state_json["advisory_context"],
        "prompt_trace": record.interviewer_state_json["prompt_trace"],
    } == {
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
        "remaining_required_documents": [],
        "risk_codes": ["supporting_evidence_missing"],
        "history_turn_count": 0,
        "document_review": {},
        "advisory_context": {
            "score_summary": {
                "category_fit": 61,
                "document_readiness": 42,
                "narrative_consistency": 77,
                "confidence": 68,
            },
            "risk_codes": ["supporting_evidence_missing"],
            "missing_evidence": [],
            "risk_level": "medium",
            "missing_evidence_summary": None,
        },
        "prompt_trace": {},
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
            "interview",
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
        assert record.interviewer_state_json | {
            "advisory_context": record.interviewer_state_json["advisory_context"],
            "prompt_trace": record.interviewer_state_json["prompt_trace"],
        } == {
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
            "remaining_required_documents": list(action.requested_documents),
            "risk_codes": expected_risk_codes,
            "history_turn_count": 0,
            "document_review": {},
            "advisory_context": {
                "score_summary": {
                    "category_fit": score.category_fit,
                    "document_readiness": score.document_readiness,
                    "narrative_consistency": score.narrative_consistency,
                    "confidence": score.confidence,
                },
                "risk_codes": [
                    risk_flag.code for risk_flag in score.risk_flags
                ],
                "missing_evidence": list(score.missing_evidence),
                "risk_level": (
                    "medium"
                    if decision == "continue_interview"
                    else "none"
                    if decision == "need_more_evidence"
                    else "high"
                ),
                "missing_evidence_summary": (
                    ", ".join(score.missing_evidence) if score.missing_evidence else None
                ),
            },
            "prompt_trace": {},
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


def test_run_turn_builds_turn_record_from_latest_user_turn(monkeypatch) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-turn-record")
    score = _build_score(missing_evidence=["funding_proof"])
    record = SessionRecord(
        session_id="sess-turn-record",
        declared_family="f1",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
    )
    history_turns = [
        SimpleNamespace(role="assistant", turn_id="turn-old-assistant"),
        SimpleNamespace(role="user", turn_id="turn-user-latest"),
    ]

    monkeypatch.setattr(
        service.session_turn_repo,
        "list_session_turns",
        lambda session_id: history_turns,
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[
                RuntimeTraceEntry(node_name="receive_input"),
                RuntimeTraceEntry(node_name="turn_decision"),
            ],
        ),
    )
    monkeypatch.setattr(
        service,
        "_decide_governor",
        lambda current_record, current_profile, current_score, trace_entries, findings=None: {
            "decision": "need_more_evidence",
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": ["funding_proof"],
        },
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        lambda session_id, current_profile, current_score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="Please upload funding proof.",
            requested_documents=["funding_proof"],
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

    response = service.run_turn(record, "I can upload it later.")

    assert response["turn_record"] == {
        "turn_id": "turn-user-latest",
        "session_id": "sess-turn-record",
        "user_turn_id": "turn-user-latest",
        "user_input": "I can upload it later.",
        "decision": "need_more_evidence",
        "assistant_message": "Please upload funding proof.",
        "requested_documents": ["funding_proof"],
        "remaining_required_documents": ["funding_proof"],
        "focus": {
            "owner": "interviewer_runtime_service",
            "kind": "required_document",
            "document_type": "funding_proof",
        },
        "trace_refs": ["receive_input", "turn_decision"],
        "artifacts": [
            {
                "kind": "requested_document",
                "document_type": "funding_proof",
            }
        ],
        "advisory_summary": {
            "risk_codes": [],
            "missing_evidence": ["funding_proof"],
            "risk_level": "none",
        },
        "document_review": {},
    }


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


def test_window_style_guard_removes_coaching_phrase_from_interview_question() -> None:
    service = InterviewerRuntimeService(db=object())
    record = SessionRecord(
        session_id="sess-window-style",
        declared_family="f1",
        current_focus_json={
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "毕业后你打算回国做什么工作？",
        },
    )
    action = InterviewNextAction(
        decision="continue_interview",
        assistant_message="我听到了。具体一点，回国后你想做什么岗位？",
        requested_documents=[],
        focus_kind="interview_question",
    )

    polished = service._polish_window_interview_action(
        record,
        action,
        history_turns=[],
        latest_user_message="我读计算机相关的",
    )

    assert polished.assistant_message == "回国后你想做什么岗位？"
    assert "我听到了" not in polished.assistant_message
    assert "具体一点" not in polished.assistant_message
    assert polished.decision == "continue_interview"
    assert polished.reason == "window_interview_style_guard"


def test_window_style_guard_advances_repeated_f1_project_detail_question() -> None:
    service = InterviewerRuntimeService(db=object())
    record = SessionRecord(
        session_id="sess-project-loop",
        declared_family="f1",
        current_focus_json={
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "这个项目里哪一部分最能帮助你回国进高校任教？",
        },
    )
    action = InterviewNextAction(
        decision="continue_interview",
        assistant_message="我听到了。具体一点，这个项目的哪门课或哪项训练最适合你以后任教？",
        requested_documents=[],
        focus_kind="interview_question",
    )

    polished = service._polish_window_interview_action(
        record,
        action,
        history_turns=[
            SimpleNamespace(
                role="assistant",
                content="第一年的学费和生活费由谁支付？",
            ),
            SimpleNamespace(
                role="assistant",
                content="毕业后你打算回国做什么工作？",
            ),
        ],
        latest_user_message="这个学校的专业很厉害啊",
    )

    assert polished.assistant_message == "这个回答太笼统。你本科读的是什么专业？"
    assert "哪门课" not in polished.assistant_message
    assert "哪项训练" not in polished.assistant_message
    assert "我听到了" not in polished.assistant_message
    assert polished.decision == "continue_interview"


def test_window_style_guard_does_not_override_specific_project_answer() -> None:
    service = InterviewerRuntimeService(db=object())
    record = SessionRecord(
        session_id="sess-specific-project",
        declared_family="f1",
        current_focus_json={
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "这个项目里哪一部分最能帮助你回国进高校任教？",
        },
    )
    action = InterviewNextAction(
        decision="continue_interview",
        assistant_message="你计划重点学习哪门课程？",
        requested_documents=[],
        focus_kind="interview_question",
    )

    polished = service._polish_window_interview_action(
        record,
        action,
        history_turns=[],
        latest_user_message="我想重点学机器学习和统计建模，回国后教数据分析课程。",
    )

    assert polished == action


def test_run_turn_does_not_backfill_score_missing_document_when_action_keeps_it_empty(
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

    assert response["assistant_message"] == "Please provide the key supporting document for this point."
    assert response["requested_documents"] == []
    assert record.current_focus_json == {
        "owner": "interviewer_runtime_service",
        "kind": "required_document",
        "document_type": None,
    }


def test_run_turn_does_not_use_governor_requested_document_when_action_is_empty(
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

    assert response["assistant_message"] == "Please provide the key supporting document for this point."
    assert response["requested_documents"] == []
    assert response["score_summary"] == {}
    assert record.current_focus_json == {
        "owner": "interviewer_runtime_service",
        "kind": "required_document",
        "document_type": None,
    }


def test_decide_governor_defaults_to_continue_interview_hint() -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-high-risk")
    score = _build_score(risk_codes=["custom_high_signal"])
    trace_entries: list[RuntimeTraceEntry] = []
    record = SessionRecord(
        session_id="sess-high-risk",
        declared_family="f1",
    )

    governor = service._decide_governor(record, profile, score, trace_entries)

    assert governor["decision"] == "continue_interview"
    assert trace_entries == []


def test_decide_governor_does_not_reuse_prior_turn_decision_as_boundary() -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-record-conflict")
    score = _build_score(risk_codes=["record_conflict"])
    trace_entries: list[RuntimeTraceEntry] = []
    record = SessionRecord(
        session_id="sess-record-conflict",
        declared_family="f1",
        current_governor_decision="high_risk_review",
    )

    governor = service._decide_governor(record, profile, score, trace_entries)

    assert governor == {
        "decision": "continue_interview",
        "blocked_actions": [],
        "rationale_refs": [],
        "requested_documents": [],
    }


def test_decide_governor_no_longer_promotes_score_only_redline_to_refusal() -> None:
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


def test_decide_governor_no_longer_turns_confirmed_findings_into_hard_refusal() -> None:
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

    assert governor["decision"] == "continue_interview"


def test_run_turn_does_not_reuse_prior_focus_document_when_action_is_empty(
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

    assert response["assistant_message"] == "Please provide the key supporting document for this point."
    assert response["requested_documents"] == []
    assert record.current_focus_json == {
        "owner": "interviewer_runtime_service",
        "kind": "required_document",
        "document_type": None,
    }


def test_run_turn_does_not_persist_turn_decision_as_boundary_decision(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-boundary-split")
    score = _build_score(missing_evidence=["funding_proof"])
    record = SessionRecord(
        session_id="sess-boundary-split",
        declared_family="f1",
        current_governor_decision="continue_interview",
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
        service.interview_runtime,
        "build_question_action",
        lambda session_id, current_profile, current_score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="Please upload funding proof.",
            requested_documents=["funding_proof"],
            decision_hint="need_more_evidence",
        ),
    )
    captured_history: list[dict] = []
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: captured_history.append(kwargs) or current_record,
    )

    response = service.run_turn(record, "I will upload it later.")

    assert response["governor_decision"] == "continue_interview"
    assert response["turn_decision"]["decision"] == "need_more_evidence"
    assert record.current_governor_decision == "continue_interview"
    assert record.interviewer_state_json["decision"] == "need_more_evidence"
    assert record.interviewer_state_json["governor_decision"] == "continue_interview"
    assert captured_history[0]["governor_history"][0].decision == "continue_interview"


def test_run_turn_clears_ready_document_request_before_persisting_state(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-ready-doc")
    score = _build_score(missing_evidence=["funding_proof"])
    record = SessionRecord(
        session_id="sess-ready-doc",
        declared_family="f1",
        current_governor_decision="continue_interview",
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
        gate_status_json={
            "status": "ready_for_interview",
            "required_documents": [
                {
                    "document_type": "funding_proof",
                    "status": "ready",
                    "meets_minimum_fields": True,
                }
            ],
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
        service.interview_runtime,
        "build_question_action",
        lambda session_id, current_profile, current_score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="Please upload funding proof.",
            requested_documents=["funding_proof"],
            decision_hint="need_more_evidence",
        ),
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )

    response = service.run_turn(record, "I uploaded it.")

    assert response["governor_decision"] == "continue_interview"
    assert response["turn_decision"]["decision"] == "continue_interview"
    assert response["requested_documents"] == []
    assert record.current_focus_json == {
        "owner": "interviewer_runtime_service",
        "kind": "interview_question",
        "question": "这份材料我看到了。你这次赴美学习什么项目？",
    }


def test_run_turn_routes_repeated_claim_document_conflict_to_review(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-conflict")
    score = _build_score(risk_codes=["school_mismatch"])
    record = SessionRecord(
        session_id="sess-conflict",
        declared_family="f1",
        current_governor_decision="continue_interview",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "你最终入读哪一所学校？",
        },
        gate_status_json={
            "status": "ready_for_interview",
            "required_documents": [],
        },
    )
    claim_conflict = {
        "conflict_type": "claim_vs_document",
        "severity": "high",
        "summary": "申请人口头学校与 I-20 和录取信显示的学校不一致。",
        "field_paths": ["/education/school_name"],
        "document_ids": ["doc-i20", "doc-admission"],
        "evidence_refs": ["evi-i20", "evi-admission"],
    }
    prior_turns = [
        SimpleNamespace(
            role="assistant",
            metadata_json={"document_review": {"claim_conflicts": [claim_conflict]}},
        ),
        SimpleNamespace(role="user", metadata_json={}, content="我读 Claimed Example University"),
        SimpleNamespace(
            role="assistant",
            metadata_json={"turn_record": {"document_review": {"claim_conflicts": [claim_conflict]}}},
        ),
        SimpleNamespace(role="user", metadata_json={}, content="我还是读 Claimed Example University"),
    ]

    monkeypatch.setattr(
        service.session_turn_repo,
        "list_session_turns",
        lambda session_id: prior_turns,
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
            findings=[],
        ),
    )

    def fake_build_question_action(
        session_id,
        current_profile,
        current_score,
        governor_decision,
        trace_entries,
        recent_turns=None,
    ) -> InterviewNextAction:
        service.interview_runtime._last_capability_tool_outputs = {
            "document_review": {"claim_conflicts": [claim_conflict]},
        }
        return InterviewNextAction(
            assistant_message="你最终入读哪一所学校？",
            requested_documents=[],
            decision="continue_interview",
        )

    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        fake_build_question_action,
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )

    response = service.run_turn(record, "我读 Claimed Example University")

    assert response["governor_decision"] == "high_risk_review"
    assert response["turn_decision"]["decision"] == "high_risk_review"
    assert record.phase_state == "interview"
    assert record.interviewer_state_json["status"] == "high_risk_review"
    assert record.current_focus_json["kind"] == "risk_review"


def test_align_action_uses_high_severity_conflict_even_if_status_reviewed() -> None:
    service = InterviewerRuntimeService(db=object())
    action = InterviewNextAction(
        decision="continue_interview",
        assistant_message="我们继续普通面试问题。",
        requested_documents=[],
        focus_kind="interview_question",
    )

    aligned = service._align_action_with_document_review(
        action,
        capability_tool_outputs={
            "document_review": {
                "review_status": "reviewed",
                "cross_document_conflicts": [
                    {
                        "conflict_type": "document_vs_document",
                        "severity": "high",
                        "summary": "DS-160 与护照首页中的护照号码不一致。",
                        "document_ids": ["doc-ds160", "doc-passport"],
                        "field_paths": ["/identity/passport_number"],
                        "evidence_refs": [],
                    }
                ],
                "claim_conflicts": [],
            }
        },
    )

    assert aligned.decision == "high_risk_review"
    assert aligned.focus_kind == "risk_review"
    assert "护照号码不一致" in aligned.assistant_message


def test_run_turn_downgrades_repeated_non_redline_conflict_refusal_to_review(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-conflict-refusal")
    score = _build_score(risk_codes=["record_conflict"])
    record = SessionRecord(
        session_id="sess-conflict-refusal",
        declared_family="f1",
        current_governor_decision="continue_interview",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "你最终入读哪一所学校？",
        },
        gate_status_json={
            "status": "ready_for_interview",
            "required_documents": [],
        },
    )
    claim_conflict = {
        "conflict_type": "claim_vs_document",
        "severity": "high",
        "summary": "申请人口头学校与已提交学校材料不一致。",
        "field_paths": ["/education/school_name"],
        "document_ids": ["doc-i20"],
        "evidence_refs": ["evi-i20"],
    }
    prior_turns = [
        SimpleNamespace(
            role="assistant",
            metadata_json={"document_review": {"claim_conflicts": [claim_conflict]}},
        ),
        SimpleNamespace(role="user", metadata_json={}, content="我读 Claimed Example University"),
        SimpleNamespace(
            role="assistant",
            metadata_json={
                "turn_record": {"document_review": {"claim_conflicts": [claim_conflict]}}
            },
        ),
        SimpleNamespace(role="user", metadata_json={}, content="我还是读 Claimed Example University"),
    ]

    monkeypatch.setattr(
        service.session_turn_repo,
        "list_session_turns",
        lambda session_id: prior_turns,
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
            findings=[],
        ),
    )

    def fake_build_question_action(
        session_id,
        current_profile,
        current_score,
        governor_decision,
        trace_entries,
        recent_turns=None,
    ) -> InterviewNextAction:
        service.interview_runtime._last_capability_tool_outputs = {
            "document_review": {"claim_conflicts": [claim_conflict]},
        }
        return InterviewNextAction(
            assistant_message="This simulated case results in refusal.",
            requested_documents=[],
            decision="simulated_refusal",
        )

    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        fake_build_question_action,
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )

    response = service.run_turn(record, "我读 Claimed Example University")

    assert response["governor_decision"] == "high_risk_review"
    assert response["turn_decision"]["decision"] == "high_risk_review"
    assert record.phase_state == "interview"
    assert record.interviewer_state_json["status"] == "high_risk_review"
    assert record.current_focus_json["kind"] == "risk_review"


def test_run_turn_keeps_redline_refusal_even_with_repeated_claim_conflict(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-redline-refusal")
    score = _build_score(risk_codes=["fraud_admission"])
    record = SessionRecord(
        session_id="sess-redline-refusal",
        declared_family="f1",
        current_governor_decision="continue_interview",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
        gate_status_json={
            "status": "ready_for_interview",
            "required_documents": [],
        },
    )
    claim_conflict = {
        "conflict_type": "claim_vs_document",
        "severity": "high",
        "summary": "申请人口头说明与已提交材料不一致。",
        "field_paths": ["/education/school_name"],
        "document_ids": ["doc-i20"],
        "evidence_refs": ["evi-i20"],
    }
    prior_turns = [
        SimpleNamespace(
            role="assistant",
            metadata_json={"document_review": {"claim_conflicts": [claim_conflict]}},
        ),
        SimpleNamespace(role="user", metadata_json={}, content="我承认提交了虚假材料"),
        SimpleNamespace(
            role="assistant",
            metadata_json={
                "turn_record": {"document_review": {"claim_conflicts": [claim_conflict]}}
            },
        ),
        SimpleNamespace(role="user", metadata_json={}, content="我承认提交了虚假材料"),
    ]

    monkeypatch.setattr(
        service.session_turn_repo,
        "list_session_turns",
        lambda session_id: prior_turns,
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
            findings=[],
        ),
    )

    def fake_build_question_action(
        session_id,
        current_profile,
        current_score,
        governor_decision,
        trace_entries,
        recent_turns=None,
    ) -> InterviewNextAction:
        service.interview_runtime._last_capability_tool_outputs = {
            "document_review": {"claim_conflicts": [claim_conflict]},
        }
        return InterviewNextAction(
            assistant_message="This simulated case results in refusal.",
            requested_documents=[],
            decision="simulated_refusal",
        )

    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        fake_build_question_action,
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )

    response = service.run_turn(record, "我承认提交了虚假材料")

    assert response["governor_decision"] == "simulated_refusal"
    assert response["turn_decision"]["decision"] == "simulated_refusal"
    assert record.phase_state == "session_closed"


def test_run_turn_does_not_converge_different_claim_conflicts(
    monkeypatch,
) -> None:
    service = InterviewerRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-different-conflicts")
    score = _build_score(risk_codes=["record_conflict"])
    record = SessionRecord(
        session_id="sess-different-conflicts",
        declared_family="f1",
        current_governor_decision="continue_interview",
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
        gate_status_json={
            "status": "ready_for_interview",
            "required_documents": [],
        },
    )
    prior_school_conflict = {
        "conflict_type": "claim_vs_document",
        "severity": "high",
        "summary": "学校口头说明与材料不一致。",
        "field_paths": ["/education/school_name"],
        "document_ids": ["doc-i20"],
        "evidence_refs": ["evi-i20"],
    }
    prior_funding_conflict = {
        "conflict_type": "claim_vs_document",
        "severity": "high",
        "summary": "资金来源口头说明与材料不一致。",
        "field_paths": ["/funding/primary_source"],
        "document_ids": ["doc-funding"],
        "evidence_refs": ["evi-funding"],
    }
    current_sponsor_conflict = {
        "conflict_type": "claim_vs_document",
        "severity": "high",
        "summary": "资助人关系口头说明与材料不一致。",
        "field_paths": ["/funding/sponsor_relationship"],
        "document_ids": ["doc-relationship"],
        "evidence_refs": ["evi-relationship"],
    }
    prior_turns = [
        SimpleNamespace(
            role="assistant",
            metadata_json={"document_review": {"claim_conflicts": [prior_school_conflict]}},
        ),
        SimpleNamespace(role="user", metadata_json={}, content="学校说明"),
        SimpleNamespace(
            role="assistant",
            metadata_json={
                "turn_record": {
                    "document_review": {"claim_conflicts": [prior_funding_conflict]}
                }
            },
        ),
        SimpleNamespace(role="user", metadata_json={}, content="资金说明"),
    ]

    monkeypatch.setattr(
        service.session_turn_repo,
        "list_session_turns",
        lambda session_id: prior_turns,
    )
    monkeypatch.setattr(
        service.interview_runtime,
        "analyze_turn",
        lambda current_record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
            findings=[],
        ),
    )

    def fake_build_question_action(
        session_id,
        current_profile,
        current_score,
        governor_decision,
        trace_entries,
        recent_turns=None,
    ) -> InterviewNextAction:
        service.interview_runtime._last_capability_tool_outputs = {
            "document_review": {"claim_conflicts": [current_sponsor_conflict]},
        }
        return InterviewNextAction(
            assistant_message="请继续澄清资助人关系。",
            requested_documents=[],
            decision="continue_interview",
        )

    monkeypatch.setattr(
        service.interview_runtime,
        "build_question_action",
        fake_build_question_action,
    )
    monkeypatch.setattr(
        service.session_repo,
        "append_runtime_history",
        lambda current_record, **kwargs: current_record,
    )

    response = service.run_turn(record, "资助人关系说明")

    assert response["governor_decision"] == "continue_interview"
    assert response["turn_decision"]["decision"] == "continue_interview"
    assert record.current_focus_json["kind"] == "interview_question"
