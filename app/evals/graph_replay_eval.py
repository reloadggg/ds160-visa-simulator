from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.agent_runtime import DS160GraphState, GraphEvent, PublicClaim


@dataclass
class GraphReplayCheck:
    name: str
    passed: bool
    detail: str | None = None


@dataclass
class GraphReplayEvalResult:
    fixture_id: str
    run_id: str
    passed: bool
    checks: list[GraphReplayCheck] = field(default_factory=list)

    @property
    def failed_checks(self) -> list[GraphReplayCheck]:
        return [check for check in self.checks if not check.passed]

    def model_dump(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "run_id": self.run_id,
            "passed": self.passed,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "detail": check.detail,
                }
                for check in self.checks
            ],
            "failed_checks": [
                {
                    "name": check.name,
                    "detail": check.detail,
                }
                for check in self.failed_checks
            ],
        }


class GraphReplayEvaluator:
    def evaluate(
        self,
        *,
        fixture_id: str,
        state: DS160GraphState,
        events: list[GraphEvent],
        max_repeated_template: int = 2,
    ) -> GraphReplayEvalResult:
        checks = [
            self._check_final_response_author(state),
            self._check_final_event(events),
            self._check_public_claim_citations(state),
            self._check_guard_failure_reason(state),
            self._check_repeated_templates(
                state,
                max_repeated_template=max_repeated_template,
            ),
        ]
        return GraphReplayEvalResult(
            fixture_id=fixture_id,
            run_id=state.run_id,
            passed=all(check.passed for check in checks),
            checks=checks,
        )

    def _check_final_response_author(self, state: DS160GraphState) -> GraphReplayCheck:
        response = state.final_response
        if response is None:
            return GraphReplayCheck(
                "final_response_author",
                False,
                "missing final response",
            )
        if response.assistant_message_author != "adjudication_agent":
            return GraphReplayCheck(
                "final_response_author",
                response.assistant_message_author == "deterministic_safe_fallback",
                f"unexpected assistant_message_author={response.assistant_message_author}",
            )
        return GraphReplayCheck("final_response_author", True)

    def _check_final_event(self, events: list[GraphEvent]) -> GraphReplayCheck:
        final_events = [event for event in events if event.event_type == "final"]
        if len(final_events) != 1:
            return GraphReplayCheck(
                "single_final_event",
                False,
                f"expected exactly one final event, got {len(final_events)}",
            )
        if not final_events[0].payload.get("final_response"):
            return GraphReplayCheck(
                "single_final_event",
                False,
                "final event missing final_response payload",
            )
        return GraphReplayCheck("single_final_event", True)

    def _check_public_claim_citations(self, state: DS160GraphState) -> GraphReplayCheck:
        response = state.final_response
        if response is None:
            return GraphReplayCheck(
                "public_claim_citations",
                False,
                "missing final response",
            )
        citation_ids = state.citation_bundle.citation_ids
        for claim in response.public_claims:
            missing = self._missing_claim_citations(claim, citation_ids)
            if missing:
                return GraphReplayCheck(
                    "public_claim_citations",
                    False,
                    (
                        f"claim {claim.claim_id} references missing citation ids: "
                        f"{sorted(missing)}"
                    ),
                )
        return GraphReplayCheck("public_claim_citations", True)

    def _missing_claim_citations(
        self,
        claim: PublicClaim,
        known_citation_ids: set[str],
    ) -> set[str]:
        return set(claim.citation_ids) - known_citation_ids

    def _check_guard_failure_reason(self, state: DS160GraphState) -> GraphReplayCheck:
        response = state.final_response
        if response is None:
            return GraphReplayCheck("guard_failure_reason", False, "missing final response")
        if response.guard_status == "passed":
            return GraphReplayCheck("guard_failure_reason", True)
        if response.incomplete_reason is None:
            return GraphReplayCheck(
                "guard_failure_reason",
                False,
                "guard failure missing incomplete_reason",
            )
        return GraphReplayCheck("guard_failure_reason", True)

    def _check_repeated_templates(
        self,
        state: DS160GraphState,
        *,
        max_repeated_template: int,
    ) -> GraphReplayCheck:
        messages = [
            item.get("content")
            for item in state.case_state.get("recent_assistant_messages", [])
            if isinstance(item, dict)
        ]
        response = state.final_response
        if response is not None:
            messages.append(response.assistant_message)
        normalized = [item.strip() for item in messages if isinstance(item, str) and item.strip()]
        if not normalized:
            return GraphReplayCheck("repeated_template", True)
        current = normalized[-1]
        repeated_count = 0
        for message in reversed(normalized):
            if message != current:
                break
            repeated_count += 1
        if repeated_count > max_repeated_template:
            return GraphReplayCheck(
                "repeated_template",
                False,
                f"message repeated {repeated_count} times",
            )
        return GraphReplayCheck("repeated_template", True)
