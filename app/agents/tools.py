from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.schemas import AgentRuntimeDeps


def register_evidence_tools(agent: Agent[AgentRuntimeDeps, object]) -> None:
    @agent.tool
    def search_evidence(
        ctx: RunContext[AgentRuntimeDeps],
        query: str,
        evidence_type: str | None = None,
        field_path: str | None = None,
        limit: int = 5,
    ) -> object:
        return ctx.deps.retrieval.search_session_evidence(
            ctx.deps.session_id,
            query,
            evidence_type=evidence_type,
            field_path=field_path,
            limit=limit,
        )

    @agent.tool
    def get_evidence_excerpt(
        ctx: RunContext[AgentRuntimeDeps],
        evidence_id: str,
    ) -> object:
        return ctx.deps.evidence.get_evidence_excerpt(evidence_id)

    @agent.tool
    def extract_document_fields(
        ctx: RunContext[AgentRuntimeDeps],
        document_id: str,
        schema_name: str,
    ) -> dict[str, str]:
        return ctx.deps.evidence.extract_document_fields(document_id, schema_name)
