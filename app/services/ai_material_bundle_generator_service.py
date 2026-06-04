from __future__ import annotations

import json
import os
from typing import Any, Literal, Protocol, TypeVar

from openai import APIStatusError, OpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app.agents.model_factory import AgentModelFactory
from app.agents.user_model_config import current_user_model_config
from app.core.settings import settings
from app.db.session import SessionLocal
from app.db.models import SessionRecord
from app.domain.document_types import normalize_document_type
from app.integrations.openai_compat_headers import openai_compat_default_headers
from app.services.admin_config_service import AdminConfigService
from app.services.runtime_errors import (
    ModelRuntimeError,
    ModelUnavailableError,
    ProviderAPIError,
)


GeneratedBundleScenario = Literal[
    "normal_f1_bundle",
    "normal_j1_bundle",
    "normal_b1_b2_bundle",
    "normal_h1b_bundle",
    "school_mismatch_bundle",
    "identity_mismatch_bundle",
    "funding_shortfall_bundle",
    "sponsor_chain_gap_bundle",
    "claim_vs_document_bundle",
]

ALLOWED_GENERATED_DOCUMENT_TYPES = {
    "ds160",
    "passport_bio",
    "i20",
    "admission_letter",
    "funding_proof",
    "relationship_proof_between_applicant_and_sponsors",
    "ds2019",
    "program_invitation",
    "sevis_fee_receipt",
    "training_plan_ds7002",
    "insurance_proof",
    "itinerary_or_trip_purpose",
    "invitation_letter",
    "employment_proof",
    "travel_history",
    "family_ties_proof",
    "i797",
    "i129_petition",
    "employer_letter",
    "offer_letter",
    "lca",
    "degree_certificate",
    "resume",
    "client_letter",
}
ORACLE_TEXT_MARKERS = (
    "Missing:",
    "Expected:",
    "Defect:",
    "This conflicts with",
)
ORACLE_LINE_PREFIXES = ("Issue:",)
DOCUMENT_TEXT_KEYS = (
    "raw_text",
    "plain_text",
    "body",
    "document_body",
    "ocr_text",
    "full_text",
    "text",
    "text_content",
    "content_text",
    "raw_content",
    "material_text",
    "text_excerpt",
    "content",
    "sections",
    "lines",
)


def find_oracle_text_marker(text: str) -> str | None:
    normalized = text.casefold()
    for marker in ORACLE_TEXT_MARKERS:
        if marker.casefold() in normalized:
            return marker
    for line in text.splitlines():
        normalized_line = line.strip().casefold()
        for prefix in ORACLE_LINE_PREFIXES:
            if normalized_line.startswith(prefix.casefold()):
                return prefix
    return None


def normalize_material_fields(value: Any) -> dict[str, str]:
    if isinstance(value, list):
        pairs: dict[str, str] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            raw_key = (
                item.get("path")
                or item.get("field_path")
                or item.get("pointer")
                or item.get("json_pointer")
                or item.get("key")
            )
            raw_value = item.get("value")
            key = str(raw_key or "").strip()
            if not key.startswith("/"):
                continue
            text_value = str(raw_value).strip()
            if text_value:
                pairs[key] = text_value
        return pairs
    if not isinstance(value, dict):
        raise ValueError("fields must be a JSON object")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key.startswith("/"):
            continue
        text_value = str(raw_value).strip()
        if key and text_value:
            normalized[key] = text_value
    return normalized


class GeneratedMaterialDocument(BaseModel):
    document_type: str
    filename: str
    raw_text: str
    fields: dict[str, str] = Field(default_factory=dict)
    counts_toward_gate: bool = True

    @field_validator("document_type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        normalized = normalize_document_type(value)
        if normalized not in ALLOWED_GENERATED_DOCUMENT_TYPES:
            raise ValueError(f"unsupported generated document type: {value}")
        return normalized

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("filename must not be empty")
        return normalized

    @field_validator("raw_text")
    @classmethod
    def validate_raw_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("raw_text must not be empty")
        marker = find_oracle_text_marker(normalized)
        if marker is not None:
            raise ValueError(f"generated material contains oracle marker: {marker}")
        return normalized

    @field_validator("fields", mode="before")
    @classmethod
    def validate_fields(cls, value: Any) -> dict[str, str]:
        normalized = normalize_material_fields(value)
        if not normalized:
            raise ValueError("fields must include at least one JSON pointer field")
        return normalized


class GeneratedMaterialSyntheticTurn(BaseModel):
    role: Literal["user"] = "user"
    content: str
    field_claims: dict[str, str] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("synthetic turn content must not be empty")
        return normalized


class GeneratedMaterialBundleOutput(BaseModel):
    documents: list[GeneratedMaterialDocument] = Field(min_length=5)
    synthetic_turns: list[GeneratedMaterialSyntheticTurn] = Field(default_factory=list)
    generation_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_required_document_mix(self) -> "GeneratedMaterialBundleOutput":
        document_types = {document.document_type for document in self.documents}
        required = {"ds160", "passport_bio"}
        missing = sorted(required - document_types)
        if missing:
            raise ValueError(f"generated bundle missing required documents: {missing}")
        return self


TOutput = TypeVar("TOutput", bound=BaseModel)


class AIMaterialBundleRunner(Protocol):
    def run(
        self,
        *,
        prompt: str,
        instructions: str,
        output_type: type[TOutput],
        runtime: dict[str, Any],
    ) -> TOutput:
        """Run the material generator and return typed structured output."""


class OpenAIChatCompletionsMaterialBundleRunner:
    """OpenAI-compatible chat adapter for AI-native material bundle generation."""

    def run(
        self,
        *,
        prompt: str,
        instructions: str,
        output_type: type[TOutput],
        runtime: dict[str, Any],
    ) -> TOutput:
        client = self._build_client(runtime)
        completion = self._create_completion(
            client=client,
            runtime=runtime,
            instructions=instructions,
            prompt=prompt,
        )
        content = completion.choices[0].message.content
        if not content:
            raise ModelRuntimeError(
                detail="材料生成模型返回了空内容。",
                status_code=502,
                provider=runtime.get("provider"),
                model=runtime.get("model"),
                upstream_code="model_output_invalid",
                error_category="model_output_invalid",
            )
        payload = self._parse_json_content(content)
        normalized = self._normalize_material_payload(payload)
        return output_type.model_validate(normalized)

    def _create_completion(
        self,
        *,
        client: OpenAI,
        runtime: dict[str, Any],
        instructions: str,
        prompt: str,
    ):
        model_name = self._string_or_none(runtime.get("model"))
        if model_name is None:
            raise ModelUnavailableError(
                detail=runtime.get("model_unavailable_detail")
                or "当前后端未配置可用的材料生成模型。",
                provider=runtime.get("provider"),
                model=model_name,
                missing_env_vars=runtime.get("model_unavailable_missing_env_vars"),
            )
        return client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )

    def _build_client(self, runtime: dict[str, Any]) -> OpenAI:
        user_config = current_user_model_config()
        with SessionLocal() as db:
            admin_config = AdminConfigService(db).effective_model_config()
        if admin_config.source == "admin":
            api_key = admin_config.api_key
            base_url = admin_config.base_url
            model_name = admin_config.model
        elif admin_config.api_key and admin_config.base_url:
            api_key = admin_config.api_key
            base_url = admin_config.base_url
            model_name = self._string_or_none(runtime.get("model"))
        elif user_config is not None:
            api_key = user_config.api_key
            base_url = user_config.base_url
            model_name = user_config.model
        else:
            api_key = os.getenv("OPENAI_API_KEY")
            base_url = os.getenv("OPENAI_BASE_URL")
            model_name = self._string_or_none(runtime.get("model"))
        if not api_key or not base_url or not model_name:
            raise ModelUnavailableError(
                detail=runtime.get("model_unavailable_detail")
                or "当前后端未配置可用的材料生成模型。",
                provider=runtime.get("provider"),
                model=model_name,
                missing_env_vars=runtime.get("model_unavailable_missing_env_vars"),
            )
        return OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.ai_material_bundle_timeout_seconds,
            default_headers=openai_compat_default_headers(),
        )

    def _parse_json_content(self, content: str) -> dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError("material generator output must be a JSON object")
        return payload

    def _normalize_material_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_documents = payload.get("documents")
        if raw_documents is None:
            raw_documents = payload.get("materials")
        if not isinstance(raw_documents, list):
            return payload

        documents = [
            self._normalize_document_payload(document)
            for document in raw_documents
            if isinstance(document, dict)
        ]
        return {
            "documents": documents,
            "synthetic_turns": self._normalize_synthetic_turns(
                payload.get("synthetic_turns")
                or payload.get("synthetic_user_turns")
                or []
            ),
            "generation_notes": self._normalize_generation_notes(
                payload.get("generation_notes") or []
            ),
        }

    def _normalize_synthetic_turns(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        turns: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                content = item.strip()
                if content:
                    turns.append(
                        {"role": "user", "content": content, "field_claims": {}}
                    )
                continue
            if isinstance(item, dict):
                content = str(item.get("content") or item.get("text") or "").strip()
                if content:
                    turns.append(
                        {
                            "role": item.get("role") or "user",
                            "content": content,
                            "field_claims": item.get("field_claims") or {},
                        }
                    )
        return turns

    def _normalize_generation_notes(self, value: Any) -> list[str]:
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _normalize_document_payload(self, document: dict[str, Any]) -> dict[str, Any]:
        document_type = document.get("document_type") or document.get("type")
        filename = document.get("filename") or document.get("file_name")
        if not filename and document_type:
            filename = f"ai_{str(document_type).strip().lower()}.txt"
        return {
            "document_type": document_type,
            "filename": filename,
            "raw_text": self._document_text(document),
            "fields": document.get("fields") or document.get("extracted_fields") or {},
            "counts_toward_gate": document.get("counts_toward_gate", True),
        }

    def _document_text(self, document: dict[str, Any]) -> str | None:
        for key in DOCUMENT_TEXT_KEYS:
            text = self._coerce_text(document.get(key))
            if text:
                return text
        return None

    def _coerce_text(self, value: Any) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        if isinstance(value, list):
            parts = [
                part
                for item in value
                if (part := self._coerce_text(item))
            ]
            return "\n".join(parts) if parts else None
        if isinstance(value, dict):
            for key in DOCUMENT_TEXT_KEYS:
                text = self._coerce_text(value.get(key))
                if text:
                    return text
            parts = [
                part
                for key, item in value.items()
                if key not in {"fields", "extracted_fields"}
                if (part := self._coerce_text(item))
            ]
            return "\n".join(parts) if parts else None
        return None

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None


class AIMaterialBundleGeneratorService:
    def __init__(
        self,
        db: Session,
        *,
        model_factory: AgentModelFactory | None = None,
        runner: AIMaterialBundleRunner | None = None,
    ) -> None:
        self.db = db
        self.model_factory = model_factory or AgentModelFactory()
        self.runner = runner or OpenAIChatCompletionsMaterialBundleRunner()

    def generate(
        self,
        *,
        record: SessionRecord,
        scenario: GeneratedBundleScenario,
        seed_text: str,
        include_synthetic_user_turns: bool,
    ) -> tuple[GeneratedMaterialBundleOutput, dict[str, Any]]:
        normalized_seed = seed_text.strip()
        if not normalized_seed:
            raise ModelRuntimeError(
                detail="缺少材料生成依据，无法用 AI 生成自洽材料包。",
                status_code=422,
            )
        runtime = self._build_runtime(record.declared_family)
        if runtime.get("model_unavailable_reason"):
            raise ModelUnavailableError(
                detail=runtime.get("model_unavailable_detail")
                or "当前后端未配置可用的材料生成模型。",
                provider=runtime.get("provider"),
                model=runtime.get("model"),
                missing_env_vars=runtime.get("model_unavailable_missing_env_vars"),
            )

        prompt = self._build_prompt(
            record=record,
            scenario=scenario,
            seed_text=normalized_seed,
            include_synthetic_user_turns=include_synthetic_user_turns,
        )
        try:
            output = self.runner.run(
                prompt=prompt,
                instructions=self._build_instructions(
                    self._target_family_for_scenario(
                        scenario,
                        record.declared_family,
                    )
                ),
                output_type=GeneratedMaterialBundleOutput,
                runtime=runtime,
            )
            output = GeneratedMaterialBundleOutput.model_validate(output)
            required_documents = self._required_documents_for_scenario(
                scenario,
                record.declared_family,
            )
            self._validate_required_documents(
                output,
                required_documents,
                runtime=runtime,
            )
        except Exception as exc:
            raise self._normalize_model_error(exc, runtime=runtime) from exc

        trace = {
            "generator": "openai_chat_completions",
            "provider": runtime.get("provider"),
            "model": runtime.get("model"),
            "reasoning_effort": runtime.get("reasoning_effort"),
            "prompt_pack_id": "ds160.ai_material_bundle",
            "prompt_version": "v1",
            "seed_text_present": True,
            "target_family": self._target_family_for_scenario(
                scenario,
                record.declared_family,
            ),
            "required_documents": required_documents,
            "timeout_seconds": settings.ai_material_bundle_timeout_seconds,
        }
        return output, trace

    def _build_runtime(self, declared_family: str | None) -> dict[str, Any]:
        if hasattr(self.model_factory, "build_runtime_config"):
            return self.model_factory.build_runtime_config(
                "material_generator_agent",
                "interview_turn",
                declared_family=declared_family,
            )
        _model, runtime = self.model_factory.build(
            "adjudication_agent",
            "interview_turn",
            declared_family=declared_family,
        )
        return runtime

    def _build_prompt(
        self,
        *,
        record: SessionRecord,
        scenario: GeneratedBundleScenario,
        seed_text: str,
        include_synthetic_user_turns: bool,
    ) -> str:
        target_family = self._target_family_for_scenario(
            scenario,
            record.declared_family,
        )
        required_documents = self._required_documents_for_scenario(
            scenario,
            record.declared_family,
        )
        payload = {
            "schema_version": "ai_material_bundle.v1",
            "session": {
                "session_id": record.session_id,
                "declared_family": record.declared_family,
            },
            "target_family": target_family,
            "scenario": scenario,
            "seed_text": seed_text,
            "existing_profile_json": dict(record.profile_json or {}),
            "include_synthetic_user_turns": include_synthetic_user_turns,
            "required_documents": required_documents,
            "family_material_guidance": self._family_material_guidance(target_family),
            "scenario_rules": self._scenario_rules(scenario),
            "output_contract": {
                "top_level_keys": [
                    "documents",
                    "synthetic_turns",
                    "generation_notes",
                ],
                "document_keys": [
                    "document_type",
                    "filename",
                    "raw_text",
                    "fields",
                    "counts_toward_gate",
                ],
                "fields_shape": {
                    "/identity/full_name": "Morgan Lee",
                    "/education/school_name": "New York University",
                },
            },
            "task": (
                "Generate realistic synthetic plain-text visa materials that match "
                "the seed_text. These are user-visible practice "
                "documents, not oracle answers. Return only structured output."
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _build_instructions(self, target_family: str | None) -> str:
        family = target_family or "unknown"
        return (
            "你是 DS-160 模拟器里的 AI 材料生成器，必须返回可解析 JSON。\n"
            f"当前材料包目标签证类别：{family}。\n"
            "核心规则：\n"
            "1. seed_text 是材料事实的唯一来源；学校、项目、资金来源、资助人、金额必须与 seed_text 一致。\n"
            "2. 生成的是看起来像真实 OCR/文本摘录的练习材料正文，不是总结、分析或风险报告。\n"
            "3. 正常场景必须按 required_documents 生成对应签证类别材料；非 F-1 不要生成 I-20 或录取信，除非 required_documents 明确要求。\n"
            "4. 字段必须用 JSON pointer，例如 /identity/full_name、/visa_intent/purpose、/funding/primary_source。\n"
            "5. 不得在材料正文里写 Issue、Missing、Expected、Defect、This conflicts with 等答案提示。\n"
            "6. 如果场景要求制造冲突，只能通过材料字段值不同或用户声明与材料不同表达，不要解释冲突。\n"
            "7. 不要把内部 scenario、oracle、expected_findings、prompt、trace 写进材料正文。\n"
            "8. 输出必须符合 GeneratedMaterialBundleOutput。\n"
            "9. 顶层只能使用 documents、synthetic_turns、generation_notes。\n"
            "10. 每个 document 必须使用 document_type、filename、raw_text、fields、counts_toward_gate。\n"
            "11. raw_text 必须是完整材料正文字符串；fields 必须是 JSON object，不要用数组。"
        )

    def _target_family_for_scenario(
        self,
        scenario: GeneratedBundleScenario,
        declared_family: str | None,
    ) -> str:
        if scenario == "normal_j1_bundle":
            return "j1"
        if scenario == "normal_b1_b2_bundle":
            return "b1_b2"
        if scenario == "normal_h1b_bundle":
            return "h1b"
        if scenario in {
            "normal_f1_bundle",
            "school_mismatch_bundle",
            "identity_mismatch_bundle",
            "funding_shortfall_bundle",
            "sponsor_chain_gap_bundle",
            "claim_vs_document_bundle",
        }:
            return "f1"
        normalized = (declared_family or "").strip().lower()
        return normalized or "f1"

    def _required_documents_for_scenario(
        self,
        scenario: GeneratedBundleScenario,
        declared_family: str | None,
    ) -> list[str]:
        target_family = self._target_family_for_scenario(scenario, declared_family)
        if target_family == "j1":
            return [
                "ds160",
                "passport_bio",
                "ds2019",
                "funding_proof",
                "program_invitation",
                "sevis_fee_receipt",
            ]
        if target_family == "b1_b2":
            return [
                "ds160",
                "passport_bio",
                "itinerary_or_trip_purpose",
                "funding_proof",
                "employment_proof",
                "invitation_letter",
            ]
        if target_family == "h1b":
            return [
                "ds160",
                "passport_bio",
                "i797",
                "employer_letter",
                "lca",
                "degree_certificate",
            ]
        return [
            "ds160",
            "passport_bio",
            "i20",
            "admission_letter",
            "funding_proof",
            "relationship_proof_between_applicant_and_sponsors",
        ]

    def _family_material_guidance(self, target_family: str) -> dict[str, Any]:
        if target_family == "j1":
            return {
                "purpose": "exchange visitor program",
                "primary_evidence": [
                    "DS-2019 program information",
                    "program sponsor or invitation letter",
                    "funding support for the exchange period",
                    "SEVIS I-901 receipt when available",
                ],
                "avoid_documents": ["i20", "admission_letter"],
            }
        if target_family == "b1_b2":
            return {
                "purpose": "temporary business or visitor travel",
                "primary_evidence": [
                    "trip purpose or itinerary",
                    "invitation or meeting letter when applicable",
                    "employment and leave proof",
                    "financial support and home ties",
                ],
                "avoid_documents": ["i20", "admission_letter", "ds2019"],
            }
        if target_family == "h1b":
            return {
                "purpose": "specialty occupation temporary work",
                "primary_evidence": [
                    "I-797 approval notice",
                    "employer support letter",
                    "LCA or role details",
                    "degree and resume evidence",
                ],
                "avoid_documents": ["i20", "admission_letter", "ds2019"],
            }
        return {
            "purpose": "academic study",
            "primary_evidence": [
                "I-20",
                "admission letter",
                "funding proof",
                "relationship proof when sponsored by parents",
            ],
            "avoid_documents": ["ds2019", "i797", "employer_letter"],
        }

    def _validate_required_documents(
        self,
        output: GeneratedMaterialBundleOutput,
        required_documents: list[str],
        *,
        runtime: dict[str, Any],
    ) -> None:
        document_types = {document.document_type for document in output.documents}
        missing = sorted(set(required_documents) - document_types)
        if missing:
            raise ModelRuntimeError(
                detail=f"AI 材料生成结果缺少当前签证类别必需材料：{missing}",
                status_code=502,
                provider=runtime.get("provider"),
                model=runtime.get("model"),
                upstream_code="model_output_invalid",
                error_category="model_output_invalid",
            )

    def _scenario_rules(self, scenario: str) -> dict[str, str]:
        if scenario == "school_mismatch_bundle":
            return {
                "mode": "seeded_conflict",
                "instruction": (
                    "Use the seed school in one study document and a generic alternate "
                    "school in another study document; do not explain the mismatch."
                ),
            }
        if scenario == "identity_mismatch_bundle":
            return {
                "mode": "seeded_conflict",
                "instruction": (
                    "Use two different synthetic passport numbers across DS-160 and "
                    "passport bio page; keep all other seed facts aligned."
                ),
            }
        if scenario == "funding_shortfall_bundle":
            return {
                "mode": "seeded_gap",
                "instruction": (
                    "Make the funding proof amount lower than the I-20 first-year cost; "
                    "do not write a conclusion about the shortfall."
                ),
            }
        if scenario == "sponsor_chain_gap_bundle":
            return {
                "mode": "seeded_gap",
                "instruction": (
                    "Use the seed sponsor/funding source, but only provide a partial "
                    "funding trail such as a balance or remittance summary."
                ),
            }
        if scenario == "claim_vs_document_bundle":
            return {
                "mode": "seeded_claim_conflict",
                "instruction": (
                    "If synthetic user turns are included, make the synthetic user claim "
                    "disagree with the funding proof on primary source."
                ),
            }
        return {
            "mode": "seeded_normal",
            "instruction": "Keep all generated materials internally consistent with the seed.",
        }

    def _normalize_model_error(
        self,
        exc: Exception,
        *,
        runtime: dict[str, Any],
    ) -> ModelRuntimeError:
        if isinstance(exc, ModelRuntimeError):
            return exc
        if isinstance(exc, APIStatusError):
            return ProviderAPIError(
                detail=f"材料生成模型服务返回错误：HTTP {exc.status_code}",
                provider=runtime.get("provider"),
                model=runtime.get("model"),
                status_code=exc.status_code,
            )
        if isinstance(exc, (json.JSONDecodeError, ValidationError, ValueError)):
            return ModelRuntimeError(
                detail=f"材料生成模型输出结构不合格：{exc.__class__.__name__}: {exc}",
                status_code=502,
                provider=runtime.get("provider"),
                model=runtime.get("model"),
                upstream_code="model_output_invalid",
                error_category="model_output_invalid",
            )
        return ModelRuntimeError(
            detail=f"材料生成失败：{exc.__class__.__name__}: {exc}",
            status_code=503,
            provider=runtime.get("provider"),
            model=runtime.get("model"),
        )
