from __future__ import annotations

import json
from typing import Any

from pydantic_ai import Agent

from app.agents.schemas import AgentRuntimeDeps, ConsistencyFinding, ScoreProposal
from app.agents.tools import register_evidence_tools


class ScoringAgentRunner:
    def __init__(self, model: Any) -> None:
        self.agent = Agent(
            model,
            deps_type=AgentRuntimeDeps,
            output_type=ScoreProposal,
            instructions=(
                "你负责给单轮 DS-160 面谈状态打分。"
                "缺失证据必须保持 unknown，不要把未知推断成否定事实。"
                "凡是涉及文档充分性、资金来源、材料完备度的判断，先调用证据工具再决定。"
                "高风险且 confirmed 的 risk_flags 必须附带 evidence_refs。"
            ),
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
