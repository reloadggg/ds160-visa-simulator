from __future__ import annotations

import base64
import json
import os
from io import BytesIO
from typing import Any, Callable

import fitz
import httpx
from pydantic import BaseModel, Field

from app.domain.evidence import DocumentSourceType

SUPPORTED_MULTIMODAL_DOCUMENT_TYPES = {
    "passport_bio": [
        "/identity/full_name",
        "/identity/passport_number",
        "/identity/nationality",
    ],
    "ds160": [
        "/identity/full_name",
        "/identity/passport_number",
        "/visa_intent/travel_purpose",
    ],
    "i20": [
        "/education/sevis_id",
        "/education/school_name",
        "/education/program_name",
    ],
    "admission_letter": [
        "/education/school_name",
        "/education/program_name",
    ],
    "ds2019": [
        "/education/sevis_id",
        "/education/sponsor_name",
        "/education/program_name",
    ],
}
SUPPORTED_UPLOAD_ASSESSMENT_DOCUMENT_TYPES = {
    **SUPPORTED_MULTIMODAL_DOCUMENT_TYPES,
    "funding_proof": ["/funding/primary_source"],
    "relationship_proof_between_applicant_and_sponsors": [
        "/identity/full_name",
        "/funding/sponsor_relationship",
        "/family/parent_names",
    ],
}


class MultimodalExtractedField(BaseModel):
    field_path: str
    value: str
    excerpt: str
    confidence: float = Field(ge=0.0, le=1.0)
    page_number: int | None = None


class MultimodalExtractedSegment(BaseModel):
    ordinal: int
    page_number: int | None = None
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MultimodalExtractionResult(BaseModel):
    source_type: DocumentSourceType
    parser_name: str
    full_text: str
    segments: list[MultimodalExtractedSegment]
    fields: list[MultimodalExtractedField] = Field(default_factory=list)


class UploadDocumentTypeCandidate(BaseModel):
    document_type: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MultimodalUploadAssessment(BaseModel):
    document_type_candidates: list[UploadDocumentTypeCandidate] = Field(
        default_factory=list
    )
    relevance: str = "unknown"
    supported_claims: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MultimodalExtractionService:
    def __init__(
        self,
        *,
        invoke_model: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        configured_base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
        configured_api_key = api_key or os.getenv("OPENAI_API_KEY")
        explicit_enabled = os.getenv("MULTIMODAL_EXTRACTION_ENABLED")
        self.invoke_model = invoke_model or self._invoke_http
        self.model_name = (
            model_name
            or os.getenv("MULTIMODAL_EXTRACTION_MODEL")
            or os.getenv("RUNTIME_DEFAULT_MODEL")
            or "gpt-5.4"
        )
        self.base_url = configured_base_url
        self.api_key = configured_api_key
        if invoke_model is not None:
            self.enabled = True
        elif explicit_enabled is not None and explicit_enabled.strip():
            self.enabled = explicit_enabled.strip().lower() in {"1", "true", "yes"}
        else:
            self.enabled = bool(configured_base_url and configured_api_key)

    def extract(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        source_type: DocumentSourceType,
        document_type: str | None,
    ) -> MultimodalExtractionResult | None:
        if document_type not in SUPPORTED_MULTIMODAL_DOCUMENT_TYPES:
            return None
        if source_type not in {DocumentSourceType.PDF, DocumentSourceType.IMAGE}:
            return None
        if not self.enabled:
            return None
        if self.invoke_model is self._invoke_http and (not self.base_url or not self.api_key):
            return None

        payload = self._build_payload(
            filename=filename,
            raw_bytes=raw_bytes,
            source_type=source_type,
            document_type=document_type,
        )
        try:
            response_payload = self.invoke_model(payload)
        except Exception:
            return None
        return self._parse_extraction_response(
            source_type=source_type,
            response_payload=response_payload,
        )

    def assess_document(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        source_type: DocumentSourceType,
        document_type_hint: str | None = None,
    ) -> MultimodalUploadAssessment:
        if source_type not in {DocumentSourceType.PDF, DocumentSourceType.IMAGE}:
            return MultimodalUploadAssessment()

        can_call_model = self.enabled and not (
            self.invoke_model is self._invoke_http and (not self.base_url or not self.api_key)
        )
        if can_call_model:
            payload = self._build_assessment_payload(
                filename=filename,
                raw_bytes=raw_bytes,
                source_type=source_type,
                document_type_hint=document_type_hint,
            )
            try:
                response_payload = self.invoke_model(payload)
                candidates = [
                    UploadDocumentTypeCandidate.model_validate(item)
                    for item in response_payload.get("document_type_candidates", [])
                ]
                return MultimodalUploadAssessment(
                    document_type_candidates=candidates,
                    relevance=str(response_payload.get("relevance", "unknown")),
                    supported_claims=[
                        str(item)
                        for item in response_payload.get("supported_claims", [])
                        if isinstance(item, str)
                    ],
                    confidence=float(response_payload.get("confidence", 0.0)),
                )
            except Exception:
                pass

        if document_type_hint is not None:
            extract_result = self.extract(
                filename=filename,
                raw_bytes=raw_bytes,
                source_type=source_type,
                document_type=document_type_hint,
            )
            if extract_result is not None:
                supported_claims = [
                    str(field_path)
                    for field in extract_result.fields
                    for field_path in [getattr(field, "field_path", None)]
                    if isinstance(field_path, str) and field_path
                ]
                confidence = max(
                    (
                        float(getattr(field, "confidence", 0.0))
                        for field in extract_result.fields
                    ),
                    default=0.0,
                )
                return MultimodalUploadAssessment(
                    document_type_candidates=[
                        UploadDocumentTypeCandidate(
                            document_type=document_type_hint,
                            confidence=confidence or 0.5,
                        )
                    ],
                    relevance="high" if extract_result.fields else "low",
                    supported_claims=supported_claims,
                    confidence=confidence or (0.2 if not extract_result.fields else 0.5),
                )

        return self._heuristic_assessment(
            filename=filename,
            document_type_hint=document_type_hint,
        )

    def _parse_extraction_response(
        self,
        *,
        source_type: DocumentSourceType,
        response_payload: dict[str, Any],
    ) -> MultimodalExtractionResult | None:
        try:
            segments = []
            for index, item in enumerate(response_payload.get("segments", [])):
                payload_item = dict(item)
                payload_item.setdefault("ordinal", index)
                segments.append(
                    MultimodalExtractedSegment.model_validate(payload_item)
                )
            fields = [
                MultimodalExtractedField.model_validate(item)
                for item in response_payload.get("fields", [])
            ]
            return MultimodalExtractionResult(
                source_type=source_type,
                parser_name="multimodal_llm",
                full_text=response_payload.get("full_text", ""),
                segments=segments,
                fields=fields,
            )
        except Exception:
            return None

    def _build_payload(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        source_type: DocumentSourceType,
        document_type: str,
    ) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是签证材料结构化抽取器。"
                        "输出 JSON，字段包括 full_text、segments、fields。"
                        "fields 中每项包含 field_path、value、excerpt、confidence、page_number。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"document_type={document_type}\n"
                                f"filename={filename}\n"
                                f"只抽取这些字段：{', '.join(SUPPORTED_MULTIMODAL_DOCUMENT_TYPES[document_type])}"
                            ),
                        },
                        *self._build_image_parts(raw_bytes, source_type),
                    ],
                },
            ],
        }

    def _build_assessment_payload(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        source_type: DocumentSourceType,
        document_type_hint: str | None,
    ) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是签证材料上传评估器。"
                        "输出 JSON，字段包括 document_type_candidates、relevance、supported_claims、confidence。"
                        "document_type_candidates 中每项包含 document_type 和 confidence。"
                        "relevance 只能是 high、medium、low、unknown。"
                        "中国户口本、出生证明、亲属关系公证等能证明申请人与父母/资助人关系的材料，"
                        "应归类为 relationship_proof_between_applicant_and_sponsors，而不是 funding_proof。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"filename={filename}\n"
                                f"document_type_hint={document_type_hint or 'none'}\n"
                                f"候选类型：{', '.join(SUPPORTED_UPLOAD_ASSESSMENT_DOCUMENT_TYPES)}"
                            ),
                        },
                        *self._build_image_parts(raw_bytes, source_type),
                    ],
                },
            ],
        }

    def _build_image_parts(
        self,
        raw_bytes: bytes,
        source_type: DocumentSourceType,
    ) -> list[dict[str, Any]]:
        if source_type == DocumentSourceType.IMAGE:
            return [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self._to_data_uri(raw_bytes, "image/png"),
                    },
                }
            ]

        pdf = fitz.open(stream=raw_bytes, filetype="pdf")
        try:
            parts: list[dict[str, Any]] = []
            for page in pdf:
                pixmap = page.get_pixmap()
                png_bytes = pixmap.tobytes("png")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": self._to_data_uri(png_bytes, "image/png"),
                        },
                    }
                )
            return parts
        finally:
            pdf.close()

    def _to_data_uri(self, raw_bytes: bytes, media_type: str) -> str:
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    def _invoke_http(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90.0,
        )
        response.raise_for_status()
        raw_payload = response.json()
        raw_content = raw_payload["choices"][0]["message"]["content"]
        if isinstance(raw_content, list):
            raw_content = "".join(
                item.get("text", "")
                for item in raw_content
                if isinstance(item, dict)
            )
        return json.loads(raw_content)

    def _heuristic_assessment(
        self,
        *,
        filename: str,
        document_type_hint: str | None,
    ) -> MultimodalUploadAssessment:
        normalized_filename = filename.lower()
        candidates: list[UploadDocumentTypeCandidate] = []
        if document_type_hint in SUPPORTED_UPLOAD_ASSESSMENT_DOCUMENT_TYPES:
            candidates.append(
                UploadDocumentTypeCandidate(
                    document_type=document_type_hint,
                    confidence=0.9,
                )
            )
        for document_type in SUPPORTED_UPLOAD_ASSESSMENT_DOCUMENT_TYPES:
            if document_type in normalized_filename and not any(
                item.document_type == document_type for item in candidates
            ):
                candidates.append(
                    UploadDocumentTypeCandidate(
                        document_type=document_type,
                        confidence=0.65,
                    )
                )
        if not candidates:
            return MultimodalUploadAssessment()
        top_candidate = candidates[0]
        return MultimodalUploadAssessment(
            document_type_candidates=candidates[:3],
            relevance="high" if document_type_hint else "medium",
            supported_claims=list(
                SUPPORTED_UPLOAD_ASSESSMENT_DOCUMENT_TYPES.get(
                    top_candidate.document_type,
                    [],
                )
            ),
            confidence=top_candidate.confidence,
        )
