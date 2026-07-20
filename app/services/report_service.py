from typing import Any

from app.domain.contracts import GovernorDecision, InterviewStateStatus
from app.domain.runtime import InterviewResult
from app.services.case_board_projection import (
    case_board_has_state,
    missing_evidence_from_case_board,
    proof_point_code,
)


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
        case_board: dict[str, Any] | None = None,
    ) -> dict:
        interviewer_state_json = interviewer_state_json or {}
        runtime_view_state = self._runtime_view_state_payload(runtime_view_state)
        case_board_payload = self._case_board_payload(case_board)
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
            case_board=case_board_payload,
        )
        interview_status = self._resolve_public_status(
            governor_decision=governor_decision,
            phase_state=phase_state,
            gate_status=gate_status,
            missing_evidence=baseline_missing_evidence,
            interviewer_state_json=effective_interviewer_state,
        )
        missing_evidence = self._resolve_missing_evidence(
            profile_json=profile_json,
            interviewer_state_json=effective_interviewer_state,
            current_focus_json=current_focus_json,
            case_board=case_board_payload,
        )
        case_strengths = self._case_strengths(case_board_payload)
        case_risk_points = self._case_risk_points(case_board_payload)
        risk_level = self._resolve_risk_level(
            interview_status=interview_status,
            interviewer_state_json=effective_interviewer_state,
            case_board=case_board_payload,
        )
        current_key_question = effective_interviewer_state.get("current_key_question")
        current_key_proof = effective_interviewer_state.get("current_key_proof")
        current_risk_code = effective_interviewer_state.get("current_risk_code")
        remaining_required_documents = list(
            effective_interviewer_state.get("remaining_required_documents", []) or []
        )
        requested_documents = self._resolve_requested_documents(
            effective_interviewer_state=effective_interviewer_state,
            current_focus_json=current_focus_json,
            remaining_required_documents=remaining_required_documents,
            missing_evidence=missing_evidence,
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
        turn_decision = {
            "decision": effective_interviewer_state.get("decision", governor_decision),
            "current_key_question": current_key_question,
            "current_key_proof": current_key_proof,
            "current_risk_code": current_risk_code,
        }
        public_governor_decision = self._public_governor_decision(
            governor_decision=governor_decision,
            effective_interviewer_state=effective_interviewer_state,
        )
        interview_result, interview_result_label, interview_result_reason = (
            self._resolve_interview_result(
                interview_status=interview_status,
                risk_level=risk_level,
                missing_evidence=missing_evidence,
                risk_points=case_risk_points,
                remaining_required_documents=remaining_required_documents,
                current_key_proof=current_key_proof,
                runtime_view_state=runtime_view_state,
            )
        )

        outcome_label = "需核验关键事实"
        summary = self._waiting_key_proof_summary(current_key_proof)
        recommended_improvements = [self._waiting_key_proof_recommendation(current_key_proof)]
        if interview_status == InterviewStateStatus.SIMULATED_REFUSAL.value:
            outcome_label = "模拟拒签结果"
            summary = "当前记录存在已确认硬冲突，系统给出模拟拒签结果。"
            recommended_improvements = ["回看证据引用并修复已确认硬冲突。"]
        elif interview_status == InterviewStateStatus.HIGH_RISK_REVIEW.value:
            outcome_label = "高风险待复核"
            summary = (
                self._document_review_issue_summary(document_review)
                or "当前面谈已识别出高风险事项，需先完成复核。"
            )
            recommended_improvements = [
                "先围绕上述高风险点给出一致解释；如材料有误，补充更新后的 I-20、录取信或对应更正材料。"
            ]
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
        elif not missing_evidence:
            outcome_label = "可继续正式问答"
            summary = "当前已进入正式 interview 阶段，可继续回答后续问题。"
            recommended_improvements = ["继续回答后续问题，并保持叙事一致。"]

        if interview_result == InterviewResult.PASSED.value:
            outcome_label = interview_result_label
            summary = interview_result_reason
            recommended_improvements = ["本轮回答已形成清晰、低风险的面签闭环，可进入复盘或开始新一轮练习。"]

        recommended_improvements = self._merge_recommendations(
            recommended_improvements,
            case_board_payload,
        )

        return {
            "session_id": session_id,
            "visa_family": visa_family,
            "governor_decision": public_governor_decision,
            "interview_status": interview_status,
            "interview_result": interview_result,
            "interview_result_label": interview_result_label,
            "interview_result_reason": interview_result_reason,
            "outcome_label": outcome_label,
            "summary": summary,
            "strengths": case_strengths or ["已完成基本签证家族识别"],
            "risk_points": case_risk_points,
            "missing_evidence": missing_evidence,
            "requested_documents": requested_documents,
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
            "case_board": case_board_payload,
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
        case_board: dict[str, Any] | None = None,
    ) -> dict:
        interviewer_state_json = interviewer_state_json or {}
        current_focus_json = current_focus_json or {}
        runtime_ledger_payload = self._runtime_ledger_payload(runtime_ledger)
        runtime_view_state_payload = self._runtime_view_state_payload(runtime_view_state)
        case_board_payload = self._case_board_payload(case_board)
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
            "case_board": case_board_payload,
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

    def _case_board_payload(
        self,
        case_board: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(case_board, dict):
            return {}
        return dict(case_board)

    def _effective_interviewer_state(
        self,
        *,
        runtime_view_state: dict[str, Any],
        interviewer_state_json: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(interviewer_state_json or {})
        has_runtime_turn = bool(runtime_view_state.get("source_turn_id"))
        contract_defaults = {
            "current_key_question": None,
            "current_key_proof": None,
            "current_risk_code": None,
            "requested_documents": [],
            "remaining_required_documents": [],
            "allowed_next_actions": [],
            "advisory_context": {},
            "document_review": {},
            "prompt_trace": {},
        }
        for key in (
            "decision",
            "governor_decision",
            "public_status",
            "risk_level",
            "source_turn_content",
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
                if has_runtime_turn and key in contract_defaults:
                    payload[key] = contract_defaults[key]
                continue
            value = runtime_view_state.get(key)
            if not has_runtime_turn and value in (None, [], {}):
                continue
            payload[key] = value
        return payload

    def _public_governor_decision(
        self,
        *,
        governor_decision: str,
        effective_interviewer_state: dict[str, Any],
    ) -> str:
        decision = effective_interviewer_state.get("decision")
        if isinstance(decision, str) and decision.strip():
            return decision.strip()
        return governor_decision

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
        case_board: dict[str, Any],
    ) -> list[str]:
        del profile_json
        missing_evidence = missing_evidence_from_case_board(case_board)

        if self._has_case_board_state(case_board):
            return missing_evidence

        has_explicit_document_summary = (
            "requested_documents" in interviewer_state_json
            or "remaining_required_documents" in interviewer_state_json
        )
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

        advisory_context = interviewer_state_json.get("advisory_context", {})
        if isinstance(advisory_context, dict):
            for document_type in self._list_payload(
                advisory_context.get("missing_evidence")
            ):
                if document_type and document_type not in missing_evidence:
                    missing_evidence.append(document_type)

        if not has_explicit_document_summary:
            current_key_proof = interviewer_state_json.get("current_key_proof")
            if current_key_proof and current_key_proof not in missing_evidence:
                missing_evidence.append(current_key_proof)
            focus_document_type = current_focus_json.get("document_type")
            if focus_document_type and focus_document_type not in missing_evidence:
                missing_evidence.append(focus_document_type)

        return missing_evidence

    def _has_case_board_state(self, case_board: dict[str, Any]) -> bool:
        return case_board_has_state(case_board)

    def _waiting_key_proof_summary(self, current_key_proof: str | None) -> str:
        if current_key_proof:
            return (
                f"当前待核实事实是 {current_key_proof}，可以先继续说明事实来源；"
                "如有材料，可作为补强证据。"
            )
        return "当前案例主线可继续推进，但仍有待核实事实需要被回答或证据支持。"

    def _waiting_key_proof_recommendation(self, current_key_proof: str | None) -> str:
        if current_key_proof:
            return f"围绕 {current_key_proof} 说明事实来源；如果有材料，可作为补强证据上传。"
        return "继续回答关键问题，并用材料或事实细节补强证据链。"

    def _document_review_issue_summary(
        self,
        document_review: dict[str, Any],
    ) -> str | None:
        for key in ("claim_conflicts", "cross_document_conflicts"):
            for item in self._list_payload(document_review.get(key)):
                if not isinstance(item, dict):
                    continue
                summary = str(item.get("summary") or "").strip()
                if summary:
                    return summary

        for value in self._list_payload(
            document_review.get("unresolved_verification_points")
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()

        reviewer_summary = str(document_review.get("reviewer_summary") or "").strip()
        if reviewer_summary:
            return reviewer_summary

        return None

    def _resolve_public_status(
        self,
        *,
        governor_decision: str,
        phase_state: str,
        gate_status: dict,
        missing_evidence: list[str],
        interviewer_state_json: dict,
    ) -> str:
        del phase_state, gate_status
        public_status = interviewer_state_json.get("public_status")
        if public_status:
            return public_status
        if governor_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return InterviewStateStatus.SIMULATED_REFUSAL.value
        if governor_decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return InterviewStateStatus.HIGH_RISK_REVIEW.value
        if governor_decision == GovernorDecision.ROUTE_CORRECTION.value:
            return InterviewStateStatus.VERIFY_KEY_ISSUE.value
        if governor_decision == GovernorDecision.NEED_MORE_EVIDENCE.value:
            if missing_evidence:
                return InterviewStateStatus.WAITING_KEY_PROOF.value
            return InterviewStateStatus.VERIFY_KEY_ISSUE.value
        return InterviewStateStatus.CONTINUE_INTERVIEW.value

    def _resolve_risk_level(
        self,
        *,
        interview_status: str,
        interviewer_state_json: dict,
        case_board: dict[str, Any],
    ) -> str:
        risk_level = interviewer_state_json.get("risk_level")
        if risk_level:
            return risk_level
        conflicts = [
            item
            for item in self._list_payload(case_board.get("conflicts"))
            if isinstance(item, dict)
        ]
        if any(item.get("severity") == "high" for item in conflicts):
            return "high"
        if conflicts:
            return "medium"
        if interview_status in {
            InterviewStateStatus.HIGH_RISK_REVIEW.value,
            InterviewStateStatus.SIMULATED_REFUSAL.value,
        }:
            return "high"
        if interview_status == InterviewStateStatus.VERIFY_KEY_ISSUE.value:
            return "medium"
        return "none"

    def _resolve_interview_result(
        self,
        *,
        interview_status: str,
        risk_level: str,
        missing_evidence: list[str],
        risk_points: list[str],
        remaining_required_documents: list[str],
        current_key_proof: str | None,
        runtime_view_state: dict[str, Any],
    ) -> tuple[str, str, str]:
        if interview_status == InterviewStateStatus.SIMULATED_REFUSAL.value:
            return (
                InterviewResult.REFUSED.value,
                "模拟拒签",
                "当前记录存在拒签级别风险或已确认硬冲突，本轮模拟结果为拒签。",
            )

        if interview_status == InterviewStateStatus.HIGH_RISK_REVIEW.value:
            return (
                InterviewResult.NOT_PASSED.value,
                "未通过：高风险待复核",
                "当前仍有高风险事项需要先复核，本轮不能给出通过结论。",
            )

        if interview_status in {
            InterviewStateStatus.VERIFY_KEY_ISSUE.value,
            InterviewStateStatus.WAITING_KEY_PROOF.value,
        }:
            return (
                InterviewResult.NOT_PASSED.value,
                "未通过：关键事实待核实",
                "当前仍有关键事实或证据链需要补强，本轮不能给出通过结论。",
            )

        if risk_level in {"medium", "high"} or risk_points:
            return (
                InterviewResult.NOT_PASSED.value,
                "未通过：风险待处理",
                "当前仍有材料或回答风险点需要处理，本轮不能给出通过结论。",
            )

        if missing_evidence or remaining_required_documents or current_key_proof:
            return (
                InterviewResult.NOT_PASSED.value,
                "未通过：材料或事实待补强",
                "当前仍有待核实材料或事实，本轮不能给出通过结论。",
            )

        if self._is_interview_closure(runtime_view_state):
            return (
                InterviewResult.PASSED.value,
                "本轮模拟通过",
                "签证官已自然结束问答，且当前没有明显风险、待补材料或关键事实冲突，本轮模拟可判定为通过。",
            )

        return (
            InterviewResult.IN_PROGRESS.value,
            "继续面谈",
            "当前没有形成拒签或通过结论，仍处于正式问答过程中。",
        )

    def _is_interview_closure(self, runtime_view_state: dict[str, Any]) -> bool:
        content = str(runtime_view_state.get("source_turn_content") or "").strip()
        if not content:
            return False
        normalized = " ".join(content.lower().split())
        closure_markers = (
            "that will be all",
            "that is all",
            "that's all",
            "no further questions",
            "i have no further questions",
            "your visa is approved",
            "your application is approved",
            "approved",
            "没有其他问题",
            "没有别的问题",
            "我这边没有其他问题",
            "问题就到这里",
            "今天就到这里",
            "面谈结束",
            "签证通过",
            "通过了",
        )
        return any(marker in normalized for marker in closure_markers)

    def _case_strengths(self, case_board: dict[str, Any]) -> list[str]:
        strengths: list[str] = []
        for claim in self._list_payload(case_board.get("claims")):
            if not isinstance(claim, dict):
                continue
            if claim.get("status") != "documented":
                continue
            field_path = str(claim.get("field_path") or "").strip()
            value = str(claim.get("value") or "").strip()
            if not field_path:
                continue
            text = f"{field_path} 已有材料证据支持"
            if value:
                text = f"{text}：{value}"
            if text not in strengths:
                strengths.append(text)
        return strengths[:5]

    def _case_risk_points(self, case_board: dict[str, Any]) -> list[str]:
        points: list[str] = []
        for conflict in self._list_payload(case_board.get("conflicts")):
            if not isinstance(conflict, dict):
                continue
            summary = str(conflict.get("summary") or "").strip()
            if summary and summary not in points:
                points.append(summary)
        for claim in self._list_payload(case_board.get("claims")):
            if not isinstance(claim, dict) or claim.get("status") != "contradicted":
                continue
            field_path = str(claim.get("field_path") or "").strip()
            value = str(claim.get("value") or "").strip()
            text = f"{field_path} 存在证据冲突" if field_path else "存在证据冲突"
            if value:
                text = f"{text}：{value}"
            if text not in points:
                points.append(text)
        return points[:5]

    def _merge_recommendations(
        self,
        recommendations: list[str],
        case_board: dict[str, Any],
    ) -> list[str]:
        merged = list(recommendations)
        for conflict in self._list_payload(case_board.get("conflicts")):
            if not isinstance(conflict, dict):
                continue
            followup = str(conflict.get("suggested_followup") or "").strip()
            if followup and followup not in merged:
                merged.append(followup)
        for proof in self._list_payload(case_board.get("proof_points")):
            if not isinstance(proof, dict):
                continue
            if proof.get("status") not in {"missing", "partial", "contradicted"}:
                continue
            question = str(proof.get("question") or "").strip()
            if not question:
                continue
            text = f"围绕待核实事实补强证据链：{question}"
            if text not in merged:
                merged.append(text)
        return merged[:6]

    def _proof_point_code(self, proof: dict[str, Any]) -> str | None:
        return proof_point_code(proof)

    def _list_payload(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _resolve_requested_documents(
        self,
        *,
        effective_interviewer_state: dict[str, Any],
        current_focus_json: dict[str, Any],
        remaining_required_documents: list[str],
        missing_evidence: list[str],
    ) -> list[str]:
        """Project top-level requested_documents aligned with turn responses.

        Prefer explicit interviewer/runtime requested_documents. When the key is
        absent, fall back to current focus + remaining required docs + missing
        evidence so the report contract stays useful for frontend consumers.
        """
        if "requested_documents" in effective_interviewer_state:
            return self._dedupe_document_types(
                effective_interviewer_state.get("requested_documents")
            )

        projected: list[str] = []
        focus_kind = current_focus_json.get("kind")
        focus_document_type = current_focus_json.get("document_type")
        if (
            focus_kind == "required_document"
            and isinstance(focus_document_type, str)
            and focus_document_type.strip()
        ):
            projected.append(focus_document_type.strip())

        for source in (remaining_required_documents, missing_evidence):
            for document_type in self._dedupe_document_types(source):
                if document_type not in projected:
                    projected.append(document_type)
        return projected

    def _dedupe_document_types(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        seen: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            document_type = item.strip()
            if document_type and document_type not in seen:
                seen.append(document_type)
        return seen
