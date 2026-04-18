from __future__ import annotations

from typing import Any

from app.agents.extractor_agent import ExtractorAgentRunner
from app.agents.model_factory import AgentModelFactory
from app.agents.schemas import AgentRuntimeDeps, ExtractorOutput, FieldUpdate
from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
from app.services.evidence_service import EvidenceService
from app.services.retrieval_service import RetrievalService


class ExtractorService:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db
        self.model_factory = AgentModelFactory()

    def apply_message(
        self,
        profile: ApplicantProfile,
        message_text: str,
    ) -> ApplicantProfile:
        profile.ds160_view["last_user_message"] = message_text
        model, _runtime = self.model_factory.build("extractor_agent", "interview_turn")
        if model is not None and self.db is not None:
            try:
                output = ExtractorAgentRunner(model=model).run(
                    deps=self._build_agent_deps(profile),
                    message_text=message_text,
                    profile_payload=profile.model_dump(mode="json"),
                )
            except Exception:
                return self._fallback_apply_message(profile, message_text)
            return self._apply_output(profile, output)
        return self._fallback_apply_message(profile, message_text)

    def _build_agent_deps(self, profile: ApplicantProfile) -> AgentRuntimeDeps:
        return AgentRuntimeDeps(
            session_id=profile.profile_id.removeprefix("profile-"),
            retrieval=RetrievalService(self.db),
            evidence=EvidenceService(self.db),
        )

    def _apply_output(
        self,
        profile: ApplicantProfile,
        output: ExtractorOutput,
    ) -> ApplicantProfile:
        for update in output.field_updates:
            self._apply_field_update(profile, update)
        return profile

    def _apply_field_update(
        self,
        profile: ApplicantProfile,
        update: FieldUpdate,
    ) -> None:
        if update.field_path != "/funding/primary_source":
            return

        existing_state = profile.field_states.get(update.field_path)
        existing_value = profile.funding.get("primary_source")
        if (
            update.state == FieldState.UNKNOWN
            and not update.value
            and ((existing_state is not None and existing_state.state != FieldState.UNKNOWN) or existing_value)
        ):
            return

        profile.field_states[update.field_path] = FieldStateRecord(state=update.state)
        normalized_value = self._normalize_funding_source(update.value)
        if normalized_value:
            profile.funding["primary_source"] = normalized_value
        elif update.state == FieldState.UNKNOWN:
            profile.funding.pop("primary_source", None)

        # CLAIMED 仍然只是用户自述，不应伪装成 document evidence。
        if update.state in {FieldState.DOCUMENTED, FieldState.CONFIRMED}:
            evidence_refs = list(update.evidence_refs)
        else:
            evidence_refs = []
        profile.field_provenance[update.field_path] = FieldProvenanceRecord(
            evidence_refs=evidence_refs,
        )

    def _fallback_apply_message(
        self,
        profile: ApplicantProfile,
        message_text: str,
    ) -> ApplicantProfile:
        normalized = message_text.lower()
        if any(token in normalized for token in ("parent", "parents", "mother", "father", "mom", "dad")):
            self._apply_claimed_funding_source(profile, "parents")
        return profile

    def _normalize_funding_source(self, value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip().lower().replace("-", "_")
        if any(token in normalized for token in ("parent", "parents", "mother", "father", "mom", "dad")):
            return "parents"
        return value

    def _apply_claimed_funding_source(
        self,
        profile: ApplicantProfile,
        funding_source: str,
    ) -> None:
        profile.field_states["/funding/primary_source"] = FieldStateRecord(
            state=FieldState.CLAIMED,
        )
        profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord()
        profile.funding["primary_source"] = funding_source
