from __future__ import annotations

from typing import Any

from app.agents.schemas import ConsistencyFinding, ScoreProposal
from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState


class ScoreStateBuilder:
    def normalize_findings(
        self,
        findings: list[dict[str, Any] | ConsistencyFinding],
    ) -> list[ConsistencyFinding]:
        return [
            item
            if isinstance(item, ConsistencyFinding)
            else ConsistencyFinding.model_validate(item)
            for item in findings
        ]

    def from_proposal(
        self,
        profile: ApplicantProfile,
        proposal: ScoreProposal,
        scoring_stage: str,
        findings: list[dict[str, Any] | ConsistencyFinding],
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
            normalized_item = self.normalize_missing_evidence_item(item)
            if normalized_item and normalized_item not in score.missing_evidence:
                score.missing_evidence.append(normalized_item)

        score = self.reconcile_profile_evidence(profile, score)
        return self.apply_findings_guards(score, findings, include_gap=False)

    def build_fallback(
        self,
        profile: ApplicantProfile,
        findings: list[dict[str, Any] | ConsistencyFinding],
        scoring_stage: str,
    ) -> ScoreState:
        score = ScoreState.minimal(
            profile_version=profile.profile_version,
            scoring_stage=scoring_stage,
        )
        default_scores = self.default_scores(profile)
        score.category_fit = default_scores["category_fit"]
        score.document_readiness = default_scores["document_readiness"]
        score.narrative_consistency = default_scores["narrative_consistency"]
        score.confidence = default_scores["confidence"]
        score = self.reconcile_profile_evidence(profile, score)
        return self.apply_findings_guards(score, findings, include_gap=True)

    def apply_findings_guards(
        self,
        score: ScoreState,
        findings: list[dict[str, Any] | ConsistencyFinding],
        *,
        include_gap: bool,
    ) -> ScoreState:
        typed_findings = self.normalize_findings(findings)
        for finding in typed_findings:
            if finding.finding_type == "gap":
                if not include_gap:
                    continue
                score.document_readiness = min(score.document_readiness, 40)
                score.narrative_consistency = min(score.narrative_consistency, 55)
                if not any(
                    flag.code == "supporting_evidence_missing"
                    for flag in score.risk_flags
                ):
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

    def default_scores(self, profile: ApplicantProfile) -> dict[str, int]:
        return {
            "category_fit": 60 if profile.visa_intent.get("declared_family") else 30,
            "document_readiness": 70,
            "narrative_consistency": 75,
            "confidence": 65,
        }

    def reconcile_profile_evidence(
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
                flag
                for flag in score.risk_flags
                if flag.code != "supporting_evidence_missing"
            ]
        return score

    def normalize_missing_evidence_item(self, item: str) -> str | None:
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
