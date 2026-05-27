from __future__ import annotations

import json
import os
from typing import Any, Literal, Protocol, TypeVar

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, RunConfig, Runner
from agents.exceptions import AgentsException
from agents.model_settings import Reasoning
from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.agents.model_factory import AgentModelFactory
from app.agents.user_model_config import current_user_model_config
from app.db.models import SessionRecord
from app.domain.document_types import normalize_document_type
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.runtime_errors import (
    ModelRuntimeError,
    ModelUnavailableError,
    ProviderAPIError,
)


GeneratedBundleScenario = Literal[
    "normal_f1_bundle",
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
}
ORACLE_TEXT_MARKERS = (
    "Issue:",
    "Missing:",
    "Expected:",
    "Defect:",
    "This conflicts with",
)


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
        lowered = normalized.casefold()
        for marker in ORACLE_TEXT_MARKERS:
            if marker.casefold() in lowered:
                raise ValueError(f"generated material contains oracle marker: {marker}")
        return normalized

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key.startswith("/"):
                continue
            text_value = str(raw_value).strip()
            if key and text_value:
                normalized[key] = text_value
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
        required = {
            "ds160",
            "passport_bio",
            "i20",
            "admission_letter",
            "funding_proof",
        }
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


class OpenAIAgentsMaterialBundleRunner:
    """OpenAI Agents SDK adapter for AI-native material bundle generation."""

    def run(
        self,
        *,
        prompt: str,
        instructions: str,
        output_type: type[TOutput],
        runtime: dict[str, Any],
    ) -> TOutput:
        agent = Agent(
            name="DS-160 Material Bundle Generator",
            instructions=instructions,
            model=self._build_model(runtime),
            model_settings=self._build_model_settings(runtime),
            output_type=output_type,
        )
        result = Runner.run_sync(
            agent,
            prompt,
            max_turns=1,
            run_config=RunConfig(
                workflow_name="ds160_ai_material_bundle_generator",
                tracing_disabled=True,
            ),
        )
        return result.final_output_as(output_type, raise_if_incorrect_type=True)

    def _build_model(self, runtime: dict[str, Any]) -> OpenAIChatCompletionsModel:
        user_config = current_user_model_config()
        api_key = (
            user_config.api_key
            if user_config is not None
            else os.getenv("OPENAI_API_KEY")
        )
        base_url = (
            user_config.base_url
            if user_config is not None
            else os.getenv("OPENAI_BASE_URL")
        )
        model_name = self._string_or_none(runtime.get("model"))
        if not api_key or not base_url or not model_name:
            raise ModelUnavailableError(
                detail=runtime.get("model_unavailable_detail")
                or "当前后端未配置可用的材料生成模型。",
                provider=runtime.get("provider"),
                model=model_name,
                missing_env_vars=runtime.get("model_unavailable_missing_env_vars"),
            )
        return OpenAIChatCompletionsModel(
            model=model_name,
            openai_client=AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
            ),
        )

    def _build_model_settings(self, runtime: dict[str, Any]) -> ModelSettings:
        reasoning_effort = self._string_or_none(runtime.get("reasoning_effort"))
        reasoning = (
            Reasoning(effort=reasoning_effort)
            if reasoning_effort in {"none", "minimal", "low", "medium", "high", "xhigh"}
            else None
        )
        return ModelSettings(
            reasoning=reasoning,
            verbosity="medium",
        )

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None


class AIMaterialBundleGeneratorService:
    MAX_TRANSCRIPT_TURNS = 12
    MAX_TRANSCRIPT_CHARS = 6_000

    def __init__(
        self,
        db: Session,
        *,
        model_factory: AgentModelFactory | None = None,
        runner: AIMaterialBundleRunner | None = None,
    ) -> None:
        self.db = db
        self.model_factory = model_factory or AgentModelFactory()
        self.runner = runner or OpenAIAgentsMaterialBundleRunner()
        self.turns = SessionTurnRepository(db)

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
                instructions=self._build_instructions(record.declared_family),
                output_type=GeneratedMaterialBundleOutput,
                runtime=runtime,
            )
        except Exception as exc:
            raise self._normalize_model_error(exc, runtime=runtime) from exc

        trace = {
            "generator": "openai_agents_sdk",
            "provider": runtime.get("provider"),
            "model": runtime.get("model"),
            "reasoning_effort": runtime.get("reasoning_effort"),
            "prompt_pack_id": "ds160.ai_material_bundle",
            "prompt_version": "v1",
            "seed_text_present": True,
        }
        return GeneratedMaterialBundleOutput.model_validate(output), trace

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
        payload = {
            "schema_version": "ai_material_bundle.v1",
            "session": {
                "session_id": record.session_id,
                "declared_family": record.declared_family,
            },
            "scenario": scenario,
            "seed_text": seed_text,
            "transcript": self._transcript_window(record.session_id),
            "existing_profile_json": dict(record.profile_json or {}),
            "include_synthetic_user_turns": include_synthetic_user_turns,
            "required_documents": [
                "ds160",
                "passport_bio",
                "i20",
                "admission_letter",
                "funding_proof",
                "relationship_proof_between_applicant_and_sponsors",
            ],
            "scenario_rules": self._scenario_rules(scenario),
            "task": (
                "Generate realistic synthetic plain-text visa materials that match "
                "the seed_text and transcript. These are user-visible practice "
                "documents, not oracle answers. Return only structured output."
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _build_instructions(self, declared_family: str | None) -> str:
        family = declared_family or "unknown"
        return (
            "你是 DS-160 模拟器里的 AI 材料生成器，必须使用 OpenAI Agents SDK 的结构化输出。\n"
            f"当前签证类别：{family}。\n"
            "核心规则：\n"
            "1. seed_text 和 transcript 是材料事实的最高优先级来源；学校、项目、资金来源、资助人、金额必须与用户说法一致。\n"
            "2. 生成的是看起来像真实 OCR/文本摘录的练习材料正文，不是总结、分析或风险报告。\n"
            "3. 正常场景必须生成 DS-160、护照首页、I-20、录取信、资金证明，最好也生成亲属关系证明。\n"
            "4. 字段必须用 JSON pointer，例如 /education/school_name、/education/program_name、/funding/primary_source。\n"
            "5. 不得在材料正文里写 Issue、Missing、Expected、Defect、This conflicts with 等答案提示。\n"
            "6. 如果场景要求制造冲突，只能通过材料字段值不同或用户声明与材料不同表达，不要解释冲突。\n"
            "7. 不要把内部 scenario、oracle、expected_findings、prompt、trace 写进材料正文。\n"
            "8. 输出必须符合 GeneratedMaterialBundleOutput。"
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

    def _transcript_window(self, session_id: str) -> list[dict[str, Any]]:
        turns = self.turns.list_session_turns(session_id)[-self.MAX_TRANSCRIPT_TURNS :]
        payload: list[dict[str, Any]] = []
        remaining_chars = self.MAX_TRANSCRIPT_CHARS
        for turn in turns:
            content = str(turn.content or "").strip()
            if not content:
                continue
            content = content[:remaining_chars]
            remaining_chars -= len(content)
            payload.append(
                {
                    "role": turn.role,
                    "content": content,
                    "turn_index": turn.turn_index,
                    "source": turn.source,
                }
            )
            if remaining_chars <= 0:
                break
        return payload

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
        if isinstance(exc, AgentsException):
            return ProviderAPIError(
                detail=f"材料生成 Agent 运行失败：{exc.__class__.__name__}",
                provider=runtime.get("provider"),
                model=runtime.get("model"),
                status_code=503,
            )
        return ModelRuntimeError(
            detail=f"材料生成失败：{exc.__class__.__name__}: {exc}",
            status_code=503,
        )
