from __future__ import annotations

from typing import Any

from app.db.models import SessionRecord
from app.domain.document_types import normalize_document_type


class GraphCaseStateBuilder:
    """Build the graph case snapshot without calling models or mutating records."""

    def __init__(
        self,
        *,
        max_recent_turns: int = 6,
        max_history_items: int = 20,
        max_text_excerpt_chars: int = 500,
    ) -> None:
        self.max_recent_turns = max(max_recent_turns, 0)
        self.max_history_items = max(max_history_items, 0)
        self.max_text_excerpt_chars = max(max_text_excerpt_chars, 0)

    def build(
        self,
        record: SessionRecord,
        turns: list[Any],
        *,
        documents: list[Any] | None = None,
        evidence_items: list[Any] | None = None,
        document_chunks: list[Any] | None = None,
    ) -> dict[str, Any]:
        normalized_turns = self._normalize_turns(turns)
        recent_turns = normalized_turns[-self.max_recent_turns :] if self.max_recent_turns else []
        normalized_documents = self._normalize_documents(documents or [])
        normalized_evidence = self._normalize_evidence_items(evidence_items or [])
        normalized_chunks = self._normalize_document_chunks(document_chunks or [])
        gate_status = self._payload(getattr(record, "gate_status_json", None))

        return {
            "schema_version": "graph_case_state.v1",
            "session": {
                "session_id": record.session_id,
                "phase_state": record.phase_state,
                "declared_family": record.declared_family,
                "current_governor_decision": record.current_governor_decision,
            },
            "profile_json": self._payload(getattr(record, "profile_json", None)),
            "route_candidates": self._list_payload(
                getattr(record, "route_candidates_json", None)
            ),
            "gate_status": gate_status,
            "gate_progress": self._build_gate_progress(gate_status),
            "current_focus": self._payload(getattr(record, "current_focus_json", None)),
            "interviewer_state": self._payload(
                getattr(record, "interviewer_state_json", None)
            ),
            "recent_turns": recent_turns,
            "history_summary": self._build_history_summary(normalized_turns),
            "documents": normalized_documents,
            "document_chunks": normalized_chunks,
            "evidence_items": normalized_evidence,
            "evidence_digest": self._build_evidence_digest(
                documents=normalized_documents,
                evidence_items=normalized_evidence,
            ),
            "runtime_trace_tail": self._tail_payloads(
                getattr(record, "runtime_trace_json", None)
            ),
            "score_history_tail": self._tail_payloads(
                getattr(record, "score_history_json", None)
            ),
            "governor_history_tail": self._tail_payloads(
                getattr(record, "governor_history_json", None)
            ),
        }

    def _normalize_turns(self, turns: list[Any]) -> list[dict[str, Any]]:
        normalized = [
            {
                "turn_id": self._string_or_none(getattr(turn, "turn_id", None)),
                "turn_index": getattr(turn, "turn_index", None),
                "session_id": self._string_or_none(getattr(turn, "session_id", None)),
                "role": self._string_or_none(getattr(turn, "role", None)),
                "source": self._string_or_none(getattr(turn, "source", None)),
                "content": self._string_or_none(getattr(turn, "content", None)) or "",
                "metadata": self._normalize_turn_metadata(
                    getattr(turn, "metadata_json", None)
                ),
            }
            for turn in turns
        ]
        return sorted(
            normalized,
            key=lambda item: (
                item["turn_index"] if isinstance(item["turn_index"], int) else 0,
                item["turn_id"] or "",
            ),
        )

    def _normalize_turn_metadata(
        self,
        metadata_json: Any,
    ) -> dict[str, Any]:
        metadata = self._payload(metadata_json)
        turn_record = self._payload(metadata.get("turn_record"))
        return {
            "phase_state": self._string_or_none(metadata.get("phase_state")),
            "governor_decision": self._string_or_none(
                metadata.get("governor_decision")
            ),
            "turn_decision": self._string_or_none(metadata.get("turn_decision"))
            or self._string_or_none(turn_record.get("decision")),
            "requested_documents": self._normalize_document_types(
                metadata.get("requested_documents")
                or turn_record.get("requested_documents")
                or []
            ),
            "current_focus_kind": self._string_or_none(
                metadata.get("current_focus_kind")
            )
            or self._string_or_none(self._payload(turn_record.get("focus")).get("kind")),
            "turn_record": turn_record,
            "runtime_view_state": self._payload(metadata.get("runtime_view_state")),
            "prompt_trace": self._payload(metadata.get("prompt_trace")),
        }

    def _normalize_documents(self, documents: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for document in documents:
            artifact = self._payload(getattr(document, "artifact_json", None))
            document_type = (
                normalize_document_type(artifact.get("document_type"))
                or self._string_or_none(artifact.get("document_type"))
            )
            normalized.append(
                {
                    "document_id": self._string_or_none(
                        getattr(document, "document_id", None)
                    ),
                    "session_id": self._string_or_none(
                        getattr(document, "session_id", None)
                    ),
                    "filename": self._string_or_none(
                        getattr(document, "filename", None)
                    )
                    or "",
                    "status": self._string_or_none(getattr(document, "status", None)),
                    "document_type": document_type,
                    "artifact_json": artifact,
                }
            )
        return sorted(
            normalized,
            key=lambda item: (item["filename"], item["document_id"] or ""),
        )

    def _normalize_document_chunks(self, chunks: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for chunk in chunks:
            text = self._string_or_none(getattr(chunk, "text", None)) or ""
            normalized.append(
                {
                    "chunk_id": self._string_or_none(getattr(chunk, "chunk_id", None)),
                    "document_id": self._string_or_none(
                        getattr(chunk, "document_id", None)
                    ),
                    "session_id": self._string_or_none(
                        getattr(chunk, "session_id", None)
                    ),
                    "ordinal": getattr(chunk, "ordinal", None),
                    "page_number": getattr(chunk, "page_number", None),
                    "text_excerpt": self._excerpt(text),
                    "text_length": len(text),
                    "metadata": self._payload(getattr(chunk, "metadata_json", None)),
                }
            )
        return sorted(
            normalized,
            key=lambda item: (
                item["document_id"] or "",
                item["ordinal"] if isinstance(item["ordinal"], int) else 0,
                item["chunk_id"] or "",
            ),
        )

    def _normalize_evidence_items(
        self,
        evidence_items: list[Any],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in evidence_items:
            normalized.append(
                {
                    "evidence_id": self._string_or_none(
                        getattr(item, "evidence_id", None)
                    ),
                    "session_id": self._string_or_none(
                        getattr(item, "session_id", None)
                    ),
                    "document_id": self._string_or_none(
                        getattr(item, "document_id", None)
                    ),
                    "chunk_id": self._string_or_none(getattr(item, "chunk_id", None)),
                    "evidence_type": self._string_or_none(
                        getattr(item, "evidence_type", None)
                    ),
                    "field_path": self._string_or_none(
                        getattr(item, "field_path", None)
                    ),
                    "value": self._string_or_none(getattr(item, "value", None)),
                    "excerpt": self._excerpt(
                        self._string_or_none(getattr(item, "excerpt", None)) or ""
                    ),
                    "confidence": getattr(item, "confidence", None),
                    "metadata": self._payload(getattr(item, "metadata_json", None)),
                }
            )
        return sorted(
            normalized,
            key=lambda item: (
                item["field_path"] or "",
                item["evidence_id"] or "",
            ),
        )

    def _build_gate_progress(self, gate_status: dict[str, Any]) -> dict[str, Any]:
        documents: list[dict[str, Any]] = []
        ready_count = 0
        uploaded_count = 0
        missing_count = 0

        for item in self._list_payload(gate_status.get("required_documents")):
            document = {
                "document_type": self._string_or_none(item.get("document_type")),
                "status": self._string_or_none(item.get("status")) or "missing",
                "is_uploaded": bool(item.get("is_uploaded", False)),
                "is_parsed": bool(item.get("is_parsed", False)),
                "meets_minimum_fields": bool(
                    item.get("meets_minimum_fields", False)
                ),
            }
            documents.append(document)
            if document["status"] == "ready":
                ready_count += 1
            elif document["status"] == "uploaded":
                uploaded_count += 1
            else:
                missing_count += 1

        return {
            "overall_status": self._string_or_none(gate_status.get("status")),
            "ready_count": ready_count,
            "uploaded_count": uploaded_count,
            "missing_count": missing_count,
            "documents": documents,
        }

    def _build_history_summary(
        self,
        turns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prior_decisions: list[str] = []
        prior_requested_documents: list[str] = []
        prior_question_topics: list[str] = []

        for turn in turns:
            metadata = self._payload(turn.get("metadata"))
            turn_record = self._payload(metadata.get("turn_record"))
            decision = self._string_or_none(metadata.get("turn_decision")) or self._string_or_none(
                turn_record.get("decision")
            )
            if decision:
                prior_decisions.append(decision)
            for document_type in self._normalize_document_types(
                metadata.get("requested_documents")
                or turn_record.get("requested_documents")
                or []
            ):
                if document_type not in prior_requested_documents:
                    prior_requested_documents.append(document_type)
            focus = self._payload(turn_record.get("focus"))
            question = self._string_or_none(focus.get("question"))
            if question:
                prior_question_topics.append(question)

        return {
            "turn_count": len(turns),
            "user_turn_count": sum(1 for turn in turns if turn.get("role") == "user"),
            "assistant_turn_count": sum(
                1 for turn in turns if turn.get("role") == "assistant"
            ),
            "prior_decisions": prior_decisions[-self.max_history_items :],
            "prior_requested_documents": prior_requested_documents[
                -self.max_history_items :
            ],
            "prior_question_topics": prior_question_topics[-self.max_history_items :],
        }

    def _build_evidence_digest(
        self,
        *,
        documents: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        uploaded_documents = [
            {
                "document_id": document.get("document_id"),
                "filename": document.get("filename"),
                "status": document.get("status"),
                "document_type": document.get("document_type"),
            }
            for document in documents
        ]
        documented_field_paths: list[str] = []
        evidence_refs: list[str] = []
        supported_claims: list[str] = []
        for item in evidence_items:
            field_path = self._string_or_none(item.get("field_path"))
            if field_path and field_path not in documented_field_paths:
                documented_field_paths.append(field_path)
            evidence_id = self._string_or_none(item.get("evidence_id"))
            if evidence_id:
                evidence_refs.append(evidence_id)
            if field_path and item.get("value") is not None:
                supported_claims.append(f"{field_path}={item['value']}")

        return {
            "uploaded_document_count": len(documents),
            "uploaded_documents": uploaded_documents,
            "documented_field_paths": documented_field_paths,
            "evidence_refs": evidence_refs,
            "supported_claims": supported_claims[-self.max_history_items :],
        }

    def _tail_payloads(self, value: Any) -> list[dict[str, Any]]:
        return self._list_payload(value)[-self.max_history_items :]

    def _normalize_document_types(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            document_type = normalize_document_type(item) or item.strip()
            if document_type and document_type not in normalized:
                normalized.append(document_type)
        return normalized

    def _excerpt(self, value: str) -> str:
        if self.max_text_excerpt_chars <= 0:
            return ""
        return value[: self.max_text_excerpt_chars]

    def _payload(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _list_payload(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
