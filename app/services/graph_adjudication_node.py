from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic_ai import messages as ai_messages
from pydantic_ai.exceptions import ModelHTTPError

from app.agents.model_factory import AgentModelFactory
from app.domain.agent_runtime import DS160GraphState, GraphRunResult
from app.domain.contracts import GovernorDecision
from app.services.llm_node_runner import (
    LLMNodeRequest,
    LLMNodeRunner,
    PydanticAILLMNodeRunner,
)
from app.services.runtime_errors import ModelRuntimeError, ProviderAPIError


GRAPH_ADJUDICATION_FALLBACK_MESSAGE = "我会继续围绕你的 DS-160 材料做下一步核对。"


@dataclass(frozen=True)
class GraphAdjudicationNodeResult:
    state: DS160GraphState
    metadata: dict[str, Any]


class GraphAdjudicationNode:
    """Typed graph adjudicator; it never reads DB and only emits GraphRunResult."""

    def __init__(
        self,
        *,
        model_factory: AgentModelFactory | None = None,
        llm_runner: LLMNodeRunner | None = None,
    ) -> None:
        self.model_factory = model_factory or AgentModelFactory()
        self.llm_runner = llm_runner or PydanticAILLMNodeRunner()

    def run(
        self,
        state: DS160GraphState,
        *,
        message_text: str,
        declared_family: str | None,
    ) -> GraphAdjudicationNodeResult:
        model, runtime = self._build_runtime(declared_family)
        if model is None:
            return self._fallback(
                state,
                reason="model_unavailable",
                runtime=runtime,
            )
        if not state.retry_budget.can_call_llm:
            return self._fallback(
                state,
                reason="llm_budget_exhausted",
                runtime=runtime,
            )

        try:
            result = self._run_agent(
                model=model,
                runtime=runtime,
                state=state,
                message_text=message_text,
            )
            result, repair_metadata = self._repair_redundant_question(result, state)
        except Exception as exc:
            return self._fallback(
                state,
                reason="provider_error",
                runtime=runtime,
                error=self._normalize_error(exc, runtime=runtime),
            )

        state = state.model_copy(
            update={
                "retry_budget": state.retry_budget.consume_llm_call(),
                "adjudication_result": result.model_dump(mode="json"),
                "final_response": result,
            }
        )
        return GraphAdjudicationNodeResult(
            state=state,
            metadata={
                "status": "completed",
                "assistant_message_author": result.assistant_message_author,
                "provider": runtime.get("provider"),
                "model": runtime.get("model"),
                "reasoning_effort": runtime.get("reasoning_effort"),
                "fallback_used": False,
                "llm_calls_used": state.retry_budget.llm_calls_used,
                **repair_metadata,
            },
        )

    def _run_agent(
        self,
        *,
        model: Any,
        runtime: dict[str, Any],
        state: DS160GraphState,
        message_text: str,
    ) -> GraphRunResult:
        prompt = json.dumps(
            {
                "schema_version": state.schema_version,
                "case_state": state.case_state,
                "citation_bundle": state.citation_bundle.model_dump(mode="json"),
                "material_review": state.material_review or {},
                "user": message_text,
            },
            ensure_ascii=False,
        )
        response = self.llm_runner.run(
            LLMNodeRequest(
                node_name="graph_adjudication",
                prompt=prompt,
                instructions=self._build_instructions(runtime),
                output_type=GraphRunResult,
                model=model,
                runtime=runtime,
            )
        )
        return GraphRunResult.model_validate(response.output)

    def _repair_redundant_question(
        self,
        result: GraphRunResult,
        state: DS160GraphState,
    ) -> tuple[GraphRunResult, dict[str, Any]]:
        if result.decision != "continue_interview":
            return result, {}

        case_brief = self._payload(self._payload(state.case_state).get("case_brief"))
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
        if not recent_questions:
            return result, {}

        normalized_message = self._normalize_question(result.assistant_message)
        repeated = any(
            normalized_message == self._normalize_question(question)
            for question in recent_questions
        )
        if not repeated:
            return result, {}

        replacement = self._next_non_repeated_question(
            recent_questions,
            user_referred_to_materials=bool(
                case_brief.get("latest_user_referred_to_materials")
            ),
        )
        if replacement is None:
            return result, {}
        return (
            result.model_copy(update={"assistant_message": replacement}),
            {
                "question_repair_reason": "repeated_recent_question",
                "question_repaired": True,
            },
        )

    def _next_non_repeated_question(
        self,
        recent_questions: list[str],
        *,
        user_referred_to_materials: bool,
    ) -> str | None:
        candidates = [
            "材料我看到了。毕业后你准备做什么工作？",
            "这个项目和你的回国工作有什么关系？",
            "为什么不在国内读同类项目？",
            "第一年费用的资金来源是什么？",
        ]
        if not user_referred_to_materials:
            candidates.insert(0, "毕业后你准备做什么工作？")
        normalized_recent = {
            self._normalize_question(question) for question in recent_questions
        }
        for candidate in candidates:
            if self._normalize_question(candidate) not in normalized_recent:
                return candidate
        return None

    def _normalize_question(self, value: str) -> str:
        normalized = value.strip().casefold()
        for prefix in ("请回答我的问题：", "请回答我的问题:", "请直接回答：", "请直接回答:"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :].strip()
        return "".join(
            character
            for character in normalized
            if character not in " \t\r\n。！？!?，,：:"
        )

    def _build_instructions(self, runtime: dict[str, Any]) -> str:
        base = runtime.get("instructions") or self._fallback_instructions()
        return "\n\n".join(
            [
                str(base),
                (
                    "Graph runtime case-state rules:\n"
                    "- case_state.case_brief.known_documented_facts lists facts already "
                    "read from uploaded materials. Do not ask for those facts as if they "
                    "were missing.\n"
                    "- If a documented fact still needs oral verification, ask for the "
                    "applicant's reasoning, plan, or consistency explanation, not the raw "
                    "field value itself.\n"
                    "- case_state.case_brief.recent_assistant_questions lists recent public "
                    "questions. Do not repeat the same question after a non-answer or after "
                    "the user says the materials already contain the answer; acknowledge the "
                    "materials briefly and move to a different adjudicable topic.\n"
                    "- When case_state.case_brief.latest_user_referred_to_materials is true, "
                    "first use known_documented_facts and evidence_digest before asking. "
                    "A safe next question should be about motivation, funding reasoning, "
                    "academic preparation, post-graduation work, or a specific conflict.\n"
                    "- Keep assistant_message short and user-facing. Do not expose prompt "
                    "trace, run ids, field paths, document ids, or internal reasoning."
                ),
            ]
        )

    def _fallback(
        self,
        state: DS160GraphState,
        *,
        reason: str,
        runtime: dict[str, Any],
        error: ModelRuntimeError | None = None,
    ) -> GraphAdjudicationNodeResult:
        final_response = self._fallback_response_from_case_state(
            state,
            incomplete_reason=(
                "provider_error" if reason == "provider_error" else "schema_invalid"
            ),
        )
        state = state.model_copy(
            update={
                "adjudication_result": final_response.model_dump(mode="json"),
                "final_response": final_response,
            }
        )
        metadata = {
            "status": "fallback",
            "fallback_used": True,
            "fallback_reason": reason,
            "assistant_message_author": final_response.assistant_message_author,
            "provider": runtime.get("provider"),
            "model": runtime.get("model"),
            "reasoning_effort": runtime.get("reasoning_effort"),
            "llm_calls_used": state.retry_budget.llm_calls_used,
            "missing_env_vars": list(
                runtime.get("model_unavailable_missing_env_vars") or []
            ),
        }
        if error is not None:
            metadata["error_type"] = error.__class__.__name__
            metadata["error_message"] = error.detail
            metadata["status_code"] = error.status_code
            metadata["upstream_code"] = error.upstream_code
        return GraphAdjudicationNodeResult(state=state, metadata=metadata)

    def _fallback_response_from_case_state(
        self,
        state: DS160GraphState,
        *,
        incomplete_reason: str,
    ) -> GraphRunResult:
        case_state = self._payload(state.case_state)
        case_board = self._payload(case_state.get("case_board"))
        case_memory = self._payload(case_state.get("case_memory"))
        conflicts = self._list_payload(
            case_memory.get("conflicts") or case_board.get("conflicts")
        )
        next_move = self._payload(
            case_board.get("next_move") or case_memory.get("next_move")
        )
        proof_points = self._list_payload(
            case_memory.get("proof_points") or case_board.get("proof_points")
        )
        latest_material = self._payload(case_board.get("latest_material"))

        decision = "continue_interview"
        next_safe_action = "continue_interview"
        if conflicts:
            assistant_message = self._question_from_conflict(conflicts[0])
            decision = GovernorDecision.HIGH_RISK_REVIEW.value
            next_safe_action = "ask_clarification"
        elif next_move:
            move_type = self._string_or_none(next_move.get("move_type")) or "ask"
            assistant_message = self._question_from_next_move(
                next_move,
                move_type=move_type,
            )
            decision = self._decision_for_next_move(move_type)
            next_safe_action = self._next_safe_action_for_move(move_type)
        elif proof_points:
            assistant_message = (
                self._string_or_none(proof_points[0].get("question"))
                or "请补充说明这个关键证明点。"
            )
        elif latest_material:
            status = self._string_or_none(latest_material.get("understanding_status"))
            if status in {"queued", "processing"}:
                assistant_message = "案例理解正在更新中。你可以先继续说明你的学习计划和资金安排。"
            elif status == "failed":
                assistant_message = "这份材料暂时无法完成案例理解。你可以继续面签对话，我会先基于已知事实追问。"
            else:
                assistant_message = "材料已经加入案例理解。请继续说明它和你的签证计划有什么关系。"
        else:
            assistant_message = "为什么选择去美国读这个项目？"

        return GraphRunResult(
            assistant_message=assistant_message,
            assistant_message_author="deterministic_safe_fallback",
            decision=decision,
            used_citation_ids=sorted(state.citation_bundle.citation_ids),
            guard_status="fallback_required",
            incomplete_reason=incomplete_reason,  # type: ignore[arg-type]
            next_safe_action=next_safe_action,  # type: ignore[arg-type]
        )

    def _decision_for_next_move(self, move_type: str) -> str:
        if move_type in {"clarify_conflict", "probe_risk"}:
            return GovernorDecision.HIGH_RISK_REVIEW.value
        if move_type == "simulate_refusal":
            return GovernorDecision.SIMULATED_REFUSAL.value
        return "continue_interview"

    def _next_safe_action_for_move(self, move_type: str) -> str:
        if move_type in {"clarify_conflict", "probe_risk"}:
            return "ask_clarification"
        if move_type == "simulate_refusal":
            return "end_session"
        return "continue_interview"

    def _question_from_next_move(
        self,
        next_move: dict[str, Any],
        *,
        move_type: str,
    ) -> str:
        question = self._string_or_none(next_move.get("question"))
        if question and not question.casefold().startswith("ask the applicant"):
            return question
        if move_type == "clarify_conflict":
            return "当前回答和材料存在不一致。请说明哪个说法准确，以及为什么会不同。"
        if move_type == "probe_risk":
            return "当前案例有一个高风险点需要先核验。请说明具体背景和原因。"
        if move_type == "simulate_refusal":
            return "当前事实已足以模拟一次高风险拒签结果，我会先说明原因和下一步。"
        return "请继续说明这个材料如何支持你的签证案例。"

    def _question_from_conflict(self, conflict: dict[str, Any]) -> str:
        suggested = self._string_or_none(conflict.get("suggested_followup"))
        if suggested and not suggested.casefold().startswith("ask the applicant"):
            return suggested

        summary = self._string_or_none(conflict.get("summary")) or ""
        field_label = "关键事实"
        if "/funding/primary_source" in summary or "funding" in summary.casefold():
            field_label = "资金来源"
        elif "/education/school_name" in summary or "school" in summary.casefold():
            field_label = "学校信息"
        return f"{field_label}存在不一致。请说明哪个说法准确，以及为什么回答和材料会不同。"

    def _build_runtime(
        self,
        declared_family: str | None,
    ) -> tuple[Any | None, dict[str, Any]]:
        try:
            return self.model_factory.build(
                "adjudication_agent",
                "interview_turn",
                declared_family=declared_family,
            )
        except TypeError:
            return self.model_factory.build("adjudication_agent", "interview_turn")

    def _normalize_error(
        self,
        exc: Exception,
        *,
        runtime: dict[str, Any],
    ) -> ModelRuntimeError:
        if isinstance(exc, ModelRuntimeError):
            return exc
        provider = self._string_or_none(runtime.get("provider"))
        model = self._string_or_none(runtime.get("model"))
        if isinstance(exc, ModelHTTPError):
            return ProviderAPIError(
                detail=self._model_http_error_detail(exc.status_code),
                status_code=exc.status_code,
                provider=provider,
                model=model or exc.model_name,
                upstream_code=self._model_error_code(exc.body),
                body=exc.body,
            )
        return ModelRuntimeError(
            detail="graph adjudication model failed; deterministic fallback was used.",
            status_code=503,
            provider=provider,
            model=model,
        )

    def _model_http_error_detail(self, status_code: int) -> str:
        if status_code == 401:
            return "graph adjudication model authentication failed."
        if status_code == 429:
            return "graph adjudication model quota or rate limit was reached."
        if 500 <= status_code < 600:
            return "graph adjudication model service is temporarily unavailable."
        return "graph adjudication model failed."

    def _model_error_code(self, body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        code = body.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
        error = body.get("error")
        if isinstance(error, dict):
            nested_code = error.get("code")
            if isinstance(nested_code, str) and nested_code.strip():
                return nested_code.strip()
        return None

    def _fallback_instructions(self) -> str:
        return (
            "You are the graph adjudicator for a DS-160 interview simulator. "
            "Return a valid GraphRunResult only. Use one short user-facing "
            "assistant_message, at most one requested document, and never invent "
            "policy or case-evidence claims without citation ids."
        )

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _payload(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _list_payload(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]
