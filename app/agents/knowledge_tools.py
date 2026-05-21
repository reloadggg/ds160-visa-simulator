from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.schemas import AgentRuntimeDeps
from app.domain.rag import PolicyKnowledgeSearchResult


def register_policy_knowledge_tools(agent: Agent[AgentRuntimeDeps, object]) -> None:
    @agent.tool
    def search_policy_knowledge(
        ctx: RunContext[AgentRuntimeDeps],
        query: str,
        visa_family: str | None = None,
        country: str | None = None,
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> object:
        if ctx.deps.policy_retrieval is None:
            return PolicyKnowledgeSearchResult.skipped_result(
                query,
                "policy_retrieval_not_configured",
            ).tool_payload()
        result = ctx.deps.policy_retrieval.search_policy(
            query,
            visa_family=visa_family,
            country=country,
            source_types=source_types,
            limit=limit,
        )
        if hasattr(result, "tool_payload"):
            return result.tool_payload()
        return result
