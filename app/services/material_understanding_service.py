from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from app.domain.case_memory import (
    CaseClaim,
    CaseConflict,
    DocumentTypeCandidate,
    EvidenceCard,
    InterviewNextMove,
    MaterialUnderstandingJob,
    MaterialUnderstandingResult,
    ProofPoint,
)
from app.domain.document_types import normalize_document_type
from app.domain.evidence import DocumentAssessment, DocumentSourceType, EvidenceItem
from app.services.multimodal_extraction_service import MultimodalExtractionService


class MaterialUnderstandingService:
    """Turn uploaded material into case-memory facts without owning the dialogue."""

    def __init__(
        self,
        *,
        multimodal_service: MultimodalExtractionService | None = None,
        invoke_model: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.multimodal_service = multimodal_service or MultimodalExtractionService()
        self.invoke_model = invoke_model or self.multimodal_service.invoke_model

    def understand(
        self,
        *,
        job_id: str,
        document_id: str,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
        source_type: DocumentSourceType,
        document_assessment: DocumentAssessment | None = None,
        legacy_evidence_items: list[EvidenceItem] | None = None,
        case_memory: dict[str, Any] | None = None,
    ) -> MaterialUnderstandingJob:
        legacy_evidence_items = legacy_evidence_items or []
        source_type = self._normalize_source_type(source_type)
        can_call_model = self.multimodal_service.can_call_model()
        if source_type not in {DocumentSourceType.PDF, DocumentSourceType.IMAGE} or (
            legacy_evidence_items and not can_call_model
        ):
            result = self._result_from_legacy_evidence(
                document_id=document_id,
                filename=filename,
                document_assessment=document_assessment,
                legacy_evidence_items=legacy_evidence_items,
            )
            return MaterialUnderstandingJob(
                job_id=job_id,
                document_id=document_id,
                status="completed",
                result=result,
            )

        if not can_call_model:
            return MaterialUnderstandingJob(
                job_id=job_id,
                document_id=document_id,
                status="failed",
                error_code="model_unavailable",
                error_message=(
                    "Material understanding requires a configured multimodal model."
                ),
            )

        payload = self._build_payload(
            session_id=session_id,
            filename=filename,
            raw_bytes=raw_bytes,
            source_type=source_type,
            document_assessment=document_assessment,
            case_memory=case_memory or {},
        )
        try:
            response_payload = self.invoke_model(payload)
            result = self._parse_result(
                document_id=document_id,
                filename=filename,
                response_payload=response_payload,
            )
        except Exception as exc:
            return MaterialUnderstandingJob(
                job_id=job_id,
                document_id=document_id,
                status="failed",
                error_code="model_error",
                error_message=str(exc) or exc.__class__.__name__,
            )

        return MaterialUnderstandingJob(
            job_id=job_id,
            document_id=document_id,
            status="completed",
            result=result,
        )

    def _build_payload(
        self,
        *,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
        source_type: DocumentSourceType,
        document_assessment: DocumentAssessment | None,
        case_memory: dict[str, Any],
    ) -> dict[str, Any]:
        assessment = document_assessment or DocumentAssessment()
        return {
            "model": self.multimodal_service.model_name,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 DS-160 签证案例材料理解节点。"
                        "只输出 JSON，不要写用户可见回复。"
                        "目标是提取可审计证据、事实主张、证明点、冲突、未知项和建议追问。"
                        "未知材料类型也可以输出证据卡；不允许用文件名伪装成视觉理解。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"session_id={session_id}\n"
                                f"filename={filename}\n"
                                f"document_type_hint={assessment.document_type or 'unknown'}\n"
                                "candidate_types="
                                f"{', '.join(assessment.document_type_candidates) or 'unknown'}\n"
                                "supported_claim_hints="
                                f"{', '.join(assessment.supported_claims) or 'none'}\n"
                                f"known_case_memory={case_memory}\n"
                                "JSON schema keys: document_type_candidates, "
                                "evidence_cards, extracted_claims, proof_points, "
                                "conflicts, unknowns, suggested_followups, confidence."
                            ),
                        },
                        *self.multimodal_service.build_visual_content_parts(
                            raw_bytes,
                            source_type,
                        ),
                    ],
                },
            ],
        }

    def _parse_result(
        self,
        *,
        document_id: str,
        filename: str,
        response_payload: dict[str, Any],
    ) -> MaterialUnderstandingResult:
        evidence_cards = [
            self._parse_evidence_card(document_id, index, item)
            for index, item in enumerate(_list_payload(response_payload.get("evidence_cards")))
        ]
        evidence_ids = {item.evidence_id for item in evidence_cards}
        extracted_claims = [
            self._parse_claim(index, item, evidence_ids)
            for index, item in enumerate(_list_payload(response_payload.get("extracted_claims")))
        ]
        claim_ids = {item.claim_id for item in extracted_claims}
        evidence_cards = [
            card.model_copy(
                update={
                    "claim_refs": [
                        claim_ref
                        for claim_ref in card.claim_refs
                        if claim_ref in claim_ids
                    ],
                    "document_id": card.document_id or document_id,
                }
            )
            for card in evidence_cards
        ]

        if not evidence_cards and extracted_claims:
            evidence_cards = [
                self._fallback_evidence_for_claim(
                    document_id=document_id,
                    filename=filename,
                    claim=claim,
                    index=index,
                )
                for index, claim in enumerate(extracted_claims)
            ]
            extracted_claims = [
                claim.model_copy(
                    update={
                        "status": "documented",
                        "supporting_evidence_ids": [
                            evidence_cards[index].evidence_id
                        ],
                    }
                )
                for index, claim in enumerate(extracted_claims)
            ]

        return MaterialUnderstandingResult(
            document_type_candidates=[
                DocumentTypeCandidate.model_validate(item)
                for item in _list_payload(
                    response_payload.get("document_type_candidates")
                )
                if _string_or_none(item.get("document_type")) is not None
            ],
            evidence_cards=evidence_cards,
            extracted_claims=extracted_claims,
            proof_points=[
                self._parse_proof_point(index, item)
                for index, item in enumerate(
                    _list_payload(response_payload.get("proof_points"))
                )
            ],
            conflicts=[
                self._parse_conflict(index, item)
                for index, item in enumerate(
                    _list_payload(response_payload.get("conflicts"))
                )
            ],
            unknowns=[
                item
                for item in _string_list(response_payload.get("unknowns"))
                if item.strip()
            ],
            suggested_followups=[
                self._parse_next_move(index, item)
                for index, item in enumerate(
                    _list_payload(response_payload.get("suggested_followups"))
                )
            ],
            confidence=_float_between_zero_and_one(response_payload.get("confidence")),
        )

    def _result_from_legacy_evidence(
        self,
        *,
        document_id: str,
        filename: str,
        document_assessment: DocumentAssessment | None,
        legacy_evidence_items: list[EvidenceItem],
    ) -> MaterialUnderstandingResult:
        assessment = document_assessment or DocumentAssessment()
        evidence_cards: list[EvidenceCard] = []
        claims: list[CaseClaim] = []
        for index, item in enumerate(legacy_evidence_items):
            evidence_id = item.evidence_id or f"ev-{document_id}-{index}"
            claim_id = self._claim_id(document_id, item.field_path, index)
            evidence_cards.append(
                EvidenceCard(
                    evidence_id=evidence_id,
                    source_type="uploaded_file",
                    document_id=document_id,
                    page_number=_page_number(item.metadata),
                    excerpt=item.excerpt or f"{item.field_path}: {item.value}",
                    claim_refs=[claim_id],
                    confidence=item.confidence,
                    metadata={
                        "filename": filename,
                        "field_path": item.field_path,
                        "legacy_evidence": True,
                    },
                )
            )
            claims.append(
                CaseClaim(
                    claim_id=claim_id,
                    field_path=item.field_path,
                    value=item.value,
                    status="documented",
                    supporting_evidence_ids=[evidence_id],
                    confidence=item.confidence,
                    metadata={
                        "document_id": document_id,
                        "filename": filename,
                    },
                )
            )

        if not evidence_cards:
            return MaterialUnderstandingResult(
                document_type_candidates=self._assessment_candidates(assessment),
                unknowns=[
                    "No structured evidence was extracted from this material."
                ],
                confidence=assessment.confidence or 0.0,
            )

        return MaterialUnderstandingResult(
            document_type_candidates=self._assessment_candidates(assessment),
            evidence_cards=evidence_cards,
            extracted_claims=claims,
            proof_points=[
                ProofPoint(
                    proof_point_id=f"proof-{document_id}-{index}",
                    visa_family="unknown",
                    question=f"Does the case have support for {claim.field_path}?",
                    status="supported",
                    why_it_matters=(
                        "The uploaded material provides a documented case fact."
                    ),
                    claim_refs=[claim.claim_id],
                    evidence_refs=list(claim.supporting_evidence_ids),
                )
                for index, claim in enumerate(claims)
            ],
            confidence=max((item.confidence for item in evidence_cards), default=0.0),
        )

    def _assessment_candidates(
        self,
        assessment: DocumentAssessment,
    ) -> list[DocumentTypeCandidate]:
        candidates: list[DocumentTypeCandidate] = []
        for item in assessment.document_type_candidates:
            normalized = normalize_document_type(item) or item
            if normalized and normalized not in {candidate.document_type for candidate in candidates}:
                candidates.append(
                    DocumentTypeCandidate(
                        document_type=normalized,
                        confidence=assessment.confidence or 0.0,
                    )
                )
        if assessment.document_type:
            normalized = normalize_document_type(assessment.document_type) or assessment.document_type
            if normalized not in {candidate.document_type for candidate in candidates}:
                candidates.insert(
                    0,
                    DocumentTypeCandidate(
                        document_type=normalized,
                        confidence=assessment.confidence or 0.0,
                    ),
                )
        return candidates

    def _parse_evidence_card(
        self,
        document_id: str,
        index: int,
        payload: dict[str, Any],
    ) -> EvidenceCard:
        return EvidenceCard(
            evidence_id=_string_or_none(payload.get("evidence_id"))
            or f"ev-{document_id}-{index}-{uuid4().hex[:6]}",
            source_type="uploaded_file",
            document_id=_string_or_none(payload.get("document_id")) or document_id,
            page_number=_positive_int_or_none(payload.get("page_number")),
            excerpt=_string_or_none(payload.get("excerpt")) or "Visual evidence",
            visual_anchor=_string_or_none(payload.get("visual_anchor")),
            claim_refs=_string_list(payload.get("claim_refs")),
            confidence=_float_between_zero_and_one(payload.get("confidence")),
            metadata=_dict_payload(payload.get("metadata")),
        )

    def _parse_claim(
        self,
        index: int,
        payload: dict[str, Any],
        evidence_ids: set[str],
    ) -> CaseClaim:
        supporting = [
            item for item in _string_list(payload.get("supporting_evidence_ids"))
            if item in evidence_ids
        ]
        conflicting = [
            item for item in _string_list(payload.get("conflicting_evidence_ids"))
            if item in evidence_ids
        ]
        status = _string_or_none(payload.get("status")) or (
            "documented" if supporting else "unknown"
        )
        if status == "documented" and not supporting:
            status = "unknown"
        if status == "contradicted" and not conflicting:
            status = "unknown"
        field_path = _string_or_none(payload.get("field_path")) or f"/unknown/{index}"
        return CaseClaim(
            claim_id=_string_or_none(payload.get("claim_id"))
            or self._claim_id("model", field_path, index),
            field_path=field_path,
            value=_string_or_none(payload.get("value")),
            status=status,
            supporting_evidence_ids=supporting,
            conflicting_evidence_ids=conflicting,
            confidence=_float_between_zero_and_one(payload.get("confidence")),
            metadata=_dict_payload(payload.get("metadata")),
        )

    def _parse_proof_point(self, index: int, payload: dict[str, Any]) -> ProofPoint:
        return ProofPoint(
            proof_point_id=_string_or_none(payload.get("proof_point_id"))
            or f"proof-model-{index}",
            visa_family=_string_or_none(payload.get("visa_family")) or "unknown",
            question=_string_or_none(payload.get("question"))
            or "What does this material help prove?",
            status=_string_or_none(payload.get("status")) or "partial",
            why_it_matters=_string_or_none(payload.get("why_it_matters"))
            or "This proof point helps the interview agent decide what to ask next.",
            claim_refs=_string_list(payload.get("claim_refs")),
            evidence_refs=_string_list(payload.get("evidence_refs")),
            metadata=_dict_payload(payload.get("metadata")),
        )

    def _parse_conflict(self, index: int, payload: dict[str, Any]) -> CaseConflict:
        return CaseConflict(
            conflict_id=_string_or_none(payload.get("conflict_id"))
            or f"conflict-model-{index}",
            claim_ids=_string_list(payload.get("claim_ids")),
            evidence_ids=_string_list(payload.get("evidence_ids")),
            summary=_string_or_none(payload.get("summary"))
            or "The material may conflict with existing case facts.",
            severity=_string_or_none(payload.get("severity")) or "medium",
            suggested_followup=_string_or_none(payload.get("suggested_followup")),
        )

    def _parse_next_move(self, index: int, payload: dict[str, Any]) -> InterviewNextMove:
        return InterviewNextMove(
            move_type=_string_or_none(payload.get("move_type")) or "ask",
            question=_string_or_none(payload.get("question"))
            or "Can you explain how this material supports your visa case?",
            reason=_string_or_none(payload.get("reason"))
            or "The uploaded material introduced a case fact worth clarifying.",
            claim_refs=_string_list(payload.get("claim_refs")),
            evidence_refs=_string_list(payload.get("evidence_refs")),
        )

    def _fallback_evidence_for_claim(
        self,
        *,
        document_id: str,
        filename: str,
        claim: CaseClaim,
        index: int,
    ) -> EvidenceCard:
        return EvidenceCard(
            evidence_id=f"ev-{document_id}-{index}-{uuid4().hex[:6]}",
            source_type="uploaded_file",
            document_id=document_id,
            excerpt=claim.value or claim.field_path,
            claim_refs=[claim.claim_id],
            confidence=claim.confidence,
            metadata={"filename": filename, "fallback_from_claim": True},
        )

    def _claim_id(self, document_id: str, field_path: str, index: int) -> str:
        normalized = field_path.strip("/").replace("/", "-").replace("_", "-")
        normalized = normalized or "unknown"
        return f"claim-{document_id}-{normalized}-{index}"

    def _normalize_source_type(self, source_type: Any) -> DocumentSourceType:
        if isinstance(source_type, DocumentSourceType):
            return source_type
        try:
            return DocumentSourceType(str(source_type))
        except ValueError:
            return DocumentSourceType.UNKNOWN


def _list_payload(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = _string_or_none(item)
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _string_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _float_between_zero_and_one(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(number, 0.0), 1.0)


def _positive_int_or_none(value: Any) -> int | None:
    if not isinstance(value, int):
        return None
    return value if value >= 1 else None


def _page_number(metadata: dict[str, Any]) -> int | None:
    value = metadata.get("page_number") if isinstance(metadata, dict) else None
    return _positive_int_or_none(value)
