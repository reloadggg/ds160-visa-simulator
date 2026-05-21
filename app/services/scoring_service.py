from __future__ import annotations

from typing import Any

from app.agents.scoring_agent import ScoringAgentRunner
from app.agents.schemas import AgentRuntimeDeps, ConsistencyFinding, ScoreProposal
from app.agents.model_factory import AgentModelFactory
from app.domain.contracts import ApplicantProfile, ScoreState
from app.services.evidence_service import EvidenceService
from app.services.retrieval_service import RetrievalService
from app.services.score_state_builder import ScoreStateBuilder
from app.services.visa_policy_retrieval_service import VisaPolicyRetrievalService


class ScoringService:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db
        self.model_factory = AgentModelFactory()
        self.score_builder = ScoreStateBuilder()

    def propose(
        self,
        profile: ApplicantProfile,
        findings: list[dict[str, Any] | ConsistencyFinding],
        scoring_stage: str,
    ) -> ScoreState:
        typed_findings = self._normalize_findings(findings)
        declared_family = profile.visa_intent.get("declared_family")
        model, runtime = self._build_agent_runtime(scoring_stage, declared_family)

        if model is not None and self.db is not None:
            try:
                proposal = ScoringAgentRunner(
                    model=model,
                    instructions=runtime.get("instructions")
                    or self.model_factory.build_instructions(
                        "scoring_agent",
                        declared_family=declared_family,
                    ),
                ).run(
                    deps=self._build_agent_deps(profile),
                    profile_payload=profile.model_dump(mode="json"),
                    findings=typed_findings,
                )
            except Exception:
                # runtime 或 tool 失败时保守降级，避免把 unknown 误判成否定事实。
                return self._fallback_score(profile, typed_findings, scoring_stage)
            return self._proposal_to_score_state(
                profile,
                proposal,
                scoring_stage,
                typed_findings,
            )

        return self._fallback_score(profile, typed_findings, scoring_stage)

    def _build_agent_runtime(
        self,
        scoring_stage: str,
        declared_family: str | None,
    ) -> tuple[Any | None, dict[str, Any]]:
        try:
            return self.model_factory.build(
                "scoring_agent",
                scoring_stage,
                declared_family=declared_family,
            )
        except TypeError as exc:
            if "declared_family" not in str(exc):
                raise
            return self.model_factory.build("scoring_agent", scoring_stage)

    def _build_agent_deps(self, profile: ApplicantProfile) -> AgentRuntimeDeps:
        return AgentRuntimeDeps(
            session_id=profile.profile_id.removeprefix("profile-"),
            retrieval=RetrievalService(self.db),
            evidence=EvidenceService(self.db),
            policy_retrieval=VisaPolicyRetrievalService(),
        )

    def _normalize_findings(
        self,
        findings: list[dict[str, Any] | ConsistencyFinding],
    ) -> list[ConsistencyFinding]:
        return self.score_builder.normalize_findings(findings)

    def _proposal_to_score_state(
        self,
        profile: ApplicantProfile,
        proposal: ScoreProposal,
        scoring_stage: str,
        findings: list[ConsistencyFinding],
    ) -> ScoreState:
        return self.score_builder.from_proposal(
            profile,
            proposal,
            scoring_stage,
            findings,
        )

    def _fallback_score(
        self,
        profile: ApplicantProfile,
        findings: list[ConsistencyFinding],
        scoring_stage: str,
    ) -> ScoreState:
        return self.score_builder.build_fallback(
            profile,
            findings,
            scoring_stage,
        )
