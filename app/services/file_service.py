from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from sqlalchemy.orm import Session

from app.domain.document_types import normalize_document_type
from app.domain.evidence import (
    DocumentAssessment,
    DocumentAssessmentMainFlowFeedback,
    DocumentSourceType,
)
from app.repositories.document_repo import DocumentRepository
from app.repositories.session_repo import SessionRepository
from app.services.gate_runtime_service import GateRuntimeService
from app.services.multimodal_extraction_service import (
    MultimodalExtractionService,
    MultimodalUploadAssessment,
    UploadDocumentTypeCandidate,
)

MAX_UPLOAD_SIZE_MB = 64
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
CASE_UNDERSTANDING_JOB_KIND = "case_understanding"
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
_CONTEXT_DOCUMENT_TYPE_HINT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "passport_bio",
        (
            "passport bio",
            "passport",
            "护照首页",
            "护照信息页",
            "护照资料页",
            "护照",
            "passport_bio",
        ),
    ),
    ("ds160", ("ds-160", "ds160", "160 表", "160表", "ds160 表", "ds160表")),
    ("ds2019", ("ds-2019", "ds2019", "2019 表", "2019表", "ds2019 表", "ds2019表")),
    ("i20", ("i-20", "i20", "i 20")),
    (
        "relationship_proof_between_applicant_and_sponsors",
        (
            "relationship proof",
            "birth certificate",
            "household register",
            "hukou",
            "family register",
            "亲属关系证明",
            "出生证明",
            "出生医学证明",
            "户口本",
            "户口簿",
            "常住人口登记卡",
            "父母关系证明",
        ),
    ),
    (
        "funding_proof",
        (
            "funding proof",
            "bank statement",
            "financial statement",
            "sponsor letter",
            "资金证明",
            "资助证明",
            "银行流水",
            "银行对账单",
            "存款证明",
            "奖学金证明",
            "资助信",
        ),
    ),
    ("admission_letter", ("admission letter", "offer letter", "录取信", "录取通知书")),
    ("itinerary_or_trip_purpose", ("itinerary", "travel plan", "行程单", "行程计划", "旅行计划")),
    ("employer_letter", ("employer letter", "employment letter", "在职证明", "雇主信")),
    ("school_letter", ("school letter", "学校证明", "学校信", "院校证明")),
    ("i797", ("i-797", "i797")),
    ("evidence_of_achievement", ("achievement", "award", "publication", "成果证明", "获奖证明", "论文")),
)


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
    understanding_status: str = "queued"
    document_assessment: DocumentAssessment | None = None
    document_type_candidates: list[str] | None = None
    relevance: str | None = None
    supported_claims: list[str] | None = None
    confidence: float | None = None
    feedback_message: str | None = None
    relevant: bool | None = None
    main_flow_feedback: dict[str, Any] | None = None
    case_board_delta: dict[str, Any] | None = None
    evidence_cards: list[dict[str, Any]] | None = None
    requested_documents: list[str] | None = None
    remaining_required_documents: list[str] | None = None
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
        context_text: str | None = None,
    ) -> FileUploadResult:
        session_record = self.sessions.get(session_id)
        if session_record is None:
            raise SessionNotFoundError(session_id)
        if len(raw_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise FileTooLargeError("Uploaded file exceeds 64MB limit")
        normalized_content_type = resolve_upload_content_type(filename, content_type)
        source_type = resolve_source_type(normalized_content_type)
        gate_runtime = GateRuntimeService(self.db)
        session_record = gate_runtime.refresh_record(session_record, save=False)
        required_document_types = self._required_document_types(session_record)
        document_type_hint = (
            normalize_document_type(document_type)
            or self._document_type_hint_from_context_text(context_text)
        )
        assessment = self._assess_upload(
            filename=filename,
            raw_bytes=raw_bytes,
            source_type=source_type,
            document_type_hint=document_type_hint,
        )
        feedback_message, relevant = self._build_assessment_feedback(
            document_type=document_type_hint,
            assessment=assessment,
        )
        case_supported_document_type = self._case_supported_document_type(
            document_type=document_type_hint,
            assessment_candidates=assessment.document_type_candidates,
        )
        counts_toward_gate = self._legacy_counts_toward_gate(
            case_supported_document_type=case_supported_document_type,
            required_document_types=required_document_types,
            relevance=assessment.relevance,
        )
        top_assessment_document_type = next(
            (
                normalize_document_type(item.document_type)
                for item in assessment.document_type_candidates
                if normalize_document_type(item.document_type) is not None
            ),
            None,
        )
        resolved_document_type = (
            document_type_hint
            or case_supported_document_type
            or top_assessment_document_type
        )
        document_assessment = DocumentAssessment(
            document_type=resolved_document_type,
            document_type_hint=document_type_hint,
            document_type_candidates=[
                item.document_type for item in assessment.document_type_candidates
            ],
            relevance=assessment.relevance,
            supported_claims=list(assessment.supported_claims),
            confidence=assessment.confidence,
            feedback_message=feedback_message,
            relevant=relevant,
            counts_toward_gate=counts_toward_gate,
        )
        artifact_json = {
            "status": "uploaded",
            "filename": filename,
            "document_type": document_assessment.document_type,
            "document_type_hint": document_assessment.document_type_hint,
            "document_type_candidates": list(document_assessment.document_type_candidates),
            "relevance": document_assessment.relevance,
            "supported_claims": list(document_assessment.supported_claims),
            "confidence": document_assessment.confidence,
            "feedback_message": document_assessment.feedback_message,
            "relevant": document_assessment.relevant,
            "document_assessment": document_assessment.to_metadata_payload(),
        }
        if document_assessment.counts_toward_gate is not None:
            artifact_json["counts_toward_gate"] = document_assessment.counts_toward_gate

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
                kind=CASE_UNDERSTANDING_JOB_KIND,
                payload_json={"document_id": document.document_id},
            )
            session_record = gate_runtime.refresh_record(session_record, save=False)
            legacy_gate_progress = self._legacy_gate_progress(session_record)
            main_flow_feedback = self._build_case_understanding_feedback(
                case_document_type=resolved_document_type,
                supported_claims=assessment.supported_claims,
                relevance=assessment.relevance,
                interviewer_focus_document_type=self._interviewer_focus_document_type(
                    session_record
                ),
            )
            evidence_cards = self._pending_evidence_cards(
                document_id=document.document_id,
                supported_claims=assessment.supported_claims,
                document_type=resolved_document_type,
                confidence=assessment.confidence,
            )
            case_board_delta = self._build_case_board_delta(
                document_id=document.document_id,
                filename=filename,
                understanding_status="queued",
                document_type=resolved_document_type,
                assessment=assessment,
                evidence_cards=evidence_cards,
                feedback_message=feedback_message,
            )
            document.artifact_json = {
                **(document.artifact_json or {}),
                "understanding_status": "queued",
                "understanding_job_kind": CASE_UNDERSTANDING_JOB_KIND,
                "case_board_delta": case_board_delta,
                "evidence_cards": evidence_cards,
            }
            if main_flow_feedback is not None:
                document_assessment = document_assessment.model_copy(
                    update={
                        "main_flow_feedback": (
                            DocumentAssessmentMainFlowFeedback.model_validate(
                                main_flow_feedback
                            )
                        )
                    }
                )
                document.artifact_json = {
                    **(document.artifact_json or {}),
                    "main_flow_feedback": main_flow_feedback,
                    "document_assessment": document_assessment.to_metadata_payload(),
                }
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return FileUploadResult(
            document_id=document.document_id,
            job_id=job.job_id,
            document_type=resolved_document_type,
            understanding_status="queued",
            document_assessment=document_assessment,
            document_type_candidates=[
                item.document_type for item in assessment.document_type_candidates
            ],
            relevance=assessment.relevance,
            supported_claims=list(assessment.supported_claims),
            confidence=assessment.confidence,
            feedback_message=feedback_message,
            relevant=relevant,
            main_flow_feedback=main_flow_feedback,
            case_board_delta=case_board_delta,
            evidence_cards=evidence_cards,
            requested_documents=self._interviewer_requested_documents(session_record),
            remaining_required_documents=[],
            gate_progress=legacy_gate_progress,
        )

    def _build_assessment_feedback(
        self,
        *,
        document_type: str | None,
        assessment,
    ) -> tuple[str | None, bool | None]:
        candidate_types = [
            item.document_type for item in assessment.document_type_candidates
        ]
        if document_type is None and not candidate_types:
            return None, None

        if assessment.relevance == "low":
            return (
                "这份材料与当前主线关联较弱，系统会保留结果；如有更直接的补强证据，可以继续上传。",
                False,
            )
        if document_type is not None and candidate_types and document_type not in candidate_types:
            return (
                (
                    f"系统当前更倾向把这份文件识别为 {candidate_types[0]}，"
                    "如识别不准，请在同一条消息里直接说明材料类型，后端会结合文本纠偏。"
                ),
                False,
            )
        if candidate_types:
            headline = f"系统识别候选类型：{', '.join(candidate_types)}。"
        else:
            headline = "系统暂时无法稳定识别这份材料的类型。"
        return (
            self._join_feedback_message(
                headline,
                (
                    f"支持主张：{', '.join(assessment.supported_claims)}。"
                    if assessment.supported_claims
                    else None
                ),
            ),
            assessment.relevance != "low",
        )

    def _assess_upload(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        source_type: DocumentSourceType,
        document_type_hint: str | None,
    ) -> MultimodalUploadAssessment:
        assess_document = getattr(self.multimodal, "assess_document", None)
        if callable(assess_document):
            try:
                return assess_document(
                    filename=filename,
                    raw_bytes=raw_bytes,
                    source_type=source_type,
                    document_type_hint=document_type_hint,
                    allow_model=False,
                )
            except TypeError:
                return assess_document(
                    filename=filename,
                    raw_bytes=raw_bytes,
                    source_type=source_type,
                    document_type_hint=document_type_hint,
                )

        extract = getattr(self.multimodal, "extract", None)
        if not callable(extract) or document_type_hint is None:
            return MultimodalUploadAssessment()
        result = extract(
            filename=filename,
            raw_bytes=raw_bytes,
            source_type=source_type,
            document_type=document_type_hint,
        )
        if result is None:
            return MultimodalUploadAssessment()
        fields = list(getattr(result, "fields", []) or [])
        relevance = "low" if not fields else "high"
        confidence = 0.0
        for field in fields:
            confidence = max(confidence, float(getattr(field, "confidence", 0.0)))
        return MultimodalUploadAssessment(
            document_type_candidates=[
                UploadDocumentTypeCandidate(
                    document_type=document_type_hint,
                    confidence=confidence or 0.5,
                )
            ],
            relevance=relevance,
            supported_claims=[
                str(getattr(field, "field_path", ""))
                for field in fields
                if getattr(field, "field_path", None)
            ],
            confidence=confidence or (0.2 if not fields else 0.5),
        )

    def _required_document_types(self, session_record) -> set[str]:
        gate_status = session_record.gate_status_json or {}
        return {
            item["document_type"]
            for item in gate_status.get("required_documents", [])
            if item.get("document_type")
        }

    def _document_type_hint_from_context_text(
        self,
        context_text: str | None,
    ) -> str | None:
        if not isinstance(context_text, str):
            return None
        normalized_context = re.sub(r"\s+", " ", context_text.strip().lower())
        if not normalized_context:
            return None

        matched_document_types: list[str] = []
        for document_type, keywords in _CONTEXT_DOCUMENT_TYPE_HINT_KEYWORDS:
            normalized_document_type = normalize_document_type(document_type) or document_type
            if any(keyword in normalized_context for keyword in keywords):
                matched_document_types.append(normalized_document_type)

        deduped_matches: list[str] = []
        for document_type in matched_document_types:
            if document_type not in deduped_matches:
                deduped_matches.append(document_type)

        if len(deduped_matches) != 1:
            return None
        return deduped_matches[0]

    def _case_supported_document_type(
        self,
        *,
        document_type: str | None,
        assessment_candidates: list[Any],
    ) -> str | None:
        normalized_document_type = normalize_document_type(document_type)
        if normalized_document_type is not None:
            return normalized_document_type
        for candidate in assessment_candidates:
            candidate_type = normalize_document_type(
                getattr(candidate, "document_type", None)
                if not isinstance(candidate, dict)
                else candidate.get("document_type")
            )
            if candidate_type is not None:
                return candidate_type
        return None

    def _build_case_understanding_feedback(
        self,
        *,
        case_document_type: str | None,
        supported_claims: list[str],
        relevance: str | None,
        interviewer_focus_document_type: str | None,
    ) -> dict[str, str | None] | None:
        current_focus_document_type = interviewer_focus_document_type or case_document_type
        current_focus_document_type = (
            normalize_document_type(current_focus_document_type)
            or current_focus_document_type
        )
        normalized_case_document_type = (
            normalize_document_type(case_document_type) or case_document_type
        )

        if (
            current_focus_document_type is None
            and normalized_case_document_type is None
            and not supported_claims
            and relevance in {None, "unknown"}
        ):
            return None

        has_case_signal = bool(normalized_case_document_type or supported_claims)
        if relevance == "low" or not has_case_signal:
            return {
                "status": "not_helpful",
                "supported_document_type": None,
                "current_focus_document_type": current_focus_document_type,
                "message": "这份材料已保存，但目前还不能支持一个明确证明点。你可以继续面签对话，系统会在案例理解中保留它。",
            }

        if normalized_case_document_type == current_focus_document_type:
            return {
                "status": "helpful",
                "supported_document_type": normalized_case_document_type,
                "current_focus_document_type": current_focus_document_type,
                "message": (
                    f"这份材料已加入案例证据，候选证明点为 {normalized_case_document_type}。"
                    "你可以继续面签对话，系统会在 Case Board 中更新理解结果。"
                ),
            }

        if interviewer_focus_document_type:
            headline = (
                f"这份材料已加入案例证据，候选证明点为 {normalized_case_document_type}，"
                f"当前对话仍在核验 {current_focus_document_type}。"
            )
        else:
            headline = (
                f"这份材料已加入案例证据，候选证明点为 {normalized_case_document_type}。"
            )
        return {
            "status": "partial_helpful",
            "supported_document_type": normalized_case_document_type,
            "current_focus_document_type": current_focus_document_type,
            "message": f"{headline} 你可以继续面签对话。",
        }

    def _pending_evidence_cards(
        self,
        *,
        document_id: str,
        supported_claims: list[str],
        document_type: str | None,
        confidence: float,
    ) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for index, claim in enumerate(supported_claims):
            cards.append(
                {
                    "evidence_id": f"pending-{document_id}-{index}",
                    "source_type": "uploaded_file",
                    "document_id": document_id,
                    "excerpt": f"候选支持主张：{claim}",
                    "claim_refs": [claim],
                    "confidence": confidence,
                    "metadata": {
                        "status": "pending_understanding",
                        "document_type": document_type,
                    },
                }
            )
        return cards

    def _build_case_board_delta(
        self,
        *,
        document_id: str,
        filename: str,
        understanding_status: str,
        document_type: str | None,
        assessment: MultimodalUploadAssessment,
        evidence_cards: list[dict[str, Any]],
        feedback_message: str | None,
    ) -> dict[str, Any]:
        return {
            "latest_material": {
                "document_id": document_id,
                "filename": filename,
                "understanding_status": understanding_status,
                "document_type": document_type,
                "document_type_candidates": [
                    item.model_dump(mode="json")
                    for item in assessment.document_type_candidates
                ],
                "relevance": assessment.relevance,
                "supported_claims": list(assessment.supported_claims),
                "confidence": assessment.confidence,
                "feedback_message": feedback_message,
                "unknowns": [
                    "案例理解任务已创建，视觉材料的完整证据、冲突和追问建议仍在更新。"
                ],
            },
            "evidence_cards": list(evidence_cards),
            "open_proof_points": [
                {
                    "proof_point_id": claim,
                    "visa_family": "unknown",
                    "question": f"这份材料能否支持 {claim}？",
                    "status": "partial",
                    "why_it_matters": "模型认为这份材料可能支持该证明点，等待案例理解进一步确认。",
                    "claim_refs": [claim],
                    "evidence_refs": [
                        card["evidence_id"]
                        for card in evidence_cards
                        if claim in card.get("claim_refs", [])
                    ],
                }
                for claim in assessment.supported_claims
            ],
            "conflicts": [],
            "next_move": {
                "move_type": "ask",
                "question": "请继续回答面签问题；材料理解完成后我会结合证据调整追问。",
                "reason": "文件已保存并进入案例理解队列，当前可以继续面签对话。",
                "claim_refs": list(assessment.supported_claims),
                "evidence_refs": [
                    card["evidence_id"] for card in evidence_cards
                ],
            },
        }

    def _interviewer_focus_document_type(self, session_record) -> str | None:
        current_focus = session_record.current_focus_json or {}
        if (
            current_focus.get("owner") == "interviewer_runtime_service"
            and current_focus.get("kind") == "required_document"
        ):
            document_type = current_focus.get("document_type")
            normalized = normalize_document_type(document_type)
            if normalized is not None:
                return normalized
            if isinstance(document_type, str) and document_type.strip():
                return document_type.strip()

        interviewer_state = session_record.interviewer_state_json or {}
        requested_documents = interviewer_state.get("requested_documents", [])
        if isinstance(requested_documents, list):
            for document_type in requested_documents:
                normalized = normalize_document_type(document_type)
                if normalized is not None:
                    return normalized
                if isinstance(document_type, str) and document_type.strip():
                    return document_type.strip()
        return None

    def _interviewer_requested_documents(self, session_record) -> list[str]:
        document_type = self._interviewer_focus_document_type(session_record)
        if document_type is None:
            return []
        return [document_type]

    def _legacy_counts_toward_gate(
        self,
        *,
        case_supported_document_type: str | None,
        required_document_types: set[str],
        relevance: str | None,
    ) -> bool | None:
        if not required_document_types:
            return None
        if case_supported_document_type is None:
            return False
        normalized_required = {
            normalize_document_type(document_type) or document_type
            for document_type in required_document_types
        }
        if case_supported_document_type not in normalized_required:
            return False
        return relevance != "low"

    def _legacy_gate_progress(self, session_record) -> dict[str, Any]:
        gate_status = session_record.gate_status_json or {}
        documents = []
        ready_count = 0
        uploaded_count = 0
        missing_count = 0
        for item in gate_status.get("required_documents", []):
            if not isinstance(item, dict):
                continue
            document_progress = {
                "document_type": item.get("document_type"),
                "status": item.get("status", "missing"),
                "is_uploaded": bool(item.get("is_uploaded", False)),
                "is_parsed": bool(item.get("is_parsed", False)),
                "meets_minimum_fields": bool(item.get("meets_minimum_fields", False)),
            }
            documents.append(document_progress)
            if document_progress["status"] == "ready":
                ready_count += 1
            elif document_progress["status"] == "uploaded":
                uploaded_count += 1
            else:
                missing_count += 1
        return {
            "overall_status": gate_status.get("status", "pending_documents"),
            "ready_count": ready_count,
            "uploaded_count": uploaded_count,
            "missing_count": missing_count,
            "documents": documents,
        }

    def _join_feedback_message(
        self,
        headline: str,
        support_message: str | None,
    ) -> str:
        if not support_message:
            return headline
        return f"{headline} {support_message}"
