class ReportService:
    def user_report(
        self,
        session_id: str,
        visa_family: str,
        governor_decision: str,
        profile_json: dict,
        phase_state: str = "intake",
        gate_status: dict | None = None,
    ) -> dict:
        missing_evidence: list[str] = []
        if profile_json.get("funding", {}).get("primary_source") == "parents":
            evidence_refs = (
                profile_json.get("field_provenance", {})
                .get("/funding/primary_source", {})
                .get("evidence_refs", [])
            )
            if not evidence_refs:
                missing_evidence.append("funding_proof")

        gate_status = gate_status or {}
        gate_overall_status = gate_status.get("status")
        outcome_label = "需补强关键证据"
        summary = "当前材料主线可识别，但关键资金支持证据尚不完整。"
        recommended_improvements = ["补充资金证明后再继续正式 interview。"]
        if governor_decision == "simulated_refusal":
            outcome_label = "模拟拒签结果"
            summary = "当前记录存在已确认硬冲突，系统给出模拟拒签结果。"
            recommended_improvements = ["回看证据引用并修复已确认硬冲突。"]
        elif phase_state == "gate_review":
            outcome_label = "补件审核中"
            if gate_overall_status == "waiting_for_parse":
                summary = "材料已提交，仍在解析中，暂不能进入正式 interview。"
                recommended_improvements = ["等待解析完成后再继续。"]
            else:
                summary = "当前仍处于补件阶段，暂不能进入正式 interview。"
                recommended_improvements = ["补齐必需材料后再继续。"]
        elif phase_state == "interview":
            outcome_label = "正式问答进行中"
            summary = "当前已进入正式 interview，可继续回答后续问题。"
            recommended_improvements = ["继续回答后续问题，并保持叙事一致。"]
        elif not missing_evidence:
            outcome_label = "可继续正式问答"
            summary = "当前材料主线基本完整，可继续常规 interview。"
            recommended_improvements = ["继续回答后续问题，并保持叙事一致。"]

        return {
            "session_id": session_id,
            "visa_family": visa_family,
            "governor_decision": governor_decision,
            "outcome_label": outcome_label,
            "summary": summary,
            "strengths": ["已完成基本签证家族识别"],
            "risk_points": [],
            "missing_evidence": missing_evidence,
            "recommended_improvements": recommended_improvements,
        }

    def internal_report(
        self,
        session_id: str,
        visa_family: str,
        governor_decision: str,
        profile_json: dict,
        runtime_trace: list | None = None,
        score_history: list | None = None,
        governor_history: list | None = None,
    ) -> dict:
        return {
            "session_id": session_id,
            "policy_pack_trace": {"policy_pack_id": f"{visa_family}.default.v1"},
            "runtime_trace": list(runtime_trace or []),
            "score_history": list(score_history or []),
            "governor_history": list(governor_history or []),
            "profile_snapshot": profile_json,
        }
