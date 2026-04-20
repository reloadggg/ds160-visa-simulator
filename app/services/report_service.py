from app.domain.contracts import GovernorDecision, InterviewStateStatus


class ReportService:
    def user_report(
        self,
        session_id: str,
        visa_family: str,
        governor_decision: str,
        profile_json: dict,
        phase_state: str = "intake",
        gate_status: dict | None = None,
        interviewer_state_json: dict | None = None,
        current_focus_json: dict | None = None,
    ) -> dict:
        interviewer_state_json = interviewer_state_json or {}
        current_focus_json = current_focus_json or {}
        missing_evidence = self._resolve_missing_evidence(
            profile_json=profile_json,
            interviewer_state_json=interviewer_state_json,
            current_focus_json=current_focus_json,
        )
        gate_status = gate_status or {}
        gate_overall_status = gate_status.get("status")
        interview_status = self._resolve_public_status(
            governor_decision=governor_decision,
            phase_state=phase_state,
            gate_status=gate_status,
            missing_evidence=missing_evidence,
            interviewer_state_json=interviewer_state_json,
        )
        risk_level = self._resolve_risk_level(
            interview_status=interview_status,
            interviewer_state_json=interviewer_state_json,
        )
        current_key_question = interviewer_state_json.get("current_key_question")
        current_key_proof = interviewer_state_json.get("current_key_proof")
        current_risk_code = interviewer_state_json.get("current_risk_code")
        allowed_next_actions = list(interviewer_state_json.get("allowed_next_actions", []))
        advisory_context = dict(interviewer_state_json.get("advisory_context", {}) or {})
        prompt_trace = dict(interviewer_state_json.get("prompt_trace", {}) or {})
        turn_decision = {
            "decision": interviewer_state_json.get("decision", governor_decision),
            "current_key_question": current_key_question,
            "current_key_proof": current_key_proof,
            "current_risk_code": current_risk_code,
        }

        outcome_label = "需补强关键证据"
        summary = self._waiting_key_proof_summary(current_key_proof)
        recommended_improvements = [self._waiting_key_proof_recommendation(current_key_proof)]
        if interview_status == InterviewStateStatus.SIMULATED_REFUSAL.value:
            outcome_label = "模拟拒签结果"
            summary = "当前记录存在已确认硬冲突，系统给出模拟拒签结果。"
            recommended_improvements = ["回看证据引用并修复已确认硬冲突。"]
        elif interview_status == InterviewStateStatus.HIGH_RISK_REVIEW.value:
            outcome_label = "高风险待复核"
            summary = (
                f"当前面谈已识别出高风险事项（{current_risk_code}），需先完成复核。"
                if current_risk_code
                else "当前面谈已识别出高风险事项，需先完成复核。"
            )
            recommended_improvements = ["围绕高风险点补充解释或关键证明。"]
        elif interview_status == InterviewStateStatus.VERIFY_KEY_ISSUE.value:
            outcome_label = "需核验关键问题"
            summary = (
                f"系统已锁定当前关键问题：{current_key_question}"
                if current_key_question
                else "系统已锁定当前关键问题，面谈将围绕该问题继续核验。"
            )
            recommended_improvements = ["直接回答当前关键问题，并保持前后一致。"]
        elif interview_status == InterviewStateStatus.CONTINUE_INTERVIEW.value:
            outcome_label = "正式问答进行中"
            summary = (
                f"当前已进入正式 interview 阶段，当前关键问题是：{current_key_question}"
                if current_key_question
                else "当前已进入正式 interview 阶段，可继续回答后续问题。"
            )
            recommended_improvements = ["继续回答后续问题，并保持叙事一致。"]
        elif phase_state == "gate_review":
            outcome_label = "补件审核中"
            if gate_overall_status == "waiting_for_parse":
                summary = "当前处于材料门控阶段。材料已提交，仍在解析中，暂不能进入正式 interview。"
                recommended_improvements = ["等待解析完成后再继续。"]
            else:
                summary = "当前处于材料门控阶段。仍缺必需材料，暂不能进入正式 interview。"
                recommended_improvements = ["补齐必需材料后再继续。"]
        elif not missing_evidence:
            outcome_label = "可继续正式问答"
            summary = "当前已进入正式 interview 阶段，可继续回答后续问题。"
            recommended_improvements = ["继续回答后续问题，并保持叙事一致。"]

        return {
            "session_id": session_id,
            "visa_family": visa_family,
            "governor_decision": governor_decision,
            "interview_status": interview_status,
            "outcome_label": outcome_label,
            "summary": summary,
            "strengths": ["已完成基本签证家族识别"],
            "risk_points": [],
            "missing_evidence": missing_evidence,
            "risk_level": risk_level,
            "current_key_question": current_key_question,
            "current_key_proof": current_key_proof,
            "current_risk_code": current_risk_code,
            "allowed_next_actions": allowed_next_actions,
            "recommended_improvements": recommended_improvements,
            "turn_decision": turn_decision,
            "advisory_context": advisory_context,
            "prompt_trace": prompt_trace,
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
        interviewer_state_json: dict | None = None,
        current_focus_json: dict | None = None,
    ) -> dict:
        interviewer_state_json = interviewer_state_json or {}
        current_focus_json = current_focus_json or {}
        return {
            "session_id": session_id,
            "policy_pack_trace": dict(
                interviewer_state_json.get("prompt_trace", {})
                or {"prompt_pack_id": f"{visa_family}.default.v1"}
            ),
            "runtime_trace": list(runtime_trace or []),
            "score_history": list(score_history or []),
            "governor_history": list(governor_history or []),
            "interviewer_state": dict(interviewer_state_json),
            "current_focus": dict(current_focus_json),
            "profile_snapshot": profile_json,
            "turn_decision": {
                "decision": interviewer_state_json.get("decision", governor_decision),
                "governor_decision": interviewer_state_json.get(
                    "governor_decision",
                    governor_decision,
                ),
            },
            "advisory_context": dict(
                interviewer_state_json.get("advisory_context", {}) or {}
            ),
        }

    def _resolve_missing_evidence(
        self,
        *,
        profile_json: dict,
        interviewer_state_json: dict,
        current_focus_json: dict,
    ) -> list[str]:
        missing_evidence: list[str] = []
        requested_documents = interviewer_state_json.get("requested_documents", [])
        for document_type in requested_documents:
            if document_type and document_type not in missing_evidence:
                missing_evidence.append(document_type)

        current_key_proof = interviewer_state_json.get("current_key_proof")
        if current_key_proof and current_key_proof not in missing_evidence:
            missing_evidence.append(current_key_proof)
        focus_document_type = current_focus_json.get("document_type")
        if focus_document_type and focus_document_type not in missing_evidence:
            missing_evidence.append(focus_document_type)

        if not missing_evidence and profile_json.get("funding", {}).get("primary_source") == "parents":
            evidence_refs = (
                profile_json.get("field_provenance", {})
                .get("/funding/primary_source", {})
                .get("evidence_refs", [])
            )
            if not evidence_refs:
                missing_evidence.append("funding_proof")
        return missing_evidence

    def _waiting_key_proof_summary(self, current_key_proof: str | None) -> str:
        if current_key_proof:
            return f"当前最关键的证明点是 {current_key_proof}，请优先补强。"
        return "当前材料主线可识别，但关键证据尚不完整。"

    def _waiting_key_proof_recommendation(self, current_key_proof: str | None) -> str:
        if current_key_proof:
            return f"优先补充 {current_key_proof}，再继续面谈。"
        return "补充关键证明后继续面谈。"

    def _resolve_public_status(
        self,
        *,
        governor_decision: str,
        phase_state: str,
        gate_status: dict,
        missing_evidence: list[str],
        interviewer_state_json: dict,
    ) -> str:
        public_status = interviewer_state_json.get("public_status")
        if public_status:
            return public_status
        if phase_state == "gate_review":
            return InterviewStateStatus.WAITING_KEY_PROOF.value
        if governor_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return InterviewStateStatus.SIMULATED_REFUSAL.value
        if governor_decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return InterviewStateStatus.HIGH_RISK_REVIEW.value
        if governor_decision == GovernorDecision.ROUTE_CORRECTION.value:
            return InterviewStateStatus.VERIFY_KEY_ISSUE.value
        if governor_decision == GovernorDecision.NEED_MORE_EVIDENCE.value:
            if missing_evidence or gate_status.get("status") == "pending_documents":
                return InterviewStateStatus.WAITING_KEY_PROOF.value
            return InterviewStateStatus.VERIFY_KEY_ISSUE.value
        return InterviewStateStatus.CONTINUE_INTERVIEW.value

    def _resolve_risk_level(
        self,
        *,
        interview_status: str,
        interviewer_state_json: dict,
    ) -> str:
        risk_level = interviewer_state_json.get("risk_level")
        if risk_level:
            return risk_level
        if interview_status in {
            InterviewStateStatus.HIGH_RISK_REVIEW.value,
            InterviewStateStatus.SIMULATED_REFUSAL.value,
        }:
            return "high"
        if interview_status == InterviewStateStatus.VERIFY_KEY_ISSUE.value:
            return "medium"
        return "none"
