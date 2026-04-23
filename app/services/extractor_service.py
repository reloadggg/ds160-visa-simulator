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

FIELD_BINDINGS: dict[str, tuple[str, str]] = {
    "/funding/primary_source": ("funding", "primary_source"),
    "/identity/full_name": ("identity", "full_name"),
    "/identity/passport_number": ("identity", "passport_number"),
    "/identity/nationality": ("identity", "nationality"),
    "/visa_intent/travel_purpose": ("visa_intent", "travel_purpose"),
    "/education/sevis_id": ("education", "sevis_id"),
    "/education/school_name": ("education", "school_name"),
    "/education/program_name": ("education", "program_name"),
    "/education/sponsor_name": ("education", "sponsor_name"),
}


class ExtractorService:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db
        self.model_factory = AgentModelFactory()

    def apply_message(
        self,
        profile: ApplicantProfile,
        message_text: str,
        recent_turns: list[Any] | None = None,
    ) -> ApplicantProfile:
        self._update_turn_history(profile, message_text, recent_turns)
        profile.ds160_view["latest_user_message"] = message_text
        profile.ds160_view["last_user_message"] = self._recent_user_messages_text(profile)
        declared_family = profile.visa_intent.get("declared_family")
        model, runtime = self._build_agent_runtime(declared_family)
        if model is not None and self.db is not None:
            try:
                output = ExtractorAgentRunner(
                    model=model,
                    instructions=runtime.get("instructions")
                    or self.model_factory.build_instructions(
                        "extractor_agent",
                        declared_family=declared_family,
                    ),
                ).run(
                    deps=self._build_agent_deps(profile),
                    message_text=message_text,
                    profile_payload=profile.model_dump(mode="json"),
                )
            except Exception:
                return self._fallback_apply_message(profile, message_text)
            return self._apply_output(profile, output)
        return self._fallback_apply_message(profile, message_text)

    def _build_agent_runtime(
        self,
        declared_family: str | None,
    ) -> tuple[Any | None, dict[str, Any]]:
        try:
            return self.model_factory.build(
                "extractor_agent",
                "interview_turn",
                declared_family=declared_family,
            )
        except TypeError as exc:
            if "declared_family" not in str(exc):
                raise
            return self.model_factory.build("extractor_agent", "interview_turn")

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
        field_binding = FIELD_BINDINGS.get(update.field_path)
        if field_binding is None:
            return

        section, key = field_binding
        container = getattr(profile, section)
        existing_state = profile.field_states.get(update.field_path)
        existing_value = container.get(key)
        normalized_value = self._normalize_field_value(update.field_path, update.value)
        effective_state = self._normalized_field_state(
            update.field_path,
            state=update.state,
            normalized_value=normalized_value,
            raw_value=update.value,
        )
        if (
            effective_state == FieldState.UNKNOWN
            and not update.value
            and ((existing_state is not None and existing_state.state != FieldState.UNKNOWN) or existing_value)
        ):
            return

        profile.field_states[update.field_path] = FieldStateRecord(state=effective_state)
        if normalized_value:
            container[key] = normalized_value
        elif effective_state == FieldState.UNKNOWN:
            container.pop(key, None)

        # CLAIMED 仍然只是用户自述，不应伪装成 document evidence。
        if effective_state in {FieldState.DOCUMENTED, FieldState.CONFIRMED}:
            evidence_refs = list(update.evidence_refs)
        else:
            evidence_refs = []
        profile.field_provenance[update.field_path] = FieldProvenanceRecord(
            evidence_refs=evidence_refs,
        )
        if effective_state == FieldState.CLAIMED and normalized_value:
            self._remember_claimed_value(profile, update.field_path, normalized_value)

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
        if self._is_unknown_funding_source(normalized):
            return None
        if any(token in normalized for token in ("parent", "parents", "mother", "father", "mom", "dad")):
            return "parents"
        return value

    def _is_unknown_funding_source(self, normalized_value: str) -> bool:
        collapsed = normalized_value.replace("_", " ")
        return collapsed in {
            "unknown",
            "undecided",
            "not decided",
            "not sure",
            "tbd",
            "to be decided",
            "to be determined",
            "unconfirmed",
            "n/a",
            "na",
        }

    def _normalized_field_state(
        self,
        field_path: str,
        *,
        state: FieldState,
        normalized_value: str | None,
        raw_value: str | None,
    ) -> FieldState:
        if (
            field_path == "/funding/primary_source"
            and state == FieldState.CLAIMED
            and normalized_value is None
            and isinstance(raw_value, str)
            and self._is_unknown_funding_source(
                raw_value.strip().lower().replace("-", "_")
            )
        ):
            return FieldState.UNKNOWN
        return state

    def _normalize_field_value(
        self,
        field_path: str,
        value: str | None,
    ) -> str | None:
        if field_path == "/funding/primary_source":
            return self._normalize_funding_source(value)
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

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
        self._remember_claimed_value(
            profile,
            "/funding/primary_source",
            funding_source,
        )

    def _remember_claimed_value(
        self,
        profile: ApplicantProfile,
        field_path: str,
        value: str,
    ) -> None:
        claim_history = profile.ds160_view.setdefault("field_claim_history", {})
        field_history = claim_history.setdefault(field_path, [])
        latest_user_turn = self._latest_user_turn(profile)
        entry = {
            "value": value,
            "content": (
                str(latest_user_turn.get("content", value))
                if latest_user_turn is not None
                else value
            ),
            "turn_id": (
                latest_user_turn.get("turn_id")
                if latest_user_turn is not None
                else None
            ),
            "turn_index": (
                latest_user_turn.get("turn_index")
                if latest_user_turn is not None
                else None
            ),
            "source": (
                latest_user_turn.get("source", "user_message")
                if latest_user_turn is not None
                else "user_message"
            ),
        }
        if field_history and field_history[-1].get("value") == value:
            if field_history[-1].get("turn_index") is None and entry["turn_index"] is not None:
                field_history[-1].update(entry)
            return
        field_history.append(entry)

    def _latest_user_turn(
        self,
        profile: ApplicantProfile,
    ) -> dict[str, Any] | None:
        turn_history = profile.ds160_view.get("turn_history", [])
        if not isinstance(turn_history, list):
            return None
        for turn in reversed(turn_history):
            if isinstance(turn, dict) and turn.get("role") == "user":
                return turn
        return None

    def _update_turn_history(
        self,
        profile: ApplicantProfile,
        message_text: str,
        recent_turns: list[Any] | None,
    ) -> None:
        if recent_turns is not None:
            normalized_history = [
                self._normalize_turn_history_entry(turn) for turn in recent_turns
            ]
        else:
            history = profile.ds160_view.get("turn_history", [])
            normalized_history = [
                self._normalize_turn_history_entry(item)
                for item in history
                if isinstance(item, dict)
            ]
            normalized_history.append(
                {
                    "turn_id": None,
                    "turn_index": self._next_history_turn_index(normalized_history),
                    "role": "user",
                    "content": message_text,
                    "source": "user_message",
                }
            )
        profile.ds160_view["turn_history"] = normalized_history[-6:]

    def _recent_user_messages_text(self, profile: ApplicantProfile) -> str:
        user_messages = [
            item.get("content", "")
            for item in profile.ds160_view.get("turn_history", [])
            if isinstance(item, dict) and item.get("role") == "user"
        ]
        return "\n".join(user_messages[-3:])

    def _normalize_turn_history_entry(self, turn: Any) -> dict[str, Any]:
        if isinstance(turn, dict):
            turn_id = turn.get("turn_id")
            turn_index = turn.get("turn_index")
            role = turn.get("role")
            content = turn.get("content", "")
            source = turn.get("source")
        else:
            turn_id = getattr(turn, "turn_id", None)
            turn_index = getattr(turn, "turn_index", None)
            role = getattr(turn, "role", None)
            content = getattr(turn, "content", "")
            source = getattr(turn, "source", None)

        return {
            "turn_id": turn_id,
            "turn_index": turn_index,
            "role": role,
            "content": content,
            "source": source,
        }

    def _next_history_turn_index(self, history: list[dict[str, Any]]) -> int:
        turn_indexes = [
            item.get("turn_index")
            for item in history
            if isinstance(item.get("turn_index"), int)
        ]
        return (max(turn_indexes) if turn_indexes else 0) + 1
