from __future__ import annotations

import json
import os
from time import time_ns
from typing import Any, Literal, Protocol
from uuid import uuid4

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, RunConfig, Runner
from agents.exceptions import AgentsException, ModelBehaviorError
from agents.model_settings import Reasoning
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAI,
)
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.model_factory import AgentModelFactory
from app.agents.user_model_config import current_user_model_config
from app.core.settings import settings
from app.db.session import SessionLocal
from app.db.evidence_models import DocumentChunkRecord
from app.db.models import SessionRecord
from app.domain.contracts import GovernorDecision
from app.integrations.openai_compat_headers import openai_compat_default_headers
from app.platform.turn_record import TurnRecord
from app.repositories.document_repo import DocumentRepository
from app.repositories.evidence_repo import EvidenceRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.case_board_projection import (
    case_board_has_state,
    missing_evidence_from_case_board,
    proof_point_code,
    unresolved_funding_claim_requires_proof,
)
from app.services.admin_config_service import AdminConfigService
from app.services.case_memory_service import CaseMemoryService
from app.services.interview_case_state_builder import InterviewCaseStateBuilder
from app.services.runtime_errors import (
    ModelRuntimeError,
    ModelUnavailableError,
    ProviderAPIError,
)


NativeDecision = Literal[
    "continue_interview",
    "need_more_evidence",
    "route_correction",
    "high_risk_review",
    "simulated_refusal",
]
NativeNextSafeAction = Literal[
    "continue_interview",
    "ask_clarification",
    "request_document",
    "retry_later",
    "manual_review",
    "end_session",
]


class NativeInterviewerOutput(BaseModel):
    assistant_message: str
    decision: NativeDecision = "continue_interview"
    requested_documents: list[str] = Field(default_factory=list)
    next_safe_action: NativeNextSafeAction = "continue_interview"
    memory_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_message_aliases(cls, value: object) -> object:
        if not isinstance(value, dict) or value.get("assistant_message"):
            return value
        nested_output = value.get("output")
        if isinstance(nested_output, dict):
            nested_message = cls._message_alias_from_mapping(nested_output)
            if nested_message:
                normalized = dict(value)
                normalized["assistant_message"] = nested_message
                return normalized
        nested_message = cls._message_alias_from_mapping(value)
        if nested_message:
            normalized = dict(value)
            normalized["assistant_message"] = nested_message
            return normalized
        return value

    @classmethod
    def _message_alias_from_mapping(cls, value: dict[str, Any]) -> str | None:
        for key in (
            "response_text",
            "user_facing_message",
            "message",
            "next_question",
            "question",
            "follow_up_question",
            "interviewer_message",
            "officer_message",
            "response",
            "text",
            "content",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        return None

    @field_validator("assistant_message")
    @classmethod
    def validate_assistant_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("assistant_message must not be empty")
        return normalized

    @field_validator("requested_documents")
    @classmethod
    def normalize_requested_documents(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            candidate = item.strip()
            if candidate and candidate not in normalized:
                normalized.append(candidate)
        return normalized[:1]


class NativeInterviewQualityValidator:
    """Validate that the LLM remains the interviewer without taking over wording."""

    INTERNAL_MARKERS = (
        "字段",
        "风险码",
        "运行时",
        "prompt",
        "runtime",
        "trace",
        "case_state",
        "turn_decision",
    )

    def validate(
        self,
        *,
        output: NativeInterviewerOutput,
        case_state: dict[str, Any],
        current_user_message: str,
    ) -> list[str]:
        violations: list[str] = []
        message = output.assistant_message
        normalized_message = self._normalize_question(message)
        case_brief = self._payload(case_state.get("case_brief"))
        recent_questions = [
            question
            for question in (
                self._string_or_none(item.get("question"))
                for item in self._list_payload(
                    case_brief.get("recent_assistant_questions")
                )
            )
            if question is not None
        ]
        answered_topics = set(self._string_list(case_brief.get("answered_topic_keys")))
        transcript = self._transcript_text(case_state)

        if any(marker in message for marker in self.INTERNAL_MARKERS):
            violations.append("assistant_message 暴露了内部系统口吻或运行字段。")

        if any(
            normalized_message == self._normalize_question(question)
            for question in recent_questions
        ):
            violations.append("assistant_message 重复了最近已经问过的原问题。")

        if (
            self._user_is_complaining_about_repeat(current_user_message)
            and recent_questions
            and self._question_topic(message) == self._question_topic(recent_questions[-1])
            and not self._acknowledges_prior_answer(message)
        ):
            violations.append(
                "用户正在纠正重复提问，assistant_message 仍停留在同一问题。"
            )

        if (
            "post_study_plan" in answered_topics
            and self._asks_raw_post_study_job(message)
        ):
            violations.append("毕业后工作计划已经回答过，不能再问原始字段问题。")

        if self._asks_undergrad_major(message) and self._transcript_has_undergrad_major(
            transcript
        ):
            violations.append("本科专业已经在上文回答过，不能再问原始字段问题。")

        return violations

    def _asks_raw_post_study_job(self, value: str) -> bool:
        normalized = value.casefold()
        return (
            any(marker in normalized for marker in ("毕业后", "回国"))
            and any(marker in normalized for marker in ("做什么工作", "什么工作", "什么岗位"))
        )

    def _asks_undergrad_major(self, value: str) -> bool:
        normalized = value.casefold()
        return "本科" in normalized and "专业" in normalized and "什么" in normalized

    def _transcript_has_undergrad_major(self, transcript: str) -> bool:
        normalized = transcript.casefold()
        return "本科" in normalized and "专业" in normalized and (
            "读的是" in normalized
            or "读的" in normalized
            or "专业是" in normalized
        )

    def _user_is_complaining_about_repeat(self, value: str) -> bool:
        normalized = value.casefold()
        return any(
            marker in normalized
            for marker in (
                "我回答过",
                "我说过",
                "已经回答",
                "刚才说了",
                "你问过",
                "重复",
                "already answered",
            )
        )

    def _acknowledges_prior_answer(self, value: str) -> bool:
        normalized = value.casefold()
        return any(
            marker in normalized
            for marker in (
                "刚才",
                "已经",
                "你说",
                "你提到",
                "你前面",
                "根据你说的",
                "as you said",
                "you mentioned",
            )
        )

    def _question_topic(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        if any(marker in normalized for marker in ("资金", "资助", "学费", "父亲", "母亲", "fund", "sponsor")):
            return "funding"
        if any(marker in normalized for marker in ("毕业后", "回国", "工作", "岗位", "career", "job")):
            return "post_study_plan"
        if any(marker in normalized for marker in ("学校", "项目", "专业", "program", "school", "major")):
            return "program_school"
        if any(marker in normalized for marker in ("本科", "成绩", "语言", "academic", "gpa")):
            return "academic_preparation"
        return None

    def _normalize_question(self, value: str) -> str:
        return "".join(
            character
            for character in value.strip().casefold()
            if character not in " \t\r\n。！？!?，,：:"
        )

    def _transcript_text(self, case_state: dict[str, Any]) -> str:
        parts = []
        for turn in self._list_payload(case_state.get("full_transcript")):
            role = self._string_or_none(turn.get("role")) or "unknown"
            content = self._string_or_none(turn.get("content"))
            if content:
                parts.append(f"{role}: {content}")
        return "\n".join(parts)

    def _payload(self, value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _list_payload(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = self._string_or_none(item)
            if text is not None and text not in normalized:
                normalized.append(text)
        return normalized

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None


class NativeInterviewerAgentRunner(Protocol):
    def run(
        self,
        *,
        prompt: str,
        instructions: str,
        output_type: type[NativeInterviewerOutput],
        runtime: dict[str, Any],
    ) -> NativeInterviewerOutput:
        """Run the visible interviewer agent and return typed output."""


class OpenAIAgentsInterviewerRunner:
    """OpenAI Agents SDK adapter for the single visible interviewer."""

    def run(
        self,
        *,
        prompt: str,
        instructions: str,
        output_type: type[NativeInterviewerOutput],
        runtime: dict[str, Any],
    ) -> NativeInterviewerOutput:
        if runtime.get("provider") == "openai_compatible":
            return self._run_chat_json_fallback(
                prompt=prompt,
                instructions=instructions,
                output_type=output_type,
                runtime=runtime,
            )
        agent = Agent(
            name="DS-160 Native Interviewer",
            instructions=instructions,
            model=self._build_model(runtime),
            model_settings=self._build_model_settings(runtime),
            output_type=output_type,
        )
        try:
            result = Runner.run_sync(
                agent,
                prompt,
                max_turns=1,
                run_config=RunConfig(
                    workflow_name="ds160_native_interviewer",
                    tracing_disabled=True,
                ),
            )
            return result.final_output_as(output_type, raise_if_incorrect_type=True)
        except ModelBehaviorError:
            return self._run_chat_json_fallback(
                prompt=prompt,
                instructions=instructions,
                output_type=output_type,
                runtime=runtime,
            )

    def _run_chat_json_fallback(
        self,
        *,
        prompt: str,
        instructions: str,
        output_type: type[NativeInterviewerOutput],
        runtime: dict[str, Any],
    ) -> NativeInterviewerOutput:
        api_key, base_url, model_name = self._resolve_model_config(runtime)
        schema = json.dumps(output_type.model_json_schema(), ensure_ascii=False)
        json_instructions = (
            f"{instructions}\n\n"
            "Return only one JSON object matching this schema. "
            "Do not include markdown, prose, or code fences.\n"
            f"{schema}"
        )
        completion = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.openai_timeout_seconds,
            max_retries=0,
            default_headers=openai_compat_default_headers(),
        ).chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": json_instructions},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content
        if not content:
            raise ModelBehaviorError(
                "Native interviewer JSON fallback returned empty content.",
            )
        try:
            payload = self._parse_json_object_content(content)
            return output_type.model_validate(payload)
        except Exception as exc:
            raise ModelBehaviorError(
                f"Native interviewer JSON fallback returned invalid output: {exc}",
            ) from exc

    def _build_model(self, runtime: dict[str, Any]) -> OpenAIChatCompletionsModel:
        api_key, base_url, model_name = self._resolve_model_config(runtime)
        return OpenAIChatCompletionsModel(
            model=model_name,
            openai_client=AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=0,
                default_headers=openai_compat_default_headers(),
            ),
        )

    def _resolve_model_config(self, runtime: dict[str, Any]) -> tuple[str, str, str]:
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
                or "当前后端未配置可用的对话模型，无法生成面签问答。",
                provider=runtime.get("provider"),
                model=model_name,
                missing_env_vars=runtime.get("model_unavailable_missing_env_vars"),
            )
        return api_key, base_url, model_name

    def _parse_json_object_content(self, content: str) -> dict[str, Any]:
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
            raise ValueError("native interviewer output must be a JSON object")
        return payload

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


class NativeInterviewerRuntimeService:
    """LLM-first interviewer runtime; support layers do not write user-facing text."""

    MAX_PROMPT_TRANSCRIPT_TURNS = 30
    MAX_PROMPT_TRANSCRIPT_CHARS = 14_000

    def __init__(
        self,
        db: Session,
        *,
        model_factory: AgentModelFactory | None = None,
        agent_runner: NativeInterviewerAgentRunner | None = None,
        case_state_builder: InterviewCaseStateBuilder | None = None,
        quality_validator: NativeInterviewQualityValidator | None = None,
    ) -> None:
        self.db = db
        self.model_factory = model_factory or AgentModelFactory()
        self.agent_runner = agent_runner or OpenAIAgentsInterviewerRunner()
        self.case_state_builder = case_state_builder or InterviewCaseStateBuilder()
        self.quality_validator = quality_validator or NativeInterviewQualityValidator()
        self.session_turn_repo = SessionTurnRepository(db)
        self.document_repo = DocumentRepository(db)
        self.evidence_repo = EvidenceRepository(db)
        self.case_memory = CaseMemoryService(db)

    def run_turn(
        self,
        record: SessionRecord,
        message_text: str,
        *,
        user_turn: Any | None = None,
    ) -> dict[str, Any]:
        run_id = self._build_run_id()
        case_state = self._build_case_state(record)
        output, quality = self._run_with_quality_retry(
            record=record,
            message_text=message_text,
            case_state=case_state,
            run_id=run_id,
        )
        return self._build_response(
            record=record,
            message_text=message_text,
            case_state=case_state,
            output=output,
            run_id=run_id,
            quality=quality,
            user_turn_id=self._turn_id(user_turn),
        )

    def run_material_change(
        self,
        record: SessionRecord,
        *,
        reason: str,
    ) -> dict[str, Any]:
        case_state = self._build_case_state(record)
        current_focus = dict(case_state.get("current_focus", {}) or {})
        decision = self._material_change_decision(record, case_state)
        document_review = self._build_document_review(case_state)
        if decision == GovernorDecision.HIGH_RISK_REVIEW.value and not current_focus:
            current_focus = {
                "owner": "native_interviewer",
                "kind": "risk_review",
                "risk_code": "material_conflict",
            }
        prompt_trace = {
            "prompt_pack_id": "ds160.native_interviewer",
            "prompt_version": "native-v0",
            "native_trigger": "material_change",
            "material_change_reason": reason,
        }
        advisory_context = self._build_advisory_context(case_state)
        return {
            "assistant_message": "",
            "governor_decision": decision,
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {
                "decision": decision,
                "assistant_message_author": "native_interviewer",
                "next_safe_action": "continue_interview",
            },
            "document_review": document_review,
            "advisory_context": advisory_context,
            "prompt_trace": prompt_trace,
            "runtime_view_state": self._build_runtime_view_state(
                decision=decision,
                current_focus=current_focus,
                requested_documents=[],
                remaining_required_documents=[],
                advisory_context=advisory_context,
                document_review=document_review,
                prompt_trace=prompt_trace,
            ),
            "agent_runtime": "native_interviewer",
            "selected_public_runtime": "native_interviewer",
        }

    def _run_with_quality_retry(
        self,
        *,
        record: SessionRecord,
        message_text: str,
        case_state: dict[str, Any],
        run_id: str,
    ) -> tuple[NativeInterviewerOutput, dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        feedback: list[str] = []
        for attempt in range(2):
            output, runtime = self._run_interviewer_agent(
                record=record,
                message_text=message_text,
                case_state=case_state,
                run_id=run_id,
                validator_feedback=feedback,
            )
            violations = self.quality_validator.validate(
                output=output,
                case_state=case_state,
                current_user_message=message_text,
            )
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "violations": violations,
                    "provider": runtime.get("provider"),
                    "model": runtime.get("model"),
                    "reasoning_effort": runtime.get("reasoning_effort"),
                }
            )
            if not violations:
                return output, {"status": "passed", "attempts": attempts}
            feedback = violations

        raise ModelRuntimeError(
            detail="模型输出未通过连续面谈质量检查，已阻止发送重复或失真的面试问题。",
            status_code=503,
            upstream_code="native_quality_guard_failed",
        )

    def _run_interviewer_agent(
        self,
        *,
        record: SessionRecord,
        message_text: str,
        case_state: dict[str, Any],
        run_id: str,
        validator_feedback: list[str],
    ) -> tuple[NativeInterviewerOutput, dict[str, Any]]:
        runtime = self._build_runtime(record.declared_family)
        if runtime.get("model_unavailable_reason"):
            raise ModelUnavailableError(
                detail=runtime.get("model_unavailable_detail")
                or "当前后端未配置可用的对话模型，无法生成面签问答。",
                provider=runtime.get("provider"),
                model=runtime.get("model"),
                missing_env_vars=runtime.get("model_unavailable_missing_env_vars"),
            )

        prompt = self._build_prompt(
            record=record,
            message_text=message_text,
            case_state=case_state,
            run_id=run_id,
            validator_feedback=validator_feedback,
        )
        try:
            output = self.agent_runner.run(
                prompt=prompt,
                instructions=self._build_instructions(record.declared_family),
                output_type=NativeInterviewerOutput,
                runtime=runtime,
            )
        except Exception as exc:
            raise self._normalize_model_error(exc, runtime=runtime) from exc
        return NativeInterviewerOutput.model_validate(output), runtime

    def _build_runtime(self, declared_family: str | None) -> dict[str, Any]:
        if hasattr(self.model_factory, "build_runtime_config"):
            return self.model_factory.build_runtime_config(
                "adjudication_agent",
                "interview_turn",
                declared_family=declared_family,
            )
        try:
            _model, runtime = self.model_factory.build(
                "adjudication_agent",
                "interview_turn",
                declared_family=declared_family,
            )
        except TypeError:
            _model, runtime = self.model_factory.build("adjudication_agent", "interview_turn")
        return runtime

    def _build_prompt(
        self,
        *,
        record: SessionRecord,
        message_text: str,
        case_state: dict[str, Any],
        run_id: str,
        validator_feedback: list[str],
    ) -> str:
        interview_context = self._build_interviewer_context(case_state)
        interview_context["full_transcript"] = self._transcript_prompt_window(
            self._list_payload(case_state.get("full_transcript"))
        )
        payload = {
            "schema_version": "native_interviewer.v0",
            "run_id": run_id,
            "session": {
                "session_id": record.session_id,
                "declared_family": record.declared_family,
                "phase_state": record.phase_state,
            },
            "current_user_message": message_text,
            "interview_context": interview_context,
            "validator_feedback": validator_feedback,
            "task": (
                "Act as the single visible visa officer. Produce the next user-facing "
                "message from the transcript first. Treat old extracted fields as "
                "untrusted hints, not as the source of truth. Do not use any hardcoded "
                "question list; generate the response from this applicant's facts."
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _build_instructions(self, declared_family: str | None) -> str:
        family = declared_family or "unknown"
        return (
            "你是这个 DS-160 模拟面谈中唯一对用户说话的签证官。"
            "你不是表单机器人，也不是客服。你必须像真人面试官一样持续记住上文。\n"
            f"当前签证类别：{family}。\n"
            "核心规则：\n"
            "1. 先读完整 transcript；上文对话是判断用户是否已回答的最高优先级事实来源。\n"
            "2. 旧抽取字段、interview memory、case brief、gate progress 都只能当低置信提示；如果它们和 transcript 冲突，以 transcript 为准。\n"
            "3. 如果用户已经说过学校、项目、本科专业、资金来源或毕业计划，不要再把这些原始字段当成缺失事实来问。\n"
            "4. 如果用户说“我回答过你了/你问过了”，先承认并复述你已掌握的事实，然后换成一个真正有价值的追问。\n"
            "5. 参考材料和政策只提供事实，不替你决定话术；你是唯一的对话主人公。\n"
            "6. 不要输出硬编码题库感的问题；每句话都要来自当前申请人的历史、材料和风险点。\n"
            "7. 正常面谈回复一到两句，只问一个自然追问；可以有半句承接。\n"
            "8. 不要暴露内部字段、风险码、run id、prompt、trace、JSON 路径或系统实现。\n"
            "9. 如果需要材料，说明自然语言材料名和原因；如果继续问答，直接问一个基于当前事实的追问。\n"
            "10. F-1 常规面谈的重点是学习目的、学校和项目选择、学术准备、资金来源、家庭/回国联系、毕业后计划。"
            "不要把面谈变成考研复试、技术答辩或工作面试。\n"
            "11. 追问必须有清晰的签证判断价值：用于核对材料一致性、学习真实性、资金可信度、回国约束或风险点。"
            "如果申请人的回答已经与材料和当前面谈逻辑对得上，就换到下一个 F-1 维度或自然收束；不要围绕同一个点反复追问。\n"
            "12. 可以问课程项目或实习，但只用于判断学术准备和学习动机。除非出现敏感研究、明显矛盾或高风险信号，"
            "不要连续追问项目实现细节、模型设计、平台细节、工程分工或技术方案。\n"
            "13. 如果申请人已经解释过项目暴露出的能力缺口、为什么需要 NYU 课程、以及毕业后回中国的岗位方向，"
            "不要再换一种说法追问“最想补哪项工程/技术能力”。这类信息已经够用于 F-1 学习动机判断。\n"
            "14. 如果历史里出现连续 assistant 问题，只把最新 assistant 问题当作当前问题；"
            "不要回填更早的 assistant 问题，也不要因为旧问题未答而把对话拉回技术细节。\n"
            "15. 如果核心 F-1 维度已经回答清楚、材料支持充分且没有明显风险，不要为了继续问而发明新问题；"
            "可以用一句自然收束说明当前回答已经比较完整，必要时只问一个非常短的最终确认。\n"
            "16. 输出必须符合 NativeInterviewerOutput 结构。"
        )

    def _build_interviewer_context(
        self,
        case_state: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "context_policy": {
                "source_of_truth_order": [
                    "full_transcript",
                    "current_user_message",
                    "document_evidence",
                    "legacy_extracted_hints",
                ],
                "legacy_extracted_hints_are_untrusted": True,
                "do_not_treat_missing_legacy_fields_as_unanswered": True,
            },
            "full_transcript": self._conversation_context(case_state),
            "document_evidence": {
                "documents": self._list_payload(case_state.get("documents")),
                "evidence_digest": self._payload(case_state.get("evidence_digest")),
                "case_memory": self._payload(case_state.get("case_memory")),
                "case_board": self._payload(case_state.get("case_board")),
            },
            "legacy_extracted_hints": {
                "profile_json": self._payload(case_state.get("profile_json")),
                "gate_progress": self._payload(case_state.get("gate_progress")),
                "interview_memory": self._payload(case_state.get("interview_memory")),
                "case_brief": self._payload(case_state.get("case_brief")),
                "history_summary": self._payload(case_state.get("history_summary")),
            },
        }

    def _conversation_context(self, case_state: dict[str, Any]) -> dict[str, Any]:
        transcript = self._list_payload(case_state.get("full_transcript"))
        compacted: list[dict[str, Any]] = []
        for item in transcript:
            turn = self._payload(item)
            role = self._string_or_none(turn.get("role"))
            if role not in {"user", "assistant"}:
                continue
            payload = {
                "turn_index": turn.get("turn_index"),
                "role": role,
                "content": self._string_or_none(turn.get("content")) or "",
            }
            if role == "assistant" and compacted and compacted[-1].get("role") == "assistant":
                compacted[-1] = payload
                continue
            compacted.append(payload)
        tail = compacted[-18:]
        return {
            "policy": {
                "consecutive_assistant_turns": (
                    "compacted_to_latest_assistant_question"
                ),
                "active_question": "latest_assistant_turn_only",
            },
            "omitted_older_turns": max(len(compacted) - len(tail), 0),
            "turns": tail,
        }

    def _build_case_state(self, record: SessionRecord) -> dict[str, Any]:
        return self.case_state_builder.build(
            record,
            self.session_turn_repo.list_session_turns(record.session_id),
            documents=self.document_repo.list_session_documents(record.session_id),
            evidence_items=self.evidence_repo.list_session_evidence(record.session_id),
            document_chunks=self._list_session_document_chunks(record.session_id),
            case_memory_snapshot=self.case_memory.get_or_build_snapshot(
                record.session_id
            ).model_dump(mode="json"),
            evidence_graph=self.case_memory.query_evidence_graph(record.session_id),
        )

    def _list_session_document_chunks(self, session_id: str) -> list[DocumentChunkRecord]:
        statement = (
            select(DocumentChunkRecord)
            .where(DocumentChunkRecord.session_id == session_id)
            .order_by(
                DocumentChunkRecord.document_id,
                DocumentChunkRecord.ordinal,
                DocumentChunkRecord.chunk_id,
            )
        )
        return list(self.db.scalars(statement))

    def _build_response(
        self,
        *,
        record: SessionRecord,
        message_text: str,
        case_state: dict[str, Any],
        output: NativeInterviewerOutput,
        run_id: str,
        quality: dict[str, Any],
        user_turn_id: str | None,
    ) -> dict[str, Any]:
        decision = output.decision
        advisory_context = self._build_advisory_context(case_state)
        missing_evidence = self._missing_evidence_documents(advisory_context)
        requested_documents = list(
            output.requested_documents
            if decision == GovernorDecision.NEED_MORE_EVIDENCE.value
            else []
        )
        remaining_required_documents = list(missing_evidence)
        current_focus = self._build_current_focus(
            decision=decision,
            assistant_message=output.assistant_message,
            requested_documents=requested_documents,
        )
        prompt_trace = self._build_prompt_trace(
            run_id=run_id,
            quality=quality,
            case_state=case_state,
        )
        runtime_view_state = self._build_runtime_view_state(
            decision=decision,
            current_focus=current_focus,
            requested_documents=requested_documents,
            remaining_required_documents=remaining_required_documents,
            advisory_context=advisory_context,
            document_review={},
            prompt_trace=prompt_trace,
        )
        turn_decision = {
            "decision": decision,
            "assistant_message_author": "native_interviewer",
            "requested_documents": requested_documents,
            "remaining_required_documents": remaining_required_documents,
            "focus_kind": current_focus.get("kind"),
            "focus_document_type": current_focus.get("document_type"),
            "focus_risk_code": current_focus.get("risk_code"),
            "governor_decision": decision,
            "guard_status": quality.get("status"),
            "next_safe_action": output.next_safe_action,
            "current_key_question": runtime_view_state.get("current_key_question"),
            "current_key_proof": runtime_view_state.get("current_key_proof"),
            "current_risk_code": runtime_view_state.get("current_risk_code"),
        }
        turn_record = TurnRecord.create(
            session_id=record.session_id,
            user_turn_id=user_turn_id,
            user_input=message_text,
            decision=decision,
            assistant_message=output.assistant_message,
            requested_documents=requested_documents,
            remaining_required_documents=remaining_required_documents,
            focus=current_focus,
            trace_refs=["native_interviewer"],
            artifacts=[
                {
                    "kind": "native_interviewer_run",
                    "run_id": run_id,
                    "quality_status": quality.get("status"),
                }
            ],
            advisory_summary={
                "risk_codes": list(advisory_context.get("risk_codes", []) or []),
                "missing_evidence": list(
                    advisory_context.get("missing_evidence", []) or []
                ),
                "risk_level": advisory_context.get("risk_level"),
            },
            document_review={},
        ).model_dump(mode="json", exclude_none=True)
        return {
            "assistant_message": output.assistant_message,
            "governor_decision": decision,
            "score_summary": advisory_context.get("score_summary", {}),
            "requested_documents": requested_documents,
            "remaining_required_documents": remaining_required_documents,
            "gate_progress": self._payload(case_state.get("gate_progress")),
            "turn_decision": turn_decision,
            "document_review": {},
            "advisory_context": advisory_context,
            "prompt_trace": prompt_trace,
            "runtime_view_state": runtime_view_state,
            "turn_record": turn_record,
            "agent_runtime": "native_interviewer",
            "selected_public_runtime": "native_interviewer",
            "native_run_id": run_id,
        }

    def _missing_evidence_documents(
        self,
        advisory_context: dict[str, Any],
    ) -> list[str]:
        values: list[str] = []
        for item in advisory_context.get("missing_evidence", []) or []:
            normalized = proof_point_code({"proof_point_id": item})
            if normalized and normalized not in values:
                values.append(normalized)
        return values

    def _build_current_focus(
        self,
        *,
        decision: str,
        assistant_message: str,
        requested_documents: list[str],
    ) -> dict[str, Any]:
        if decision == GovernorDecision.NEED_MORE_EVIDENCE.value:
            return {
                "owner": "native_interviewer",
                "kind": "required_document",
                "document_type": requested_documents[0] if requested_documents else None,
            }
        if decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return {
                "owner": "native_interviewer",
                "kind": "risk_review",
                "question": assistant_message,
            }
        if decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return {
                "owner": "native_interviewer",
                "kind": "refusal",
                "reason": assistant_message,
            }
        if decision == GovernorDecision.ROUTE_CORRECTION.value:
            return {
                "owner": "native_interviewer",
                "kind": "route_correction",
                "question": assistant_message,
            }
        return {
            "owner": "native_interviewer",
            "kind": "interview_question",
            "question": assistant_message,
        }

    def _build_runtime_view_state(
        self,
        *,
        decision: str,
        current_focus: dict[str, Any],
        requested_documents: list[str],
        remaining_required_documents: list[str],
        advisory_context: dict[str, Any],
        document_review: dict[str, Any],
        prompt_trace: dict[str, Any],
    ) -> dict[str, Any]:
        current_key_question = self._string_or_none(current_focus.get("question"))
        current_key_proof = self._string_or_none(current_focus.get("document_type"))
        current_risk_code = self._string_or_none(current_focus.get("risk_code"))
        public_status = self._public_status(
            decision=decision,
            current_key_proof=current_key_proof,
            current_risk_code=current_risk_code,
        )
        return {
            "source_turn_id": None,
            "decision": decision,
            "governor_decision": decision,
            "public_status": public_status,
            "risk_level": advisory_context.get("risk_level"),
            "current_focus": current_focus,
            "current_key_question": current_key_question,
            "current_key_proof": current_key_proof,
            "current_risk_code": current_risk_code,
            "requested_documents": requested_documents,
            "remaining_required_documents": remaining_required_documents,
            "allowed_next_actions": self._allowed_next_actions(
                decision=decision,
                current_key_question=current_key_question,
                current_key_proof=current_key_proof,
            ),
            "advisory_context": advisory_context,
            "document_review": document_review,
            "prompt_trace": prompt_trace,
        }

    def _build_prompt_trace(
        self,
        *,
        run_id: str,
        quality: dict[str, Any],
        case_state: dict[str, Any],
    ) -> dict[str, Any]:
        attempts = list(quality.get("attempts", []) or [])
        last_attempt = attempts[-1] if attempts else {}
        return {
            "prompt_pack_id": "ds160.native_interviewer",
            "prompt_version": "native-v0",
            "native_run_id": run_id,
            "assistant_message_author": "native_interviewer",
            "guard_status": quality.get("status"),
            "quality_attempt_count": len(attempts),
            "provider": last_attempt.get("provider"),
            "model": last_attempt.get("model"),
            "reasoning_effort": last_attempt.get("reasoning_effort"),
            "transcript_turn_count": self._payload(case_state.get("transcript")).get(
                "turn_count"
            ),
        }

    def _build_advisory_context(self, case_state: dict[str, Any]) -> dict[str, Any]:
        case_board = self._payload(case_state.get("case_board"))
        interviewer_state = self._payload(case_state.get("interviewer_state"))
        advisory = self._payload(interviewer_state.get("advisory_context"))
        if advisory:
            if case_board_has_state(case_board):
                advisory["missing_evidence"] = missing_evidence_from_case_board(
                    case_board
                )
            return self._with_unresolved_funding_claim(case_board, advisory)
        latest_score = self._latest_payload(case_state.get("score_history_tail"))
        risk_flags = latest_score.get("risk_flags", [])
        risk_codes = [
            item.get("code")
            for item in risk_flags
            if isinstance(item, dict) and self._string_or_none(item.get("code"))
        ]
        advisory_context = {
            "score_summary": {
                key: int(latest_score.get(key, 0))
                for key in (
                    "category_fit",
                    "document_readiness",
                    "narrative_consistency",
                    "confidence",
                )
                if isinstance(latest_score.get(key, 0), int)
            },
            "risk_codes": risk_codes,
            "missing_evidence": (
                missing_evidence_from_case_board(case_board)
                if case_board_has_state(case_board)
                else [
                    item
                    for item in latest_score.get("missing_evidence", [])
                    if isinstance(item, str) and item.strip()
                ]
            ),
            "risk_level": "high" if risk_codes else "none",
        }
        return self._with_unresolved_funding_claim(case_board, advisory_context)

    def _with_unresolved_funding_claim(
        self,
        case_board: dict[str, Any],
        advisory_context: dict[str, Any],
    ) -> dict[str, Any]:
        if not unresolved_funding_claim_requires_proof(case_board):
            return advisory_context

        missing_evidence = list(advisory_context.get("missing_evidence", []) or [])
        if "funding_proof" not in missing_evidence:
            missing_evidence.append("funding_proof")

        risk_codes = list(advisory_context.get("risk_codes", []) or [])
        if "supporting_evidence_missing" not in risk_codes:
            risk_codes.append("supporting_evidence_missing")

        return {
            **advisory_context,
            "missing_evidence": missing_evidence,
            "risk_codes": risk_codes,
            "risk_level": (
                advisory_context.get("risk_level")
                if advisory_context.get("risk_level") not in {None, "none"}
                else "medium"
            ),
        }

    def _material_change_decision(
        self,
        record: SessionRecord,
        case_state: dict[str, Any],
    ) -> str:
        review = self._build_document_review(case_state)
        if review.get("recommended_next_step") == "high_risk_review":
            return GovernorDecision.HIGH_RISK_REVIEW.value
        if record.current_governor_decision in {
            GovernorDecision.ROUTE_CORRECTION.value,
            GovernorDecision.SIMULATED_REFUSAL.value,
        }:
            return record.current_governor_decision
        return GovernorDecision.CONTINUE_INTERVIEW.value

    def _build_document_review(self, case_state: dict[str, Any]) -> dict[str, Any]:
        case_memory = self._payload(case_state.get("case_memory"))
        conflicts = self._list_payload(case_memory.get("conflicts"))
        if not conflicts:
            return {}
        high_conflicts = [
            conflict
            for conflict in conflicts
            if self._string_or_none(conflict.get("severity")) == "high"
            or self._string_or_none(conflict.get("severity")) == "medium"
        ]
        if not high_conflicts:
            return {}
        return {
            "review_status": "high_risk",
            "recommended_next_step": "high_risk_review",
            "claim_conflicts": high_conflicts,
            "reviewer_summary": "证据核验识别到冲突，当前应先围绕冲突点复核。",
        }

    def _public_status(
        self,
        *,
        decision: str,
        current_key_proof: str | None,
        current_risk_code: str | None,
    ) -> str:
        if decision == GovernorDecision.NEED_MORE_EVIDENCE.value:
            return "waiting_key_proof" if current_key_proof else decision
        if decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return "verify_key_issue" if current_risk_code else "high_risk_review"
        return decision

    def _allowed_next_actions(
        self,
        *,
        decision: str,
        current_key_question: str | None,
        current_key_proof: str | None,
    ) -> list[str]:
        if decision == GovernorDecision.NEED_MORE_EVIDENCE.value or current_key_proof:
            return ["upload_key_proof", "explain_missing_proof"]
        if decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return ["clarify_key_issue", "wait_for_review"]
        if decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return ["review_refusal_result"]
        if current_key_question:
            return ["answer_question", "continue_interview"]
        return ["continue_interview"]

    def _transcript_prompt_window(
        self,
        full_transcript: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        retained_reversed: list[dict[str, Any]] = []
        retained_chars = 0
        for item in reversed(full_transcript):
            content = self._string_or_none(item.get("content")) or ""
            next_chars = retained_chars + len(content)
            if (
                retained_reversed
                and (
                    len(retained_reversed) >= self.MAX_PROMPT_TRANSCRIPT_TURNS
                    or next_chars > self.MAX_PROMPT_TRANSCRIPT_CHARS
                )
            ):
                break
            retained_reversed.append(dict(item))
            retained_chars = next_chars
        return list(reversed(retained_reversed))

    def _normalize_model_error(
        self,
        exc: Exception,
        *,
        runtime: dict[str, Any],
    ) -> ModelRuntimeError:
        provider = self._string_or_none(runtime.get("provider"))
        model = self._string_or_none(runtime.get("model"))
        if isinstance(exc, ModelRuntimeError):
            return exc
        if isinstance(exc, ModelBehaviorError):
            return ModelRuntimeError(
                detail="上游模型返回内容不符合面谈结构化输出要求，后端已阻止发送不完整回复。",
                status_code=502,
                provider=provider,
                model=model,
                upstream_code="model_output_invalid",
                error_category="model_output_invalid",
            )
        cause = exc.__cause__ if isinstance(exc.__cause__, Exception) else None
        if isinstance(exc, AgentsException) and cause is not None:
            return self._normalize_model_error(cause, runtime=runtime)
        if isinstance(exc, APIStatusError):
            upstream_code = self._model_error_code(getattr(exc, "body", None))
            return ProviderAPIError(
                detail=self._provider_status_error_detail(
                    exc.status_code or 503,
                    upstream_code=upstream_code,
                ),
                status_code=exc.status_code or 503,
                provider=provider,
                model=model,
                upstream_code=upstream_code,
                body=getattr(exc, "body", None),
            )
        if isinstance(exc, APITimeoutError):
            return ModelRuntimeError(
                detail=self._provider_timeout_error_detail(model=model),
                status_code=504,
                provider=provider,
                model=model,
                upstream_code="upstream_timeout",
                error_category="upstream_timeout",
            )
        if isinstance(exc, APIConnectionError):
            return ModelRuntimeError(
                detail="上游模型服务连接失败，请检查 Base URL、网络或服务可用性。",
                status_code=502,
                provider=provider,
                model=model,
                upstream_code="upstream_connection_error",
                error_category="upstream_connection_error",
            )
        if isinstance(exc, AgentsException):
            return ModelRuntimeError(
                detail="面谈模型代理运行失败，可能是模型输出解析或 Agents SDK 内部错误。",
                status_code=503,
                provider=provider,
                model=model,
                upstream_code="agent_runtime_error",
                error_category="agent_runtime_error",
            )
        return ModelRuntimeError(
            detail=f"面谈运行时内部错误：{exc.__class__.__name__}",
            status_code=500,
            provider=provider,
            model=model,
            upstream_code="native_interviewer_internal_error",
            error_category="internal_error",
        )

    def _provider_status_error_detail(
        self,
        status_code: int,
        *,
        upstream_code: str | None,
    ) -> str:
        if status_code in {401, 403}:
            return "上游模型认证失败，请检查 API Key、Base URL 或模型访问权限。"
        if status_code == 429:
            return "上游模型额度已耗尽或请求过于频繁，请稍后重试或更换模型配置。"
        if status_code == 504:
            return self._provider_timeout_error_detail(model=None)
        suffix = f"（错误码：{upstream_code}）" if upstream_code else ""
        return f"上游模型服务返回 HTTP {status_code}{suffix}，本轮面谈回复未生成。"

    def _provider_timeout_error_detail(self, *, model: str | None) -> str:
        model_label = f"（模型：{model}）" if model else ""
        return f"上游模型请求超时{model_label}，本轮面谈回复未生成。"

    def _model_error_code(self, body: object | None) -> str | None:
        if isinstance(body, dict):
            code = body.get("code")
            if isinstance(code, str) and code:
                return code
            error = body.get("error")
            if isinstance(error, dict):
                error_code = error.get("code") or error.get("type")
                if isinstance(error_code, str) and error_code:
                    return error_code
        return None

    def _build_run_id(self) -> str:
        return f"native-run-{time_ns():020d}-{uuid4().hex[:8]}"

    def _turn_id(self, turn: Any | None) -> str | None:
        return self._string_or_none(getattr(turn, "turn_id", None))

    def _latest_payload(self, value: Any) -> dict[str, Any]:
        payloads = self._list_payload(value)
        return payloads[-1] if payloads else {}

    def _payload(self, value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _list_payload(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None
