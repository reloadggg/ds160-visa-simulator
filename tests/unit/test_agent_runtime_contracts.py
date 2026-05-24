from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.agent_runtime import (
    CitationBundle,
    CitationRef,
    DS160GraphState,
    GraphEvent,
    GraphRunResult,
    GroundingCheckResult,
    GroundingViolation,
    PublicClaim,
    RetryBudget,
)


def _citation(citation_id: str = "cite-i20-school") -> CitationRef:
    return CitationRef(
        citation_id=citation_id,
        source_type="case_evidence",
        source_authority="user_provided",
        source_id="source-session-1",
        document_id="doc-i20",
        chunk_id="chunk-school",
        span_start=10,
        span_end=84,
        content_hash="sha256:abc123",
        quote_or_summary="I-20 lists Example University as the school.",
        retrieved_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
        published_or_effective_date=date(2026, 5, 1),
        staleness_policy="stable",
        claim_ids=["claim-school"],
    )


def test_graph_state_accepts_cited_user_visible_claim() -> None:
    citation = _citation()
    final_response = GraphRunResult(
        assistant_message=(
            "你的说法是纽约大学，但 I-20 显示 Example University，请解释这个差异。"
        ),
        assistant_message_author="adjudication_agent",
        decision="high_risk_review",
        public_claims=[
            PublicClaim(
                claim_id="claim-school",
                claim_type="case_evidence",
                text="I-20 显示 Example University。",
                citation_ids=[citation.citation_id],
            )
        ],
        used_citation_ids=[citation.citation_id],
        guard_status="passed",
        next_safe_action="ask_clarification",
    )

    state = DS160GraphState(
        session_id="sess-1",
        run_id="run-1",
        citation_bundle=CitationBundle(citations=[citation]),
        final_response=final_response,
    )

    assert state.final_response is not None
    assert state.final_response.assistant_message_author == "adjudication_agent"
    assert state.citation_bundle.citation_ids == {"cite-i20-school"}


def test_official_policy_claim_requires_citation() -> None:
    with pytest.raises(ValidationError, match="official_policy claims require citation"):
        PublicClaim(
            claim_id="claim-policy",
            claim_type="official_policy",
            text="F-1 需要 I-20。",
            citation_ids=[],
        )


def test_case_evidence_claim_requires_citation() -> None:
    with pytest.raises(ValidationError, match="case_evidence claims require citation"):
        PublicClaim(
            claim_id="claim-case",
            claim_type="case_evidence",
            text="录取信显示 Example University。",
            citation_ids=[],
        )


def test_product_guidance_can_be_uncited() -> None:
    claim = PublicClaim(
        claim_id="claim-guidance",
        claim_type="product_guidance",
        text="建议下一轮优先练习资金来源说明。",
    )

    assert claim.citation_ids == []


def test_final_response_cannot_reference_unknown_citation() -> None:
    known = _citation("cite-known")
    final_response = GraphRunResult(
        assistant_message="请解释材料里的学校差异。",
        decision="high_risk_review",
        public_claims=[
            PublicClaim(
                claim_id="claim-school",
                claim_type="case_evidence",
                text="I-20 显示 Example University。",
                citation_ids=["cite-missing"],
            )
        ],
        used_citation_ids=["cite-missing"],
        next_safe_action="ask_clarification",
    )

    with pytest.raises(ValidationError, match="unknown citation ids"):
        DS160GraphState(
            session_id="sess-1",
            run_id="run-1",
            citation_bundle=CitationBundle(citations=[known]),
            final_response=final_response,
        )


def test_guard_failure_requires_violation_and_incomplete_reason() -> None:
    with pytest.raises(ValidationError, match="at least one violation"):
        GroundingCheckResult(status="failed")

    guard_result = GroundingCheckResult(
        status="failed",
        violations=[
            GroundingViolation(
                code="missing_case_evidence",
                detail="材料冲突断言没有 case evidence citation。",
                claim_id="claim-school",
            )
        ],
    )

    with pytest.raises(ValidationError, match="requires incomplete_reason"):
        GraphRunResult(
            assistant_message="请解释材料里的学校差异。",
            decision="high_risk_review",
            guard_status=guard_result.status,
            next_safe_action="ask_clarification",
        )


def test_retry_budget_raises_when_exhausted() -> None:
    budget = RetryBudget(max_llm_calls=1, llm_calls_used=1)

    with pytest.raises(ValueError, match="LLM call retry budget exhausted"):
        budget.consume_llm_call()


def test_graph_events_validate_terminal_payloads() -> None:
    with pytest.raises(ValidationError, match="final_response"):
        GraphEvent(event_type="final", run_id="run-1", sequence=7)

    with pytest.raises(ValidationError, match="error_code"):
        GraphEvent(event_type="error", run_id="run-1", sequence=8)

    final_event = GraphEvent(
        event_type="final",
        run_id="run-1",
        sequence=9,
        payload={"final_response": {"assistant_message": "ok"}},
    )

    assert final_event.schema_version == "agent-runtime.v1"
