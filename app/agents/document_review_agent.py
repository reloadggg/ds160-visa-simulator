from __future__ import annotations

import json
from typing import Any

from pydantic_ai import Agent

from app.agents.schemas import AgentRuntimeDeps, DocumentReviewResult
from app.agents.tools import register_evidence_tools


class DocumentReviewAgentRunner:
    def __init__(self, model: Any, instructions: str) -> None:
        self.agent = Agent(
            model,
            deps_type=AgentRuntimeDeps,
            output_type=DocumentReviewResult,
            instructions=instructions,
        )
        register_evidence_tools(self.agent)

    def run(
        self,
        *,
        deps: AgentRuntimeDeps,
        dynamic_turn_context: dict[str, Any],
        review_context: dict[str, Any],
        user_message: str,
        boundary_decision: str,
    ) -> DocumentReviewResult:
        prompt = json.dumps(
            {
                "dynamic_turn_context": dynamic_turn_context,
                "review_context": review_context,
                "user": user_message,
                "boundary_decision": boundary_decision,
            },
            ensure_ascii=False,
        )
        return self.agent.run_sync(prompt, deps=deps).output
