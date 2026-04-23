from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.contracts import ScoreState
from app.domain.runtime import RuntimeTraceEntry
from app.services.retrieval_service import RetrievalService


@dataclass
class CapabilityOrchestrationResult:
    capability_plan: list[dict[str, Any]] = field(default_factory=list)
    tool_outputs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    trace_entries: list[RuntimeTraceEntry] = field(default_factory=list)


class CapabilityOrchestrator:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db

    def orchestrate(
        self,
        *,
        session_id: str,
        governor_decision: str,
        latest_user_message: str,
        dynamic_turn_context: dict[str, Any],
        score: ScoreState,
    ) -> CapabilityOrchestrationResult:
        evidence_digest = self._payload(dynamic_turn_context.get("evidence_digest"))
        focus_thread = self._payload(dynamic_turn_context.get("focus_thread"))
        advisory_context = self._payload(dynamic_turn_context.get("advisory_context"))

        capability_plan: list[dict[str, Any]] = []
        tool_outputs: dict[str, Any] = {}

        document_assessment = self._document_assessment_output(
            evidence_digest=evidence_digest,
        )
        capability_plan.append(
            self._plan_entry(
                capability_name="document_assessment",
                governor_decision=governor_decision,
                completed=bool(document_assessment),
                reason=(
                    "上传材料和主线反馈已进入当前上下文"
                    if document_assessment
                    else "当前回合没有可消费的上传材料摘要"
                ),
                summary=(
                    f"uploaded={document_assessment['uploaded_document_count']}"
                    if document_assessment
                    else "no_uploaded_document_context"
                ),
            )
        )
        if document_assessment:
            tool_outputs["document_assessment"] = document_assessment

        evidence_retrieval = self._evidence_retrieval_output(
            session_id=session_id,
            latest_user_message=latest_user_message,
            evidence_digest=evidence_digest,
            focus_thread=focus_thread,
        )
        capability_plan.append(
            self._plan_entry(
                capability_name="evidence_retrieval",
                governor_decision=governor_decision,
                completed=evidence_retrieval is not None,
                reason=(
                    "根据当前主线和用户最新输入主动检索证据"
                    if evidence_retrieval is not None
                    else "当前回合没有足够稳定的检索锚点"
                ),
                summary=(
                    f"hits={evidence_retrieval['hit_count']}"
                    if evidence_retrieval is not None
                    else "no_query"
                ),
            )
        )
        if evidence_retrieval is not None:
            tool_outputs["evidence_retrieval"] = evidence_retrieval

        consistency_review = self._consistency_review_output(
            score=score,
            advisory_context=advisory_context,
            focus_thread=focus_thread,
        )
        capability_plan.append(
            self._plan_entry(
                capability_name="consistency_review",
                governor_decision=governor_decision,
                completed=consistency_review is not None,
                reason=(
                    "当前风险/缺口需要显式一致性摘要"
                    if consistency_review is not None
                    else "当前回合没有需要额外展开的一致性信号"
                ),
                summary=(
                    f"risk_codes={len(consistency_review['risk_codes'])}"
                    if consistency_review is not None
                    else "no_consistency_signal"
                ),
            )
        )
        if consistency_review is not None:
            tool_outputs["consistency_review"] = consistency_review

        artifacts = self._build_artifacts(tool_outputs)
        trace_entries = [
            RuntimeTraceEntry(
                node_name="decide_capability",
                summary=self._plan_summary(capability_plan),
                metadata={
                    "governor_decision": governor_decision,
                    "capability_plan": capability_plan,
                },
            ),
            RuntimeTraceEntry(
                node_name="resolve_capability",
                summary=self._resolve_summary(tool_outputs),
                metadata={
                    "governor_decision": governor_decision,
                    "capability_plan": capability_plan,
                    "resolved_capabilities": sorted(tool_outputs.keys()),
                    "artifacts": artifacts,
                },
            ),
        ]
        return CapabilityOrchestrationResult(
            capability_plan=capability_plan,
            tool_outputs=tool_outputs,
            artifacts=artifacts,
            trace_entries=trace_entries,
        )

    def _document_assessment_output(
        self,
        *,
        evidence_digest: dict[str, Any],
    ) -> dict[str, Any] | None:
        current_focus_document_type = self._string_or_none(
            evidence_digest.get("current_focus_document_type")
        )
        uploaded_documents = self._list_payload(evidence_digest.get("uploaded_documents"))
        active_feedback = self._payload(evidence_digest.get("active_main_flow_feedback"))
        supported_claims = self._string_list(evidence_digest.get("supported_claims"))
        uploaded_document_count = self._int_or_zero(
            evidence_digest.get("uploaded_document_count")
        )
        if (
            not current_focus_document_type
            and not uploaded_documents
            and not active_feedback
            and not supported_claims
        ):
            return None

        relevant_uploaded_documents = [
            document
            for document in uploaded_documents
            if (
                self._payload(document.get("main_flow_feedback")).get(
                    "current_focus_document_type"
                )
                == current_focus_document_type
                or self._string_or_none(document.get("document_type"))
                == current_focus_document_type
            )
        ]
        if not relevant_uploaded_documents:
            relevant_uploaded_documents = uploaded_documents

        return {
            "current_focus_document_type": current_focus_document_type,
            "uploaded_document_count": uploaded_document_count or len(uploaded_documents),
            "supported_claims": supported_claims[:5],
            "active_main_flow_feedback": active_feedback,
            "relevant_uploaded_documents": relevant_uploaded_documents[:3],
        }

    def _evidence_retrieval_output(
        self,
        *,
        session_id: str,
        latest_user_message: str,
        evidence_digest: dict[str, Any],
        focus_thread: dict[str, Any],
    ) -> dict[str, Any] | None:
        current_focus_document_type = self._string_or_none(
            evidence_digest.get("current_focus_document_type")
        )
        current_key_proof = self._string_or_none(focus_thread.get("current_key_proof"))
        supported_claims = self._string_list(evidence_digest.get("supported_claims"))
        query = (
            current_focus_document_type
            or current_key_proof
            or self._string_or_none(latest_user_message)
        )
        if query is None:
            return None

        normalized_query = query.replace("_", " ")
        field_path = supported_claims[0] if supported_claims else None
        hits: list[dict[str, Any]] = []
        try:
            retrieval = RetrievalService(self.db)
            raw_hits = retrieval.search_session_evidence(
                session_id,
                normalized_query,
                evidence_type=current_focus_document_type,
                field_path=field_path,
                limit=3,
            )
            hits = [
                {
                    "evidence_id": hit.evidence_id,
                    "document_id": hit.document_id,
                    "evidence_type": hit.evidence_type,
                    "field_path": hit.field_path,
                    "excerpt": hit.excerpt,
                    "filename": hit.filename,
                    "source_type": hit.source_type.value,
                    "score": hit.score,
                }
                for hit in raw_hits
            ]
        except Exception:
            hits = []

        return {
            "query": normalized_query,
            "evidence_type": current_focus_document_type,
            "field_path": field_path,
            "hit_count": len(hits),
            "hits": hits,
        }

    def _consistency_review_output(
        self,
        *,
        score: ScoreState,
        advisory_context: dict[str, Any],
        focus_thread: dict[str, Any],
    ) -> dict[str, Any] | None:
        risk_codes = self._string_list(advisory_context.get("risk_codes"))
        missing_evidence = self._string_list(advisory_context.get("missing_evidence"))
        current_risk_code = self._string_or_none(focus_thread.get("current_risk_code"))
        risk_level = self._string_or_none(advisory_context.get("risk_level"))
        if current_risk_code and current_risk_code not in risk_codes:
            risk_codes.insert(0, current_risk_code)
        if not risk_codes and not missing_evidence and not score.risk_flags:
            return None

        top_risk_flags = [
            {
                "code": risk_flag.code,
                "severity": risk_flag.severity,
                "status": risk_flag.status,
                "evidence_refs": list(risk_flag.evidence_refs),
            }
            for risk_flag in score.risk_flags[:3]
        ]
        return {
            "risk_level": risk_level,
            "risk_codes": risk_codes,
            "missing_evidence": missing_evidence,
            "top_risk_flags": top_risk_flags,
        }

    def _build_artifacts(
        self,
        tool_outputs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        document_assessment = self._payload(tool_outputs.get("document_assessment"))
        if document_assessment:
            artifacts.append(
                {
                    "kind": "capability",
                    "capability_name": "document_assessment",
                    "status": "completed",
                    "current_focus_document_type": document_assessment.get(
                        "current_focus_document_type"
                    ),
                    "uploaded_document_count": document_assessment.get(
                        "uploaded_document_count"
                    ),
                    "feedback_status": self._payload(
                        document_assessment.get("active_main_flow_feedback")
                    ).get("status"),
                }
            )
        evidence_retrieval = self._payload(tool_outputs.get("evidence_retrieval"))
        if evidence_retrieval:
            artifacts.append(
                {
                    "kind": "capability",
                    "capability_name": "evidence_retrieval",
                    "status": "completed",
                    "query": evidence_retrieval.get("query"),
                    "hit_count": evidence_retrieval.get("hit_count", 0),
                }
            )
        consistency_review = self._payload(tool_outputs.get("consistency_review"))
        if consistency_review:
            artifacts.append(
                {
                    "kind": "capability",
                    "capability_name": "consistency_review",
                    "status": "completed",
                    "risk_codes": list(consistency_review.get("risk_codes", []) or []),
                    "missing_evidence": list(
                        consistency_review.get("missing_evidence", []) or []
                    ),
                }
            )
        return artifacts

    def _plan_entry(
        self,
        *,
        capability_name: str,
        governor_decision: str,
        completed: bool,
        reason: str,
        summary: str,
    ) -> dict[str, Any]:
        return {
            "capability_name": capability_name,
            "status": "completed" if completed else "skipped",
            "governor_decision": governor_decision,
            "reason": reason,
            "summary": summary,
        }

    def _plan_summary(self, capability_plan: list[dict[str, Any]]) -> str:
        completed = [
            item["capability_name"]
            for item in capability_plan
            if item.get("status") == "completed"
        ]
        if completed:
            return f"planned={','.join(completed)}"
        return "planned=none"

    def _resolve_summary(self, tool_outputs: dict[str, Any]) -> str:
        if tool_outputs:
            return f"resolved={','.join(sorted(tool_outputs))}"
        return "resolved=none"

    def _payload(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _list_payload(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            normalized_item = self._string_or_none(item)
            if normalized_item and normalized_item not in normalized:
                normalized.append(normalized_item)
        return normalized

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _int_or_zero(self, value: Any) -> int:
        if isinstance(value, int) and value >= 0:
            return value
        return 0
