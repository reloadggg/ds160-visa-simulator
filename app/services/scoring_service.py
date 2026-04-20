from __future__ import annotations

from typing import Any

from app.agents.scoring_agent import ScoringAgentRunner
from app.agents.schemas import AgentRuntimeDeps, ConsistencyFinding, ScoreProposal
from app.agents.model_factory import AgentModelFactory
from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState
from app.services.evidence_service import EvidenceService
from app.services.retrieval_service import RetrievalService


class ScoringService:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db
        self.model_factory = AgentModelFactory()

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
            score = self._proposal_to_score_state(profile, proposal, scoring_stage)
            score = self._reconcile_profile_evidence(profile, score)
            return self._apply_findings_guards(score, typed_findings, include_gap=False)

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
        )

    def _normalize_findings(
        self,
        findings: list[dict[str, Any] | ConsistencyFinding],
    ) -> list[ConsistencyFinding]:
        return [
            item
            if isinstance(item, ConsistencyFinding)
            else ConsistencyFinding.model_validate(item)
            for item in findings
        ]

    def _proposal_to_score_state(
        self,
        profile: ApplicantProfile,
        proposal: ScoreProposal,
        scoring_stage: str,
    ) -> ScoreState:
        score = ScoreState.minimal(
            profile_version=profile.profile_version,
            scoring_stage=scoring_stage,
        )
        score.category_fit = proposal.category_fit
        score.document_readiness = proposal.document_readiness
        score.narrative_consistency = proposal.narrative_consistency
        score.confidence = proposal.confidence
        score.risk_flags = [
            RiskFlag(
                code=item.code,
                severity=item.severity,
                status=item.status,
                evidence_refs=list(item.evidence_refs),
            )
            for item in proposal.risk_flags
        ]

        for item in proposal.missing_evidence:
            normalized_item = self._normalize_missing_evidence_item(item)
            if normalized_item and normalized_item not in score.missing_evidence:
                score.missing_evidence.append(normalized_item)
        return score

    def _fallback_score(
        self,
        profile: ApplicantProfile,
        findings: list[ConsistencyFinding],
        scoring_stage: str,
    ) -> ScoreState:
        score = ScoreState.minimal(
            profile_version=profile.profile_version,
            scoring_stage=scoring_stage,
        )
        default_scores = self._default_scores(profile)
        score.category_fit = default_scores["category_fit"]
        score.document_readiness = default_scores["document_readiness"]
        score.narrative_consistency = default_scores["narrative_consistency"]
        score.confidence = default_scores["confidence"]
        score = self._reconcile_profile_evidence(profile, score)
        return self._apply_findings_guards(score, findings, include_gap=True)

    def _apply_findings_guards(
        self,
        score: ScoreState,
        findings: list[ConsistencyFinding],
        *,
        include_gap: bool,
    ) -> ScoreState:
        for finding in findings:
            if finding.finding_type == "gap":
                if not include_gap:
                    continue
                score.document_readiness = min(score.document_readiness, 40)
                score.narrative_consistency = min(score.narrative_consistency, 55)
                if not any(flag.code == "supporting_evidence_missing" for flag in score.risk_flags):
                    score.risk_flags.append(
                        RiskFlag(
                            code="supporting_evidence_missing",
                            severity="medium",
                            status="supported",
                            evidence_refs=[],
                        )
                    )
                if "funding_proof" not in score.missing_evidence:
                    score.missing_evidence.append("funding_proof")
                continue

            score.document_readiness = min(score.document_readiness, 30)
            score.narrative_consistency = min(score.narrative_consistency, 15)
            score.confidence = max(score.confidence, 85)
            if not any(flag.code == finding.finding_type for flag in score.risk_flags):
                score.risk_flags.append(
                    RiskFlag(
                        code=finding.finding_type,
                        severity=finding.severity,
                        status=finding.status,
                        evidence_refs=list(finding.evidence_refs),
                    )
                )
        return score

    def _default_scores(self, profile: ApplicantProfile) -> dict[str, int]:
        return {
            "category_fit": 60 if profile.visa_intent.get("declared_family") else 30,
            "document_readiness": 70,
            "narrative_consistency": 75,
            "confidence": 65,
        }

    def _reconcile_profile_evidence(
        self,
        profile: ApplicantProfile,
        score: ScoreState,
    ) -> ScoreState:
        funding_refs = profile.field_provenance.get("/funding/primary_source")
        if funding_refs and funding_refs.evidence_refs:
            score.missing_evidence = [
                item for item in score.missing_evidence if item != "funding_proof"
            ]
            score.risk_flags = [
                flag for flag in score.risk_flags if flag.code != "supporting_evidence_missing"
            ]
        return score

    def _normalize_missing_evidence_item(self, item: str) -> str | None:
        normalized = item.strip().lower().replace("-", "_")
        funding_markers = (
            "funding",
            "sponsor",
            "bank statement",
            "bank_statement",
            "affidavit",
            "liquid funds",
            "liquid_funds",
            "financial sponsor",
            "financial_sponsor",
            "tuition",
        )
        if normalized == "funding_proof":
            return "funding_proof"
        if normalized == "travel_history":
            return "travel_history"
        if any(marker in normalized for marker in funding_markers):
            return "funding_proof"
        if "travel history" in normalized or "travel_history" in normalized:
            return "travel_history"
        return None
