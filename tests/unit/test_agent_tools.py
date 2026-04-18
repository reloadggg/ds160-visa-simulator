from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.models.test import TestModel

from app.agents.schemas import AgentRuntimeDeps, EvidenceExcerpt, EvidenceHit
from app.agents.tools import register_evidence_tools


class StubRetrievalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search_session_evidence(
        self,
        session_id: str,
        query: str,
        *,
        evidence_type: str | None = None,
        field_path: str | None = None,
        limit: int = 5,
    ) -> list[EvidenceHit]:
        self.calls.append(
            {
                "session_id": session_id,
                "query": query,
                "evidence_type": evidence_type,
                "field_path": field_path,
                "limit": limit,
            }
        )
        return [
            EvidenceHit(
                evidence_id="evi-1",
                document_id="doc-1",
                chunk_id="chunk-1",
                evidence_type="funding_proof",
                field_path="/funding/primary_source",
                excerpt="Parent sponsor bank statement",
                filename="funding_proof.txt",
                source_type="text",
                score=3.0,
            )
        ]


class StubEvidenceService:
    def __init__(self) -> None:
        self.excerpt_calls: list[str] = []
        self.field_calls: list[tuple[str, str]] = []

    def get_evidence_excerpt(self, evidence_id: str) -> EvidenceExcerpt | None:
        self.excerpt_calls.append(evidence_id)
        return EvidenceExcerpt(
            evidence_id=evidence_id,
            document_id="doc-1",
            chunk_id="chunk-1",
            excerpt="Parent sponsor bank statement",
            filename="funding_proof.txt",
            source_type="text",
        )

    def extract_document_fields(self, document_id: str, schema_name: str) -> dict[str, str]:
        self.field_calls.append((document_id, schema_name))
        return {"primary_source": "parents"}


def test_register_evidence_tools_exposes_and_runs_tools() -> None:
    retrieval = StubRetrievalService()
    evidence = StubEvidenceService()
    deps = AgentRuntimeDeps(
        session_id="sess-1",
        retrieval=retrieval,
        evidence=evidence,
    )
    agent = Agent(TestModel(call_tools=["search_evidence"]), deps_type=AgentRuntimeDeps)

    register_evidence_tools(agent)

    with capture_run_messages() as messages:
        result = agent.run_sync("查找证据", deps=deps)

    assert '"search_evidence"' in result.output
    assert len(retrieval.calls) == 1
    assert retrieval.calls[0]["session_id"] == "sess-1"
    assert retrieval.calls[0]["query"]
    assert evidence.excerpt_calls == []
    assert evidence.field_calls == []
    assert any(
        getattr(part, "tool_name", None) == "search_evidence"
        for message in messages
        for part in getattr(message, "parts", [])
    )
