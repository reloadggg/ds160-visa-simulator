from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.document_review_agent import DocumentReviewAgentRunner
from app.agents.model_factory import AgentModelFactory
from app.agents.schemas import AgentRuntimeDeps, DocumentReviewResult
from app.domain.evidence import DocumentAssessment
from app.domain.contracts import ScoreState
from app.domain.runtime import RuntimeTraceEntry
from app.repositories.document_repo import DocumentRepository
from app.services.evidence_service import EvidenceService
from app.services.retrieval_service import RetrievalService
from app.services.visa_policy_retrieval_service import VisaPolicyRetrievalService


@dataclass
class CapabilityOrchestrationResult:
    capability_plan: list[dict[str, Any]] = field(default_factory=list)
    tool_outputs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    trace_entries: list[RuntimeTraceEntry] = field(default_factory=list)


class CapabilityOrchestrator:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db
        self.model_factory = AgentModelFactory()
        has_sqlalchemy_session = hasattr(db, "scalars")
        self.document_repo = DocumentRepository(db) if has_sqlalchemy_session else None
        self.evidence = EvidenceService(db) if has_sqlalchemy_session else None

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
        gate_progress = self._payload(dynamic_turn_context.get("gate_progress"))
        review_context = self._build_document_review_context(
            session_id=session_id,
            dynamic_turn_context=dynamic_turn_context,
            evidence_digest=evidence_digest,
            focus_thread=focus_thread,
            advisory_context=advisory_context,
            gate_progress=gate_progress,
        )

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

        document_review = self._document_review_output(
            session_id=session_id,
            governor_decision=governor_decision,
            latest_user_message=latest_user_message,
            dynamic_turn_context=dynamic_turn_context,
            evidence_digest=evidence_digest,
            focus_thread=focus_thread,
            advisory_context=advisory_context,
            gate_progress=gate_progress,
            review_context=review_context,
        )
        capability_plan.append(
            self._plan_entry(
                capability_name="document_review",
                governor_decision=governor_decision,
                completed=document_review is not None,
                reason=(
                    "基于当前材料、门控进度和既有风险生成材料核验结论"
                    if document_review is not None
                    else "当前回合没有足够的材料核验上下文"
                ),
                summary=(
                    f"status={document_review['review_status']}"
                    if document_review is not None
                    else "no_document_review"
                ),
            )
        )
        if document_review is not None:
            tool_outputs["document_review"] = document_review

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

        policy_knowledge = self._policy_knowledge_retrieval_output(
            latest_user_message=latest_user_message,
            dynamic_turn_context=dynamic_turn_context,
            evidence_digest=evidence_digest,
            focus_thread=focus_thread,
        )
        capability_plan.append(
            self._plan_entry(
                capability_name="policy_knowledge_retrieval",
                governor_decision=governor_decision,
                completed=policy_knowledge is not None
                and not bool(policy_knowledge.get("skipped")),
                reason=self._policy_knowledge_reason(policy_knowledge),
                summary=self._policy_knowledge_summary(policy_knowledge),
            )
        )
        if policy_knowledge is not None:
            tool_outputs["policy_knowledge_retrieval"] = policy_knowledge

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
                    "document_review_context": self._context_trace_summary(
                        review_context
                    ),
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
                    "document_review_context": self._context_trace_summary(
                        review_context
                    ),
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
        remaining_required_documents = self._string_list(
            evidence_digest.get("remaining_required_documents")
        )
        verified_documents = self._string_list(
            evidence_digest.get("verified_documents")
        )
        if (
            not current_focus_document_type
            and not uploaded_documents
            and not active_feedback
            and not supported_claims
            and not remaining_required_documents
            and not verified_documents
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
            "remaining_required_documents": remaining_required_documents,
            "verified_documents": verified_documents,
        }

    def _document_review_output(
        self,
        *,
        session_id: str,
        governor_decision: str,
        latest_user_message: str,
        dynamic_turn_context: dict[str, Any],
        evidence_digest: dict[str, Any],
        focus_thread: dict[str, Any],
        advisory_context: dict[str, Any],
        gate_progress: dict[str, Any],
        review_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        fallback = self._fallback_document_review(
            evidence_digest=evidence_digest,
            focus_thread=focus_thread,
            advisory_context=advisory_context,
            gate_progress=gate_progress,
        )
        if review_context is None:
            review_context = self._build_document_review_context(
                session_id=session_id,
                dynamic_turn_context=dynamic_turn_context,
                evidence_digest=evidence_digest,
                focus_thread=focus_thread,
                advisory_context=advisory_context,
                gate_progress=gate_progress,
            )
        context_fallback = self._fallback_document_review_from_context(
            review_context,
        )
        if context_fallback is not None:
            fallback = self._merge_document_review_payload(context_fallback, fallback)
        if not review_context and fallback is None:
            return None
        if self.db is None or self.document_repo is None or self.evidence is None:
            return fallback

        declared_family = self._string_or_none(dynamic_turn_context.get("declared_family"))
        model, runtime = self._build_review_agent_runtime(declared_family)
        if model is None:
            return fallback

        try:
            result = DocumentReviewAgentRunner(
                model=model,
                instructions=runtime.get("instructions")
                or self.model_factory.build_instructions(
                    "document_review_agent",
                    declared_family=declared_family,
                ),
            ).run(
                deps=self._build_agent_deps(session_id),
                dynamic_turn_context=dynamic_turn_context,
                review_context=review_context,
                user_message=latest_user_message,
                boundary_decision=governor_decision,
            )
        except Exception:
            return fallback

        payload = result.model_dump(mode="json")
        return self._merge_document_review_payload(payload, fallback)

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

    def _policy_knowledge_retrieval_output(
        self,
        *,
        latest_user_message: str,
        dynamic_turn_context: dict[str, Any],
        evidence_digest: dict[str, Any],
        focus_thread: dict[str, Any],
    ) -> dict[str, Any] | None:
        query = self._policy_query(
            latest_user_message=latest_user_message,
            evidence_digest=evidence_digest,
            focus_thread=focus_thread,
        )
        if query is None:
            return None

        case_brief = self._payload(dynamic_turn_context.get("case_brief"))
        visa_family = self._string_or_none(
            dynamic_turn_context.get("declared_family")
        ) or self._string_or_none(case_brief.get("declared_family"))
        try:
            result = VisaPolicyRetrievalService().search_policy(
                query,
                visa_family=visa_family,
                limit=5,
            )
        except Exception:
            return {
                "query": query,
                "hit_count": 0,
                "hits": [],
                "citations": [],
                "citation_policy": "official_first",
                "skipped": True,
                "skip_reason": "retrieval_error",
            }
        if hasattr(result, "tool_payload"):
            return result.tool_payload()
        if isinstance(result, dict):
            return dict(result)
        return None

    def _policy_query(
        self,
        *,
        latest_user_message: str,
        evidence_digest: dict[str, Any],
        focus_thread: dict[str, Any],
    ) -> str | None:
        candidates = [
            self._string_or_none(evidence_digest.get("current_focus_document_type")),
            self._string_or_none(focus_thread.get("current_key_proof")),
            self._string_or_none(focus_thread.get("current_key_question")),
            self._string_or_none(latest_user_message),
        ]
        for candidate in candidates:
            if candidate:
                return candidate.replace("_", " ")
        return None

    def _policy_knowledge_reason(self, policy_knowledge: dict[str, Any] | None) -> str:
        if policy_knowledge is None:
            return "当前回合没有足够稳定的政策检索锚点"
        if policy_knowledge.get("skipped"):
            return f"政策知识检索跳过：{policy_knowledge.get('skip_reason')}"
        return "根据当前签证类型、主线和用户输入检索美签政策知识"

    def _policy_knowledge_summary(self, policy_knowledge: dict[str, Any] | None) -> str:
        if policy_knowledge is None:
            return "no_query"
        if policy_knowledge.get("skipped"):
            return f"skipped={policy_knowledge.get('skip_reason')}"
        return f"hits={policy_knowledge.get('hit_count', 0)}"

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
        document_review = self._payload(tool_outputs.get("document_review"))
        if document_review:
            artifacts.append(
                {
                    "kind": "capability",
                    "capability_name": "document_review",
                    "status": "completed",
                    "review_status": document_review.get("review_status"),
                    "primary_document": document_review.get("primary_document"),
                    "remaining_required_count": len(
                        document_review.get("remaining_required_documents", []) or []
                    ),
                    "conflict_count": len(
                        document_review.get("cross_document_conflicts", []) or []
                    )
                    + len(document_review.get("claim_conflicts", []) or []),
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
        policy_knowledge = self._payload(tool_outputs.get("policy_knowledge_retrieval"))
        if policy_knowledge:
            artifacts.append(
                {
                    "kind": "capability",
                    "capability_name": "policy_knowledge_retrieval",
                    "status": (
                        "skipped"
                        if policy_knowledge.get("skipped")
                        else "completed"
                    ),
                    "query": policy_knowledge.get("query"),
                    "hit_count": policy_knowledge.get("hit_count", 0),
                    "skip_reason": policy_knowledge.get("skip_reason"),
                    "policy_citations": list(
                        policy_knowledge.get("citations", []) or []
                    ),
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

    def _context_trace_summary(self, review_context: dict[str, Any]) -> dict[str, Any]:
        documents = [
            document
            for document in review_context.get("documents", [])
            if isinstance(document, dict)
        ]
        return {
            "document_count": len(documents),
            "document_ids": [
                str(document.get("document_id"))
                for document in documents[:8]
                if document.get("document_id")
            ],
        }

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

    def _build_review_agent_runtime(
        self,
        declared_family: str | None,
    ) -> tuple[Any | None, dict[str, Any]]:
        try:
            return self.model_factory.build(
                "document_review_agent",
                "interview_turn",
                declared_family=declared_family,
            )
        except TypeError as exc:
            if "declared_family" not in str(exc):
                raise
            return self.model_factory.build("document_review_agent", "interview_turn")

    def _build_agent_deps(self, session_id: str) -> AgentRuntimeDeps:
        return AgentRuntimeDeps(
            session_id=session_id,
            retrieval=RetrievalService(self.db),
            evidence=EvidenceService(self.db),
            policy_retrieval=VisaPolicyRetrievalService(),
        )

    def _build_document_review_context(
        self,
        *,
        session_id: str,
        dynamic_turn_context: dict[str, Any],
        evidence_digest: dict[str, Any],
        focus_thread: dict[str, Any],
        advisory_context: dict[str, Any],
        gate_progress: dict[str, Any],
    ) -> dict[str, Any]:
        if self.document_repo is None or self.evidence is None:
            return {}

        documents = []
        for document in self.document_repo.list_session_documents(session_id):
            artifact_json = document.artifact_json or {}
            artifact_metadata = self._payload(artifact_json.get("metadata"))
            assessment = DocumentAssessment.from_artifact(document.artifact_json)
            document_type = self._string_or_none(assessment.document_type)
            candidate_document_types = self._document_type_candidates(assessment)
            extracted_fields_by_document_type = {
                candidate: self.evidence.extract_document_fields(
                    document.document_id,
                    candidate,
                )
                for candidate in candidate_document_types
            }
            extracted_fields = (
                extracted_fields_by_document_type.get(document_type, {})
                if document_type is not None
                else {}
            )
            main_flow_feedback = (
                {}
                if assessment.main_flow_feedback is None
                else assessment.main_flow_feedback.model_dump(
                    mode="json",
                    exclude_none=True,
                )
            )
            raw_text = getattr(document, "raw_text", "") or ""
            documents.append(
                {
                    "document_id": document.document_id,
                    "filename": document.filename,
                    "status": document.status,
                    "document_type": document_type,
                    "document_type_candidates": list(
                        assessment.document_type_candidates
                    ),
                    "relevance": assessment.relevance,
                    "extracted_fields_by_document_type": extracted_fields_by_document_type,
                    "supported_claims": list(assessment.supported_claims),
                    "counts_toward_gate": assessment.counts_toward_gate,
                    "main_flow_feedback": main_flow_feedback,
                    "extracted_fields": extracted_fields,
                    "raw_text_excerpt": raw_text[:1200],
                    "synthetic_metadata": {
                        "debug_material_bundle": bool(
                            artifact_metadata.get("debug_material_bundle")
                        ),
                    },
                }
            )

        profile_snapshot = self._payload(dynamic_turn_context.get("profile_snapshot"))
        current_focus = self._payload(dynamic_turn_context.get("current_focus"))
        if (
            not documents
            and not gate_progress.get("required_documents")
            and not evidence_digest.get("missing_evidence")
            and not advisory_context.get("risk_codes")
        ):
            return {}
        return {
            "current_focus": current_focus,
            "focus_thread": focus_thread,
            "gate_progress": gate_progress,
            "evidence_digest": evidence_digest,
            "advisory_context": advisory_context,
            "profile_claims": self._profile_claims(profile_snapshot),
            "documents": documents,
        }


    def _document_type_candidates(self, assessment: DocumentAssessment) -> list[str]:
        return self._string_list(
            [
                *([assessment.document_type] if assessment.document_type else []),
                *assessment.document_type_candidates,
            ]
        )

    def _profile_claims(self, profile_snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "identity": self._payload(profile_snapshot.get("identity")),
            "education": self._payload(profile_snapshot.get("education")),
            "funding": self._payload(profile_snapshot.get("funding")),
            "visa_intent": self._payload(profile_snapshot.get("visa_intent")),
            "travel": self._payload(profile_snapshot.get("travel")),
            "field_states": self._payload(profile_snapshot.get("field_states")),
            "ds160_view": self._payload(profile_snapshot.get("ds160_view")),
            "document_evidence_snapshot": self._payload(
                self._payload(profile_snapshot.get("ds160_view")).get(
                    "document_evidence_snapshot"
                )
            ),
        }

    def _fallback_document_review(
        self,
        *,
        evidence_digest: dict[str, Any],
        focus_thread: dict[str, Any],
        advisory_context: dict[str, Any],
        gate_progress: dict[str, Any],
    ) -> dict[str, Any] | None:
        remaining_required_documents = self._remaining_required_documents(
            gate_progress,
            evidence_digest,
        )
        verified_documents = self._verified_documents(
            gate_progress,
            evidence_digest,
        )
        current_focus_document = self._string_or_none(
            focus_thread.get("current_key_proof")
        ) or self._string_or_none(evidence_digest.get("current_focus_document_type"))
        risk_codes = self._string_list(advisory_context.get("risk_codes"))
        risk_level = self._string_or_none(advisory_context.get("risk_level"))
        awaiting_parse = self._awaiting_parse_documents(gate_progress)
        unresolved_points = list(remaining_required_documents)
        for document_type in awaiting_parse:
            message = f"{document_type} 已上传但仍在解析"
            if message not in unresolved_points:
                unresolved_points.append(message)

        cross_document_conflicts: list[dict[str, Any]] = []
        if "record_conflict" in risk_codes:
            cross_document_conflicts.append(
                {
                    "conflict_type": "document_vs_document",
                    "severity": "high" if risk_level == "high" else "medium",
                    "summary": "当前材料之间存在待核实的记录冲突，需要先完成交叉核验。",
                    "field_paths": [],
                    "document_ids": [],
                    "evidence_refs": [],
                }
            )

        claim_conflicts: list[dict[str, Any]] = []
        if "evasive_answer" in risk_codes:
            claim_conflicts.append(
                {
                    "conflict_type": "claim_vs_document",
                    "severity": "medium",
                    "summary": "当前口头说明仍未正面回应材料核验点。",
                    "field_paths": [],
                    "document_ids": [],
                    "evidence_refs": [],
                }
            )

        if not remaining_required_documents and not verified_documents and not risk_codes:
            return None

        review_status = "reviewed"
        recommended_next_step = "continue_interview"
        if cross_document_conflicts or (risk_level == "high" and risk_codes):
            review_status = "high_risk"
            recommended_next_step = "high_risk_review"
        elif awaiting_parse:
            review_status = "awaiting_parse"
            recommended_next_step = "request_documents"
        elif remaining_required_documents:
            review_status = "awaiting_documents"
            recommended_next_step = "request_documents"
        elif claim_conflicts:
            review_status = "needs_clarification"
            recommended_next_step = "clarify_conflict"

        if review_status == "high_risk":
            reviewer_summary = "材料核验已识别高风险冲突，当前应先围绕冲突点复核，不宜继续普通追问。"
        elif review_status == "awaiting_parse":
            reviewer_summary = "已有材料进入解析队列，但关键核验仍未完成，不能把上传评估直接当作已验证事实。"
        elif review_status == "awaiting_documents":
            reviewer_summary = "当前仍有关键材料缺失，应一次性告知完整待补清单，并标出当前最优先材料。"
        elif review_status == "needs_clarification":
            reviewer_summary = "材料与口头说明之间仍有未解开的核验点，需要先做定向澄清。"
        else:
            reviewer_summary = "当前关键材料已形成基础核验结论，可继续围绕主线问答。"

        return DocumentReviewResult(
            review_status=review_status,
            primary_document=current_focus_document
            or (remaining_required_documents[0] if remaining_required_documents else None),
            remaining_required_documents=remaining_required_documents,
            verified_documents=verified_documents,
            cross_document_conflicts=cross_document_conflicts,
            claim_conflicts=claim_conflicts,
            unresolved_verification_points=unresolved_points,
            suspicious_documents=[],
            reviewer_summary=reviewer_summary,
            recommended_next_step=recommended_next_step,
        ).model_dump(mode="json")

    def _fallback_document_review_from_context(
        self,
        review_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not review_context:
            return None

        documents = [
            document
            for document in review_context.get("documents", [])
            if isinstance(document, dict)
        ]
        if not documents:
            return None

        cross_document_conflicts: list[dict[str, Any]] = []
        claim_conflicts: list[dict[str, Any]] = []
        unresolved_points: list[str] = []

        school_conflict = self._field_value_conflict(
            documents,
            field_path="/education/school_name",
        )
        if school_conflict is not None:
            cross_document_conflicts.append(
                {
                    "conflict_type": "document_vs_document",
                    "severity": "high",
                    "summary": "I-20 与录取信中的学校名称不一致，需要核对最终入读学校。",
                    "document_ids": school_conflict["document_ids"],
                    "field_paths": ["/education/school_name"],
                    "evidence_refs": [],
                }
            )

        passport_conflict = self._field_value_conflict(
            documents,
            field_path="/identity/passport_number",
        )
        if passport_conflict is not None:
            cross_document_conflicts.append(
                {
                    "conflict_type": "document_vs_document",
                    "severity": "high",
                    "summary": "DS-160 与护照首页中的护照号码不一致。",
                    "document_ids": passport_conflict["document_ids"],
                    "field_paths": ["/identity/passport_number"],
                    "evidence_refs": [],
                }
            )

        funding_shortfall = self._funding_shortfall(documents)
        if funding_shortfall is not None:
            cross_document_conflicts.append(
                {
                    "conflict_type": "missing_verification",
                    "severity": "high",
                    "summary": (
                        "资金证明金额低于 I-20 第一年度费用，当前资金能力无法覆盖 "
                        "I-20 列示费用。"
                    ),
                    "document_ids": funding_shortfall["document_ids"],
                    "field_paths": [
                        "/education/first_year_cost",
                        "/funding/available_funds",
                    ],
                    "evidence_refs": [],
                }
            )
            unresolved_points.append("资金证明金额不足以覆盖 I-20 第一年度费用")

        if self._has_equity_chain_gap(documents):
            equity_document_ids = [
                str(document.get("document_id"))
                for document in documents
                if self._document_field_value(document, "/funding/source_detail")
            ]
            cross_document_conflicts.append(
                {
                    "conflict_type": "missing_verification",
                    "severity": "medium",
                    "summary": "资金来源涉及股权转让收益，但现有材料缺少独立链路证明。",
                    "document_ids": equity_document_ids,
                    "field_paths": [
                        "/funding/source_detail",
                        "/funding/equity_ownership",
                    ],
                    "evidence_refs": [],
                }
            )
            unresolved_points.append(
                "股权资金来源还需要公司登记、转让协议、税务或付款流水交叉核验"
            )

        profile_claims = self._payload(review_context.get("profile_claims"))
        funding_claim = (
            self._latest_claim_history_value(
                profile_claims,
                "/funding/primary_source",
            )
            or self._payload(profile_claims.get("funding")).get("primary_source")
        )
        documented_funding = self._single_document_field_value(
            documents,
            "/funding/primary_source",
        )
        if (
            isinstance(funding_claim, str)
            and isinstance(documented_funding, str)
            and funding_claim.strip().casefold()
            != documented_funding.strip().casefold()
        ):
            claim_conflicts.append(
                {
                    "conflict_type": "claim_vs_document",
                    "severity": "high",
                    "summary": "口头资金来源说明与已提交资金证明不一致。",
                    "document_ids": [
                        str(document.get("document_id"))
                        for document in documents
                        if self._document_field_value(
                            document,
                            "/funding/primary_source",
                        )
                    ],
                    "field_paths": ["/funding/primary_source"],
                    "evidence_refs": [],
                }
            )

        if not cross_document_conflicts and not claim_conflicts and not unresolved_points:
            return None

        high_conflicts = [*cross_document_conflicts, *claim_conflicts]
        review_status = (
            "high_risk"
            if any(item.get("severity") == "high" for item in high_conflicts)
            else "needs_clarification"
            if claim_conflicts
            else "reviewed"
        )
        recommended_next_step = (
            "high_risk_review"
            if review_status == "high_risk"
            else "clarify_conflict"
            if claim_conflicts
            else "continue_interview"
        )
        return DocumentReviewResult(
            review_status=review_status,
            primary_document=self._context_primary_document(
                cross_document_conflicts,
                claim_conflicts,
            ),
            remaining_required_documents=[],
            verified_documents=self._context_verified_documents(documents),
            cross_document_conflicts=cross_document_conflicts,
            claim_conflicts=claim_conflicts,
            unresolved_verification_points=unresolved_points,
            suspicious_documents=[],
            reviewer_summary=(
                "材料核验根据已提交材料字段识别到冲突或待核验缺口，"
                "需要先围绕这些点复核。"
            ),
            recommended_next_step=recommended_next_step,
        ).model_dump(mode="json")

    def _field_value_conflict(
        self,
        documents: list[dict[str, Any]],
        *,
        field_path: str,
    ) -> dict[str, Any] | None:
        values: dict[str, list[str]] = {}
        for document in documents:
            value = self._document_field_value(document, field_path)
            if value is None:
                continue
            normalized = value.strip().casefold()
            if not normalized:
                continue
            values.setdefault(normalized, []).append(str(document.get("document_id")))
        if len(values) <= 1:
            return None
        return {
            "values": sorted(values),
            "document_ids": sorted(
                {
                    document_id
                    for document_ids in values.values()
                    for document_id in document_ids
                    if document_id
                }
            ),
        }

    def _funding_shortfall(
        self,
        documents: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        cost_documents: list[str] = []
        fund_documents: list[str] = []
        first_year_cost: float | None = None
        available_funds: float | None = None
        for document in documents:
            cost_value = self._document_field_value(document, "/education/first_year_cost")
            if cost_value is not None:
                parsed_cost = self._parse_money(cost_value)
                if parsed_cost is not None:
                    first_year_cost = max(first_year_cost or 0, parsed_cost)
                    cost_documents.append(str(document.get("document_id")))

            funds_value = self._document_field_value(document, "/funding/available_funds")
            if funds_value is not None:
                parsed_funds = self._parse_money(funds_value)
                if parsed_funds is not None:
                    available_funds = max(available_funds or 0, parsed_funds)
                    fund_documents.append(str(document.get("document_id")))

        if (
            first_year_cost is None
            or available_funds is None
            or available_funds >= first_year_cost
        ):
            return None
        return {
            "first_year_cost": first_year_cost,
            "available_funds": available_funds,
            "document_ids": sorted(set(cost_documents + fund_documents)),
        }

    def _has_equity_chain_gap(self, documents: list[dict[str, Any]]) -> bool:
        has_equity_funding = any(
            (
                self._document_field_value(document, "/funding/source_detail")
                or ""
            ).casefold().find("equity") >= 0
            or (
                self._document_field_value(document, "/funding/equity_ownership")
                is not None
            )
            for document in documents
        )
        if not has_equity_funding:
            return False
        document_types = {
            str(document.get("document_type") or "")
            for document in documents
            if isinstance(document.get("document_type"), str)
        }
        chain_documents = {
            "company_registration",
            "equity_transfer_agreement",
            "tax_record",
            "payment_trail",
        }
        return not bool(document_types & chain_documents)

    def _single_document_field_value(
        self,
        documents: list[dict[str, Any]],
        field_path: str,
    ) -> str | None:
        values = [
            value
            for document in documents
            if (value := self._document_field_value(document, field_path)) is not None
        ]
        if not values:
            return None
        return values[0]

    def _latest_claim_history_value(
        self,
        profile_claims: dict[str, Any],
        field_path: str,
    ) -> str | None:
        claim_history = self._payload(
            self._payload(profile_claims.get("ds160_view")).get(
                "field_claim_history"
            )
        )
        field_history = claim_history.get(field_path)
        if not isinstance(field_history, list):
            return None
        for item in reversed(field_history):
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _document_field_value(
        self,
        document: dict[str, Any],
        field_path: str,
    ) -> str | None:
        fields = self._payload(document.get("extracted_fields"))
        value = self._field_value_from_payload(fields, field_path)
        if isinstance(value, str) and value.strip():
            return value.strip()

        by_type = self._payload(document.get("extracted_fields_by_document_type"))
        for candidate_fields in by_type.values():
            payload = self._payload(candidate_fields)
            value = self._field_value_from_payload(payload, field_path)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _field_value_from_payload(
        self,
        fields: dict[str, Any],
        field_path: str,
    ) -> Any:
        if field_path in fields:
            return fields.get(field_path)
        short_key = field_path.rsplit("/", 1)[-1]
        return fields.get(short_key)

    def _parse_money(self, value: str) -> float | None:
        normalized = "".join(
            character for character in value if character.isdigit() or character == "."
        )
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None

    def _context_primary_document(
        self,
        cross_document_conflicts: list[dict[str, Any]],
        claim_conflicts: list[dict[str, Any]],
    ) -> str | None:
        for conflict in [*cross_document_conflicts, *claim_conflicts]:
            field_paths = conflict.get("field_paths")
            if not isinstance(field_paths, list):
                continue
            if "/funding/available_funds" in field_paths:
                return "funding_proof"
            if "/funding/source_detail" in field_paths:
                return "funding_proof"
            if "/funding/primary_source" in field_paths:
                return "funding_proof"
            if "/identity/passport_number" in field_paths:
                return "passport_bio"
            if "/education/school_name" in field_paths:
                return "i20"
        return None

    def _context_verified_documents(self, documents: list[dict[str, Any]]) -> list[str]:
        verified: list[str] = []
        for document in documents:
            document_type = self._string_or_none(document.get("document_type"))
            if document_type and document_type not in verified:
                verified.append(document_type)
        return verified

    def _merge_document_review_payload(
        self,
        payload: dict[str, Any],
        fallback: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not fallback:
            return self._normalize_document_review_risk(payload)

        merged = dict(payload)
        for key in (
            "remaining_required_documents",
            "verified_documents",
            "cross_document_conflicts",
            "claim_conflicts",
            "unresolved_verification_points",
            "suspicious_documents",
        ):
            merged[key] = self._merge_review_list_values(
                merged.get(key),
                fallback.get(key, []),
            )
        if not merged.get("primary_document"):
            merged["primary_document"] = fallback.get("primary_document")
        if not merged.get("review_status"):
            merged["review_status"] = fallback.get("review_status")
        if not merged.get("reviewer_summary"):
            merged["reviewer_summary"] = fallback.get("reviewer_summary")
        if not merged.get("recommended_next_step"):
            merged["recommended_next_step"] = fallback.get("recommended_next_step")
        merged = self._normalize_document_review_risk(merged)
        return DocumentReviewResult.model_validate(merged).model_dump(mode="json")

    def _merge_review_list_values(
        self,
        primary: Any,
        fallback: Any,
    ) -> list[Any]:
        values: list[Any] = []
        seen: set[str] = set()
        for item in [
            *self._review_list(primary),
            *self._review_list(fallback),
        ]:
            marker = repr(item)
            if marker in seen:
                continue
            seen.add(marker)
            values.append(item)
        return values

    def _review_list(self, value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        return []

    def _normalize_document_review_risk(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(payload)
        conflicts = [
            item
            for item in [
                *(merged.get("cross_document_conflicts", []) or []),
                *(merged.get("claim_conflicts", []) or []),
            ]
            if isinstance(item, dict)
        ]
        has_high_conflict = any(
            self._is_confirmed_high_review_conflict(item) for item in conflicts
        )
        if has_high_conflict:
            merged["review_status"] = "high_risk"
            merged["recommended_next_step"] = "high_risk_review"
        elif conflicts and merged.get("review_status") in {None, "", "reviewed"}:
            merged["review_status"] = "needs_clarification"
            merged["recommended_next_step"] = "clarify_conflict"
        return merged

    def _is_confirmed_high_review_conflict(self, conflict: dict[str, Any]) -> bool:
        if conflict.get("severity") != "high":
            return False
        summary = (self._string_or_none(conflict.get("summary")) or "").casefold()
        has_material_anchor = bool(
            self._string_list(conflict.get("document_ids"))
            or self._string_list(conflict.get("evidence_refs"))
        )
        if self._looks_like_unverified_missing_evidence(summary):
            return False if not has_material_anchor else True
        conflict_type = self._string_or_none(conflict.get("conflict_type"))
        if conflict_type in {"document_vs_document", "claim_vs_document"}:
            return True
        if conflict_type != "missing_verification":
            return False
        if not self._string_list(conflict.get("document_ids")):
            return False
        field_paths = set(self._string_list(conflict.get("field_paths")))
        if {
            "/education/first_year_cost",
            "/funding/available_funds",
        }.issubset(field_paths):
            return True
        shortfall_markers = (
            "低于",
            "不足",
            "无法覆盖",
            "不能覆盖",
            "below",
            "less than",
            "shortfall",
            "insufficient",
            "cannot cover",
        )
        return any(marker in summary for marker in shortfall_markers)

    def _looks_like_unverified_missing_evidence(self, summary: str) -> bool:
        if not summary:
            return False
        missing_markers = (
            "no funding proof",
            "no sponsor",
            "not provided",
            "has not been provided",
            "missing",
            "unverified",
            "not verified",
            "awaiting",
            "缺少",
            "未提供",
            "未提交",
            "未验证",
            "待验证",
            "待补",
        )
        return any(marker in summary for marker in missing_markers)

    def _remaining_required_documents(
        self,
        gate_progress: dict[str, Any],
        evidence_digest: dict[str, Any],
    ) -> list[str]:
        digest_remaining = self._string_list(
            evidence_digest.get("remaining_required_documents")
        )
        current_focus_document_type = self._string_or_none(
            evidence_digest.get("current_focus_document_type")
        )
        if current_focus_document_type and current_focus_document_type in digest_remaining:
            return digest_remaining

        documents = self._list_payload(gate_progress.get("required_documents"))
        remaining = [
            self._string_or_none(item.get("document_type"))
            for item in documents
            if item.get("status") != "ready"
        ]
        normalized = self._string_list(remaining)
        if normalized:
            return normalized
        return digest_remaining or self._string_list(evidence_digest.get("missing_evidence"))

    def _verified_documents(
        self,
        gate_progress: dict[str, Any],
        evidence_digest: dict[str, Any],
    ) -> list[str]:
        documents = self._list_payload(gate_progress.get("required_documents"))
        verified = [
            self._string_or_none(item.get("document_type"))
            for item in documents
            if item.get("status") == "ready"
        ]
        normalized = self._string_list(verified)
        if normalized:
            return normalized
        return self._string_list(evidence_digest.get("verified_documents"))

    def _awaiting_parse_documents(self, gate_progress: dict[str, Any]) -> list[str]:
        return self._string_list(
            [
                item.get("document_type")
                for item in self._list_payload(gate_progress.get("required_documents"))
                if item.get("status") == "uploaded"
            ]
        )

    def _int_or_zero(self, value: Any) -> int:
        if isinstance(value, int) and value >= 0:
            return value
        return 0
