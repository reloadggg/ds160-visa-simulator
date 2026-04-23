from types import SimpleNamespace

from app.agents.schemas import InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, FieldState, RiskFlag, ScoreState
from app.domain.runtime import RuntimeTraceEntry
from app.platform.runtime_ledger import RuntimeViewState, SessionLedger, SessionReadModel
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


def test_build_question_action_appends_capability_trace_before_turn_decision(
    monkeypatch,
) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-cap")
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    trace_entries = []

    def fake_question_action(
        session_id,
        current_profile,
        current_score,
        governor_decision,
        recent_turns=None,
    ):
        service._last_capability_trace_entries = [
            RuntimeTraceEntry(node_name="decide_capability", summary="planned=evidence_retrieval"),
            RuntimeTraceEntry(node_name="resolve_capability", summary="resolved=evidence_retrieval"),
        ]
        return (
            InterviewNextAction(
                assistant_message="What is the purpose of your travel?",
                requested_documents=[],
                decision_hint="continue_interview",
            ),
            RuntimeTraceEntry(
                node_name="turn_decision",
                summary="decision=continue_interview",
            ),
        )

    monkeypatch.setattr(service, "_question_action", fake_question_action)

    service.build_question_action(
        "sess-cap",
        profile,
        score,
        "continue_interview",
        trace_entries,
    )

    assert [entry.node_name for entry in trace_entries] == [
        "decide_capability",
        "resolve_capability",
        "turn_decision",
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


def test_build_dynamic_turn_context_includes_phase3_structured_fields(
    monkeypatch,
) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-1")
    profile.profile_version = 3
    profile.visa_intent["purpose"] = "study"
    profile.education["school_name"] = "Test University"
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"].state = FieldState.DOCUMENTED
    profile.field_provenance["/funding/primary_source"].evidence_refs = ["doc:bank"]
    score = ScoreState.minimal(profile_version=3, scoring_stage="interview_turn")
    score.missing_evidence = ["i20"]
    score.risk_flags = [
        RiskFlag(
            code="supporting_evidence_missing",
            severity="medium",
            status="supported",
            evidence_refs=[],
        )
    ]
    record = SessionRecord(
        session_id="sess-1",
        declared_family="f1",
        phase_state="interview",
        current_governor_decision="need_more_evidence",
        gate_status_json={"status": "ready_for_interview"},
    )
    read_model = SessionReadModel(
        session_id="sess-1",
        phase_state="interview",
        declared_family="f1",
        current_governor_decision="need_more_evidence",
        runtime_ledger=SessionLedger(
            session_id="sess-1",
            phase_state="interview",
        ),
        runtime_view_state=RuntimeViewState(
            source_turn_id="turn-a1",
            decision="need_more_evidence",
            governor_decision="need_more_evidence",
            current_focus={
                "owner": "interviewer_runtime_service",
                "kind": "required_document",
                "document_type": "funding_proof",
            },
            current_key_proof="funding_proof",
            current_risk_code="supporting_evidence_missing",
            requested_documents=["funding_proof"],
            allowed_next_actions=["upload_key_proof"],
            advisory_context={"risk_codes": ["supporting_evidence_missing"]},
            prompt_trace={"model": "gpt-test"},
        ),
    )
    monkeypatch.setattr(service.session_repo, "get", lambda session_id: record)
    monkeypatch.setattr(
        service.document_repo,
        "list_session_documents",
        lambda session_id: [],
    )
    monkeypatch.setattr(
        service.session_read_model,
        "build_from_record",
        lambda current_record, turns=None: read_model,
    )

    payload = service._build_dynamic_turn_context(
        session_id="sess-1",
        profile=profile,
        score=score,
        governor_decision="continue_interview",
        recent_turns=[
            SimpleNamespace(role="user", content="My parents will sponsor me."),
        ],
        latest_user_message="My parents will sponsor me.",
        declared_family="f1",
    )

    assert payload["case_brief"] == {
        "declared_family": "f1",
        "phase_state": "interview",
        "boundary_decision": "continue_interview",
        "last_turn_decision": "need_more_evidence",
        "profile_version": 3,
        "travel_purpose": "study",
        "school_name": "Test University",
        "funding_source": "parents",
    }
    assert payload["focus_thread"] == {
        "current_focus": {
            "owner": "interviewer_runtime_service",
            "kind": "required_document",
            "document_type": "funding_proof",
        },
        "last_turn_decision": "need_more_evidence",
        "public_status": None,
        "current_key_question": None,
        "current_key_proof": "funding_proof",
        "current_risk_code": "supporting_evidence_missing",
        "requested_documents": ["funding_proof"],
        "allowed_next_actions": ["upload_key_proof"],
    }
    assert payload["evidence_digest"] == {
        "missing_evidence": ["i20"],
        "requested_documents": ["funding_proof"],
        "current_focus_document_type": "funding_proof",
        "documented_field_paths": ["/funding/primary_source"],
        "evidence_refs": ["doc:bank"],
        "supported_claims": [],
        "active_main_flow_feedback": {},
        "uploaded_document_count": 0,
        "uploaded_documents": [],
    }
    assert payload["memory_strata"]["facts_memory"]["funding_source"] == "parents"
    assert payload["memory_strata"]["derived_memory"]["risk_codes"] == [
        "supporting_evidence_missing"
    ]
    assert payload["current_focus"]["document_type"] == "funding_proof"
    assert payload["last_turn_decision"] == "need_more_evidence"
    assert payload["gate_progress"] == {"status": "ready_for_interview"}


def test_build_dynamic_turn_context_compresses_older_turns_into_history_summary(
    monkeypatch,
) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-2")
    score = ScoreState.minimal(profile_version=1, scoring_stage="interview_turn")
    record = SessionRecord(
        session_id="sess-2",
        declared_family="f1",
        phase_state="interview",
        current_governor_decision="continue_interview",
        gate_status_json={},
    )
    read_model = SessionReadModel(
        session_id="sess-2",
        phase_state="interview",
        declared_family="f1",
        current_governor_decision="continue_interview",
        runtime_ledger=SessionLedger(
            session_id="sess-2",
            phase_state="interview",
        ),
        runtime_view_state=RuntimeViewState(
            source_turn_id=None,
            decision="continue_interview",
            governor_decision="continue_interview",
        ),
    )
    monkeypatch.setattr(service.session_repo, "get", lambda session_id: record)
    monkeypatch.setattr(
        service.document_repo,
        "list_session_documents",
        lambda session_id: [],
    )
    monkeypatch.setattr(
        service.session_read_model,
        "build_from_record",
        lambda current_record, turns=None: read_model,
    )
    recent_turns = [
        SimpleNamespace(
            role="assistant",
            content="Please upload funding proof.",
            metadata_json={
                "turn_record": {
                    "decision": "need_more_evidence",
                    "requested_documents": ["funding_proof"],
                }
            },
        ),
        SimpleNamespace(role="user", content="Here is my bank statement."),
        SimpleNamespace(role="assistant", content="Thanks."),
        SimpleNamespace(role="user", content="It shows parent funds."),
        SimpleNamespace(role="assistant", content="Which school admitted you?"),
        SimpleNamespace(role="user", content="Test University."),
        SimpleNamespace(role="assistant", content="Why this school?"),
        SimpleNamespace(role="user", content="It fits my program."),
    ]

    payload = service._build_dynamic_turn_context(
        session_id="sess-2",
        profile=profile,
        score=score,
        governor_decision="continue_interview",
        recent_turns=recent_turns,
        latest_user_message="It fits my program.",
        declared_family="f1",
    )

    assert len(payload["recent_turns"]) == 6
    assert payload["compression"] == {
        "strategy": "recent_turns_tail+history_summary",
        "recent_turn_window": 6,
        "retained_turn_count": 6,
        "summarized_turn_count": 2,
    }
    assert payload["history_summary"] == {
        "summarized_turn_count": 2,
        "summarized_user_turn_count": 1,
        "summarized_assistant_turn_count": 1,
        "prior_decisions": ["need_more_evidence"],
        "prior_requested_documents": ["funding_proof"],
    }


def test_build_dynamic_turn_context_includes_uploaded_document_feedback_in_evidence_digest(
    monkeypatch,
) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("profile-sess-3")
    score = ScoreState.minimal(profile_version=1, scoring_stage="interview_turn")
    score.missing_evidence = ["funding_proof"]
    record = SessionRecord(
        session_id="sess-3",
        declared_family="f1",
        phase_state="interview",
        current_governor_decision="need_more_evidence",
        gate_status_json={"status": "ready_for_interview"},
    )
    read_model = SessionReadModel(
        session_id="sess-3",
        phase_state="interview",
        declared_family="f1",
        current_governor_decision="need_more_evidence",
        runtime_ledger=SessionLedger(
            session_id="sess-3",
            phase_state="interview",
        ),
        runtime_view_state=RuntimeViewState(
            source_turn_id="turn-a3",
            decision="need_more_evidence",
            governor_decision="need_more_evidence",
            current_focus={
                "owner": "interviewer_runtime_service",
                "kind": "required_document",
                "document_type": "funding_proof",
            },
            current_key_proof="funding_proof",
            requested_documents=["funding_proof"],
        ),
    )
    document_records = [
        SimpleNamespace(
            document_id="doc-helpful",
            filename="funding-proof.pdf",
            status="uploaded",
            artifact_json={
                "document_assessment": {
                    "document_type": "funding_proof",
                    "supported_claims": ["/funding/primary_source"],
                    "main_flow_feedback": {
                        "status": "helpful",
                        "supported_document_type": "funding_proof",
                        "current_focus_document_type": "funding_proof",
                        "message": "这份材料对当前关键证明 funding_proof 有帮助。",
                    },
                }
            },
        ),
        SimpleNamespace(
            document_id="doc-secondary",
            filename="passport.pdf",
            status="uploaded",
            artifact_json={
                "document_assessment": {
                    "document_type": "passport_bio",
                    "supported_claims": ["/identity/passport_number"],
                    "main_flow_feedback": {
                        "status": "not_helpful",
                        "current_focus_document_type": "funding_proof",
                        "message": "这份材料对当前主线没有直接帮助。",
                    },
                }
            },
        ),
    ]
    monkeypatch.setattr(service.session_repo, "get", lambda session_id: record)
    monkeypatch.setattr(
        service.document_repo,
        "list_session_documents",
        lambda session_id: document_records,
    )
    monkeypatch.setattr(
        service.session_read_model,
        "build_from_record",
        lambda current_record, turns=None: read_model,
    )

    payload = service._build_dynamic_turn_context(
        session_id="sess-3",
        profile=profile,
        score=score,
        governor_decision="continue_interview",
        recent_turns=[
            SimpleNamespace(role="user", content="I uploaded the file."),
        ],
        latest_user_message="I uploaded the file.",
        declared_family="f1",
    )

    assert payload["evidence_digest"] == {
        "missing_evidence": ["funding_proof"],
        "requested_documents": ["funding_proof"],
        "current_focus_document_type": "funding_proof",
        "documented_field_paths": [],
        "evidence_refs": [],
        "supported_claims": [
            "/funding/primary_source",
            "/identity/passport_number",
        ],
        "active_main_flow_feedback": {
            "status": "helpful",
            "supported_document_type": "funding_proof",
            "current_focus_document_type": "funding_proof",
            "message": "这份材料对当前关键证明 funding_proof 有帮助。",
            "document_id": "doc-helpful",
            "filename": "funding-proof.pdf",
            "document_type": "funding_proof",
            "supported_claims": ["/funding/primary_source"],
        },
        "uploaded_document_count": 2,
        "uploaded_documents": [
            {
                "document_id": "doc-helpful",
                "filename": "funding-proof.pdf",
                "status": "uploaded",
                "document_type": "funding_proof",
                "relevance": None,
                "supported_claims": ["/funding/primary_source"],
                "counts_toward_gate": None,
                "main_flow_feedback": {
                    "status": "helpful",
                    "supported_document_type": "funding_proof",
                    "current_focus_document_type": "funding_proof",
                    "message": "这份材料对当前关键证明 funding_proof 有帮助。",
                },
            },
            {
                "document_id": "doc-secondary",
                "filename": "passport.pdf",
                "status": "uploaded",
                "document_type": "passport_bio",
                "relevance": None,
                "supported_claims": ["/identity/passport_number"],
                "counts_toward_gate": None,
                "main_flow_feedback": {
                    "status": "not_helpful",
                    "current_focus_document_type": "funding_proof",
                    "message": "这份材料对当前主线没有直接帮助。",
                },
            },
        ],
    }
