from __future__ import annotations

import json
from typing import Any

from pydantic_ai import Agent

from app.agents.schemas import AgentRuntimeDeps, ExtractorOutput
from app.agents.tools import register_evidence_tools


class ExtractorAgentRunner:
    def __init__(self, model: Any) -> None:
        self.agent = Agent(
            model,
            deps_type=AgentRuntimeDeps,
            output_type=ExtractorOutput,
            instructions=(
                "你负责从单轮 DS-160 用户消息中提取结构化字段更新。"
                "不要把 unknown 误判成否定事实。"
                "如果要做依赖文档的判断，先调用证据工具。"
            ),
        )
        register_evidence_tools(self.agent)

    def run(
        self,
        *,
        deps: AgentRuntimeDeps,
        message_text: str,
        profile_payload: dict[str, Any],
    ) -> ExtractorOutput:
        prompt = json.dumps(
            {
                "message_text": message_text,
                "profile": profile_payload,
            },
            ensure_ascii=False,
        )
        return self.agent.run_sync(prompt, deps=deps).output
