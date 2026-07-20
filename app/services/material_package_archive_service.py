from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord
from app.domain.document_types import normalize_document_type
from app.repositories.document_repo import DocumentRepository
from app.repositories.session_repo import SessionRepository
from app.services.case_memory_service import CaseMemoryService
from app.services.debug_material_bundle_service import DOCUMENT_TYPE_LABELS
from app.services.gate_runtime_service import GateRuntimeService
from app.services.message_service import MessageService
from app.services.profile_recompute_service import ProfileRecomputeService
from app.services.runtime_errors import ModelRuntimeError


VALIDATED_ARCHIVE_SOURCE_REASON = "validated_f1_demo_material_package"
F1_VALIDATED_PACKAGE_REQUIRED_DOCUMENT_TYPES = (
    "ds160",
    "passport_bio",
    "i20",
    "admission_letter",
    "funding_proof",
    "relationship_proof_between_applicant_and_sponsors",
)


class MaterialPackageNotReadyError(RuntimeError):
    def __init__(
        self,
        package_id: str,
        *,
        status: str,
        warning: str | None,
    ) -> None:
        self.package_id = package_id
        self.status = status
        self.warning = warning
        detail = warning or f"Material package {package_id} is not ready to import."
        super().__init__(detail)


class MaterialPackageArchiveService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sessions = SessionRepository(db)
        self.documents = DocumentRepository(db)

    def list_packages(self) -> dict[str, Any]:
        packages = [
            self._package_payload(package_id, documents)
            for package_id, documents in self._group_archived_documents().items()
        ]
        packages.sort(
            key=lambda item: (
                str(item.get("source_session_id") or ""),
                str(item.get("package_id") or ""),
            ),
            reverse=True,
        )
        return {"packages": packages}

    def import_package(self, session_id: str, package_id: str) -> dict[str, Any]:
        record = self.sessions.get(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")

        source_documents = self._group_archived_documents().get(package_id)
        if not source_documents:
            raise LookupError(f"Material package not found: {package_id}")

        status, _status_label, warning = self._package_status(source_documents)
        if status != "ready":
            raise MaterialPackageNotReadyError(
                package_id,
                status=status,
                warning=warning,
            )

        imported_bundle_id = f"pkg-import-{uuid4().hex[:12]}"
        imported_documents: list[dict[str, Any]] = []
        for source_document in source_documents:
            imported_document = self._copy_document(
                source_document,
                target_session_id=session_id,
                source_package_id=package_id,
                imported_bundle_id=imported_bundle_id,
            )
            imported_documents.append(
                self._document_payload(imported_document, include_raw_text=True)
            )

        ProfileRecomputeService(self.db).recompute_session(session_id, save=False)
        GateRuntimeService(self.db).refresh_record(record, save=False)
        # Import copies artifacts with material understanding but does not go
        # through upsert_material_understanding — force case memory rebuild so
        # sticky empty snapshots from prior chat turns do not hide new claims.
        CaseMemoryService(self.db).rebuild_and_persist(session_id)
        self.db.commit()

        main_flow_response: dict[str, Any] = {}
        refresh_error: str | None = None
        try:
            main_flow_response = MessageService(self.db).refresh_after_material_change(
                session_id,
                reason=f"material_package_import:{package_id}",
            )
        except ModelRuntimeError as exc:
            refresh_error = exc.detail
            self.db.rollback()
        except Exception as exc:
            refresh_error = f"{exc.__class__.__name__}: {exc}"
            self.db.rollback()

        self.db.refresh(record)
        return {
            "session_id": session_id,
            "package_id": package_id,
            "imported_bundle_id": imported_bundle_id,
            "import_status": "partial" if refresh_error else "imported",
            "status_label": "已导入，有刷新警告" if refresh_error else "已导入",
            "documents": imported_documents,
            "assistant_message": main_flow_response.get("assistant_message"),
            "governor_decision": main_flow_response.get("governor_decision"),
            "requested_documents": list(
                main_flow_response.get("requested_documents", []) or []
            ),
            "remaining_required_documents": list(
                main_flow_response.get("remaining_required_documents", []) or []
            ),
            "turn_decision": dict(main_flow_response.get("turn_decision", {}) or {}),
            "document_review": dict(
                main_flow_response.get("document_review", {}) or {}
            ),
            "runtime_view_state": dict(
                main_flow_response.get("runtime_view_state", {}) or {}
            ),
            "material_refresh": dict(
                main_flow_response.get("material_refresh", {}) or {}
            ),
            "phase_state": record.phase_state,
            "gate_status": record.gate_status_json,
            "main_flow_refresh_error": refresh_error,
        }

    def _group_archived_documents(self) -> dict[str, list[DocumentRecord]]:
        documents = list(
            self.db.scalars(select(DocumentRecord).order_by(DocumentRecord.document_id))
        )
        grouped: dict[str, list[DocumentRecord]] = {}
        for document in documents:
            metadata = self._document_metadata(document)
            if not metadata.get("debug_material_bundle"):
                continue
            if metadata.get("material_package_import"):
                continue
            if not self._is_validated_archive_source(metadata):
                continue
            package_id = self._string_or_none(metadata.get("synthetic_bundle_id"))
            if not package_id:
                continue
            grouped.setdefault(package_id, []).append(document)
        return grouped

    def _package_payload(
        self,
        package_id: str,
        documents: list[DocumentRecord],
    ) -> dict[str, Any]:
        first_metadata = self._document_metadata(documents[0])
        scenario = self._string_or_none(first_metadata.get("debug_bundle_scenario"))
        scenario_label = self._string_or_none(
            first_metadata.get("debug_bundle_scenario_label")
        )
        visa_family = self._string_or_none(first_metadata.get("visa_family"))
        if visa_family is None:
            visa_family = self._source_session_visa_family(documents)
        document_payloads = [
            self._document_payload(document, include_raw_text=False)
            for document in documents
        ]
        status, status_label, warning = self._package_status(documents)
        validation_status = self._string_or_none(
            first_metadata.get("validation_status")
        )
        if status != "ready" and validation_status == "passed":
            validation_status = "incomplete"
        if validation_status is None and self._is_validated_archive_source(
            first_metadata
        ):
            validation_status = "passed" if status == "ready" else "incomplete"
        return {
            "package_id": package_id,
            "label": scenario_label or package_id,
            "scenario": scenario,
            "scenario_label": scenario_label,
            "source_session_id": documents[0].session_id,
            "created_at": None,
            "status": status,
            "status_label": status_label,
            "warning": warning,
            "document_count": len(documents),
            "document_types": [
                item["document_type"]
                for item in document_payloads
                if item.get("document_type")
            ],
            "validation_status": validation_status,
            "source_validation_session_id": self._string_or_none(
                first_metadata.get("source_validation_session_id")
            ),
            "demo_template_id": self._string_or_none(
                first_metadata.get("demo_template_id")
            ),
            "archive_source_reason": self._string_or_none(
                first_metadata.get("archive_source_reason")
            ),
            "intent": self._string_or_none(first_metadata.get("intent")),
            "visa_family": visa_family,
            "documents": document_payloads,
        }

    def _package_status(
        self,
        documents: list[DocumentRecord],
    ) -> tuple[str, str, str | None]:
        if not documents:
            return "failed", "失败不可导入", "没有可导入的材料。"

        missing_document_type = [
            document.filename
            for document in documents
            if not self._document_type(document)
        ]
        if missing_document_type:
            return (
                "partial",
                "有警告",
                f"{len(missing_document_type)} 份材料缺少 document_type。",
            )

        missing_required = self._missing_required_document_types(documents)
        if missing_required:
            return (
                "partial",
                "有警告",
                "缺少必要材料类型：" + ", ".join(missing_required),
            )

        incomplete = [
            document.filename
            for document in documents
            if document.status != "parsed"
            or (document.artifact_json or {}).get("understanding_status")
            != "completed"
        ]
        if incomplete:
            return (
                "partial",
                "有警告",
                f"{len(incomplete)} 份材料的理解状态不完整。",
            )
        return "ready", "可导入", None

    def _is_validated_archive_source(self, metadata: dict[str, Any]) -> bool:
        if self._string_or_none(metadata.get("source_validation_session_id")) is None:
            return False
        if metadata.get("archive_source_reason") == VALIDATED_ARCHIVE_SOURCE_REASON:
            return True
        return metadata.get("demo_template_archive_source") is True

    def _missing_required_document_types(
        self,
        documents: list[DocumentRecord],
    ) -> list[str]:
        required = self._required_document_types_for_package(documents)
        if not required:
            return []
        present = {self._document_type(document) for document in documents}
        return [
            document_type
            for document_type in required
            if document_type not in present
        ]

    def _required_document_types_for_package(
        self,
        documents: list[DocumentRecord],
    ) -> tuple[str, ...]:
        if not documents:
            return ()
        first_metadata = self._document_metadata(documents[0])
        configured = first_metadata.get("required_document_types")
        if isinstance(configured, list):
            normalized = tuple(
                document_type
                for document_type in (
                    normalize_document_type(item)
                    for item in configured
                    if isinstance(item, str)
                )
                if document_type
            )
            if normalized:
                return normalized
        visa_family = self._string_or_none(first_metadata.get("visa_family"))
        if visa_family and visa_family.lower() == "f1":
            return F1_VALIDATED_PACKAGE_REQUIRED_DOCUMENT_TYPES
        if (
            first_metadata.get("archive_source_reason")
            == VALIDATED_ARCHIVE_SOURCE_REASON
        ):
            return F1_VALIDATED_PACKAGE_REQUIRED_DOCUMENT_TYPES
        return ()

    def _document_type(self, document: DocumentRecord) -> str | None:
        return normalize_document_type(
            self._string_or_none(self._document_metadata(document).get("document_type"))
        )

    def _copy_document(
        self,
        source_document: DocumentRecord,
        *,
        target_session_id: str,
        source_package_id: str,
        imported_bundle_id: str,
    ) -> DocumentRecord:
        source_chunks = list(
            self.db.scalars(
                select(DocumentChunkRecord)
                .where(DocumentChunkRecord.document_id == source_document.document_id)
                .order_by(DocumentChunkRecord.ordinal)
            )
        )
        source_evidence = list(
            self.db.scalars(
                select(EvidenceItemRecord)
                .where(EvidenceItemRecord.document_id == source_document.document_id)
                .order_by(EvidenceItemRecord.evidence_id)
            )
        )

        chunk_id_map = {
            chunk.chunk_id: f"chunk-{uuid4().hex[:12]}" for chunk in source_chunks
        }
        evidence_id_map = {
            evidence.evidence_id: f"evi-{uuid4().hex[:12]}"
            for evidence in source_evidence
        }
        document = self.documents.create_document(
            session_id=target_session_id,
            filename=source_document.filename,
            raw_bytes=source_document.raw_bytes or b"",
            raw_text=source_document.raw_text or "",
            artifact_json={},
        )
        document.status = source_document.status
        replacements = {
            source_document.document_id: document.document_id,
            source_document.session_id: target_session_id,
            **chunk_id_map,
            **evidence_id_map,
        }
        artifact_json = self._rewrite_value(
            deepcopy(source_document.artifact_json or {}),
            replacements,
        )
        artifact_json["document_id"] = document.document_id
        artifact_json["session_id"] = target_session_id
        metadata = dict(artifact_json.get("metadata") or {})
        metadata.update(
            {
                "debug_material_bundle": True,
                "synthetic_bundle_id": imported_bundle_id,
                "material_package_import": True,
                "archived_package_id": source_package_id,
                "source_session_id": source_document.session_id,
                "source_document_id": source_document.document_id,
            }
        )
        artifact_json["metadata"] = metadata
        document.artifact_json = artifact_json

        self.db.add_all(
            [
                DocumentChunkRecord(
                    chunk_id=chunk_id_map[chunk.chunk_id],
                    document_id=document.document_id,
                    session_id=target_session_id,
                    ordinal=chunk.ordinal,
                    page_number=chunk.page_number,
                    text=chunk.text,
                    metadata_json=self._import_metadata(
                        chunk.metadata_json,
                        source_package_id=source_package_id,
                        imported_bundle_id=imported_bundle_id,
                    ),
                )
                for chunk in source_chunks
            ]
        )
        self.db.add_all(
            [
                EvidenceItemRecord(
                    evidence_id=evidence_id_map[evidence.evidence_id],
                    session_id=target_session_id,
                    document_id=document.document_id,
                    chunk_id=chunk_id_map.get(evidence.chunk_id, evidence.chunk_id),
                    evidence_type=evidence.evidence_type,
                    field_path=evidence.field_path,
                    value=evidence.value,
                    excerpt=evidence.excerpt,
                    confidence=evidence.confidence,
                    metadata_json=self._import_metadata(
                        evidence.metadata_json,
                        source_package_id=source_package_id,
                        imported_bundle_id=imported_bundle_id,
                    ),
                )
                for evidence in source_evidence
            ]
        )
        self.db.add(document)
        self.db.flush()
        return document

    def _document_payload(
        self,
        document: DocumentRecord,
        *,
        include_raw_text: bool,
    ) -> dict[str, Any]:
        metadata = self._document_metadata(document)
        document_type = self._string_or_none(metadata.get("document_type"))
        payload: dict[str, Any] = {
            "document_id": document.document_id,
            "filename": document.filename,
            "document_type": document_type,
            "document_type_label": (
                DOCUMENT_TYPE_LABELS.get(document_type or "")
                if document_type
                else None
            ),
            "content_url": (
                f"/v1/sessions/{document.session_id}/files/"
                f"{document.document_id}/content"
            ),
            "status": document.status,
            "understanding_status": (document.artifact_json or {}).get(
                "understanding_status"
            ),
            "fields": self._document_fields(document),
        }
        if include_raw_text:
            payload["raw_text"] = document.raw_text or ""
        return payload

    def _document_fields(self, document: DocumentRecord) -> dict[str, str]:
        evidence = list(
            self.db.scalars(
                select(EvidenceItemRecord).where(
                    EvidenceItemRecord.document_id == document.document_id
                )
            )
        )
        return {
            item.field_path: item.value
            for item in evidence
            if item.field_path and item.value is not None
        }

    def _import_metadata(
        self,
        metadata_json: dict[str, Any] | None,
        *,
        source_package_id: str,
        imported_bundle_id: str,
    ) -> dict[str, Any]:
        metadata = dict(metadata_json or {})
        metadata.update(
            {
                "synthetic_bundle_id": imported_bundle_id,
                "material_package_import": True,
                "archived_package_id": source_package_id,
            }
        )
        return metadata

    def _rewrite_value(self, value: Any, replacements: dict[str, str]) -> Any:
        if isinstance(value, dict):
            return {
                key: self._rewrite_value(item, replacements)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._rewrite_value(item, replacements) for item in value]
        if isinstance(value, str):
            rewritten = value
            for old, new in replacements.items():
                rewritten = rewritten.replace(old, new)
            return rewritten
        return value

    def _document_metadata(self, document: DocumentRecord) -> dict[str, Any]:
        artifact = dict(document.artifact_json or {})
        metadata = dict(artifact.get("metadata") or {})
        if "document_type" not in metadata and artifact.get("document_type"):
            metadata["document_type"] = artifact.get("document_type")
        return metadata

    def _source_session_visa_family(
        self,
        documents: list[DocumentRecord],
    ) -> str | None:
        session_id = (
            self._string_or_none(documents[0].session_id) if documents else None
        )
        if session_id is None:
            return None
        record = self.sessions.get(session_id)
        if record is None:
            return None
        return self._string_or_none(record.declared_family)

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
