from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
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
    def evaluate_fixture_file(
        self,
        path: str | Path,
        *,
        max_repeated_template: int = 2,
    ) -> GraphReplayEvalResult:
        fixture = GraphReplayFixture.from_file(path)
        return self.evaluate(
            fixture_id=fixture.fixture_id,
            state=fixture.state,
            events=fixture.events,
            max_repeated_template=max_repeated_template,
        )

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
            self._check_chat_not_blocked_by_gate(state),
            self._check_replay_records_case_memory(state),
            self._check_case_board_records_next_move(state),
            self._check_conflict_specific_clarification(state),
            self._check_high_risk_not_waiting_for_full_package(state),
            self._check_ocr_not_used_for_applicant_image(state),
            self._check_public_claim_citations(state),
            self._check_policy_claims_use_official_citations(state),
            self._check_case_claims_use_case_evidence_citations(state),
            self._check_guard_failure_reason(state),
            self._check_failure_diagnostics(state, events),
            self._check_high_risk_explains_what_why_next(state),
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

    def _check_chat_not_blocked_by_gate(self, state: DS160GraphState) -> GraphReplayCheck:
        case_state = self._payload(state.case_state)
        if not case_state.get("assert_chat_starts_without_materials"):
            return GraphReplayCheck("chat_not_blocked_by_gate", True)
        response = state.final_response
        if response is None:
            return GraphReplayCheck("chat_not_blocked_by_gate", False, "missing final response")
        if response.decision == "need_more_evidence":
            return GraphReplayCheck(
                "chat_not_blocked_by_gate",
                False,
                "no-material replay should not be blocked by Gate readiness",
            )
        message = response.assistant_message.casefold()
        blocked_markers = ("must upload", "材料齐", "补齐材料", "waiting_for_parse")
        if any(marker.casefold() in message for marker in blocked_markers):
            return GraphReplayCheck(
                "chat_not_blocked_by_gate",
                False,
                "assistant message still frames chat as material-gated",
            )
        return GraphReplayCheck("chat_not_blocked_by_gate", True)

    def _check_replay_records_case_memory(self, state: DS160GraphState) -> GraphReplayCheck:
        case_state = self._payload(state.case_state)
        if not case_state.get("assert_case_memory"):
            return GraphReplayCheck("case_memory_recorded", True)
        case_memory = self._payload(case_state.get("case_memory"))
        claims = self._list_payload(case_memory.get("claims"))
        evidence_cards = self._list_payload(case_memory.get("evidence_cards"))
        if not claims or not evidence_cards:
            return GraphReplayCheck(
                "case_memory_recorded",
                False,
                "replay must record claims and evidence cards",
            )
        return GraphReplayCheck("case_memory_recorded", True)

    def _check_case_board_records_next_move(self, state: DS160GraphState) -> GraphReplayCheck:
        case_state = self._payload(state.case_state)
        if not case_state.get("assert_case_board_next_move"):
            return GraphReplayCheck("case_board_next_move", True)
        case_board = self._payload(case_state.get("case_board"))
        latest_material = self._payload(case_board.get("latest_material"))
        next_move = self._payload(case_board.get("next_move"))
        if not latest_material:
            return GraphReplayCheck(
                "case_board_next_move",
                False,
                "case board missing latest material",
            )
        if not next_move:
            return GraphReplayCheck(
                "case_board_next_move",
                False,
                "case board missing next move",
            )
        return GraphReplayCheck("case_board_next_move", True)

    def _check_conflict_specific_clarification(
        self,
        state: DS160GraphState,
    ) -> GraphReplayCheck:
        case_state = self._payload(state.case_state)
        if not case_state.get("assert_conflict_specific_clarification"):
            return GraphReplayCheck("conflict_specific_clarification", True)
        response = state.final_response
        if response is None:
            return GraphReplayCheck(
                "conflict_specific_clarification",
                False,
                "missing final response",
            )
        conflicts = self._list_payload(
            self._payload(case_state.get("case_memory")).get("conflicts")
        )
        if not conflicts:
            return GraphReplayCheck(
                "conflict_specific_clarification",
                False,
                "fixture expected a case-memory conflict",
            )
        if response.next_safe_action != "ask_clarification":
            return GraphReplayCheck(
                "conflict_specific_clarification",
                False,
                "conflict replay should ask a clarification",
            )
        message = response.assistant_message.casefold()
        conflict_terms = ("self", "parents", "fund", "资金", "父母", "自费")
        if not any(term in message for term in conflict_terms):
            return GraphReplayCheck(
                "conflict_specific_clarification",
                False,
                "clarification does not mention the concrete conflict",
            )
        return GraphReplayCheck("conflict_specific_clarification", True)

    def _check_high_risk_not_waiting_for_full_package(
        self,
        state: DS160GraphState,
    ) -> GraphReplayCheck:
        case_state = self._payload(state.case_state)
        if not case_state.get("assert_high_risk_without_full_package"):
            return GraphReplayCheck("high_risk_without_full_package", True)
        response = state.final_response
        if response is None:
            return GraphReplayCheck(
                "high_risk_without_full_package",
                False,
                "missing final response",
            )
        if response.decision not in {"high_risk_review", "simulated_refusal"}:
            return GraphReplayCheck(
                "high_risk_without_full_package",
                False,
                f"unexpected decision={response.decision}",
            )
        if response.requested_documents:
            return GraphReplayCheck(
                "high_risk_without_full_package",
                False,
                "high-risk simulation should not require a complete material package",
            )
        return GraphReplayCheck("high_risk_without_full_package", True)

    def _check_ocr_not_used_for_applicant_image(
        self,
        state: DS160GraphState,
    ) -> GraphReplayCheck:
        case_state = self._payload(state.case_state)
        if not case_state.get("assert_ocr_not_used"):
            return GraphReplayCheck("ocr_not_used_for_applicant_image", True)
        documents = self._list_payload(case_state.get("documents"))
        for document in documents:
            artifact = self._payload(document.get("artifact"))
            if artifact.get("source_type") != "image":
                continue
            parser_name = str(
                artifact.get("parser_name")
                or artifact.get("source_type")
                or ""
            ).casefold()
            raw_text = str(document.get("raw_text") or "").casefold()
            if "ocr" in parser_name or "tesseract" in parser_name or "ocr" in raw_text:
                return GraphReplayCheck(
                    "ocr_not_used_for_applicant_image",
                    False,
                    "image replay still carries OCR parser/text markers",
                )
        return GraphReplayCheck("ocr_not_used_for_applicant_image", True)

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

    def _check_policy_claims_use_official_citations(
        self,
        state: DS160GraphState,
    ) -> GraphReplayCheck:
        response = state.final_response
        if response is None:
            return GraphReplayCheck("official_policy_citation", False, "missing final response")
        citations = {citation.citation_id: citation for citation in state.citation_bundle.citations}
        for claim in response.public_claims:
            if claim.claim_type != "official_policy":
                continue
            for citation_id in claim.citation_ids:
                citation = citations.get(citation_id)
                if citation is None or citation.source_type != "official_policy":
                    return GraphReplayCheck(
                        "official_policy_citation",
                        False,
                        f"official policy claim {claim.claim_id} lacks official citation",
                    )
        return GraphReplayCheck("official_policy_citation", True)

    def _check_case_claims_use_case_evidence_citations(
        self,
        state: DS160GraphState,
    ) -> GraphReplayCheck:
        response = state.final_response
        if response is None:
            return GraphReplayCheck("case_evidence_citation", False, "missing final response")
        citations = {citation.citation_id: citation for citation in state.citation_bundle.citations}
        for claim in response.public_claims:
            if claim.claim_type != "case_evidence":
                continue
            for citation_id in claim.citation_ids:
                citation = citations.get(citation_id)
                if citation is None or citation.source_type != "case_evidence":
                    return GraphReplayCheck(
                        "case_evidence_citation",
                        False,
                        f"case evidence claim {claim.claim_id} lacks case citation",
                    )
        return GraphReplayCheck("case_evidence_citation", True)

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

    def _check_failure_diagnostics(
        self,
        state: DS160GraphState,
        events: list[GraphEvent],
    ) -> GraphReplayCheck:
        response = state.final_response
        if response is None:
            return GraphReplayCheck("failure_diagnostics", False, "missing final response")
        if response.guard_status == "passed" and response.assistant_message_author == "adjudication_agent":
            return GraphReplayCheck("failure_diagnostics", True)
        diagnostic_events = [
            event
            for event in events
            if event.event_type
            in {"adjudication_completed", "guard_completed", "fallback_used", "error"}
        ]
        if not diagnostic_events:
            return GraphReplayCheck(
                "failure_diagnostics",
                False,
                "fallback or guard failure has no diagnostic event",
            )
        for event in diagnostic_events:
            payload = event.payload
            if payload.get("fallback_reason") or payload.get("guard_result") or payload.get("error_code"):
                return GraphReplayCheck("failure_diagnostics", True)
        return GraphReplayCheck(
            "failure_diagnostics",
            False,
            "diagnostic events lack fallback_reason, guard_result, or error_code",
        )

    def _check_high_risk_explains_what_why_next(
        self,
        state: DS160GraphState,
    ) -> GraphReplayCheck:
        response = state.final_response
        if response is None:
            return GraphReplayCheck("high_risk_what_why_next", False, "missing final response")
        if response.decision != "high_risk_review":
            return GraphReplayCheck("high_risk_what_why_next", True)
        message = response.assistant_message.strip()
        has_citation = any(
            claim.claim_type in {"case_evidence", "official_policy"} and claim.citation_ids
            for claim in response.public_claims
        )
        asks_next = "？" in message or "?" in message or response.next_safe_action in {
            "ask_clarification",
            "request_document",
            "manual_review",
        }
        explains_what = bool(message) and not self._is_generic_risk_message(message)
        if has_citation and asks_next and explains_what:
            return GraphReplayCheck("high_risk_what_why_next", True)
        return GraphReplayCheck(
            "high_risk_what_why_next",
            False,
            "high-risk response must include concrete what, cited why, and next action",
        )

    def _is_generic_risk_message(self, message: str) -> bool:
        normalized = message.strip().lower()
        generic_messages = {
            "this case needs additional review before the interview can continue.",
            "当前案例需要进一步审核。",
            "你的情况需要进一步核对。",
        }
        return normalized in generic_messages

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

    def _payload(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _list_payload(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]


class GraphReplayFixture:
    def __init__(
        self,
        *,
        fixture_id: str,
        state: DS160GraphState,
        events: list[GraphEvent],
        expected: dict[str, Any] | None = None,
    ) -> None:
        self.fixture_id = fixture_id
        self.state = state
        self.events = events
        self.expected = expected or {}

    @classmethod
    def from_file(cls, path: str | Path) -> "GraphReplayFixture":
        import json

        fixture_path = Path(path)
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture_id = str(payload.get("fixture_id") or fixture_path.stem)
        return cls(
            fixture_id=fixture_id,
            state=DS160GraphState.model_validate(payload["state"]),
            events=[
                GraphEvent.model_validate(event)
                for event in payload.get("events", [])
            ],
            expected=payload.get("expected", {}),
        )
