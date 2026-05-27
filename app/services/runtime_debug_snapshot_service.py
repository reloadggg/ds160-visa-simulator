from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.app_version import backend_version_payload
from app.core.settings import settings
from app.db.models import DocumentRecord, SessionRecord, SessionTurnRecord
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.session_read_model_service import SessionReadModelService


SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "csrf",
    "password",
    "secret",
    "token",
)

MAX_RECENT_TURNS = 10
MAX_TRACE_ITEMS = 120
MAX_CONTENT_CHARS = 1800


class RuntimeDebugSnapshotService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sessions = SessionRepository(db)
        self.turns = SessionTurnRepository(db)
        self.read_models = SessionReadModelService(db)

    def build(self, session_id: str) -> dict[str, Any]:
        record = self.sessions.get(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")

        turns = self.turns.list_session_turns(session_id)
        read_model = self.read_models.build_from_record(record, turns=turns)
        latest_turn = turns[-1] if turns else None
        latest_assistant_turn = self._latest_turn(turns, role="assistant")
        latest_metadata = dict(
            getattr(latest_assistant_turn, "metadata_json", None) or {}
        )
        interviewer_state = dict(record.interviewer_state_json or {})
        runtime_view_state = read_model.runtime_view_state.model_dump(mode="json")
        runtime_ledger = read_model.runtime_ledger.model_dump(mode="json")

        snapshot = {
            "schema_version": "ds160.runtime_debug.v1",
            "backend": {
                **backend_version_payload(),
                "agent_runtime": settings.agent_runtime,
                "agent_runtime_trace_enabled": settings.agent_runtime_trace_enabled,
                "debug_enabled": self.debug_enabled(),
            },
            "session": self._session_payload(record),
            "current_runtime": self._current_runtime_payload(
                latest_metadata=latest_metadata,
                interviewer_state=interviewer_state,
            ),
            "latest_turn": self._turn_payload(latest_turn, include_metadata=True),
            "recent_turns": [
                self._turn_payload(turn, include_metadata=False)
                for turn in turns[-MAX_RECENT_TURNS:]
            ],
            "runtime_trace": self._limit_list(record.runtime_trace_json or []),
            "score_history": self._limit_list(record.score_history_json or []),
            "governor_history": self._limit_list(record.governor_history_json or []),
            "runtime_ledger": runtime_ledger,
            "runtime_view_state": runtime_view_state,
            "interviewer_state": interviewer_state,
            "last_material_refresh": self._payload(
                interviewer_state.get("last_material_refresh")
            ),
            "document_review": self._payload(
                runtime_view_state.get("document_review")
                or interviewer_state.get("document_review")
            ),
            "material_generation": self._material_generation_payload(session_id, turns),
            "errors": self._error_payload(
                latest_metadata=latest_metadata,
                interviewer_state=interviewer_state,
            ),
        }
        return self._redact(snapshot)

    @staticmethod
    def debug_enabled() -> bool:
        return bool(settings.allow_runtime_debug or settings.allow_debug_fill)

    def _session_payload(self, record: SessionRecord) -> dict[str, Any]:
        return {
            "session_id": record.session_id,
            "phase_state": record.phase_state,
            "declared_family": record.declared_family,
            "current_governor_decision": record.current_governor_decision,
            "current_focus": dict(record.current_focus_json or {}),
            "gate_status": dict(record.gate_status_json or {}),
        }

    def _current_runtime_payload(
        self,
        *,
        latest_metadata: dict[str, Any],
        interviewer_state: dict[str, Any],
    ) -> dict[str, Any]:
        last_material_refresh = self._payload(
            interviewer_state.get("last_material_refresh")
        )
        return {
            "configured_runtime": settings.agent_runtime,
            "turn_agent_runtime": latest_metadata.get("agent_runtime"),
            "turn_selected_public_runtime": latest_metadata.get(
                "selected_public_runtime"
            ),
            "material_agent_runtime": last_material_refresh.get("agent_runtime"),
            "material_selected_public_runtime": last_material_refresh.get(
                "selected_public_runtime"
            ),
            "native_run_id": latest_metadata.get("native_run_id")
            or last_material_refresh.get("native_run_id"),
            "graph_run_id": latest_metadata.get("graph_run_id")
            or last_material_refresh.get("graph_run_id"),
            "graph_runtime_error": latest_metadata.get("graph_runtime_error")
            or last_material_refresh.get("graph_runtime_error"),
            "prompt_trace": latest_metadata.get("prompt_trace")
            or last_material_refresh.get("prompt_trace"),
        }

    def _material_generation_payload(
        self,
        session_id: str,
        turns: list[SessionTurnRecord],
    ) -> dict[str, Any]:
        documents = list(
            self.db.scalars(
                select(DocumentRecord)
                .where(DocumentRecord.session_id == session_id)
                .order_by(DocumentRecord.document_id.asc())
            )
        )
        latest_bundle_id = self._latest_synthetic_bundle_id(turns)
        grouped: dict[str, list[DocumentRecord]] = {}
        for document in documents:
            metadata = self._document_metadata(document)
            if not metadata.get("debug_material_bundle"):
                continue
            bundle_id = self._string_or_none(metadata.get("synthetic_bundle_id"))
            if not bundle_id:
                continue
            grouped.setdefault(bundle_id, []).append(document)

        if not grouped:
            return {}

        bundle_id = latest_bundle_id if latest_bundle_id in grouped else next(
            reversed(grouped)
        )
        bundle_documents = grouped[bundle_id]
        first_metadata = self._document_metadata(bundle_documents[0])
        generation = self._payload(first_metadata.get("debug_generation"))
        return {
            "bundle_id": bundle_id,
            "scenario": self._string_or_none(
                first_metadata.get("debug_bundle_scenario")
            ),
            "scenario_label": self._string_or_none(
                first_metadata.get("debug_bundle_scenario_label")
            ),
            "document_count": len(bundle_documents),
            "document_types": [
                self._string_or_none(
                    (document.artifact_json or {}).get("document_type")
                )
                or self._string_or_none(self._document_metadata(document).get("document_type"))
                for document in bundle_documents
            ],
            "generation": generation,
        }

    def _error_payload(
        self,
        *,
        latest_metadata: dict[str, Any],
        interviewer_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        graph_error = latest_metadata.get("graph_runtime_error")
        if isinstance(graph_error, dict) and graph_error:
            errors.append({"source": "latest_turn.graph_runtime_error", **graph_error})
        last_material_refresh = self._payload(
            interviewer_state.get("last_material_refresh")
        )
        material_graph_error = last_material_refresh.get("graph_runtime_error")
        if isinstance(material_graph_error, dict) and material_graph_error:
            errors.append(
                {
                    "source": "last_material_refresh.graph_runtime_error",
                    **material_graph_error,
                }
            )
        runtime_error = interviewer_state.get("graph_runtime_error")
        if isinstance(runtime_error, dict) and runtime_error:
            errors.append({"source": "interviewer_state.graph_runtime_error", **runtime_error})
        return errors

    def _latest_synthetic_bundle_id(
        self,
        turns: list[SessionTurnRecord],
    ) -> str | None:
        for turn in reversed(turns):
            metadata = dict(turn.metadata_json or {})
            bundle_id = self._string_or_none(metadata.get("synthetic_bundle_id"))
            if bundle_id:
                return bundle_id
        return None

    def _latest_turn(
        self,
        turns: list[SessionTurnRecord],
        *,
        role: str,
    ) -> SessionTurnRecord | None:
        for turn in reversed(turns):
            if turn.role == role:
                return turn
        return None

    def _turn_payload(
        self,
        turn: SessionTurnRecord | None,
        *,
        include_metadata: bool,
    ) -> dict[str, Any] | None:
        if turn is None:
            return None
        payload: dict[str, Any] = {
            "turn_id": turn.turn_id,
            "turn_index": turn.turn_index,
            "session_id": turn.session_id,
            "role": turn.role,
            "source": turn.source,
            "client_message_id": turn.client_message_id,
            "content": self._truncate(turn.content),
        }
        if include_metadata:
            payload["metadata"] = dict(turn.metadata_json or {})
        return payload

    def _document_metadata(self, document: DocumentRecord) -> dict[str, Any]:
        artifact = dict(document.artifact_json or {})
        metadata = self._payload(artifact.get("metadata"))
        if "document_type" not in metadata and artifact.get("document_type"):
            metadata["document_type"] = artifact.get("document_type")
        return metadata

    def _limit_list(self, value: list[Any]) -> list[Any]:
        return list(value[-MAX_TRACE_ITEMS:])

    def _payload(self, value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _truncate(self, value: str | None) -> str:
        text = value or ""
        if len(text) <= MAX_CONTENT_CHARS:
            return text
        return f"{text[:MAX_CONTENT_CHARS]}..."

    def _redact(self, value: Any, *, parent_key: str = "") -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if self._is_sensitive_key(key_text):
                    redacted[key_text] = "[redacted]"
                    continue
                redacted[key_text] = self._redact(item, parent_key=key_text)
            return redacted
        if isinstance(value, list):
            return [self._redact(item, parent_key=parent_key) for item in value]
        return value

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = key.casefold()
        return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)
