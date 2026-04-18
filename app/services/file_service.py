from __future__ import annotations

import mimetypes
from pathlib import Path
from dataclasses import dataclass

from sqlalchemy.orm import Session

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
        feedback_message, relevant = self._analyze_relevance(
            filename=filename,
            raw_bytes=raw_bytes,
            content_type=normalized_content_type,
            document_type=document_type,
        )

        try:
            document = self.repo.create_document(
                session_id=session_id,
                filename=filename,
                raw_bytes=raw_bytes,
                raw_text="",
                artifact_json={
                    "status": "uploaded",
                    "filename": filename,
                    "document_type": document_type,
                    "feedback_message": feedback_message,
                    "relevant": relevant,
                },
            )
            job = self.repo.enqueue_job(
                session_id=session_id,
                kind="gate_parse",
                payload_json={"document_id": document.document_id},
            )
            GateRuntimeService(self.db).refresh_record(session_record, save=False)
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
