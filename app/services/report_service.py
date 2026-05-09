from typing import Any

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
        runtime_view_state: dict[str, Any] | None = None,
        interviewer_state_json: dict | None = None,
        current_focus_json: dict | None = None,
    ) -> dict:
        interviewer_state_json = interviewer_state_json or {}
        runtime_view_state = self._runtime_view_state_payload(runtime_view_state)
        has_runtime_turn = bool(runtime_view_state.get("source_turn_id"))
        effective_interviewer_state = self._effective_interviewer_state(
            runtime_view_state=runtime_view_state,
            interviewer_state_json=interviewer_state_json,
        )
        if has_runtime_turn:
            current_focus_json = dict(runtime_view_state.get("current_focus") or {})
        else:
            current_focus_json = dict(
                runtime_view_state.get("current_focus")
                or current_focus_json
                or {}
            )
        gate_status = gate_status or {}
        baseline_missing_evidence = self._resolve_missing_evidence(
            profile_json=profile_json,
            interviewer_state_json=effective_interviewer_state,
            current_focus_json=current_focus_json,
        )
        gate_overall_status = gate_status.get("status")
        interview_status = self._resolve_public_status(
            governor_decision=governor_decision,
            phase_state=phase_state,
            gate_status=gate_status,
            missing_evidence=baseline_missing_evidence,
            interviewer_state_json=effective_interviewer_state,
        )
        if (
            phase_state == "gate_review"
            and interview_status == InterviewStateStatus.WAITING_KEY_PROOF.value
        ):
            (
                effective_interviewer_state,
                current_focus_json,
            ) = self._apply_gate_review_primary_focus(
                interviewer_state_json=effective_interviewer_state,
                current_focus_json=current_focus_json,
                gate_status=gate_status,
            )
        missing_evidence = self._resolve_missing_evidence(
            profile_json=profile_json,
            interviewer_state_json=effective_interviewer_state,
            current_focus_json=current_focus_json,
        )
        risk_level = self._resolve_risk_level(
            interview_status=interview_status,
            interviewer_state_json=effective_interviewer_state,
        )
        current_key_question = effective_interviewer_state.get("current_key_question")
        current_key_proof = effective_interviewer_state.get("current_key_proof")
        current_risk_code = effective_interviewer_state.get("current_risk_code")
        remaining_required_documents = list(
            effective_interviewer_state.get("remaining_required_documents", []) or []
        )
        allowed_next_actions = list(
            effective_interviewer_state.get("allowed_next_actions", [])
        )
        advisory_context = dict(
            effective_interviewer_state.get("advisory_context", {}) or {}
        )
        document_review = dict(
            effective_interviewer_state.get("document_review", {}) or {}
        )
        prompt_trace = dict(
            effective_interviewer_state.get("prompt_trace", {}) or {}
        )
        if (
            phase_state == "gate_review"
            and interview_status == InterviewStateStatus.WAITING_KEY_PROOF.value
        ):
            advisory_context["missing_evidence"] = list(missing_evidence)
            advisory_context["risk_level"] = risk_level
            if missing_evidence:
                advisory_context["missing_evidence_summary"] = ", ".join(missing_evidence)
        turn_decision = {
            "decision": effective_interviewer_state.get("decision", governor_decision),
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
            "remaining_required_documents": remaining_required_documents,
            "risk_level": risk_level,
            "current_key_question": current_key_question,
            "current_key_proof": current_key_proof,
            "current_risk_code": current_risk_code,
            "allowed_next_actions": allowed_next_actions,
            "recommended_improvements": recommended_improvements,
            "turn_decision": turn_decision,
            "advisory_context": advisory_context,
            "document_review": document_review,
            "prompt_trace": prompt_trace,
        }

    def internal_report(
        self,
        session_id: str,
        visa_family: str,
        governor_decision: str,
        profile_json: dict,
        runtime_ledger: dict[str, Any] | None = None,
        runtime_view_state: dict[str, Any] | None = None,
        runtime_trace: list | None = None,
        score_history: list | None = None,
        governor_history: list | None = None,
        interviewer_state_json: dict | None = None,
        current_focus_json: dict | None = None,
    ) -> dict:
        interviewer_state_json = interviewer_state_json or {}
        current_focus_json = current_focus_json or {}
        runtime_ledger_payload = self._runtime_ledger_payload(runtime_ledger)
        runtime_view_state_payload = self._runtime_view_state_payload(runtime_view_state)
        runtime_trace_payload = self._legacy_event_payloads(
            runtime_ledger_payload,
            event_type="trace",
        ) or list(runtime_trace or [])
        score_history_payload = self._legacy_event_payloads(
            runtime_ledger_payload,
            event_type="scorer",
        ) or list(score_history or [])
        governor_history_payload = self._legacy_event_payloads(
            runtime_ledger_payload,
            event_type="boundary",
        ) or list(governor_history or [])
        return {
            "session_id": session_id,
            "policy_pack_trace": dict(
                runtime_view_state_payload.get("prompt_trace")
                or interviewer_state_json.get("prompt_trace", {})
                or {"prompt_pack_id": f"{visa_family}.default.v1"}
            ),
            "runtime_trace": runtime_trace_payload,
            "score_history": score_history_payload,
            "governor_history": governor_history_payload,
            "runtime_ledger": runtime_ledger_payload,
            "runtime_view_state": runtime_view_state_payload,
            "interviewer_state": dict(interviewer_state_json),
            "current_focus": dict(current_focus_json),
            "profile_snapshot": profile_json,
            "turn_decision": {
                "decision": runtime_view_state_payload.get("decision")
                or interviewer_state_json.get("decision", governor_decision),
                "governor_decision": runtime_view_state_payload.get("governor_decision")
                or interviewer_state_json.get("governor_decision", governor_decision),
                "remaining_required_documents": list(
                    runtime_view_state_payload.get("remaining_required_documents", [])
                    or interviewer_state_json.get("remaining_required_documents", [])
                    or []
                ),
            },
            "advisory_context": dict(
                runtime_view_state_payload.get("advisory_context")
                or interviewer_state_json.get("advisory_context", {})
                or {}
            ),
            "document_review": dict(
                runtime_view_state_payload.get("document_review")
                or interviewer_state_json.get("document_review", {})
                or {}
            ),
        }

    def _runtime_ledger_payload(
        self,
        runtime_ledger: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(runtime_ledger, dict):
            return {}
        return dict(runtime_ledger)

    def _runtime_view_state_payload(
        self,
        runtime_view_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(runtime_view_state, dict):
            return {}
        return dict(runtime_view_state)

    def _effective_interviewer_state(
        self,
        *,
        runtime_view_state: dict[str, Any],
        interviewer_state_json: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(interviewer_state_json or {})
        has_runtime_turn = bool(runtime_view_state.get("source_turn_id"))
        for key in (
            "decision",
            "governor_decision",
            "public_status",
            "risk_level",
            "current_key_question",
            "current_key_proof",
            "current_risk_code",
            "requested_documents",
            "allowed_next_actions",
            "advisory_context",
            "remaining_required_documents",
            "document_review",
            "prompt_trace",
        ):
            if key not in runtime_view_state:
                continue
            value = runtime_view_state.get(key)
            if not has_runtime_turn and value in (None, [], {}):
                continue
            payload[key] = value
        return payload

    def _legacy_event_payloads(
        self,
        runtime_ledger: dict[str, Any],
        *,
        event_type: str,
    ) -> list[dict[str, Any]]:
        events = runtime_ledger.get("events", [])
        if not isinstance(events, list):
            return []
        payloads: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict) or event.get("event_type") != event_type:
                continue
            payload = event.get("payload")
            if isinstance(payload, dict):
                payloads.append(dict(payload))
        return payloads

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
        remaining_required_documents = interviewer_state_json.get(
            "remaining_required_documents", []
        )
        for document_type in remaining_required_documents:
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

    def _apply_gate_review_primary_focus(
        self,
        *,
        interviewer_state_json: dict[str, Any],
        current_focus_json: dict[str, Any],
        gate_status: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        primary_document = self._gate_primary_document(gate_status)
        if not primary_document:
            return interviewer_state_json, current_focus_json

        payload = dict(interviewer_state_json or {})
        payload["current_key_question"] = None
        payload["current_key_proof"] = primary_document
        payload["requested_documents"] = [primary_document]
        if not payload.get("allowed_next_actions"):
            payload["allowed_next_actions"] = [
                "upload_key_proof",
                "explain_missing_proof",
            ]

        next_focus = {
            "owner": "gate_runtime_service",
            "kind": "required_document",
            "document_type": primary_document,
        }
        return payload, next_focus

    def _gate_primary_document(self, gate_status: dict[str, Any]) -> str | None:
        required_documents = gate_status.get("required_documents", [])
        if not isinstance(required_documents, list):
            return None

        for item in required_documents:
            if not isinstance(item, dict):
                continue
            document_type = item.get("document_type")
            if isinstance(document_type, str) and item.get("status", "missing") == "missing":
                return document_type

        for item in required_documents:
            if not isinstance(item, dict):
                continue
            document_type = item.get("document_type")
            if isinstance(document_type, str) and not item.get("meets_minimum_fields", False):
                return document_type

        return None

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
