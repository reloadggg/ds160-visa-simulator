from app.domain.runtime import build_initial_gate_status
from app.services.report_service import ReportService


def test_internal_report_returns_session_runtime_histories() -> None:
    service = ReportService()
    runtime_trace = [{"node_name": "score_case", "summary": "missing=1 risk_flags=0"}]
    score_history = [
        {
            "scoring_stage": "interview_turn",
            "category_fit": 65,
            "document_readiness": 72,
            "narrative_consistency": 70,
            "confidence": 68,
            "missing_evidence": ["funding_proof"],
            "risk_flags": [],
            "summary": "missing=1 risk_flags=0",
        }
    ]
    governor_history = [
        {"decision": "need_more_evidence", "summary": "decision=need_more_evidence"}
    ]

    payload = service.internal_report(
        session_id="sess-1",
        visa_family="f1",
        governor_decision="need_more_evidence",
        profile_json={"funding": {"primary_source": "parents"}},
        runtime_trace=runtime_trace,
        score_history=score_history,
        governor_history=governor_history,
    )

    assert payload["runtime_trace"] == runtime_trace
    assert payload["score_history"] == score_history
    assert payload["governor_history"] == governor_history


def test_user_report_does_not_block_on_gate_review_without_turn_focus() -> None:
    service = ReportService()
    gate_status = build_initial_gate_status(
        declared_family="f1",
        scenario_key="parent_sponsored",
        required_documents=["funding_proof"],
    )
    gate_status["status"] = "waiting_for_parse"

    payload = service.user_report(
        session_id="sess-1",
        visa_family="f1",
        governor_decision="need_more_evidence",
        profile_json={"funding": {"primary_source": "parents"}},
        phase_state="gate_review",
        gate_status=gate_status,
    )

    assert payload["interview_status"] == "verify_key_issue"
    assert payload["outcome_label"] == "需核验关键问题"
    assert "关键问题" in payload["summary"]


def test_user_report_prefers_interviewer_state_when_gate_still_needs_documents() -> None:
    service = ReportService()
    gate_status = build_initial_gate_status(
        declared_family="f1",
        scenario_key="parent_sponsored",
        required_documents=["funding_proof"],
    )
    gate_status["status"] = "pending_documents"

    payload = service.user_report(
        session_id="sess-1",
        visa_family="f1",
        governor_decision="continue_interview",
        profile_json={"funding": {"primary_source": "parents"}},
        phase_state="gate_review",
        gate_status=gate_status,
        interviewer_state_json={
            "public_status": "continue_interview",
            "current_key_question": "What is the purpose of your travel?",
            "allowed_next_actions": ["answer_question", "continue_interview"],
        },
    )

    assert payload["interview_status"] == "continue_interview"
    assert payload["outcome_label"] == "正式问答进行中"
    assert payload["current_key_proof"] is None
    assert (
        payload["summary"]
        == "当前已进入正式 interview 阶段，当前关键问题是：What is the purpose of your travel?"
    )
    assert payload["recommended_improvements"] == ["继续回答后续问题，并保持叙事一致。"]


def test_user_report_prefers_runtime_view_state_over_stale_interviewer_state() -> None:
    service = ReportService()

    payload = service.user_report(
        session_id="sess-2",
        visa_family="f1",
        governor_decision="continue_interview",
        profile_json={"funding": {"primary_source": "self"}},
        phase_state="interview",
        runtime_view_state={
            "source_turn_id": "turn-assistant-2",
            "decision": "continue_interview",
            "governor_decision": "continue_interview",
            "public_status": "continue_interview",
            "risk_level": "none",
            "current_focus": {
                "kind": "interview_question",
                "question": "What is the purpose of your travel?",
            },
            "current_key_question": "What is the purpose of your travel?",
            "current_key_proof": None,
            "current_risk_code": None,
            "requested_documents": [],
            "allowed_next_actions": ["answer_question", "continue_interview"],
            "advisory_context": {
                "risk_codes": [],
                "missing_evidence": [],
                "risk_level": "none",
            },
            "prompt_trace": {
                "prompt_pack_id": "ds160.interviewer",
                "prompt_version": "v2",
                "provider": "openai",
                "model": "gpt-5.4",
            },
        },
        interviewer_state_json={
            "public_status": "waiting_key_proof",
            "current_key_question": "STALE QUESTION",
            "current_key_proof": "funding_proof",
            "allowed_next_actions": ["upload_key_proof"],
        },
    )

    assert payload["interview_status"] == "continue_interview"
    assert payload["current_key_question"] == "What is the purpose of your travel?"
    assert payload["current_key_proof"] is None
    assert payload["allowed_next_actions"] == ["answer_question", "continue_interview"]
    assert payload["prompt_trace"]["model"] == "gpt-5.4"


def test_user_report_keeps_runtime_focus_during_gate_review() -> None:
    service = ReportService()
    gate_status = build_initial_gate_status(
        declared_family="f1",
        scenario_key="gate-primary-focus",
        required_documents=["ds160", "funding_proof"],
    )
    gate_status["status"] = "pending_documents"

    payload = service.user_report(
        session_id="sess-gate-focus",
        visa_family="f1",
        governor_decision="need_more_evidence",
        profile_json={"funding": {"primary_source": "self"}},
        phase_state="gate_review",
        gate_status=gate_status,
        runtime_view_state={
            "source_turn_id": "turn-assistant-stale",
            "decision": "need_more_evidence",
            "governor_decision": "continue_interview",
            "public_status": "waiting_key_proof",
            "current_key_question": None,
            "current_key_proof": "funding_proof",
            "requested_documents": ["funding_proof"],
            "allowed_next_actions": ["upload_key_proof", "explain_missing_proof"],
        },
        interviewer_state_json={
            "current_key_proof": "funding_proof",
            "requested_documents": ["funding_proof"],
        },
    )

    assert payload["interview_status"] == "waiting_key_proof"
    assert payload["current_key_question"] is None
    assert payload["current_key_proof"] == "funding_proof"
    assert payload["missing_evidence"] == ["funding_proof"]
    assert payload["recommended_improvements"] == [
        "围绕 funding_proof 说明事实来源；如果有材料，可作为证据补充上传。"
    ]


def test_user_report_projects_case_board_facts_conflicts_and_proof_points() -> None:
    service = ReportService()

    payload = service.user_report(
        session_id="sess-case-board-report",
        visa_family="f1",
        governor_decision="continue_interview",
        profile_json={},
        phase_state="interview",
        case_board={
            "schema_version": "case_board.v1",
            "claims": [
                {
                    "claim_id": "claim-school",
                    "field_path": "/education/school_name",
                    "value": "Example University",
                    "status": "documented",
                    "supporting_evidence_ids": ["ev-i20-school"],
                    "conflicting_evidence_ids": [],
                },
                {
                    "claim_id": "claim-funding-user",
                    "field_path": "/funding/primary_source",
                    "value": "self",
                    "status": "contradicted",
                    "supporting_evidence_ids": [],
                    "conflicting_evidence_ids": ["ev-bank-parent"],
                },
            ],
            "evidence_cards": [
                {
                    "evidence_id": "ev-i20-school",
                    "source_type": "uploaded_file",
                    "document_id": "doc-i20",
                    "excerpt": "School Name: Example University",
                    "claim_refs": ["claim-school"],
                },
                {
                    "evidence_id": "ev-bank-parent",
                    "source_type": "uploaded_file",
                    "document_id": "doc-bank",
                    "excerpt": "Parent sponsor account",
                    "claim_refs": ["claim-funding-user"],
                },
            ],
            "proof_points": [
                {
                    "proof_point_id": "proof-funding-source",
                    "visa_family": "f1",
                    "question": "Who will pay for your first year of study?",
                    "status": "partial",
                    "why_it_matters": "Funding source must be credible.",
                }
            ],
            "conflicts": [
                {
                    "conflict_id": "conflict-funding-source",
                    "claim_ids": ["claim-funding-user"],
                    "evidence_ids": ["ev-bank-parent"],
                    "summary": "用户说自费，但银行材料显示父母资助。",
                    "severity": "high",
                    "suggested_followup": "请解释资金来源到底是本人还是父母。",
                }
            ],
        },
    )

    assert payload["strengths"] == [
        "/education/school_name 已有材料证据支持：Example University"
    ]
    assert payload["risk_level"] == "high"
    assert payload["risk_points"] == [
        "用户说自费，但银行材料显示父母资助。",
        "/funding/primary_source 存在证据冲突：self",
    ]
    assert payload["missing_evidence"][0] == "proof-funding-source"
    assert "请解释资金来源到底是本人还是父母。" in payload[
        "recommended_improvements"
    ]
    assert payload["case_board"]["claims"][0]["claim_id"] == "claim-school"


def test_internal_report_prefers_runtime_view_state_for_turn_summary() -> None:
    service = ReportService()

    payload = service.internal_report(
        session_id="sess-3",
        visa_family="f1",
        governor_decision="continue_interview",
        profile_json={"funding": {"primary_source": "self"}},
        runtime_ledger={"events": []},
        runtime_view_state={
            "source_turn_id": "turn-assistant-3",
            "decision": "continue_interview",
            "governor_decision": "continue_interview",
            "advisory_context": {
                "risk_codes": [],
                "missing_evidence": [],
                "risk_level": "none",
            },
            "prompt_trace": {
                "prompt_pack_id": "ds160.interviewer",
                "prompt_version": "v2",
                "provider": "openai",
                "model": "gpt-5.4",
            },
        },
        interviewer_state_json={
            "decision": "need_more_evidence",
            "governor_decision": "need_more_evidence",
            "advisory_context": {"risk_level": "medium"},
            "prompt_trace": {"model": "stale-model"},
        },
    )

    assert payload["policy_pack_trace"]["model"] == "gpt-5.4"
    assert payload["turn_decision"] == {
        "decision": "continue_interview",
        "governor_decision": "continue_interview",
        "remaining_required_documents": [],
    }
    assert payload["advisory_context"]["risk_level"] == "none"
    assert payload["runtime_view_state"]["decision"] == "continue_interview"
