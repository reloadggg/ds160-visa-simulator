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
    assert payload["interview_result"] == "not_passed"
    assert payload["interview_result_label"] == "未通过：关键事实待核实"
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
    assert payload["interview_result"] == "in_progress"
    assert payload["interview_result_label"] == "继续面谈"
    assert payload["outcome_label"] == "正式问答进行中"
    assert payload["current_key_proof"] is None
    assert (
        payload["summary"]
        == "当前已进入正式 interview 阶段，当前关键问题是：What is the purpose of your travel?"
    )
    assert payload["recommended_improvements"] == ["继续回答后续问题，并保持叙事一致。"]


def test_user_report_prefers_case_board_over_legacy_requested_documents() -> None:
    service = ReportService()

    payload = service.user_report(
        session_id="sess-case-board-over-gate",
        visa_family="f1",
        governor_decision="need_more_evidence",
        profile_json={},
        phase_state="interview",
        interviewer_state_json={
            "requested_documents": ["funding_proof"],
            "remaining_required_documents": ["funding_proof"],
            "current_key_proof": "funding_proof",
        },
        current_focus_json={
            "kind": "required_document",
            "document_type": "funding_proof",
        },
        case_board={
            "schema_version": "case_board.v1",
            "claims": [
                {
                    "claim_id": "claim-funding-source",
                    "field_path": "/funding/primary_source",
                    "value": "parents",
                    "status": "documented",
                    "supporting_evidence_ids": ["ev-bank"],
                    "conflicting_evidence_ids": [],
                }
            ],
            "evidence_cards": [
                {
                    "evidence_id": "ev-bank",
                    "source_type": "uploaded_file",
                    "document_id": "doc-bank",
                    "excerpt": "Parent sponsor bank statement",
                    "claim_refs": ["claim-funding-source"],
                }
            ],
            "proof_points": [],
            "conflicts": [],
        },
    )

    assert payload["missing_evidence"] == []
    assert payload["interview_status"] == "verify_key_issue"


def test_user_report_treats_case_board_latest_material_as_state() -> None:
    service = ReportService()

    payload = service.user_report(
        session_id="sess-case-board-latest-material",
        visa_family="f1",
        governor_decision="need_more_evidence",
        profile_json={},
        phase_state="interview",
        interviewer_state_json={
            "requested_documents": ["funding_proof"],
            "remaining_required_documents": ["funding_proof"],
        },
        current_focus_json={
            "kind": "required_document",
            "document_type": "funding_proof",
        },
        case_board={
            "schema_version": "case_board.v1",
            "latest_material": {
                "document_id": "doc-funding",
                "filename": "funding.pdf",
                "understanding_status": "queued",
                "unknowns": ["案例理解仍在更新。"],
            },
            "claims": [],
            "evidence_cards": [],
            "proof_points": [],
            "conflicts": [],
        },
    )

    assert payload["missing_evidence"] == []
    assert payload["interview_status"] == "verify_key_issue"


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
    assert payload["interview_result"] == "in_progress"
    assert payload["current_key_question"] == "What is the purpose of your travel?"
    assert payload["current_key_proof"] is None
    assert payload["allowed_next_actions"] == ["answer_question", "continue_interview"]
    assert payload["prompt_trace"]["model"] == "gpt-5.4"


def test_user_report_does_not_promote_unresolved_gap_to_current_key_proof() -> None:
    service = ReportService()

    payload = service.user_report(
        session_id="sess-runtime-gap-no-proof-focus",
        visa_family="f1",
        governor_decision="continue_interview",
        profile_json={"funding": {"primary_source": "parents"}},
        phase_state="interview",
        runtime_view_state={
            "source_turn_id": "turn-assistant-2",
            "decision": "continue_interview",
            "governor_decision": "continue_interview",
            "public_status": "continue_interview",
            "current_focus": {
                "kind": "interview_question",
                "question": "How does this program fit your future plan?",
            },
            "current_key_question": "How does this program fit your future plan?",
            "requested_documents": [],
            "remaining_required_documents": ["funding_proof"],
            "advisory_context": {"missing_evidence": ["funding_proof"]},
        },
        interviewer_state_json={
            "current_key_proof": "funding_proof",
            "requested_documents": ["funding_proof"],
            "remaining_required_documents": ["funding_proof"],
        },
        current_focus_json={
            "kind": "required_document",
            "document_type": "funding_proof",
        },
    )

    assert payload["interview_status"] == "continue_interview"
    assert payload["missing_evidence"] == ["funding_proof"]
    assert payload["remaining_required_documents"] == ["funding_proof"]
    assert payload["current_key_question"] == (
        "How does this program fit your future plan?"
    )
    assert payload["current_key_proof"] is None
    assert payload["interview_result"] == "not_passed"
    assert payload["interview_result_label"] == "未通过：材料或事实待补强"


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
        "围绕 funding_proof 说明事实来源；如果有材料，可作为补强证据上传。"
    ]
    assert "待证明点" not in payload["summary"]
    assert "上传对应证据" not in payload["summary"]


def test_user_report_respects_empty_runtime_document_lists_for_missing_evidence() -> None:
    service = ReportService()

    payload = service.user_report(
        session_id="sess-runtime-empty-documents",
        visa_family="f1",
        governor_decision="need_more_evidence",
        profile_json={},
        phase_state="interview",
        runtime_view_state={
            "source_turn_id": "turn-assistant-empty-docs",
            "decision": "need_more_evidence",
            "governor_decision": "need_more_evidence",
            "public_status": "waiting_key_proof",
            "current_focus": {
                "kind": "required_document",
                "document_type": "funding_proof",
            },
            "current_key_proof": "funding_proof",
            "requested_documents": [],
            "remaining_required_documents": [],
            "allowed_next_actions": ["upload_key_proof"],
        },
        interviewer_state_json={
            "current_key_proof": "funding_proof",
            "requested_documents": [],
            "remaining_required_documents": [],
        },
    )

    assert payload["interview_status"] == "waiting_key_proof"
    assert payload["missing_evidence"] == []
    assert payload["remaining_required_documents"] == []


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


def test_user_report_high_risk_summary_uses_document_conflict_detail() -> None:
    service = ReportService()

    payload = service.user_report(
        session_id="sess-high-risk-detail",
        visa_family="f1",
        governor_decision="high_risk_review",
        profile_json={},
        phase_state="interview",
        interviewer_state_json={
            "public_status": "high_risk_review",
            "risk_level": "high",
            "current_risk_code": "record_conflict",
            "document_review": {
                "claim_conflicts": [
                    {
                        "summary": (
                            "口头陈述为纽约大学数据科学硕士，但 I-20 和录取信显示 "
                            "Example University / Master of Example Analytics。"
                        )
                    }
                ]
            },
        },
    )

    assert payload["summary"] == (
        "口头陈述为纽约大学数据科学硕士，但 I-20 和录取信显示 "
        "Example University / Master of Example Analytics。"
    )
    assert "record_conflict" not in payload["summary"]
    assert payload["interview_result"] == "not_passed"
    assert payload["interview_result_label"] == "未通过：高风险待复核"


def test_user_report_marks_passed_only_after_natural_low_risk_closure() -> None:
    service = ReportService()

    payload = service.user_report(
        session_id="sess-pass-closure",
        visa_family="f1",
        governor_decision="continue_interview",
        profile_json={"funding": {"primary_source": "parents"}},
        phase_state="interview",
        runtime_view_state={
            "source_turn_id": "turn-assistant-pass",
            "source_turn_content": (
                "All right, Mr. Lee. Your study plan, funding, and intention "
                "to return to China are clear; that will be all for now."
            ),
            "decision": "continue_interview",
            "governor_decision": "continue_interview",
            "public_status": "continue_interview",
            "risk_level": "none",
            "current_focus": {},
            "current_key_question": None,
            "current_key_proof": None,
            "current_risk_code": None,
            "requested_documents": [],
            "remaining_required_documents": [],
            "allowed_next_actions": ["continue_interview"],
            "advisory_context": {
                "risk_codes": [],
                "missing_evidence": [],
                "risk_level": "none",
            },
        },
    )

    assert payload["interview_status"] == "continue_interview"
    assert payload["interview_result"] == "passed"
    assert payload["interview_result_label"] == "本轮模拟通过"
    assert payload["outcome_label"] == "本轮模拟通过"
    assert "没有明显风险" in payload["summary"]
    assert payload["recommended_improvements"] == [
        "本轮回答已形成清晰、低风险的面签闭环，可进入复盘或开始新一轮练习。"
    ]


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
