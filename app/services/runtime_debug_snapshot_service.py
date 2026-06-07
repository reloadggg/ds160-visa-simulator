from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.app_version import backend_version_payload
from app.core.settings import settings
from app.db.models import DocumentRecord, SessionRecord, SessionTurnRecord
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.case_memory_service import CASE_MEMORY_TOMBSTONE_KEY, CaseMemoryService
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
        self.case_memory = CaseMemoryService(db)
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
        case_board = self.case_memory.public_case_board(session_id)
        evidence_graph = self.case_memory.public_evidence_graph(session_id)
        runtime_trace = self._limit_list(record.runtime_trace_json or [])
        material_understanding = self._material_understanding_payload(session_id)
        material_generation = self._material_generation_payload(session_id, turns)
        errors = self._error_payload(
            latest_metadata=latest_metadata,
            interviewer_state=interviewer_state,
            material_understanding=material_understanding,
        )
        timeline = self._timeline_payload(
            runtime_trace=runtime_trace,
            material_understanding=material_understanding,
            material_generation=material_generation,
            last_material_refresh=self._payload(
                interviewer_state.get("last_material_refresh")
            ),
            errors=errors,
        )

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
            "runtime_trace": runtime_trace,
            "score_history": self._limit_list(record.score_history_json or []),
            "governor_history": self._limit_list(record.governor_history_json or []),
            "runtime_ledger": runtime_ledger,
            "runtime_view_state": runtime_view_state,
            "case_board": case_board,
            "evidence_graph": evidence_graph,
            "interviewer_state": interviewer_state,
            "last_material_refresh": self._payload(
                interviewer_state.get("last_material_refresh")
            ),
            "document_review": self._payload(
                runtime_view_state.get("document_review")
                or interviewer_state.get("document_review")
            ),
            "material_understanding": material_understanding,
            "material_generation": material_generation,
            "timeline": timeline,
            "errors": errors,
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
        turn_runtime_execution = self._payload(
            latest_metadata.get("runtime_execution")
        )
        material_runtime_execution = self._payload(
            last_material_refresh.get("runtime_execution")
        )
        current_runtime_execution = (
            turn_runtime_execution
            or material_runtime_execution
            or self._payload(interviewer_state.get("runtime_execution"))
        )
        return {
            "configured_runtime": settings.agent_runtime,
            "runtime_execution": current_runtime_execution,
            "turn_runtime_execution": turn_runtime_execution,
            "material_runtime_execution": material_runtime_execution,
            "execution_runtime": current_runtime_execution.get("execution_runtime"),
            "public_runtime": current_runtime_execution.get("public_runtime"),
            "requested_public_runtime": current_runtime_execution.get(
                "requested_public_runtime"
            ),
            "runtime_engine": current_runtime_execution.get("runtime_engine"),
            "fail_open_to_legacy": current_runtime_execution.get(
                "fail_open_to_legacy"
            ),
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
            if self._document_tombstoned(document):
                continue
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

    def _material_understanding_payload(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        documents = list(
            self.db.scalars(
                select(DocumentRecord)
                .where(DocumentRecord.session_id == session_id)
                .order_by(DocumentRecord.document_id.asc())
            )
        )
        payloads: list[dict[str, Any]] = []
        for document in documents:
            if self._document_tombstoned(document):
                continue
            artifact = dict(document.artifact_json or {})
            case_board_delta = self._payload(artifact.get("case_board_delta"))
            latest_material = self._payload(
                case_board_delta.get("latest_material")
            )
            status = (
                self._string_or_none(artifact.get("understanding_status"))
                or self._string_or_none(latest_material.get("understanding_status"))
                or self._string_or_none(document.status)
            )
            understanding_error = self._payload(
                artifact.get("understanding_error")
                or latest_material.get("understanding_error")
            )
            if not status and not understanding_error:
                continue
            payloads.append(
                self._drop_empty_values(
                    {
                        "document_id": document.document_id,
                        "filename": document.filename,
                        "document_status": document.status,
                        "understanding_status": status,
                        "document_type": self._string_or_none(
                            artifact.get("document_type")
                        )
                        or self._string_or_none(latest_material.get("document_type")),
                        "understanding_error": understanding_error,
                        "latest_material": latest_material,
                    }
                )
            )
        return payloads[-40:]

    def _error_payload(
        self,
        *,
        latest_metadata: dict[str, Any],
        interviewer_state: dict[str, Any],
        material_understanding: list[dict[str, Any]],
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
        for material in material_understanding:
            understanding_error = self._payload(material.get("understanding_error"))
            if not understanding_error:
                continue
            errors.append(
                {
                    "source": "material_understanding",
                    "document_id": material.get("document_id"),
                    "filename": material.get("filename"),
                    **understanding_error,
                }
            )
        return errors

    def _timeline_payload(
        self,
        *,
        runtime_trace: list[Any],
        material_understanding: list[dict[str, Any]],
        material_generation: dict[str, Any],
        last_material_refresh: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        timeline: list[dict[str, Any]] = []

        if material_generation:
            scenario = self._string_or_none(material_generation.get("scenario"))
            document_count = material_generation.get("document_count")
            generation = self._payload(material_generation.get("generation"))
            summary_parts = [
                f"scenario={scenario}" if scenario else None,
                f"documents={document_count}" if document_count is not None else None,
                (
                    f"source={generation.get('source')}"
                    if generation.get("source")
                    else None
                ),
            ]
            timeline.append(
                self._drop_empty_values(
                    {
                        "phase": "material_generation",
                        "step": "debug_material_bundle",
                        "status": "completed",
                        "summary": " ".join(
                            item for item in summary_parts if item is not None
                        ),
                        "payload": material_generation,
                    }
                )
            )

        for material in material_understanding:
            status = self._string_or_none(material.get("understanding_status"))
            filename = self._string_or_none(material.get("filename"))
            document_id = self._string_or_none(material.get("document_id"))
            timeline.append(
                self._drop_empty_values(
                    {
                        "phase": "material_understanding",
                        "step": "document",
                        "status": status or "unknown",
                        "summary": self._material_timeline_summary(
                            filename=filename,
                            document_id=document_id,
                            status=status,
                            error=self._payload(material.get("understanding_error")),
                        ),
                        "document_id": document_id,
                        "payload": material,
                    }
                )
            )

        for entry in runtime_trace:
            if not isinstance(entry, dict):
                continue
            step = self._string_or_none(entry.get("node_name")) or self._string_or_none(
                entry.get("step")
            )
            summary = self._string_or_none(entry.get("summary")) or step
            status = "completed"
            if entry.get("fallback_used"):
                status = "fallback"
            timeline.append(
                self._drop_empty_values(
                    {
                        "phase": "runtime",
                        "step": step or "trace",
                        "status": status,
                        "summary": summary,
                        "payload": entry,
                    }
                )
            )

        if last_material_refresh:
            runtime_execution = self._payload(
                last_material_refresh.get("runtime_execution")
            )
            graph_error = self._payload(last_material_refresh.get("graph_runtime_error"))
            refresh_error = self._string_or_none(
                last_material_refresh.get("main_flow_refresh_error")
            )
            status = "failed" if graph_error or refresh_error else "completed"
            timeline.append(
                self._drop_empty_values(
                    {
                        "phase": "material_refresh",
                        "step": "runtime",
                        "status": status,
                        "summary": self._material_refresh_summary(
                            runtime_execution=runtime_execution,
                            graph_error=graph_error,
                            refresh_error=refresh_error,
                        ),
                        "payload": last_material_refresh,
                    }
                )
            )

        for error in errors:
            source = self._string_or_none(error.get("source")) or "error"
            message = self._string_or_none(error.get("message")) or self._string_or_none(
                error.get("error_message")
            )
            timeline.append(
                self._drop_empty_values(
                    {
                        "phase": "error",
                        "step": source,
                        "status": "failed",
                        "summary": message or source,
                        "document_id": self._string_or_none(error.get("document_id")),
                        "payload": error,
                    }
                )
            )

        return timeline[-MAX_TRACE_ITEMS:]

    def _material_timeline_summary(
        self,
        *,
        filename: str | None,
        document_id: str | None,
        status: str | None,
        error: dict[str, Any],
    ) -> str:
        label = filename or document_id or "material"
        error_message = self._string_or_none(error.get("message"))
        if error_message:
            return f"{label}: {status or 'unknown'} ({error_message})"
        return f"{label}: {status or 'unknown'}"

    def _material_refresh_summary(
        self,
        *,
        runtime_execution: dict[str, Any],
        graph_error: dict[str, Any],
        refresh_error: str | None,
    ) -> str:
        if refresh_error:
            return refresh_error
        if graph_error:
            return (
                self._string_or_none(graph_error.get("error_message"))
                or "material refresh runtime failed"
            )
        public_runtime = self._string_or_none(runtime_execution.get("public_runtime"))
        execution_runtime = self._string_or_none(
            runtime_execution.get("execution_runtime")
        )
        if public_runtime or execution_runtime:
            return f"public={public_runtime or 'unknown'} execution={execution_runtime or 'unknown'}"
        return "material refresh completed"

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

    def _document_tombstoned(self, document: DocumentRecord) -> bool:
        artifact = dict(document.artifact_json or {})
        return document.status == "tombstoned" or bool(
            artifact.get(CASE_MEMORY_TOMBSTONE_KEY)
        )

    def _limit_list(self, value: list[Any]) -> list[Any]:
        return list(value[-MAX_TRACE_ITEMS:])

    def _payload(self, value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _drop_empty_values(self, value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item
            for key, item in value.items()
            if item not in (None, "", [], {})
        }

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
