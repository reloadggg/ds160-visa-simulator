from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from pydantic_ai import Agent
from pydantic_ai import messages as ai_messages

from app.agents.schemas import AgentRuntimeDeps, InterviewNextAction
from app.agents.tools import register_evidence_tools


@dataclass
class AdjudicationAgentRunResult:
    output: InterviewNextAction
    provider: str | None
    model: str | None
    tool_calls: list[dict[str, Any]]
    retry_count: int = 0


class AdjudicationAgentRunner:
    def __init__(self, model: Any, instructions: str) -> None:
        self.agent = Agent(
            model,
            deps_type=AgentRuntimeDeps,
            output_type=InterviewNextAction,
            instructions=instructions,
        )
        register_evidence_tools(self.agent)

    def run(
        self,
        *,
        deps: AgentRuntimeDeps,
        dynamic_turn_context: dict[str, Any],
        tool_outputs: dict[str, Any] | None = None,
        user_message: str,
        boundary_decision: str,
    ) -> AdjudicationAgentRunResult:
        prompt = json.dumps(
            {
                "dynamic_turn_context": dynamic_turn_context,
                "tool_outputs": dict(tool_outputs or {}),
                "user": user_message,
                "boundary_decision": boundary_decision,
            },
            ensure_ascii=False,
        )
        result = self.agent.run_sync(prompt, deps=deps)
        provider = None
        model = None
        tool_calls: list[dict[str, Any]] = []
        response_count = 0
        for message in result.new_messages():
            if isinstance(message, ai_messages.ModelResponse):
                response_count += 1
                provider = provider or message.provider_name
                model = model or message.model_name
                for part in message.parts:
                    if not isinstance(part, ai_messages.ToolCallPart):
                        continue
                    tool_calls.append(
                        {
                            "tool_name": part.tool_name,
                            "args": part.args_as_dict(),
                            "tool_call_id": part.tool_call_id,
                        }
                    )
                continue
            if not isinstance(message, ai_messages.ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, ai_messages.ToolReturnPart):
                    continue
                for tool_call in tool_calls:
                    if tool_call["tool_call_id"] != part.tool_call_id:
                        continue
                    tool_call["outcome"] = part.outcome
                    break
        return AdjudicationAgentRunResult(
            output=result.output,
            provider=provider,
            model=model,
            tool_calls=tool_calls,
            retry_count=max(response_count - 1, 0),
        )
