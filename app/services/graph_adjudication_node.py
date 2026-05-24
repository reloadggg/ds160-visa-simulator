from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent
from pydantic_ai import messages as ai_messages
from pydantic_ai.exceptions import ModelHTTPError

from app.agents.model_factory import AgentModelFactory
from app.domain.agent_runtime import DS160GraphState, GraphRunResult
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
    ) -> None:
        self.model_factory = model_factory or AgentModelFactory()

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
        agent = Agent(
            model,
            output_type=GraphRunResult,
            instructions=runtime.get("instructions") or self._fallback_instructions(),
        )
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
        run_result = agent.run_sync(prompt)
        return run_result.output

    def _fallback(
        self,
        state: DS160GraphState,
        *,
        reason: str,
        runtime: dict[str, Any],
        error: ModelRuntimeError | None = None,
    ) -> GraphAdjudicationNodeResult:
        final_response = GraphRunResult(
            assistant_message=GRAPH_ADJUDICATION_FALLBACK_MESSAGE,
            assistant_message_author="deterministic_safe_fallback",
            decision="continue_interview",
            used_citation_ids=sorted(state.citation_bundle.citation_ids),
            guard_status="fallback_required",
            incomplete_reason="provider_error"
            if reason == "provider_error"
            else "schema_invalid",
            next_safe_action="continue_interview",
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
