from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.domain.document_types import DOCUMENT_TYPE_ALIASES, normalize_document_type
from app.domain.evidence import DocumentSourceType
from app.repositories.document_repo import DocumentRepository
from app.repositories.session_repo import SessionRepository
from app.services.gate_runtime_service import GateRuntimeService
from app.services.multimodal_extraction_service import MultimodalExtractionService

MAX_UPLOAD_SIZE_MB = 64
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
ALLOWED_UPLOAD_MIME_TYPES = (
    "application/pdf",
    "image/png",
    "image/jpeg",
)
_ALLOWED_UPLOAD_EXTENSIONS_BY_MIME = {
    "application/pdf": (".pdf",),
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
}
_ALLOWED_UPLOAD_EXTENSION_TO_MIME = {
    extension: content_type
    for content_type, extensions in _ALLOWED_UPLOAD_EXTENSIONS_BY_MIME.items()
    for extension in extensions
}
_CONTENT_TYPE_ALIASES = {
    "image/jpg": "image/jpeg",
}
_GENERIC_BINARY_CONTENT_TYPE = "application/octet-stream"


class SessionNotFoundError(LookupError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class FileTooLargeError(ValueError):
    pass


class UnsupportedFileTypeError(ValueError):
    pass


@dataclass
class FileUploadResult:
    document_id: str
    job_id: str
    document_type: str | None
    feedback_message: str | None = None
    relevant: bool | None = None
    main_flow_feedback: dict[str, Any] | None = None
    requested_documents: list[str] | None = None
    gate_progress: dict[str, Any] | None = None


def _normalize_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    normalized = content_type.split(";", 1)[0].strip().lower()
    if not normalized:
        return None
    return _CONTENT_TYPE_ALIASES.get(normalized, normalized)


def resolve_upload_content_type(filename: str, content_type: str | None = None) -> str:
    suffix = Path(filename).suffix.lower()
    normalized_content_type = _normalize_content_type(content_type)

    if normalized_content_type in _ALLOWED_UPLOAD_EXTENSIONS_BY_MIME:
        allowed_extensions = _ALLOWED_UPLOAD_EXTENSIONS_BY_MIME[normalized_content_type]
        if suffix and suffix not in allowed_extensions:
            raise UnsupportedFileTypeError(
                "Only PDF and PNG/JPG/JPEG images are supported"
            )
        return normalized_content_type

    if suffix in _ALLOWED_UPLOAD_EXTENSION_TO_MIME:
        return _ALLOWED_UPLOAD_EXTENSION_TO_MIME[suffix]

    guessed_content_type, _ = mimetypes.guess_type(filename)
    normalized_guess = _normalize_content_type(guessed_content_type)
    if normalized_guess in _ALLOWED_UPLOAD_EXTENSIONS_BY_MIME:
        return normalized_guess

    if normalized_content_type == _GENERIC_BINARY_CONTENT_TYPE:
        raise UnsupportedFileTypeError(
            "Only PDF and PNG/JPG/JPEG images are supported"
        )

    raise UnsupportedFileTypeError("Only PDF and PNG/JPG/JPEG images are supported")


def resolve_source_type(content_type: str) -> DocumentSourceType:
    if content_type == "application/pdf":
        return DocumentSourceType.PDF
    return DocumentSourceType.IMAGE


class FileService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = DocumentRepository(db)
        self.sessions = SessionRepository(db)
        self.multimodal = MultimodalExtractionService()

    def upload(
        self,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
        content_type: str | None = None,
        document_type: str | None = None,
    ) -> FileUploadResult:
        session_record = self.sessions.get(session_id)
        if session_record is None:
            raise SessionNotFoundError(session_id)
        if len(raw_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise FileTooLargeError("Uploaded file exceeds 64MB limit")
        normalized_content_type = resolve_upload_content_type(filename, content_type)
        gate_runtime = GateRuntimeService(self.db)
        session_record = gate_runtime.refresh_record(session_record, save=False)
        pre_upload_support = gate_runtime.build_gate_support(session_record)
        required_document_types = self._required_document_types(session_record)
        feedback_message, relevant = self._analyze_relevance(
            filename=filename,
            raw_bytes=raw_bytes,
            content_type=normalized_content_type,
            document_type=document_type,
        )
        supported_document_type = self._supported_document_type(
            filename=filename,
            document_type=document_type,
            required_document_types=required_document_types,
        )
        counts_toward_gate = self._counts_toward_gate(
            supported_document_type=supported_document_type,
            required_document_types=required_document_types,
            relevant=relevant,
        )
        artifact_json = {
            "status": "uploaded",
            "filename": filename,
            "document_type": document_type,
            "feedback_message": feedback_message,
            "relevant": relevant,
        }
        if counts_toward_gate is not None:
            artifact_json["counts_toward_gate"] = counts_toward_gate

        try:
            document = self.repo.create_document(
                session_id=session_id,
                filename=filename,
                raw_bytes=raw_bytes,
                raw_text="",
                artifact_json=artifact_json,
            )
            job = self.repo.enqueue_job(
                session_id=session_id,
                kind="gate_parse",
                payload_json={"document_id": document.document_id},
            )
            session_record = gate_runtime.refresh_record(session_record, save=False)
            post_upload_support = gate_runtime.build_gate_support(session_record)
            main_flow_feedback = self._build_main_flow_feedback(
                supported_document_type=supported_document_type,
                counts_toward_gate=counts_toward_gate,
                pre_upload_support=pre_upload_support,
                post_upload_support=post_upload_support,
            )
            if main_flow_feedback is not None:
                document.artifact_json = {
                    **(document.artifact_json or {}),
                    "main_flow_feedback": main_flow_feedback,
                }
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return FileUploadResult(
            document_id=document.document_id,
            job_id=job.job_id,
            document_type=document_type,
            feedback_message=feedback_message,
            relevant=relevant,
            main_flow_feedback=main_flow_feedback,
            requested_documents=post_upload_support["requested_documents"],
            gate_progress=post_upload_support["gate_progress"],
        )

    def _analyze_relevance(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        content_type: str,
        document_type: str | None,
    ) -> tuple[str | None, bool | None]:
        if document_type is None:
            return None, None

        result = self.multimodal.extract(
            filename=filename,
            raw_bytes=raw_bytes,
            source_type=resolve_source_type(content_type),
            document_type=document_type,
        )
        if result is None:
            return (
                f"暂时无法判断这份文件是否属于 {document_type}，系统会继续尝试解析。",
                None,
            )
        if not result.fields:
            return (
                f"这份文件看起来不像当前要求的 {document_type} 材料，请检查后重新上传。",
                False,
            )
        return (
            f"已识别出 {len(result.fields)} 个与 {document_type} 相关的字段，系统将继续处理。",
            True,
        )

    def _required_document_types(self, session_record) -> set[str]:
        gate_status = session_record.gate_status_json or {}
        return {
            item["document_type"]
            for item in gate_status.get("required_documents", [])
            if item.get("document_type")
        }

    def _supported_document_type(
        self,
        *,
        filename: str,
        document_type: str | None,
        required_document_types: set[str],
    ) -> str | None:
        normalized_required_document_types = {
            normalize_document_type(required_document_type)
            for required_document_type in required_document_types
            if normalize_document_type(required_document_type) is not None
        }
        normalized_document_type = normalize_document_type(document_type)
        if normalized_document_type in normalized_required_document_types:
            return normalized_document_type

        lowered_filename = filename.lower()
        for required_document_type in normalized_required_document_types:
            if required_document_type in lowered_filename:
                return required_document_type
        for alias, normalized_document_type in DOCUMENT_TYPE_ALIASES.items():
            if (
                alias in lowered_filename
                and normalized_document_type in normalized_required_document_types
            ):
                return normalized_document_type
        return None

    def _build_main_flow_feedback(
        self,
        *,
        supported_document_type: str | None,
        counts_toward_gate: bool | None,
        pre_upload_support: dict[str, Any],
        post_upload_support: dict[str, Any],
    ) -> dict[str, str | None] | None:
        current_focus_document_type = (
            post_upload_support.get("primary_document")
            or pre_upload_support.get("primary_document")
        )
        support_message = post_upload_support.get("support_message")

        if (
            current_focus_document_type is None
            and supported_document_type is None
            and counts_toward_gate is None
        ):
            return None

        if not counts_toward_gate or supported_document_type is None:
            return {
                "status": "not_helpful",
                "supported_document_type": None,
                "current_focus_document_type": current_focus_document_type,
                "message": self._join_feedback_message(
                    "这份材料对当前主线没有直接帮助。",
                    support_message,
                ),
            }

        if pre_upload_support.get("primary_document") == supported_document_type:
            return {
                "status": "helpful",
                "supported_document_type": supported_document_type,
                "current_focus_document_type": current_focus_document_type,
                "message": self._join_feedback_message(
                    f"这份材料对当前关键证明 {supported_document_type} 有帮助。",
                    support_message,
                ),
            }

        return {
            "status": "partial_helpful",
            "supported_document_type": supported_document_type,
            "current_focus_document_type": current_focus_document_type,
            "message": self._join_feedback_message(
                f"这份材料对 {supported_document_type} 有帮助，但当前主线没有改变。",
                support_message,
            ),
        }

    def _counts_toward_gate(
        self,
        *,
        supported_document_type: str | None,
        required_document_types: set[str],
        relevant: bool | None,
    ) -> bool | None:
        if not required_document_types:
            return None
        if supported_document_type is None:
            return False
        return relevant is not False

    def _join_feedback_message(
        self,
        headline: str,
        support_message: str | None,
    ) -> str:
        if not support_message:
            return headline
        return f"{headline} {support_message}"
