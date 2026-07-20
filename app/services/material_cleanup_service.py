from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AccessKeyRecord, AccessKeySessionRecord, DocumentRecord
from app.repositories.document_repo import DocumentRepository
from app.services.case_memory_service import CaseMemoryService
from app.services.gate_runtime_service import GateRuntimeService
from app.services.material_package_archive_service import VALIDATED_ARCHIVE_SOURCE_REASON
from app.services.profile_recompute_service import ProfileRecomputeService


@dataclass(frozen=True)
class MaterialCleanupResult:
    key_id: str
    session_count: int
    cleared_document_count: int
    skipped_template_count: int
    affected_session_ids: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "session_count": self.session_count,
            "cleared_document_count": self.cleared_document_count,
            "skipped_template_count": self.skipped_template_count,
            "affected_session_ids": self.affected_session_ids,
        }


class MaterialCleanupService:
    """Key-scoped material cleanup using tombstones instead of hard deletes."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def clear_access_key_materials(self, key_id: str) -> MaterialCleanupResult:
        if self.db.get(AccessKeyRecord, key_id) is None:
            raise LookupError("access key not found")
        session_ids = self._session_ids_for_key(key_id)
        return self.clear_session_materials(key_id=key_id, session_ids=session_ids)

    def clear_session_materials(
        self,
        *,
        key_id: str,
        session_ids: list[str],
    ) -> MaterialCleanupResult:
        normalized_session_ids = [item for item in dict.fromkeys(session_ids) if item]
        if not normalized_session_ids:
            return MaterialCleanupResult(
                key_id=key_id,
                session_count=0,
                cleared_document_count=0,
                skipped_template_count=0,
                affected_session_ids=[],
            )

        documents = list(
            self.db.scalars(
                select(DocumentRecord)
                .where(DocumentRecord.session_id.in_(normalized_session_ids))
                .order_by(
                    DocumentRecord.session_id.asc(),
                    DocumentRecord.document_id.asc(),
                )
            )
        )
        clearable_document_ids: list[str] = []
        skipped_template_count = 0
        for document in documents:
            if self._is_tombstoned(document):
                continue
            if self._is_template_or_archive_related(document):
                skipped_template_count += 1
                continue
            clearable_document_ids.append(document.document_id)

        affected_sessions: list[str] = []
        if clearable_document_ids:
            DocumentRepository(self.db).cancel_jobs_for_documents(clearable_document_ids)
            snapshots = CaseMemoryService(self.db).tombstone_documents(
                document_ids=clearable_document_ids,
                reason="access_key_material_cleanup",
            )
            affected_sessions = sorted(snapshots)
            gate_runtime = GateRuntimeService(self.db)
            profile_recompute = ProfileRecomputeService(self.db)
            case_memory = CaseMemoryService(self.db)
            for session_id in affected_sessions:
                profile_recompute.recompute_session(session_id, save=False)
                case_memory.rebuild_and_persist(session_id)
                gate_runtime.refresh_session(session_id, save=False)

        return MaterialCleanupResult(
            key_id=key_id,
            session_count=len(normalized_session_ids),
            cleared_document_count=len(clearable_document_ids),
            skipped_template_count=skipped_template_count,
            affected_session_ids=affected_sessions,
        )

    def _session_ids_for_key(self, key_id: str) -> list[str]:
        return list(
            self.db.scalars(
                select(AccessKeySessionRecord.session_id)
                .where(AccessKeySessionRecord.key_id == key_id)
                .order_by(AccessKeySessionRecord.created_at.asc())
            )
        )

    def _is_template_or_archive_related(self, document: DocumentRecord) -> bool:
        metadata = self._document_metadata(document)
        if metadata.get("material_package_import") is True:
            return True
        if self._string_or_none(metadata.get("archived_package_id")) is not None:
            return True
        if self._string_or_none(metadata.get("source_document_id")) is not None:
            return True
        if metadata.get("archive_source_reason") == VALIDATED_ARCHIVE_SOURCE_REASON:
            return True
        if metadata.get("demo_template_archive_source") is True:
            return True
        if metadata.get("validation_status") == "passed":
            return True
        if (
            metadata.get("debug_material_bundle") is True
            and self._string_or_none(metadata.get("source_validation_session_id"))
            is not None
        ):
            return True
        return False

    def _is_tombstoned(self, document: DocumentRecord) -> bool:
        return DocumentRepository.is_document_tombstoned(document)

    def _document_metadata(self, document: DocumentRecord) -> dict[str, Any]:
        artifact = dict(document.artifact_json or {})
        metadata = dict(artifact)
        nested = artifact.get("metadata")
        if isinstance(nested, dict):
            metadata.update(nested)
        if "document_type" not in metadata and artifact.get("document_type"):
            metadata["document_type"] = artifact.get("document_type")
        return metadata

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
