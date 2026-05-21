from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from app.agents.knowledge_tools import register_policy_knowledge_tools
from app.agents.schemas import AgentRuntimeDeps
from app.domain.rag import PolicyKnowledgeHit, PolicyKnowledgeSearchResult


class StubPolicyRetrieval:
    def __init__(self) -> None:
        self.calls = []

    def search_policy(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        hit = PolicyKnowledgeHit(
            chunk_id="chunk-1",
            source_id="src-1",
            source_type="federal_official",
            title="DS-160",
            url="https://example.test/ds160",
            excerpt="DS-160 official guidance",
            final_score=0.8,
        )
        return PolicyKnowledgeSearchResult.from_hits(query=query, hits=[hit])


def test_policy_knowledge_tool_uses_injected_retrieval() -> None:
    policy_retrieval = StubPolicyRetrieval()
    deps = AgentRuntimeDeps(
        session_id="sess-1",
        retrieval=object(),
        evidence=object(),
        policy_retrieval=policy_retrieval,
    )
    agent = Agent(
        TestModel(call_tools=["search_policy_knowledge"]),
        deps_type=AgentRuntimeDeps,
    )
    register_policy_knowledge_tools(agent)

    result = agent.run_sync("查政策", deps=deps)

    assert '"search_policy_knowledge"' in result.output
    assert len(policy_retrieval.calls) == 1
    assert policy_retrieval.calls[0]["query"]


def test_policy_knowledge_tool_skips_without_injected_retrieval() -> None:
    deps = AgentRuntimeDeps(
        session_id="sess-1",
        retrieval=object(),
        evidence=object(),
    )
    agent = Agent(
        TestModel(call_tools=["search_policy_knowledge"]),
        deps_type=AgentRuntimeDeps,
    )
    register_policy_knowledge_tools(agent)

    result = agent.run_sync("查政策", deps=deps)

    assert "policy_retrieval_not_configured" in result.output
