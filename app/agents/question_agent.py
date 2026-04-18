from __future__ import annotations

import json
from typing import Any

from pydantic_ai import Agent

from app.agents.schemas import AgentRuntimeDeps, InterviewNextAction
from app.agents.tools import register_evidence_tools


class QuestionAgentRunner:
    def __init__(self, model: Any) -> None:
        self.agent = Agent(
            model,
            deps_type=AgentRuntimeDeps,
            output_type=InterviewNextAction,
            instructions=(
                "你负责生成 DS-160 模拟面谈的下一步助手动作。"
                "Governor 才是最终决策层，你不能自行改变 governor_decision。"
                "如果需要更多证据，requested_documents 必须非空。"
            ),
        )
        register_evidence_tools(self.agent)

    def run(
        self,
        *,
        deps: AgentRuntimeDeps,
        profile_payload: dict[str, Any],
        score_payload: dict[str, Any],
        governor_decision: str,
    ) -> InterviewNextAction:
        prompt = json.dumps(
            {
                "profile": profile_payload,
                "score": score_payload,
                "governor_decision": governor_decision,
            },
            ensure_ascii=False,
        )
        return self.agent.run_sync(prompt, deps=deps).output
