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


class MultimodalExtractionService:
    def __init__(
        self,
        *,
        invoke_model: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.invoke_model = invoke_model or self._invoke_http
        self.model_name = (
            model_name
            or os.getenv("MULTIMODAL_EXTRACTION_MODEL")
            or os.getenv("RUNTIME_DEFAULT_MODEL")
            or "gpt-5.4"
        )
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.enabled = (
            invoke_model is not None
            or os.getenv("MULTIMODAL_EXTRACTION_ENABLED", "").lower() in {"1", "true", "yes"}
        )

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
