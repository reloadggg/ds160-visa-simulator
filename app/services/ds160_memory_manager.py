from __future__ import annotations

from typing import Any

from app.domain.contracts import ApplicantProfile, InterviewRiskLevel, ScoreState
from app.domain.evidence import DocumentAssessment
from app.domain.runtime import (
    DS160CaseBrief,
    DS160EvidenceDigest,
    DS160FocusThread,
    DS160MemoryBundle,
    DS160MemoryStrata,
    TurnAdvisoryContext,
)
from app.platform.runtime_ledger import SessionReadModel


class DS160MemoryManager:
    def build(
        self,
        *,
        profile: ApplicantProfile,
        score: ScoreState,
        advisory_context: TurnAdvisoryContext,
        read_model: SessionReadModel | None,
        declared_family: str | None,
        phase_state: str,
        boundary_decision: str,
        documents: list[Any] | None = None,
    ) -> DS160MemoryBundle:
        runtime_view_state = read_model.runtime_view_state if read_model is not None else None
        current_focus = dict(
            getattr(runtime_view_state, "current_focus", {})
            or getattr(getattr(read_model, "runtime_ledger", None), "current_focus", {})
            or {}
        )
        last_turn_decision = self._string_or_none(
            getattr(runtime_view_state, "decision", None)
        ) or self._string_or_none(
            getattr(read_model, "current_governor_decision", None)
        )
        requested_documents = list(
            getattr(runtime_view_state, "requested_documents", []) or []
        )
        current_focus_document_type = self._string_or_none(
            current_focus.get("document_type")
        )
        if not requested_documents and current_focus_document_type:
            requested_documents = [current_focus_document_type]

        focus_thread = DS160FocusThread(
            current_focus=current_focus,
            last_turn_decision=last_turn_decision,
            public_status=self._string_or_none(
                getattr(runtime_view_state, "public_status", None)
            ),
            current_key_question=self._string_or_none(
                getattr(runtime_view_state, "current_key_question", None)
            )
            or self._string_or_none(current_focus.get("question")),
            current_key_proof=self._string_or_none(
                getattr(runtime_view_state, "current_key_proof", None)
            )
            or current_focus_document_type,
            current_risk_code=self._string_or_none(
                getattr(runtime_view_state, "current_risk_code", None)
            )
            or self._string_or_none(current_focus.get("risk_code")),
            requested_documents=requested_documents,
            allowed_next_actions=list(
                getattr(runtime_view_state, "allowed_next_actions", []) or []
            ),
        )
        evidence_digest = self._build_evidence_digest(
            profile=profile,
            score=score,
            requested_documents=requested_documents,
            current_focus_document_type=current_focus_document_type,
            documents=documents,
        )
        case_brief = DS160CaseBrief(
            declared_family=declared_family,
            phase_state=phase_state,
            boundary_decision=boundary_decision,
            last_turn_decision=last_turn_decision,
            profile_version=profile.profile_version,
            travel_purpose=self._travel_purpose(profile),
            school_name=self._school_name(profile),
            funding_source=self._string_or_none(profile.funding.get("primary_source")),
        )
        memory_strata = self._build_memory_strata(
            case_brief=case_brief,
            focus_thread=focus_thread,
            evidence_digest=evidence_digest,
            advisory_context=advisory_context,
        )
        return DS160MemoryBundle(
            case_brief=case_brief,
            focus_thread=focus_thread,
            evidence_digest=evidence_digest,
            memory_strata=memory_strata,
            current_focus=current_focus,
            last_turn_decision=last_turn_decision,
        )

    def _build_evidence_digest(
        self,
        *,
        profile: ApplicantProfile,
        score: ScoreState,
        requested_documents: list[str],
        current_focus_document_type: str | None,
        documents: list[Any] | None,
    ) -> DS160EvidenceDigest:
        documented_field_paths = sorted(
            field_path
            for field_path, field_state in profile.field_states.items()
            if getattr(field_state, "state", None) in {"documented", "confirmed"}
        )
        evidence_refs = self._dedupe(
            evidence_ref
            for provenance in profile.field_provenance.values()
            for evidence_ref in getattr(provenance, "evidence_refs", [])
        )
        uploaded_documents, supported_claims, active_main_flow_feedback = (
            self._uploaded_document_digest(
                documents=documents or [],
                current_focus_document_type=current_focus_document_type,
                requested_documents=requested_documents,
            )
        )
        return DS160EvidenceDigest(
            missing_evidence=self._dedupe(score.missing_evidence),
            requested_documents=self._dedupe(requested_documents),
            current_focus_document_type=current_focus_document_type,
            documented_field_paths=documented_field_paths,
            evidence_refs=evidence_refs,
            supported_claims=supported_claims,
            active_main_flow_feedback=active_main_flow_feedback,
            uploaded_document_count=len(uploaded_documents),
            uploaded_documents=uploaded_documents,
        )

    def _build_memory_strata(
        self,
        *,
        case_brief: DS160CaseBrief,
        focus_thread: DS160FocusThread,
        evidence_digest: DS160EvidenceDigest,
        advisory_context: TurnAdvisoryContext,
    ) -> DS160MemoryStrata:
        risk_level = advisory_context.risk_level
        if isinstance(risk_level, InterviewRiskLevel):
            risk_level_value = risk_level.value
        else:
            risk_level_value = str(risk_level)
        return DS160MemoryStrata(
            facts_memory={
                "declared_family": case_brief.declared_family,
                "travel_purpose": case_brief.travel_purpose,
                "school_name": case_brief.school_name,
                "funding_source": case_brief.funding_source,
                "profile_version": case_brief.profile_version,
            },
            working_memory={
                "current_focus": focus_thread.current_focus,
                "last_turn_decision": focus_thread.last_turn_decision,
                "current_key_question": focus_thread.current_key_question,
                "current_key_proof": focus_thread.current_key_proof,
            },
            evidence_memory={
                "missing_evidence": evidence_digest.missing_evidence,
                "requested_documents": evidence_digest.requested_documents,
                "documented_field_paths": evidence_digest.documented_field_paths,
                "evidence_refs": evidence_digest.evidence_refs,
            },
            derived_memory={
                "score_summary": dict(advisory_context.score_summary),
                "risk_codes": list(advisory_context.risk_codes),
                "risk_level": risk_level_value,
                "missing_evidence_summary": advisory_context.missing_evidence_summary,
            },
            audit_memory={
                "public_status": focus_thread.public_status,
                "allowed_next_actions": list(focus_thread.allowed_next_actions),
                "boundary_decision": case_brief.boundary_decision,
            },
        )

    def _travel_purpose(self, profile: ApplicantProfile) -> str | None:
        return self._string_or_none(
            profile.travel.get("purpose")
            or profile.visa_intent.get("purpose")
            or profile.ds160_view.get("travel_purpose")
        )

    def _school_name(self, profile: ApplicantProfile) -> str | None:
        return self._string_or_none(
            profile.education.get("school_name")
            or profile.education.get("institution")
            or profile.education.get("school")
        )

    def _uploaded_document_digest(
        self,
        *,
        documents: list[Any],
        current_focus_document_type: str | None,
        requested_documents: list[str],
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        uploaded_documents: list[dict[str, Any]] = []
        supported_claims: list[str] = []
        active_main_flow_feedback: dict[str, Any] = {}
        active_feedback_rank = -1
        requested_document_set = set(self._dedupe(requested_documents))

        for document in documents:
            artifact_json = getattr(document, "artifact_json", None)
            assessment = DocumentAssessment.from_artifact(artifact_json)
            document_payload = {
                "document_id": getattr(document, "document_id", None),
                "filename": getattr(document, "filename", None),
                "status": getattr(document, "status", None),
                "document_type": assessment.document_type,
                "relevance": assessment.relevance,
                "supported_claims": list(assessment.supported_claims),
                "counts_toward_gate": assessment.counts_toward_gate,
                "main_flow_feedback": (
                    {}
                    if assessment.main_flow_feedback is None
                    else assessment.main_flow_feedback.model_dump(
                        mode="json",
                        exclude_none=True,
                    )
                ),
            }
            uploaded_documents.append(document_payload)
            for claim in assessment.supported_claims:
                if claim not in supported_claims:
                    supported_claims.append(claim)
            feedback_payload = document_payload["main_flow_feedback"]
            if not feedback_payload:
                continue
            feedback_rank = self._feedback_rank(
                feedback_payload,
                current_focus_document_type=current_focus_document_type,
                requested_document_set=requested_document_set,
            )
            if feedback_rank <= active_feedback_rank:
                continue
            active_feedback_rank = feedback_rank
            active_main_flow_feedback = {
                **feedback_payload,
                "document_id": document_payload["document_id"],
                "filename": document_payload["filename"],
                "document_type": document_payload["document_type"],
                "supported_claims": list(document_payload["supported_claims"]),
            }

        return uploaded_documents, supported_claims, active_main_flow_feedback

    def _feedback_rank(
        self,
        feedback_payload: dict[str, Any],
        *,
        current_focus_document_type: str | None,
        requested_document_set: set[str],
    ) -> int:
        status = self._string_or_none(feedback_payload.get("status")) or ""
        focus_document_type = self._string_or_none(
            feedback_payload.get("current_focus_document_type")
        )
        supported_document_type = self._string_or_none(
            feedback_payload.get("supported_document_type")
        )

        base_rank = {
            "helpful": 30,
            "partial_helpful": 20,
            "not_helpful": 10,
        }.get(status, 0)
        if (
            current_focus_document_type
            and focus_document_type == current_focus_document_type
        ):
            base_rank += 5
        if supported_document_type and supported_document_type in requested_document_set:
            base_rank += 3
        return base_rank

    def _dedupe(self, items: Any) -> list[str]:
        normalized: list[str] = []
        for item in items:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
