from __future__ import annotations

import json
from typing import Any

from pydantic_ai import Agent

from app.agents.schemas import AgentRuntimeDeps, ConsistencyFinding, ScoreProposal
from app.agents.tools import register_evidence_tools


class ScoringAgentRunner:
    def __init__(self, model: Any, instructions: str) -> None:
        self.agent = Agent(
            model,
            deps_type=AgentRuntimeDeps,
            output_type=ScoreProposal,
            instructions=instructions,
        )
        register_evidence_tools(self.agent)

    def run(
        self,
        *,
        deps: AgentRuntimeDeps,
        profile_payload: dict[str, Any],
        findings: list[ConsistencyFinding],
    ) -> ScoreProposal:
        prompt = json.dumps(
            {
                "profile": profile_payload,
                "findings": [item.model_dump(mode="json") for item in findings],
            },
            ensure_ascii=False,
        )
        return self.agent.run_sync(prompt, deps=deps).output
