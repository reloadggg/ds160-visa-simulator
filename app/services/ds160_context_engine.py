from __future__ import annotations

from typing import Any

from app.domain.contracts import ApplicantProfile
from app.domain.runtime import (
    ContextCompressionSnapshot,
    DS160MemoryBundle,
    PromptRoleContract,
    TurnAdvisoryContext,
    TurnContextSnapshot,
    TurnHistorySummary,
)


class DS160ContextEngine:
    RECENT_TURN_WINDOW = 6

    def build_dynamic_turn_context(
        self,
        *,
        session_id: str,
        declared_family: str | None,
        phase_state: str,
        latest_user_message: str,
        profile: ApplicantProfile,
        advisory_context: TurnAdvisoryContext,
        gate_progress: dict[str, Any] | None,
        recent_turns: list[Any] | None,
        memory_bundle: DS160MemoryBundle,
        capability_plan: list[dict[str, Any]] | None = None,
        prompt_roles: PromptRoleContract | None = None,
    ) -> TurnContextSnapshot:
        recent_turn_payload, history_summary, compression = self._turn_history_context(
            recent_turns
        )
        return TurnContextSnapshot(
            session_id=session_id,
            declared_family=declared_family,
            phase_state=phase_state,
            latest_user_message=latest_user_message,
            recent_turns=recent_turn_payload,
            profile_snapshot=profile.model_dump(mode="json"),
            current_focus=dict(memory_bundle.current_focus),
            advisory_context=advisory_context,
            gate_progress=dict(gate_progress or {}),
            last_turn_decision=memory_bundle.last_turn_decision,
            prompt_roles=prompt_roles or PromptRoleContract(),
            case_brief=memory_bundle.case_brief,
            focus_thread=memory_bundle.focus_thread,
            evidence_digest=memory_bundle.evidence_digest,
            memory_strata=memory_bundle.memory_strata,
            capability_plan=list(capability_plan or []),
            history_summary=history_summary,
            compression=compression,
        )

    def _turn_history_context(
        self,
        recent_turns: list[Any] | None,
    ) -> tuple[list[dict[str, str]], TurnHistorySummary, ContextCompressionSnapshot]:
        if recent_turns is None:
            return (
                [],
                TurnHistorySummary(),
                ContextCompressionSnapshot(
                    retained_turn_count=0,
                    summarized_turn_count=0,
                ),
            )

        retained_turns = list(recent_turns[-self.RECENT_TURN_WINDOW :])
        summarized_turns = list(recent_turns[: -self.RECENT_TURN_WINDOW])
        return (
            self._recent_turn_payload(retained_turns),
            self._history_summary(summarized_turns),
            ContextCompressionSnapshot(
                retained_turn_count=len(retained_turns),
                summarized_turn_count=len(summarized_turns),
            ),
        )

    def _recent_turn_payload(
        self,
        turns: list[Any],
    ) -> list[dict[str, str]]:
        payload: list[dict[str, str]] = []
        for turn in turns:
            role = self._turn_attr(turn, "role")
            content = self._turn_attr(turn, "content")
            if not isinstance(role, str) or not isinstance(content, str):
                continue
            payload.append({"role": role, "content": content})
        return payload

    def _history_summary(
        self,
        turns: list[Any],
    ) -> TurnHistorySummary:
        prior_decisions: list[str] = []
        prior_requested_documents: list[str] = []
        user_turn_count = 0
        assistant_turn_count = 0

        for turn in turns:
            role = self._turn_attr(turn, "role")
            if role == "user":
                user_turn_count += 1
                continue
            if role != "assistant":
                continue
            assistant_turn_count += 1
            metadata = self._turn_metadata(turn)
            turn_record = metadata.get("turn_record", {})
            runtime_view_state = metadata.get("runtime_view_state", {})

            decision = (
                self._string_or_none(turn_record.get("decision"))
                or self._string_or_none(runtime_view_state.get("decision"))
                or self._string_or_none(metadata.get("turn_decision"))
            )
            if decision and decision not in prior_decisions:
                prior_decisions.append(decision)

            requested_documents = turn_record.get("requested_documents")
            if not isinstance(requested_documents, list):
                requested_documents = runtime_view_state.get("requested_documents", [])
            for item in requested_documents:
                if not isinstance(item, str):
                    continue
                document_type = item.strip()
                if document_type and document_type not in prior_requested_documents:
                    prior_requested_documents.append(document_type)

        return TurnHistorySummary(
            summarized_turn_count=len(turns),
            summarized_user_turn_count=user_turn_count,
            summarized_assistant_turn_count=assistant_turn_count,
            prior_decisions=prior_decisions,
            prior_requested_documents=prior_requested_documents,
        )

    def _turn_metadata(self, turn: Any) -> dict[str, Any]:
        if isinstance(turn, dict):
            metadata = turn.get("metadata_json") or turn.get("metadata") or {}
        else:
            metadata = getattr(turn, "metadata_json", None) or getattr(turn, "metadata", None) or {}
        if isinstance(metadata, dict):
            return metadata
        return {}

    def _turn_attr(self, turn: Any, key: str) -> Any:
        if isinstance(turn, dict):
            return turn.get(key)
        return getattr(turn, key, None)

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
