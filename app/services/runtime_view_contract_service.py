from __future__ import annotations

from typing import Any

from app.platform.runtime_ledger import RuntimeViewState


class RuntimeViewContractService:
    @staticmethod
    def payload(
        runtime_view_state: RuntimeViewState | dict[str, Any] | None,
        *,
        anchored_only: bool = False,
    ) -> dict[str, Any]:
        if isinstance(runtime_view_state, RuntimeViewState):
            payload = runtime_view_state.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            )
        elif isinstance(runtime_view_state, dict):
            payload = dict(runtime_view_state)
        else:
            payload = {}

        if anchored_only and not payload.get("source_turn_id"):
            return {}
        return payload

    @staticmethod
    def governor_decision(
        runtime_view_state: dict[str, Any],
        fallback: dict[str, Any] | None = None,
    ) -> str | None:
        fallback = fallback or {}
        return fallback.get("governor_decision") or runtime_view_state.get(
            "governor_decision"
        )

    @staticmethod
    def requested_documents(
        runtime_view_state: dict[str, Any],
        fallback: dict[str, Any] | None = None,
    ) -> list[str]:
        fallback = fallback or {}
        if runtime_view_state.get("source_turn_id"):
            return list(
                runtime_view_state.get("requested_documents", [])
                or fallback.get("requested_documents", [])
                or []
            )
        return list(
            fallback.get("requested_documents", [])
            or runtime_view_state.get("requested_documents", [])
            or []
        )

    @staticmethod
    def remaining_required_documents(
        runtime_view_state: dict[str, Any],
        fallback: dict[str, Any] | None = None,
    ) -> list[str]:
        fallback = fallback or {}
        if runtime_view_state.get("source_turn_id"):
            return list(
                runtime_view_state.get("remaining_required_documents", [])
                or fallback.get("remaining_required_documents", [])
                or []
            )
        return list(
            fallback.get("remaining_required_documents", [])
            or runtime_view_state.get("remaining_required_documents", [])
            or []
        )

    @staticmethod
    def turn_decision(
        runtime_view_state: dict[str, Any],
        fallback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fallback = fallback or {}
        payload = dict(fallback.get("turn_decision", {}) or {})
        if runtime_view_state.get("source_turn_id"):
            if runtime_view_state.get("decision") is not None:
                payload["decision"] = runtime_view_state.get("decision")
            payload["requested_documents"] = list(
                runtime_view_state.get("requested_documents", []) or []
            )
            payload["remaining_required_documents"] = list(
                runtime_view_state.get("remaining_required_documents", []) or []
            )
            payload["current_key_question"] = runtime_view_state.get(
                "current_key_question"
            )
            payload["current_key_proof"] = runtime_view_state.get("current_key_proof")
            payload["current_risk_code"] = runtime_view_state.get("current_risk_code")
        return payload

    @staticmethod
    def document_review(
        runtime_view_state: dict[str, Any],
        fallback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fallback = fallback or {}
        if runtime_view_state.get("source_turn_id"):
            return dict(
                runtime_view_state.get("document_review", {})
                or fallback.get("document_review", {})
                or {}
            )
        return dict(
            fallback.get("document_review", {})
            or runtime_view_state.get("document_review", {})
            or {}
        )

    @staticmethod
    def prompt_trace(
        runtime_view_state: dict[str, Any],
        fallback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fallback = fallback or {}
        if runtime_view_state.get("source_turn_id"):
            return dict(
                runtime_view_state.get("prompt_trace", {})
                or fallback.get("prompt_trace", {})
                or {}
            )
        return dict(
            fallback.get("prompt_trace", {})
            or runtime_view_state.get("prompt_trace", {})
            or {}
        )
