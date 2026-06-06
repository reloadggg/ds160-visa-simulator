from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    CaseMemorySnapshotRecord,
    DocumentRecord,
    SessionTurnRecord,
    utc_now_naive,
)
from app.domain.case_memory import (
    CaseClaim,
    CaseConflict,
    CaseConflictResolution,
    CaseMemorySnapshot,
    EvidenceCard,
    InterviewNextMove,
    MaterialUnderstandingJob,
    MaterialUnderstandingResult,
    ProofPoint,
)
from app.repositories.document_repo import DocumentRepository
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository


CASE_MEMORY_RESULT_KEY = "material_understanding_result"
CASE_MEMORY_JOB_KEY = "material_understanding_job"
CASE_MEMORY_USER_CLAIMS_KEY = "case_memory_claims"
CASE_MEMORY_USER_EVIDENCE_KEY = "case_memory_evidence_cards"
CASE_MEMORY_RESOLVED_CONFLICTS_KEY = "case_memory_resolved_conflicts"
CASE_MEMORY_TOMBSTONE_KEY = "case_memory_tombstone"
INTERNAL_DEBUG_METADATA_KEYS = {
    "expected_findings",
    "synthetic_bundle_id",
    "debug_bundle_scenario",
    "debug_bundle_scenario_label",
    "scenario_label",
    "debug_fill_scenario",
    "debug_fill_scenario_label",
}


class CaseMemoryService:
    """Persist and project AI-native case memory through document artifacts."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.sessions = SessionRepository(db)
        self.turns = SessionTurnRepository(db)

    def upsert_material_understanding(
        self,
        *,
        document_id: str,
        job: MaterialUnderstandingJob,
    ) -> CaseMemorySnapshot:
        document = self.documents.get_document(document_id)
        if document is None:
            raise LookupError(f"Document not found: {document_id}")

        artifact = dict(document.artifact_json or {})
        artifact[CASE_MEMORY_JOB_KEY] = job.model_dump(mode="json", exclude_none=True)
        artifact["understanding_status"] = job.status
        if job.result is not None:
            artifact[CASE_MEMORY_RESULT_KEY] = job.result.model_dump(mode="json")
            artifact["evidence_cards"] = [
                item.model_dump(mode="json") for item in job.result.evidence_cards
            ]
            artifact["case_board_delta"] = self._case_board_delta(
                document=document,
                result=job.result,
                status=job.status,
            )
        else:
            artifact["understanding_error"] = {
                "code": job.error_code,
                "message": job.error_message,
            }
            artifact["case_board_delta"] = self._unavailable_delta(
                document=document,
                job=job,
            )

        document.artifact_json = artifact
        self.documents.save_document(document)
        self.db.flush()
        snapshot = self.build_snapshot(document.session_id)
        self._persist_snapshot(document.session_id, snapshot)
        return snapshot

    def add_user_turn_claims(
        self,
        *,
        session_id: str,
        turn_id: str,
        claims: list[CaseClaim],
    ) -> CaseMemorySnapshot:
        turn = self.db.get(SessionTurnRecord, turn_id)
        if turn is None or turn.session_id != session_id:
            raise LookupError(f"Turn not found: {session_id}/{turn_id}")
        if turn.role != "user":
            raise ValueError("case memory user claims must attach to a user turn")

        metadata = dict(turn.metadata_json or {})
        existing_claims = [
            CaseClaim.model_validate(item)
            for item in _list_payload(metadata.get(CASE_MEMORY_USER_CLAIMS_KEY))
        ]
        existing_evidence = [
            EvidenceCard.model_validate(item)
            for item in _list_payload(metadata.get(CASE_MEMORY_USER_EVIDENCE_KEY))
        ]

        claims_by_id = {claim.claim_id: claim for claim in existing_claims}
        evidence_by_id = {
            evidence.evidence_id: evidence for evidence in existing_evidence
        }
        for claim in claims:
            normalized_claim = claim.model_copy(update={"status": "stated"})
            evidence_id = self._user_turn_evidence_id(turn_id, normalized_claim.claim_id)
            evidence = EvidenceCard(
                evidence_id=evidence_id,
                source_type="user_turn",
                excerpt=turn.content,
                claim_refs=[normalized_claim.claim_id],
                confidence=normalized_claim.confidence,
                metadata={
                    "turn_id": turn.turn_id,
                    "turn_index": turn.turn_index,
                    "source": turn.source,
                },
            )
            claims_by_id[normalized_claim.claim_id] = normalized_claim
            evidence_by_id[evidence.evidence_id] = evidence

        metadata[CASE_MEMORY_USER_CLAIMS_KEY] = [
            item.model_dump(mode="json")
            for item in sorted(claims_by_id.values(), key=lambda item: item.claim_id)
        ]
        metadata[CASE_MEMORY_USER_EVIDENCE_KEY] = [
            item.model_dump(mode="json")
            for item in sorted(
                evidence_by_id.values(),
                key=lambda item: item.evidence_id,
            )
        ]
        turn.metadata_json = metadata
        self.db.add(turn)
        self.db.flush()
        snapshot = self.build_snapshot(session_id)
        self._persist_snapshot(session_id, snapshot)
        return snapshot

    def extract_explicit_user_turn_claims(
        self,
        *,
        turn_id: str,
        message_text: str,
    ) -> list[CaseClaim]:
        funding_source = self._explicit_funding_source(message_text)
        if funding_source is None:
            return []
        return [
            CaseClaim(
                claim_id=f"claim-{turn_id}-funding-primary-source",
                field_path="/funding/primary_source",
                value=funding_source,
                status="stated",
                confidence=0.72,
                metadata={
                    "source": "explicit_user_turn",
                    "capture_method": "conservative_phrase_match",
                },
            )
        ]

    def resolve_conflicts(
        self,
        *,
        session_id: str,
        conflict_ids: list[str],
        resolution_note: str | None = None,
    ) -> CaseMemorySnapshot:
        record = self.sessions.get(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")
        normalized_ids = _dedupe(conflict_ids)
        interviewer_state = dict(record.interviewer_state_json or {})
        resolved = dict(
            interviewer_state.get(CASE_MEMORY_RESOLVED_CONFLICTS_KEY) or {}
        )
        for conflict_id in normalized_ids:
            resolved[conflict_id] = {
                "status": "resolved",
                "note": resolution_note,
            }
        interviewer_state[CASE_MEMORY_RESOLVED_CONFLICTS_KEY] = resolved
        record.interviewer_state_json = interviewer_state
        self.sessions.save(record)
        snapshot = self.build_snapshot(session_id)
        self._persist_snapshot(session_id, snapshot)
        return snapshot

    def tombstone_document(
        self,
        *,
        document_id: str,
        reason: str = "document_removed",
    ) -> CaseMemorySnapshot:
        document = self.documents.get_document(document_id)
        if document is None:
            raise LookupError(f"Document not found: {document_id}")
        artifact = dict(document.artifact_json or {})
        artifact[CASE_MEMORY_TOMBSTONE_KEY] = {
            "status": "tombstoned",
            "reason": reason,
        }
        document.artifact_json = artifact
        document.status = "tombstoned"
        self.documents.save_document(document)
        self.db.flush()
        snapshot = self.build_snapshot(document.session_id)
        self._persist_snapshot(document.session_id, snapshot)
        return snapshot

    def build_snapshot(self, session_id: str) -> CaseMemorySnapshot:
        documents = self.documents.list_session_documents(session_id)
        claims_by_id: dict[str, CaseClaim] = {}
        evidence_by_id: dict[str, EvidenceCard] = {}
        proof_by_id: dict[str, ProofPoint] = {}
        conflicts_by_id: dict[str, CaseConflict] = {}
        next_move: InterviewNextMove | None = None
        latest_material = self._latest_material_from_documents(documents)
        conflict_resolutions = self._conflict_resolutions(session_id)
        resolved_conflict_ids = {
            resolution.conflict_id
            for resolution in conflict_resolutions
            if resolution.status == "resolved"
        }

        for document in documents:
            if self._document_tombstoned(document):
                continue
            result = self._result_from_document(document)
            if result is None:
                continue
            if next_move is None and result.suggested_followups:
                next_move = result.suggested_followups[0]
            for evidence in result.evidence_cards:
                evidence_by_id[evidence.evidence_id] = evidence
            for claim in result.extracted_claims:
                claim = self._canonicalize_claim(claim)
                claims_by_id[claim.claim_id] = self._merge_claim(
                    claims_by_id.get(claim.claim_id),
                    claim,
                )
            for proof in result.proof_points:
                proof_by_id[proof.proof_point_id] = proof
            for conflict in result.conflicts:
                if conflict.conflict_id not in resolved_conflict_ids:
                    conflicts_by_id[conflict.conflict_id] = conflict

        for turn in self.turns.list_session_turns(session_id):
            for evidence in self._user_evidence_from_turn(turn):
                evidence_by_id[evidence.evidence_id] = evidence
            for claim in self._user_claims_from_turn(turn):
                claim = self._canonicalize_claim(claim)
                claims_by_id[claim.claim_id] = self._merge_claim(
                    claims_by_id.get(claim.claim_id),
                    claim,
                )

        claims_by_id, generated_conflicts = self._apply_field_conflicts(
            claims_by_id,
            evidence_by_id,
            resolved_conflict_ids=resolved_conflict_ids,
        )
        for conflict in generated_conflicts:
            conflicts_by_id.setdefault(conflict.conflict_id, conflict)

        self._apply_funding_proof_gap(
            session_id=session_id,
            claims_by_id=claims_by_id,
            evidence_by_id=evidence_by_id,
            proof_by_id=proof_by_id,
        )

        if conflicts_by_id:
            next_move = self._next_move_from_conflict(
                sorted(
                    conflicts_by_id.values(),
                    key=lambda item: item.conflict_id,
                )[0]
            )

        return CaseMemorySnapshot(
            latest_material=latest_material,
            claims=sorted(claims_by_id.values(), key=lambda item: item.claim_id),
            evidence_cards=sorted(
                evidence_by_id.values(),
                key=lambda item: item.evidence_id,
            ),
            proof_points=sorted(
                proof_by_id.values(),
                key=lambda item: item.proof_point_id,
            ),
            conflicts=sorted(
                conflicts_by_id.values(),
                key=lambda item: item.conflict_id,
            ),
            conflict_resolutions=sorted(
                conflict_resolutions,
                key=lambda item: item.conflict_id,
            ),
            next_move=next_move,
        )

    def build_board(self, session_id: str) -> dict[str, Any]:
        snapshot = self.get_or_build_snapshot(session_id)
        return {
            "schema_version": "case_board.v1",
            "latest_material": snapshot.latest_material,
            "claims": [item.model_dump(mode="json") for item in snapshot.claims],
            "evidence_cards": [
                item.model_dump(mode="json") for item in snapshot.evidence_cards
            ],
            "proof_points": [
                item.model_dump(mode="json") for item in snapshot.proof_points
            ],
            "conflicts": [
                item.model_dump(mode="json") for item in snapshot.conflicts
            ],
            "conflict_resolutions": [
                item.model_dump(mode="json")
                for item in snapshot.conflict_resolutions
            ],
            "next_move": (
                None
                if snapshot.next_move is None
                else snapshot.next_move.model_dump(mode="json")
            ),
        }

    def get_snapshot(self, session_id: str) -> CaseMemorySnapshot | None:
        record = self.db.get(CaseMemorySnapshotRecord, session_id)
        if record is None:
            return None
        payload = dict(record.snapshot_json or {})
        return CaseMemorySnapshot.model_validate(payload)

    def get_or_build_snapshot(self, session_id: str) -> CaseMemorySnapshot:
        snapshot = self.get_snapshot(session_id)
        if snapshot is not None:
            return snapshot
        snapshot = self.build_snapshot(session_id)
        if self.sessions.get(session_id) is not None:
            self._persist_snapshot(session_id, snapshot)
        return snapshot

    def query_evidence_graph(
        self,
        session_id: str,
        *,
        field_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        snapshot = self.get_or_build_snapshot(session_id)
        selected_field_paths = set(_dedupe(field_paths or []))
        claims = [
            claim
            for claim in snapshot.claims
            if not selected_field_paths or claim.field_path in selected_field_paths
        ]
        claim_ids = {claim.claim_id for claim in claims}
        proof_points = [
            proof
            for proof in snapshot.proof_points
            if not selected_field_paths or set(proof.claim_refs).intersection(claim_ids)
        ]
        conflicts = [
            conflict
            for conflict in snapshot.conflicts
            if not selected_field_paths
            or set(conflict.claim_ids).intersection(claim_ids)
        ]
        evidence_cards = self._evidence_cards_for_query(
            snapshot=snapshot,
            claim_ids=claim_ids,
            proof_points=proof_points,
            conflicts=conflicts,
            filtered=bool(selected_field_paths),
        )
        return {
            "schema_version": "evidence_graph.v1",
            "session_id": session_id,
            "field_paths": sorted(selected_field_paths),
            "claims": [item.model_dump(mode="json") for item in claims],
            "evidence_cards": [
                item.model_dump(mode="json") for item in evidence_cards
            ],
            "proof_points": [
                item.model_dump(mode="json") for item in proof_points
            ],
            "conflicts": [item.model_dump(mode="json") for item in conflicts],
            "conflict_resolutions": [
                item.model_dump(mode="json")
                for item in snapshot.conflict_resolutions
            ],
            "edges": self._evidence_graph_edges(
                claims=claims,
                evidence_cards=evidence_cards,
                proof_points=proof_points,
                conflicts=conflicts,
            ),
            "next_move": (
                None
                if snapshot.next_move is None
                else snapshot.next_move.model_dump(mode="json")
            ),
        }

    def public_case_board(self, session_id: str) -> dict[str, Any]:
        return self.sanitize_public_payload(self.build_board(session_id))

    def public_evidence_graph(
        self,
        session_id: str,
        *,
        field_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.sanitize_public_payload(
            self.query_evidence_graph(session_id, field_paths=field_paths)
        )

    def sanitize_public_payload(self, value: Any) -> Any:
        return self._sanitize_public_payload(value)

    def _persist_snapshot(
        self,
        session_id: str,
        snapshot: CaseMemorySnapshot,
    ) -> None:
        payload = {
            "schema_version": "case_memory_snapshot.v1",
            **snapshot.model_dump(mode="json"),
        }
        record = self.db.get(CaseMemorySnapshotRecord, session_id)
        if record is None:
            try:
                with self.db.begin_nested():
                    self.db.add(
                        CaseMemorySnapshotRecord(
                            session_id=session_id,
                            snapshot_json=payload,
                            updated_at=utc_now_naive(),
                        )
                    )
                    self.db.flush()
                return
            except IntegrityError:
                record = self.db.get(CaseMemorySnapshotRecord, session_id)
                if record is None:
                    raise

        record.snapshot_json = payload
        record.updated_at = utc_now_naive()
        self.db.add(record)
        self.db.flush()

    def _evidence_cards_for_query(
        self,
        *,
        snapshot: CaseMemorySnapshot,
        claim_ids: set[str],
        proof_points: list[ProofPoint],
        conflicts: list[CaseConflict],
        filtered: bool,
    ) -> list[EvidenceCard]:
        if not filtered:
            return list(snapshot.evidence_cards)

        evidence_ids: set[str] = set()
        for claim in snapshot.claims:
            if claim.claim_id in claim_ids:
                evidence_ids.update(claim.supporting_evidence_ids)
                evidence_ids.update(claim.conflicting_evidence_ids)
        for proof in proof_points:
            evidence_ids.update(proof.evidence_refs)
        for conflict in conflicts:
            evidence_ids.update(conflict.evidence_ids)

        return [
            evidence
            for evidence in snapshot.evidence_cards
            if evidence.evidence_id in evidence_ids
            or set(evidence.claim_refs).intersection(claim_ids)
        ]

    def _evidence_graph_edges(
        self,
        *,
        claims: list[CaseClaim],
        evidence_cards: list[EvidenceCard],
        proof_points: list[ProofPoint],
        conflicts: list[CaseConflict],
    ) -> list[dict[str, Any]]:
        evidence_ids = {evidence.evidence_id for evidence in evidence_cards}
        claim_ids = {claim.claim_id for claim in claims}
        edges_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

        def add_edge(source: str, target: str, relation: str) -> None:
            key = (source, target, relation)
            edges_by_key.setdefault(
                key,
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                },
            )

        for claim in claims:
            for evidence_id in claim.supporting_evidence_ids:
                if evidence_id in evidence_ids:
                    add_edge(claim.claim_id, evidence_id, "support")
            for evidence_id in claim.conflicting_evidence_ids:
                if evidence_id in evidence_ids:
                    add_edge(claim.claim_id, evidence_id, "conflict")
        for evidence in evidence_cards:
            for claim_id in evidence.claim_refs:
                if claim_id in claim_ids:
                    add_edge(claim_id, evidence.evidence_id, "support")
        for proof in proof_points:
            for claim_id in proof.claim_refs:
                if claim_id in claim_ids:
                    add_edge(proof.proof_point_id, claim_id, "requires_claim")
            for evidence_id in proof.evidence_refs:
                if evidence_id in evidence_ids:
                    add_edge(proof.proof_point_id, evidence_id, "requires_evidence")
        for conflict in conflicts:
            for claim_id in conflict.claim_ids:
                if claim_id in claim_ids:
                    add_edge(conflict.conflict_id, claim_id, "conflict")
            for evidence_id in conflict.evidence_ids:
                if evidence_id in evidence_ids:
                    add_edge(conflict.conflict_id, evidence_id, "conflict")

        return [
            edges_by_key[key]
            for key in sorted(edges_by_key, key=lambda item: (item[0], item[1], item[2]))
        ]

    def _document_tombstoned(self, document: DocumentRecord) -> bool:
        artifact = dict(document.artifact_json or {})
        if document.status in {"deleted", "tombstoned"}:
            return True
        tombstone = artifact.get(CASE_MEMORY_TOMBSTONE_KEY)
        return isinstance(tombstone, dict) and tombstone.get("status") == "tombstoned"

    def _result_from_document(
        self,
        document: DocumentRecord,
    ) -> MaterialUnderstandingResult | None:
        artifact = dict(document.artifact_json or {})
        payload = artifact.get(CASE_MEMORY_RESULT_KEY)
        if not isinstance(payload, dict):
            return None
        return MaterialUnderstandingResult.model_validate(payload)

    def _latest_material_from_documents(
        self,
        documents: list[DocumentRecord],
    ) -> dict[str, Any] | None:
        for document in reversed(documents):
            if self._document_tombstoned(document):
                continue
            artifact = dict(document.artifact_json or {})
            delta = artifact.get("case_board_delta")
            if not isinstance(delta, dict):
                continue
            latest_material = delta.get("latest_material")
            if isinstance(latest_material, dict) and latest_material:
                return self.sanitize_public_payload(latest_material)
        return None

    def _user_claims_from_turn(self, turn: SessionTurnRecord) -> list[CaseClaim]:
        metadata = dict(turn.metadata_json or {})
        claims: list[CaseClaim] = []
        for item in _list_payload(metadata.get(CASE_MEMORY_USER_CLAIMS_KEY)):
            claim = CaseClaim.model_validate(item)
            if self._is_stale_user_funding_claim(turn, claim):
                continue
            claims.append(claim)
        return claims

    def _user_evidence_from_turn(self, turn: SessionTurnRecord) -> list[EvidenceCard]:
        metadata = dict(turn.metadata_json or {})
        return [
            EvidenceCard.model_validate(item)
            for item in _list_payload(metadata.get(CASE_MEMORY_USER_EVIDENCE_KEY))
        ]

    def _conflict_resolutions(
        self,
        session_id: str,
    ) -> list[CaseConflictResolution]:
        record = self.sessions.get(session_id)
        if record is None:
            return []
        interviewer_state = dict(record.interviewer_state_json or {})
        resolved = interviewer_state.get(CASE_MEMORY_RESOLVED_CONFLICTS_KEY)
        if not isinstance(resolved, dict):
            return []
        resolutions: list[CaseConflictResolution] = []
        for conflict_id, payload in resolved.items():
            if not isinstance(conflict_id, str) or not isinstance(payload, dict):
                continue
            if payload.get("status") != "resolved":
                continue
            note = payload.get("note")
            resolutions.append(
                CaseConflictResolution(
                    conflict_id=conflict_id,
                    note=note if isinstance(note, str) else None,
                )
            )
        return resolutions

    def _merge_claim(
        self,
        existing: CaseClaim | None,
        incoming: CaseClaim,
    ) -> CaseClaim:
        if existing is None:
            return incoming
        status = incoming.status
        if existing.status == "contradicted" or incoming.status == "contradicted":
            status = "contradicted"
        elif existing.status == "documented" or incoming.status == "documented":
            status = "documented"
        return existing.model_copy(
            update={
                "status": status,
                "supporting_evidence_ids": _dedupe(
                    [
                        *existing.supporting_evidence_ids,
                        *incoming.supporting_evidence_ids,
                    ]
                ),
                "conflicting_evidence_ids": _dedupe(
                    [
                        *existing.conflicting_evidence_ids,
                        *incoming.conflicting_evidence_ids,
                    ]
                ),
                "confidence": max(existing.confidence, incoming.confidence),
            }
        )

    def _apply_field_conflicts(
        self,
        claims_by_id: dict[str, CaseClaim],
        evidence_by_id: dict[str, EvidenceCard],
        *,
        resolved_conflict_ids: set[str],
    ) -> tuple[dict[str, CaseClaim], list[CaseConflict]]:
        grouped: dict[str, list[CaseClaim]] = {}
        for claim in claims_by_id.values():
            if claim.value is None:
                continue
            grouped.setdefault(claim.field_path, []).append(claim)

        updated = dict(claims_by_id)
        conflicts: list[CaseConflict] = []
        for field_path, claims in grouped.items():
            values_by_normalized: dict[str, str] = {}
            for claim in claims:
                if claim.value is None:
                    continue
                normalized_value = self._normalize_claim_value(
                    field_path=field_path,
                    value=claim.value,
                )
                if normalized_value:
                    values_by_normalized[normalized_value] = claim.value
            if len(values_by_normalized) <= 1:
                continue

            conflict_id = self._field_conflict_id(field_path)
            if conflict_id in resolved_conflict_ids:
                continue
            evidence_ids = self._claim_group_evidence_ids(claims, evidence_by_id)
            for claim in claims:
                own_evidence = set(claim.supporting_evidence_ids)
                own_evidence.update(self._evidence_ids_for_claim(evidence_by_id, claim))
                conflicting = [
                    evidence_id
                    for evidence_id in evidence_ids
                    if evidence_id not in own_evidence
                ]
                if not conflicting:
                    continue
                updated[claim.claim_id] = claim.model_copy(
                    update={
                        "status": "contradicted",
                        "conflicting_evidence_ids": _dedupe(
                            [
                                *claim.conflicting_evidence_ids,
                                *own_evidence,
                                *conflicting,
                            ]
                        ),
                    }
                )
            conflicts.append(
                CaseConflict(
                    conflict_id=conflict_id,
                    claim_ids=[claim.claim_id for claim in claims],
                    evidence_ids=evidence_ids,
                    summary=(
                        f"{field_path} has conflicting values: "
                        f"{', '.join(values_by_normalized.values())}."
                    ),
                    severity="medium",
                    suggested_followup=(
                        "Ask the applicant to reconcile the stated answer with "
                        "the uploaded evidence."
                    ),
                )
            )
        return updated, conflicts

    def _claim_group_evidence_ids(
        self,
        claims: list[CaseClaim],
        evidence_by_id: dict[str, EvidenceCard],
    ) -> list[str]:
        values: list[str] = []
        for claim in claims:
            values.extend(claim.supporting_evidence_ids)
            values.extend(claim.conflicting_evidence_ids)
            values.extend(self._evidence_ids_for_claim(evidence_by_id, claim))
        return _dedupe(values)

    def _apply_funding_proof_gap(
        self,
        *,
        session_id: str,
        claims_by_id: dict[str, CaseClaim],
        evidence_by_id: dict[str, EvidenceCard],
        proof_by_id: dict[str, ProofPoint],
    ) -> None:
        if "funding_proof" in proof_by_id:
            return

        funding_claims = [
            claim
            for claim in claims_by_id.values()
            if claim.field_path == "/funding/primary_source"
            and claim.value is not None
        ]
        if not funding_claims:
            return

        documented_claims = [
            claim
            for claim in funding_claims
            if claim.status == "documented"
            and (
                claim.supporting_evidence_ids
                or self._evidence_ids_for_claim(evidence_by_id, claim)
            )
        ]
        if documented_claims:
            return

        record = self.sessions.get(session_id)
        visa_family = record.declared_family if record is not None else "unknown"
        proof_by_id["funding_proof"] = ProofPoint(
            proof_point_id="funding_proof",
            visa_family=visa_family or "unknown",
            question="Funding source needs documentary support.",
            status="missing",
            why_it_matters=(
                "A stated F-1 funding source must stay visible as unresolved "
                "until a parsed funding document supports it."
            ),
            claim_refs=sorted(claim.claim_id for claim in funding_claims),
            evidence_refs=[],
            metadata={"source": "case_memory_funding_gap_guard"},
        )

    def _evidence_ids_for_claim(
        self,
        evidence_by_id: dict[str, EvidenceCard],
        claim: CaseClaim,
    ) -> list[str]:
        return [
            evidence.evidence_id
            for evidence in evidence_by_id.values()
            if claim.claim_id in evidence.claim_refs
        ]

    def _field_conflict_id(self, field_path: str) -> str:
        normalized = field_path.strip("/").replace("/", "-").replace("_", "-")
        return f"conflict-{normalized or 'unknown'}"

    def _canonicalize_claim(self, claim: CaseClaim) -> CaseClaim:
        if claim.field_path != "/funding/primary_source":
            return claim
        if not isinstance(claim.value, str):
            return claim
        canonical_value = self._canonical_funding_source_value(claim.value)
        if canonical_value is None or canonical_value == claim.value:
            return claim
        return claim.model_copy(update={"value": canonical_value})

    def _normalize_claim_value(self, *, field_path: str, value: str) -> str:
        if field_path == "/funding/primary_source":
            canonical_value = self._canonical_funding_source_value(value)
            if canonical_value:
                return canonical_value
        return " ".join(value.strip().casefold().split())

    def _user_turn_evidence_id(self, turn_id: str, claim_id: str) -> str:
        return f"ev-{turn_id}-{claim_id}"

    def _is_stale_user_funding_claim(
        self,
        turn: SessionTurnRecord,
        claim: CaseClaim,
    ) -> bool:
        if claim.field_path != "/funding/primary_source":
            return False
        if claim.metadata.get("source") != "explicit_user_turn":
            return False
        expected_source = self._explicit_funding_source(turn.content)
        if expected_source is None:
            return True
        canonical_value = (
            self._canonical_funding_source_value(claim.value)
            if isinstance(claim.value, str)
            else None
        )
        return canonical_value != expected_source

    def _explicit_funding_source(self, message_text: str) -> str | None:
        normalized = message_text.casefold()
        parent_markers = (
            "parents",
            "parent",
            "family",
            "mother",
            "father",
            "父母",
            "家里",
            "家庭",
            "爸爸",
            "妈妈",
            "父亲",
            "母亲",
        )
        funding_action_markers = (
            "pay",
            "fund",
            "sponsor",
            "support",
            "cover",
            "finance",
            "资助",
            "支付",
            "出钱",
            "付款",
        )
        funding_context_markers = (
            "tuition",
            "living expense",
            "living expenses",
            "fee",
            "fees",
            "cost",
            "costs",
            "expense",
            "expenses",
            "bank",
            "funding",
            "financial",
            "study",
            "program",
            "学费",
            "生活费",
            "费用",
            "资金",
            "存款",
            "银行",
            "留学",
            "学业",
            "学费和生活费",
        )
        self_markers = (
            "self-funded",
            "self funded",
            "myself",
            "i will pay",
            "i pay",
            "自费",
            "自己支付",
            "自己承担",
        )
        has_funding_action = any(
            marker in normalized for marker in funding_action_markers
        )
        has_funding_context = any(
            marker in normalized for marker in funding_context_markers
        )
        if any(marker in normalized for marker in self_markers) and (
            has_funding_action or has_funding_context
        ):
            return "self"
        if any(marker in normalized for marker in parent_markers) and any(
            marker in normalized
            for marker in (*funding_action_markers, *funding_context_markers)
        ):
            return "parents"
        return None

    def _canonical_funding_source_value(self, value: str) -> str | None:
        normalized = " ".join(value.strip().casefold().replace("-", " ").split())
        if not normalized:
            return None
        if normalized in {
            "parents",
            "parent",
            "family",
            "father",
            "mother",
            "father and mother",
            "mother and father",
            "父母",
            "家庭",
            "家里",
            "爸爸",
            "妈妈",
            "父亲",
            "母亲",
        }:
            return "parents"
        if normalized in {
            "self",
            "self funded",
            "self funding",
            "self pay",
            "self financed",
            "自费",
            "自己支付",
            "自己承担",
        }:
            return "self"
        return None

    def _case_board_delta(
        self,
        *,
        document: DocumentRecord,
        result: MaterialUnderstandingResult,
        status: str,
    ) -> dict[str, Any]:
        artifact = dict(document.artifact_json or {})
        return {
            "latest_material": {
                "document_id": document.document_id,
                "filename": document.filename,
                "understanding_status": status,
                "document_type": artifact.get("document_type"),
                "document_type_candidates": [
                    item.model_dump(mode="json")
                    for item in result.document_type_candidates
                ],
                "confidence": result.confidence,
                "unknowns": list(result.unknowns),
            },
            "evidence_cards": [
                item.model_dump(mode="json") for item in result.evidence_cards
            ],
            "claims": [
                item.model_dump(mode="json") for item in result.extracted_claims
            ],
            "open_proof_points": [
                item.model_dump(mode="json") for item in result.proof_points
            ],
            "conflicts": [
                item.model_dump(mode="json") for item in result.conflicts
            ],
            "next_move": (
                result.suggested_followups[0].model_dump(mode="json")
                if result.suggested_followups
                else (
                    self._next_move_from_conflict(result.conflicts[0]).model_dump(
                        mode="json"
                    )
                    if result.conflicts
                    else None
                )
            ),
        }

    def _unavailable_delta(
        self,
        *,
        document: DocumentRecord,
        job: MaterialUnderstandingJob,
    ) -> dict[str, Any]:
        return {
            "latest_material": {
                "document_id": document.document_id,
                "filename": document.filename,
                "understanding_status": job.status,
                "unknowns": [
                    job.error_message
                    or "Material understanding is unavailable for this file."
                ],
            },
            "evidence_cards": [],
            "claims": [],
            "open_proof_points": [],
            "conflicts": [],
            "next_move": None,
        }

    def _next_move_from_conflict(self, conflict: CaseConflict) -> InterviewNextMove:
        return InterviewNextMove(
            move_type="clarify_conflict",
            question=(
                conflict.suggested_followup
                or "Please clarify the inconsistency between your answer and the uploaded evidence."
            ),
            reason=conflict.summary,
            claim_refs=list(conflict.claim_ids),
            evidence_refs=list(conflict.evidence_ids),
        )

    def _sanitize_public_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._sanitize_public_payload(item)
                for key, item in value.items()
                if key not in INTERNAL_DEBUG_METADATA_KEYS
            }
        if isinstance(value, list):
            return [
                self._sanitize_public_payload(item)
                for item in value
            ]
        return value


def _dedupe(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _list_payload(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]
