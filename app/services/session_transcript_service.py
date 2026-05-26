from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import SessionRecord, SessionTurnRecord
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.case_memory_service import CaseMemoryService
from app.services.interview_memory_service import (
    INTERVIEW_MEMORY_KEY,
    InterviewMemoryService,
)


PUBLIC_TRANSCRIPT_ROLES = {"user", "assistant"}
MAX_CLIENT_MESSAGE_ID_CHARS = 128


class SessionTranscriptService:
    """Owns public session transcript import/projection across API entrypoints."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.turns = SessionTurnRepository(db)
        self.case_memory = CaseMemoryService(db)
        self.interview_memory = InterviewMemoryService()

    def build_public_transcript(self, session_id: str) -> list[dict[str, Any]]:
        return [self._public_turn_payload(turn) for turn in self._public_turns(session_id)]

    def import_compat_messages(
        self,
        *,
        session_id: str,
        messages: list[Any],
        phase_state: str | None = None,
    ) -> list[SessionTurnRecord]:
        existing_counts = Counter(
            (turn.role, turn.content) for turn in self._public_turns(session_id)
        )
        imported: list[SessionTurnRecord] = []
        for index, message in enumerate(messages):
            role = self._message_role(message)
            content = self._message_content(message)
            if role not in PUBLIC_TRANSCRIPT_ROLES or not content:
                continue

            key = (role, content)
            if existing_counts[key] > 0:
                existing_counts[key] -= 1
                continue

            metadata = {
                "phase_state": phase_state,
                "source": "chat_completions_import",
                "compat_message_index": index,
                "compat_message_key": self.compat_message_key(
                    role=role,
                    content=content,
                    index=index,
                ),
            }
            metadata = {key: value for key, value in metadata.items() if value is not None}
            if role == "user":
                imported_turn = self.turns.append_user_turn(
                    session_id=session_id,
                    content=content,
                    source="chat_completions_import",
                    metadata_json=metadata,
                    commit=False,
                )
            else:
                imported_turn = self.turns.append_assistant_turn(
                    session_id=session_id,
                    content=content,
                    source="chat_completions_import",
                    metadata_json=metadata,
                    commit=False,
                )
            imported.append(imported_turn)
        self._backfill_imported_user_turn_memory(session_id)
        return imported

    def compat_message_key(
        self,
        *,
        role: str,
        content: str,
        index: int,
    ) -> str:
        digest = sha256(f"{index}\0{role}\0{content}".encode("utf-8")).hexdigest()
        return f"chatcmpl-msg:{digest[:24]}"

    def compat_request_client_message_id(
        self,
        *,
        session_id: str,
        messages: list[Any],
        last_user_index: int,
        context_fingerprint: str | None = None,
    ) -> str:
        digest_input = "\n".join(
            f"{index}:{self._message_role(message)}:{self._message_content(message)}"
            for index, message in enumerate(messages)
        )
        digest = sha256(
            (
                f"{session_id}\0{last_user_index}\0"
                f"{context_fingerprint or ''}\0{digest_input}"
            ).encode("utf-8")
        ).hexdigest()
        return f"chatcmpl:{digest[:32]}"

    def responses_request_client_message_id(
        self,
        *,
        session_id: str,
        messages: list[Any],
        last_user_index: int,
        previous_response_id: str | None,
        context_fingerprint: str | None = None,
    ) -> str:
        digest_input = "\n".join(
            f"{index}:{self._message_role(message)}:{self._message_content(message)}"
            for index, message in enumerate(messages)
        )
        digest = sha256(
            (
                f"{session_id}\0{previous_response_id or ''}\0"
                f"{last_user_index}\0{context_fingerprint or ''}\0{digest_input}"
            ).encode("utf-8")
        ).hexdigest()
        return f"respmsg:{digest[:32]}"

    def session_external_context_fingerprint(self, record: SessionRecord) -> str:
        digest_input = json.dumps(
            {
                "session_id": record.session_id,
                "profile_document_evidence_snapshot": self._jsonable(
                    self._profile_document_evidence_snapshot(record)
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return sha256(digest_input.encode("utf-8")).hexdigest()

    def http_idempotency_client_message_id(
        self,
        *,
        endpoint: str,
        idempotency_key: str | None,
        payload_fingerprint: str,
    ) -> str | None:
        normalized = self._normalized_idempotency_key(idempotency_key)
        if normalized is None:
            return None
        digest = sha256(
            f"{endpoint}\0{normalized}\0{payload_fingerprint}".encode("utf-8")
        ).hexdigest()
        return f"httpidem:{digest[:32]}"

    def request_payload_fingerprint(self, messages: list[Any]) -> str:
        digest_input = "\n".join(
            f"{index}:{self._message_role(message)}:{self._message_content(message)}"
            for index, message in enumerate(messages)
        )
        return sha256(digest_input.encode("utf-8")).hexdigest()

    def normalize_client_message_id(self, value: str | None) -> str | None:
        normalized = self._normalized_idempotency_key(value)
        if normalized is None:
            return None
        if len(normalized) <= MAX_CLIENT_MESSAGE_ID_CHARS:
            return normalized
        digest = sha256(normalized.encode("utf-8")).hexdigest()
        return f"clientmsg:{digest[:48]}"

    def _public_turns(self, session_id: str) -> list[SessionTurnRecord]:
        return [
            turn
            for turn in self.turns.list_session_turns(session_id)
            if turn.role in PUBLIC_TRANSCRIPT_ROLES
        ]

    def _public_turn_payload(self, turn: SessionTurnRecord) -> dict[str, Any]:
        return {
            "turn_id": turn.turn_id,
            "turn_index": turn.turn_index,
            "role": turn.role,
            "content": turn.content,
            "source": turn.source,
            "metadata": dict(turn.metadata_json or {}),
        }

    def _message_role(self, message: Any) -> str | None:
        if isinstance(message, dict):
            role = message.get("role")
        else:
            role = getattr(message, "role", None)
        return role if isinstance(role, str) else None

    def _message_content(self, message: Any) -> str:
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        return content if isinstance(content, str) else ""

    def _capture_imported_user_turn_memory(
        self,
        *,
        session_id: str,
        user_turn: SessionTurnRecord,
    ) -> None:
        self._capture_interview_memory(session_id=session_id, user_turn=user_turn)
        claims = self.case_memory.extract_explicit_user_turn_claims(
            turn_id=user_turn.turn_id,
            message_text=user_turn.content,
        )
        if claims:
            self.case_memory.add_user_turn_claims(
                session_id=session_id,
                turn_id=user_turn.turn_id,
                claims=claims,
            )

    def _backfill_imported_user_turn_memory(self, session_id: str) -> None:
        for turn in self.turns.list_session_turns(session_id):
            if turn.role != "user" or turn.source != "chat_completions_import":
                continue
            self._capture_imported_user_turn_memory(
                session_id=session_id,
                user_turn=turn,
            )

    def _capture_interview_memory(
        self,
        *,
        session_id: str,
        user_turn: SessionTurnRecord,
    ) -> None:
        memory = self.interview_memory.annotate_user_answer(
            assistant_turn=self._previous_assistant_turn(session_id, user_turn),
            user_turn=user_turn,
        )
        if not memory:
            return
        metadata = dict(user_turn.metadata_json or {})
        metadata[INTERVIEW_MEMORY_KEY] = memory
        user_turn.metadata_json = metadata
        self.db.add(user_turn)
        self.db.flush()

    def _previous_assistant_turn(
        self,
        session_id: str,
        user_turn: SessionTurnRecord,
    ) -> SessionTurnRecord | None:
        previous_assistant: SessionTurnRecord | None = None
        for turn in self.turns.list_session_turns(session_id):
            if turn.turn_index >= user_turn.turn_index:
                break
            if turn.role == "assistant":
                previous_assistant = turn
        return previous_assistant

    def _normalized_idempotency_key(self, value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _profile_document_evidence_snapshot(
        self,
        record: SessionRecord,
    ) -> Any:
        profile = getattr(record, "profile_json", None)
        if not isinstance(profile, dict):
            return None
        ds160_view = profile.get("ds160_view")
        if not isinstance(ds160_view, dict):
            return None
        return ds160_view.get("document_evidence_snapshot")

    def _jsonable(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self._jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._jsonable(item) for item in value]
        if isinstance(value, str | int | float | bool) or value is None:
            return value
        return str(value)
