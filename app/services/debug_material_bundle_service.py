from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import DocumentRecord, SessionTurnRecord
from app.domain.case_memory import (
    CaseConflict,
    CaseClaim,
    DocumentTypeCandidate,
    EvidenceCard,
    InterviewNextMove,
    MaterialUnderstandingJob,
    MaterialUnderstandingResult,
    ProofPoint,
)
from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
from app.domain.evidence import DocumentChunk, DocumentSourceType, EvidenceItem
from app.repositories.document_repo import DocumentRepository
from app.repositories.evidence_repo import EvidenceRepository
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.ai_material_bundle_generator_service import (
    AIMaterialBundleGeneratorService,
    GeneratedBundleScenario,
    GeneratedMaterialBundleOutput,
)
from app.services.gate_runtime_service import GateRuntimeService
from app.services.message_service import MessageService
from app.services.profile_recompute_service import ProfileRecomputeService
from app.services.runtime_errors import ModelRuntimeError


DebugMaterialBundleScenario = Literal[
    "normal_f1_bundle",
    "school_mismatch_bundle",
    "identity_mismatch_bundle",
    "funding_shortfall_bundle",
    "sponsor_chain_gap_bundle",
    "claim_vs_document_bundle",
]


@dataclass(frozen=True)
class ExpectedFinding:
    kind: str
    description: str
    field_path: str | None = None
    document_types: list[str] = field(default_factory=list)
    severity: str = "medium"
    visible_to_model: bool = False


@dataclass(frozen=True)
class SyntheticTurnSpec:
    role: Literal["user"]
    content: str
    field_claims: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SyntheticDocumentSpec:
    document_type: str
    filename: str
    text: str
    fields: dict[str, str]
    counts_toward_gate: bool = True


@dataclass(frozen=True)
class DebugMaterialBundleSpec:
    scenario: DebugMaterialBundleScenario
    scenario_label: str
    documents: list[SyntheticDocumentSpec]
    expected_findings: list[ExpectedFinding] = field(default_factory=list)
    synthetic_turns: list[SyntheticTurnSpec] = field(default_factory=list)


@dataclass(frozen=True)
class DebugMaterialBundleEvent:
    event: str
    data: dict[str, Any]


SYNTHETIC_APPLICANT_NAME = "TEST APPLICANT"
SYNTHETIC_PASSPORT_NUMBER = "X00000000"
SYNTHETIC_CONFLICT_PASSPORT_NUMBER = "Y11111111"
SYNTHETIC_NATIONALITY = "EXAMPLELAND"
SYNTHETIC_SEVIS_ID = "N0000000000"
SYNTHETIC_SCHOOL_NAME = "Example University"
SYNTHETIC_CONFLICT_SCHOOL_NAME = "Alternate Example University"
SYNTHETIC_PROGRAM_NAME = "Master of Example Analytics"
SYNTHETIC_PARENT_NAMES = ("PARENT SPONSOR A", "PARENT SPONSOR B")
SYNTHETIC_COMPANY_NAME = "Example Family Business LLC"

DEBUG_MATERIAL_BUNDLE_SCENARIOS: dict[str, str] = {
    "normal_f1_bundle": "自洽 F-1 基准材料包",
    "school_mismatch_bundle": "学校材料冲突包",
    "identity_mismatch_bundle": "身份号码冲突包",
    "funding_shortfall_bundle": "资金金额不足包",
    "sponsor_chain_gap_bundle": "父母股权资金链缺口包",
    "claim_vs_document_bundle": "口头声明与材料冲突包",
}

DOCUMENT_TYPE_LABELS: dict[str, str] = {
    "ds160": "DS-160 确认页",
    "passport_bio": "护照首页",
    "i20": "I-20",
    "admission_letter": "录取信",
    "funding_proof": "资金证明",
    "relationship_proof_between_applicant_and_sponsors": "亲属关系证明",
}

ORACLE_TEXT_MARKERS = (
    "Issue:",
    "Missing:",
    "Expected:",
    "Defect:",
    "This conflicts with",
)

_CLAIM_FIELD_BINDINGS: dict[str, tuple[str, str]] = {
    "/funding/primary_source": ("funding", "primary_source"),
    "/education/school_name": ("education", "school_name"),
    "/identity/passport_number": ("identity", "passport_number"),
}


class DebugMaterialBundleService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sessions = SessionRepository(db)
        self.documents = DocumentRepository(db)
        self.evidence = EvidenceRepository(db)
        self.turns = SessionTurnRepository(db)

    def create_bundle(
        self,
        session_id: str,
        *,
        scenario: str,
        include_synthetic_user_turns: bool = True,
        seed_text: str | None = None,
        generation_mode: str = "ai_if_available",
    ) -> dict[str, Any]:
        final_payload: dict[str, Any] | None = None
        for event in self.create_bundle_events(
            session_id,
            scenario=scenario,
            include_synthetic_user_turns=include_synthetic_user_turns,
            seed_text=seed_text,
            generation_mode=generation_mode,
            include_accepted=False,
        ):
            if event.event == "final":
                final_payload = event.data
        if final_payload is None:
            raise RuntimeError("debug material bundle did not produce a final payload")
        return final_payload

    def create_bundle_events(
        self,
        session_id: str,
        *,
        scenario: str,
        include_synthetic_user_turns: bool = True,
        seed_text: str | None = None,
        generation_mode: str = "ai_if_available",
        include_accepted: bool = True,
    ) -> Iterator[DebugMaterialBundleEvent]:
        if include_accepted:
            yield DebugMaterialBundleEvent("accepted", {"session_id": session_id})

        record = self.sessions.get(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")

        normalized_scenario = self._normalize_scenario(scenario)
        bundle_spec, generation_metadata = self._build_bundle_spec_for_request(
            record,
            normalized_scenario,
            include_synthetic_user_turns=include_synthetic_user_turns,
            seed_text=seed_text,
            generation_mode=generation_mode,
        )
        bundle_id = f"dbg-bundle-{uuid4().hex[:12]}"
        yield DebugMaterialBundleEvent(
            "debug_bundle_started",
            {
                "session_id": record.session_id,
                "bundle_id": bundle_id,
                "scenario": bundle_spec.scenario,
                "scenario_label": bundle_spec.scenario_label,
                "document_count": len(bundle_spec.documents),
                "generation_source": generation_metadata["source"],
            },
        )

        created_documents: list[dict[str, Any]] = []
        for document_spec in bundle_spec.documents:
            document, document_payload, evidence_count = self._create_parsed_document(
                record.session_id,
                bundle_id=bundle_id,
                bundle_spec=bundle_spec,
                document_spec=document_spec,
                generation_metadata=generation_metadata,
            )
            created_documents.append(document_payload)
            yield DebugMaterialBundleEvent(
                "document_created",
                {
                    "bundle_id": bundle_id,
                    "document_id": document.document_id,
                    "filename": document.filename,
                    "document_type": document_spec.document_type,
                    "document_type_label": self._document_type_label(
                        document_spec.document_type
                    ),
                },
            )
            yield DebugMaterialBundleEvent(
                "evidence_written",
                {
                    "bundle_id": bundle_id,
                    "document_id": document.document_id,
                    "evidence_count": evidence_count,
                    "fields": dict(document_spec.fields),
                },
            )

        synthetic_turn_payloads = self._write_synthetic_turns(
            record,
            bundle_id=bundle_id,
            bundle_spec=bundle_spec,
        )

        ProfileRecomputeService(self.db).recompute_session(record.session_id, save=False)
        yield DebugMaterialBundleEvent(
            "profile_recomputed",
            {"session_id": record.session_id, "bundle_id": bundle_id},
        )

        GateRuntimeService(self.db).refresh_record(record, save=False)
        yield DebugMaterialBundleEvent(
            "gate_refreshed",
            {
                "session_id": record.session_id,
                "bundle_id": bundle_id,
                "gate_status": record.gate_status_json,
            },
        )

        self.db.commit()
        yield DebugMaterialBundleEvent(
            "document_review_started",
            {"session_id": record.session_id, "bundle_id": bundle_id},
        )

        main_flow_response: dict[str, Any] = {}
        refresh_error: str | None = None
        try:
            main_flow_response = MessageService(self.db).refresh_after_material_change(
                record.session_id,
                reason=f"debug_material_bundle:{bundle_spec.scenario}",
            )
        except ModelRuntimeError as exc:
            refresh_error = exc.detail
            self.db.rollback()
        except Exception as exc:
            refresh_error = f"{exc.__class__.__name__}: {exc}"
            self.db.rollback()

        self.db.refresh(record)
        yield DebugMaterialBundleEvent(
            "governor_decided",
            {
                "session_id": record.session_id,
                "bundle_id": bundle_id,
                "governor_decision": main_flow_response.get("governor_decision"),
                "turn_decision": dict(
                    main_flow_response.get("turn_decision", {}) or {}
                ),
            },
        )

        final_payload = {
            "session_id": record.session_id,
            "bundle_id": bundle_id,
            "scenario": bundle_spec.scenario,
            "scenario_label": bundle_spec.scenario_label,
            "documents": created_documents,
            "synthetic_turns": synthetic_turn_payloads,
            "expected_findings": [
                finding.__dict__ for finding in bundle_spec.expected_findings
            ],
            "assistant_message": main_flow_response.get("assistant_message"),
            "governor_decision": main_flow_response.get("governor_decision"),
            "requested_documents": list(
                main_flow_response.get("requested_documents", []) or []
            ),
            "remaining_required_documents": list(
                main_flow_response.get("remaining_required_documents", []) or []
            ),
            "turn_decision": dict(main_flow_response.get("turn_decision", {}) or {}),
            "document_review": dict(
                main_flow_response.get("document_review", {}) or {}
            ),
            "runtime_view_state": dict(
                main_flow_response.get("runtime_view_state", {}) or {}
            ),
            "material_refresh": dict(
                main_flow_response.get("material_refresh", {}) or {}
            ),
            "phase_state": record.phase_state,
            "gate_status": record.gate_status_json,
            "main_flow_refresh_error": refresh_error,
            "generation": generation_metadata,
        }
        yield DebugMaterialBundleEvent("final", final_payload)

    def available_scenarios(self) -> list[dict[str, str]]:
        return [
            {"value": value, "label": label}
            for value, label in DEBUG_MATERIAL_BUNDLE_SCENARIOS.items()
        ]

    def _normalize_scenario(self, scenario: str) -> DebugMaterialBundleScenario:
        normalized = scenario.strip().lower()
        if normalized in DEBUG_MATERIAL_BUNDLE_SCENARIOS:
            return normalized  # type: ignore[return-value]
        raise ValueError(f"unsupported debug material bundle scenario: {scenario}")

    def _build_bundle_spec(
        self,
        scenario: DebugMaterialBundleScenario,
        *,
        include_synthetic_user_turns: bool,
    ) -> DebugMaterialBundleSpec:
        base_documents = self._normal_documents()
        synthetic_turns: list[SyntheticTurnSpec] = []
        expected_findings: list[ExpectedFinding] = []

        if scenario == "normal_f1_bundle":
            return DebugMaterialBundleSpec(
                scenario=scenario,
                scenario_label=DEBUG_MATERIAL_BUNDLE_SCENARIOS[scenario],
                documents=base_documents,
            )

        if scenario == "school_mismatch_bundle":
            documents = [
                *self._identity_documents(),
                self._i20_document(school_name=SYNTHETIC_SCHOOL_NAME),
                self._admission_letter_document(
                    school_name=SYNTHETIC_CONFLICT_SCHOOL_NAME
                ),
                self._normal_funding_document(),
                self._relationship_document(),
            ]
            expected_findings.append(
                ExpectedFinding(
                    kind="cross_document_conflict",
                    field_path="/education/school_name",
                    document_types=["i20", "admission_letter"],
                    description=(
                        "I-20 and admission letter contain different school names."
                    ),
                    severity="high",
                )
            )
            if include_synthetic_user_turns:
                synthetic_turns.append(
                    SyntheticTurnSpec(
                        role="user",
                        content=(
                            "I will study at Example University in the Master of "
                            "Example Analytics program."
                        ),
                        field_claims={
                            "/education/school_name": SYNTHETIC_SCHOOL_NAME
                        },
                    )
                )
            return DebugMaterialBundleSpec(
                scenario=scenario,
                scenario_label=DEBUG_MATERIAL_BUNDLE_SCENARIOS[scenario],
                documents=documents,
                expected_findings=expected_findings,
                synthetic_turns=synthetic_turns,
            )

        if scenario == "identity_mismatch_bundle":
            documents = [
                self._ds160_document(passport_number=SYNTHETIC_PASSPORT_NUMBER),
                self._passport_document(
                    passport_number=SYNTHETIC_CONFLICT_PASSPORT_NUMBER
                ),
                *self._study_documents(),
                self._normal_funding_document(),
                self._relationship_document(),
            ]
            expected_findings.append(
                ExpectedFinding(
                    kind="cross_document_conflict",
                    field_path="/identity/passport_number",
                    document_types=["ds160", "passport_bio"],
                    description=(
                        "DS-160 confirmation and passport bio page contain "
                        "different passport numbers."
                    ),
                    severity="high",
                )
            )
            return DebugMaterialBundleSpec(
                scenario=scenario,
                scenario_label=DEBUG_MATERIAL_BUNDLE_SCENARIOS[scenario],
                documents=documents,
                expected_findings=expected_findings,
            )

        if scenario == "funding_shortfall_bundle":
            documents = [
                *self._identity_documents(),
                self._i20_document(first_year_cost_usd="68000"),
                self._admission_letter_document(),
                self._funding_document(available_funds_usd="9800"),
                self._relationship_document(),
            ]
            expected_findings.append(
                ExpectedFinding(
                    kind="funding_shortfall",
                    field_path="/funding/available_funds",
                    document_types=["i20", "funding_proof"],
                    description=(
                        "Available funds are below the first-year cost listed "
                        "on the I-20."
                    ),
                    severity="high",
                )
            )
            return DebugMaterialBundleSpec(
                scenario=scenario,
                scenario_label=DEBUG_MATERIAL_BUNDLE_SCENARIOS[scenario],
                documents=documents,
                expected_findings=expected_findings,
            )

        if scenario == "sponsor_chain_gap_bundle":
            documents = [
                *self._identity_documents(),
                *self._study_documents(),
                self._equity_funding_document(),
                self._relationship_document(),
            ]
            expected_findings.append(
                ExpectedFinding(
                    kind="funding_source_chain_gap",
                    field_path="/funding/source_detail",
                    document_types=["funding_proof"],
                    description=(
                        "Funding source relies on equity transfer proceeds, "
                        "but no separate registration, transfer, tax, or "
                        "payment trail documents are present."
                    ),
                    severity="medium",
                )
            )
            return DebugMaterialBundleSpec(
                scenario=scenario,
                scenario_label=DEBUG_MATERIAL_BUNDLE_SCENARIOS[scenario],
                documents=documents,
                expected_findings=expected_findings,
            )

        documents = base_documents
        if include_synthetic_user_turns:
            synthetic_turns.append(
                SyntheticTurnSpec(
                    role="user",
                    content=(
                        "I am self-funded and will pay the tuition and living "
                        "expenses with my own savings."
                    ),
                    field_claims={"/funding/primary_source": "self"},
                )
            )
        expected_findings.append(
            ExpectedFinding(
                kind="claim_vs_document_conflict",
                field_path="/funding/primary_source",
                document_types=["funding_proof"],
                description=(
                    "The user says the case is self-funded, while funding "
                    "documents show parent sponsorship."
                ),
                severity="high",
            )
        )
        return DebugMaterialBundleSpec(
            scenario=scenario,
            scenario_label=DEBUG_MATERIAL_BUNDLE_SCENARIOS[scenario],
            documents=documents,
            expected_findings=expected_findings,
            synthetic_turns=synthetic_turns,
        )

    def _build_bundle_spec_for_request(
        self,
        record,
        scenario: DebugMaterialBundleScenario,
        *,
        include_synthetic_user_turns: bool,
        seed_text: str | None,
        generation_mode: str,
    ) -> tuple[DebugMaterialBundleSpec, dict[str, Any]]:
        normalized_mode = (generation_mode or "ai_if_available").strip().lower()
        requested_seed = (seed_text or "").strip()
        resolved_seed, seed_source = self._resolve_generation_seed(
            record,
            requested_seed=requested_seed,
        )
        should_try_ai = normalized_mode in {
            "ai",
            "ai_if_seeded",
            "ai_if_available",
        } and bool(resolved_seed)
        if should_try_ai:
            try:
                generated, trace = AIMaterialBundleGeneratorService(self.db).generate(
                    record=record,
                    scenario=scenario,  # type: ignore[arg-type]
                    seed_text=resolved_seed,
                    include_synthetic_user_turns=include_synthetic_user_turns,
                )
                return self._bundle_spec_from_generated_output(
                    scenario,
                    generated,
                ), {
                    "source": "ai",
                    "mode": normalized_mode,
                    "seed_text_present": True,
                    "seed_source": seed_source,
                    "request_seed_text_present": bool(requested_seed),
                    "fallback_used": False,
                    "trace": trace,
                }
            except ModelRuntimeError as exc:
                raise ModelRuntimeError(
                    detail=(
                        "AI 材料生成失败，未写入任何演示占位材料。"
                        f"请稍后重试或更换模型。原始错误：{exc.detail}"
                    ),
                    status_code=exc.status_code,
                    provider=exc.provider,
                    model=exc.model,
                    upstream_code=exc.upstream_code,
                    body=exc.body,
                    missing_env_vars=exc.missing_env_vars,
                ) from exc

        fallback_spec = self._build_bundle_spec(
            scenario,
            include_synthetic_user_turns=include_synthetic_user_turns,
        )
        return fallback_spec, {
            "source": "deterministic",
            "mode": normalized_mode or "deterministic",
            "seed_text_present": bool(resolved_seed),
            "seed_source": seed_source,
            "request_seed_text_present": bool(requested_seed),
            "fallback_used": False,
        }

    def _resolve_generation_seed(
        self,
        record,
        *,
        requested_seed: str,
    ) -> tuple[str, str | None]:
        if requested_seed:
            return requested_seed, "request"

        user_turns: list[str] = []
        for turn in self.turns.list_session_turns(record.session_id):
            if turn.role != "user":
                continue
            metadata = dict(turn.metadata_json or {})
            if turn.source.startswith("debug_") or metadata.get("synthetic"):
                continue
            content = str(turn.content or "").strip()
            if content:
                user_turns.append(content)

        if user_turns:
            return "\n\n".join(user_turns[-4:])[:4000], "session_transcript"

        return "", None

    def _bundle_spec_from_generated_output(
        self,
        scenario: DebugMaterialBundleScenario,
        generated: GeneratedMaterialBundleOutput,
    ) -> DebugMaterialBundleSpec:
        documents = [
            SyntheticDocumentSpec(
                document_type=document.document_type,
                filename=document.filename,
                text=self._safe_material_text(document.raw_text),
                fields=dict(document.fields),
                counts_toward_gate=document.counts_toward_gate,
            )
            for document in generated.documents
        ]
        synthetic_turns = [
            SyntheticTurnSpec(
                role=turn.role,
                content=turn.content,
                field_claims=dict(turn.field_claims),
            )
            for turn in generated.synthetic_turns
        ]
        return DebugMaterialBundleSpec(
            scenario=scenario,
            scenario_label=DEBUG_MATERIAL_BUNDLE_SCENARIOS[scenario],
            documents=documents,
            expected_findings=self._expected_findings_for_scenario(scenario),
            synthetic_turns=synthetic_turns,
        )

    def _expected_findings_for_scenario(
        self,
        scenario: DebugMaterialBundleScenario,
    ) -> list[ExpectedFinding]:
        if scenario == "school_mismatch_bundle":
            return [
                ExpectedFinding(
                    kind="cross_document_conflict",
                    field_path="/education/school_name",
                    document_types=["i20", "admission_letter"],
                    description=(
                        "I-20 and admission letter contain different school names."
                    ),
                    severity="high",
                )
            ]
        if scenario == "identity_mismatch_bundle":
            return [
                ExpectedFinding(
                    kind="cross_document_conflict",
                    field_path="/identity/passport_number",
                    document_types=["ds160", "passport_bio"],
                    description=(
                        "DS-160 confirmation and passport bio page contain "
                        "different passport numbers."
                    ),
                    severity="high",
                )
            ]
        if scenario == "funding_shortfall_bundle":
            return [
                ExpectedFinding(
                    kind="funding_shortfall",
                    field_path="/funding/available_funds",
                    document_types=["i20", "funding_proof"],
                    description=(
                        "Available funds are below the first-year cost listed "
                        "on the I-20."
                    ),
                    severity="high",
                )
            ]
        if scenario == "sponsor_chain_gap_bundle":
            return [
                ExpectedFinding(
                    kind="funding_source_chain_gap",
                    field_path="/funding/source_detail",
                    document_types=["funding_proof"],
                    description=(
                        "Funding source relies on equity transfer proceeds, "
                        "but no separate registration, transfer, tax, or "
                        "payment trail documents are present."
                    ),
                    severity="medium",
                )
            ]
        if scenario == "claim_vs_document_bundle":
            return [
                ExpectedFinding(
                    kind="claim_vs_document_conflict",
                    field_path="/funding/primary_source",
                    document_types=["funding_proof"],
                    description=(
                        "The user says the case is self-funded, while funding "
                        "documents show parent sponsorship."
                    ),
                    severity="high",
                )
            ]
        return []

    def _normal_documents(self) -> list[SyntheticDocumentSpec]:
        return [
            *self._identity_documents(),
            *self._study_documents(),
            self._normal_funding_document(),
            self._relationship_document(),
        ]

    def _identity_documents(self) -> list[SyntheticDocumentSpec]:
        return [self._ds160_document(), self._passport_document()]

    def _study_documents(self) -> list[SyntheticDocumentSpec]:
        return [self._i20_document(), self._admission_letter_document()]

    def _ds160_document(
        self,
        *,
        passport_number: str = SYNTHETIC_PASSPORT_NUMBER,
    ) -> SyntheticDocumentSpec:
        text = (
            "U.S. Department of State\n"
            "Online Nonimmigrant Visa Application (DS-160) Confirmation\n"
            "Application ID: AA00EXAMPLE\n"
            "Confirmation No.: EXM20260524001\n"
            "Applicant Name Provided: TEST APPLICANT\n"
            f"Passport/Travel Document Number: {passport_number}\n"
            "Purpose of Trip to U.S.: STUDENT (F1)\n"
            "Intended Date of Arrival: 15 AUG 2026\n"
            "Application Location: U.S. Consulate Example Post\n"
            "Barcode Area: [machine readable confirmation barcode omitted]\n"
        )
        return SyntheticDocumentSpec(
            document_type="ds160",
            filename="debug_ds160_confirmation.txt",
            text=self._safe_material_text(text),
            fields={
                "/identity/full_name": SYNTHETIC_APPLICANT_NAME,
                "/identity/passport_number": passport_number,
                "/visa_intent/travel_purpose": "STUDENT (F1)",
            },
        )

    def _passport_document(
        self,
        *,
        passport_number: str = SYNTHETIC_PASSPORT_NUMBER,
    ) -> SyntheticDocumentSpec:
        text = (
            "PASSPORT BIOGRAPHIC PAGE - OCR TEXT\n"
            "Type: P\n"
            f"Passport No.: {passport_number}\n"
            "Surname: TEST\n"
            "Given Names: APPLICANT\n"
            f"Full Name: {SYNTHETIC_APPLICANT_NAME}\n"
            f"Nationality: {SYNTHETIC_NATIONALITY}\n"
            "Date of Birth: 15 JAN 2001\n"
            "Place of Birth: EXAMPLE CITY\n"
            "Issued On: 20 FEB 2023\n"
            "Date of Expiry: 19 FEB 2033\n"
            "MRZ: P<EXAMPLELAND<<TEST<<APPLICANT<<<<<<<<<<<<<<\n"
        )
        return SyntheticDocumentSpec(
            document_type="passport_bio",
            filename="debug_passport_bio.txt",
            text=self._safe_material_text(text),
            fields={
                "/identity/full_name": SYNTHETIC_APPLICANT_NAME,
                "/identity/passport_number": passport_number,
                "/identity/nationality": SYNTHETIC_NATIONALITY,
            },
        )

    def _i20_document(
        self,
        *,
        school_name: str = SYNTHETIC_SCHOOL_NAME,
        first_year_cost_usd: str = "68000",
    ) -> SyntheticDocumentSpec:
        text = (
            "U.S. Department of Homeland Security\n"
            "Certificate of Eligibility for Nonimmigrant Student Status (F-1)\n"
            f"SEVIS ID: {SYNTHETIC_SEVIS_ID}\n"
            f"Student Name: {SYNTHETIC_APPLICANT_NAME}\n"
            "Country of Birth: EXAMPLELAND\n"
            "School Information\n"
            f"School Name: {school_name}\n"
            "School Code: EXM214F00000000\n"
            f"Program of Study: {SYNTHETIC_PROGRAM_NAME}\n"
            "Education Level: Master's\n"
            "Program Start Date: 26 AUG 2026\n"
            "Program End Date: 20 MAY 2028\n"
            "Financials - Estimated average costs for 9 months\n"
            "Tuition and Fees: USD 42000\n"
            "Living Expenses: USD 21000\n"
            "Other Costs: USD 5000\n"
            f"First Year Cost Total: USD {first_year_cost_usd}\n"
            "Funding Listed by School: Family Funds\n"
        )
        return SyntheticDocumentSpec(
            document_type="i20",
            filename="debug_i20.txt",
            text=self._safe_material_text(text),
            fields={
                "/education/sevis_id": SYNTHETIC_SEVIS_ID,
                "/education/school_name": school_name,
                "/education/program_name": SYNTHETIC_PROGRAM_NAME,
                "/education/first_year_cost": first_year_cost_usd,
            },
        )

    def _admission_letter_document(
        self,
        *,
        school_name: str = SYNTHETIC_SCHOOL_NAME,
    ) -> SyntheticDocumentSpec:
        text = (
            f"{school_name}\n"
            "Office of Graduate Admission\n"
            "Admission Notice\n"
            "Date: 18 MAR 2026\n"
            f"Student: {SYNTHETIC_APPLICANT_NAME}\n"
            f"School Name: {school_name}\n"
            f"Program: {SYNTHETIC_PROGRAM_NAME}\n"
            "Term: Fall 2026\n"
            "Enrollment Status: admitted as a full-time student\n"
            "Campus: Main Campus\n"
            "This notice confirms admission only; tuition billing is issued separately.\n"
        )
        return SyntheticDocumentSpec(
            document_type="admission_letter",
            filename="debug_admission_letter.txt",
            text=self._safe_material_text(text),
            fields={
                "/identity/full_name": SYNTHETIC_APPLICANT_NAME,
                "/education/school_name": school_name,
                "/education/program_name": SYNTHETIC_PROGRAM_NAME,
            },
        )

    def _normal_funding_document(self) -> SyntheticDocumentSpec:
        return self._funding_document(available_funds_usd="82000")

    def _funding_document(self, *, available_funds_usd: str) -> SyntheticDocumentSpec:
        parent_a, parent_b = SYNTHETIC_PARENT_NAMES
        text = (
            "Example Commercial Bank\n"
            "Certificate of Deposit Balance - OCR Extract\n"
            "Certificate No.: ECB-2026-0510-0007\n"
            "Issue Date: 10 MAY 2026\n"
            f"Account Holder: {parent_a}; {parent_b}\n"
            "Primary Source of Support: parents\n"
            "Sponsor Relationship: parents\n"
            f"Student Beneficiary: {SYNTHETIC_APPLICANT_NAME}\n"
            "Currency: USD\n"
            f"Available Balance: USD {available_funds_usd}\n"
            "Account Type: savings deposit and time deposit\n"
            "Funds Status: available without lien or hold as of issue date\n"
            "Bank Officer: EXAMPLE BANK OFFICER\n"
        )
        return SyntheticDocumentSpec(
            document_type="funding_proof",
            filename="debug_parent_funding_certificate.txt",
            text=self._safe_material_text(text),
            fields={
                "/funding/primary_source": "parents",
                "/funding/available_funds": available_funds_usd,
                "/funding/sponsor_relationship": "parents",
            },
        )

    def _equity_funding_document(self) -> SyntheticDocumentSpec:
        parent_a, parent_b = SYNTHETIC_PARENT_NAMES
        text = (
            "Example Commercial Bank\n"
            "Incoming Remittance and Balance Summary - OCR Extract\n"
            "Statement Ref.: ECB-IR-2026-0412-019\n"
            "Issue Date: 10 MAY 2026\n"
            f"Account Holder: {parent_a}; {parent_b}\n"
            "Primary Source of Support: parents\n"
            "Sponsor Relationship: parents\n"
            f"Student Beneficiary: {SYNTHETIC_APPLICANT_NAME}\n"
            "Available Balance: USD 82000\n"
            "Recent Credit: USD 76500 received on 12 APR 2026\n"
            "Remittance Memo: family company equity transfer proceeds\n"
            f"Company Name on Memo: {SYNTHETIC_COMPANY_NAME}\n"
            f"Equity Ownership Statement on Cover Sheet: {parent_a} holds 38% shares in "
            f"{SYNTHETIC_COMPANY_NAME}\n"
            "Account Type: savings deposit\n"
        )
        return SyntheticDocumentSpec(
            document_type="funding_proof",
            filename="debug_parent_equity_funding_certificate.txt",
            text=self._safe_material_text(text),
            fields={
                "/funding/primary_source": "parents",
                "/funding/available_funds": "82000",
                "/funding/sponsor_relationship": "parents",
                "/funding/source_detail": "family company equity transfer proceeds",
                "/funding/equity_ownership": (
                    f"{parent_a} holds 38% shares in {SYNTHETIC_COMPANY_NAME}."
                ),
            },
        )

    def _relationship_document(self) -> SyntheticDocumentSpec:
        parent_a, parent_b = SYNTHETIC_PARENT_NAMES
        text = (
            "Household Register Extract / Notarial Birth Relationship OCR\n"
            "Document No.: HR-EXAMPLE-2026-0321\n"
            "Applicant: TEST APPLICANT\n"
            f"Father: {parent_a}\n"
            f"Mother: {parent_b}\n"
            "Relationship: parents\n"
            "Household Address: 100 Example Road, Example City\n"
            "Notary Office: Example City Notary Office\n"
            "Seal/Signature: visible on scanned copy\n"
        )
        return SyntheticDocumentSpec(
            document_type="relationship_proof_between_applicant_and_sponsors",
            filename="debug_relationship_proof.txt",
            text=self._safe_material_text(text),
            fields={
                "/identity/full_name": SYNTHETIC_APPLICANT_NAME,
                "/funding/sponsor_relationship": "parents",
                "/family/parent_names": f"{parent_a}; {parent_b}",
            },
        )

    def _create_parsed_document(
        self,
        session_id: str,
        *,
        bundle_id: str,
        bundle_spec: DebugMaterialBundleSpec,
        document_spec: SyntheticDocumentSpec,
        generation_metadata: dict[str, Any],
    ) -> tuple[DocumentRecord, dict[str, Any], int]:
        document_assessment = {
            "document_type": document_spec.document_type,
            "document_type_candidates": [document_spec.document_type],
            "relevance": "high",
            "supported_claims": list(document_spec.fields.keys()),
            "confidence": 1.0,
            "relevant": True,
            "counts_toward_gate": document_spec.counts_toward_gate,
        }
        artifact_json = {
            "document_id": "pending",
            "session_id": session_id,
            "filename": document_spec.filename,
            "content_type": "text/plain; charset=utf-8",
            "source_type": DocumentSourceType.TEXT.value,
            "parser_name": "debug_material_bundle",
            "status": "parsed",
            "page_count": 1,
            "metadata": {
                "debug_fill": True,
                "debug_material_bundle": True,
                "synthetic_bundle_id": bundle_id,
                "debug_bundle_scenario": bundle_spec.scenario,
                "debug_bundle_scenario_label": bundle_spec.scenario_label,
                "document_type": document_spec.document_type,
                "debug_generation": dict(generation_metadata),
                "visible_to_model": True,
                "counts_toward_gate": document_spec.counts_toward_gate,
                "relevant": True,
                "document_assessment": document_assessment,
            },
        }
        document = self.documents.create_document(
            session_id=session_id,
            filename=document_spec.filename,
            raw_bytes=document_spec.text.encode("utf-8"),
            raw_text=document_spec.text,
            artifact_json=artifact_json,
        )
        document.status = "parsed"
        artifact = dict(document.artifact_json or {})
        artifact["document_id"] = document.document_id
        document.artifact_json = artifact

        chunk = DocumentChunk(
            chunk_id=f"chunk-{uuid4().hex[:12]}",
            document_id=document.document_id,
            session_id=session_id,
            ordinal=0,
            page_number=1,
            text=document_spec.text,
            metadata={
                "debug_fill": True,
                "debug_material_bundle": True,
                "synthetic_bundle_id": bundle_id,
                "debug_bundle_scenario": bundle_spec.scenario,
            },
        )
        evidence_items = [
            EvidenceItem(
                evidence_id=f"evi-{uuid4().hex[:12]}",
                session_id=session_id,
                document_id=document.document_id,
                chunk_id=chunk.chunk_id,
                evidence_type=document_spec.document_type,
                field_path=field_path,
                value=value,
                excerpt=self._field_excerpt(document_spec.text, field_path, value),
                confidence=1.0,
                metadata={
                    "debug_fill": True,
                    "debug_material_bundle": True,
                    "synthetic_bundle_id": bundle_id,
                    "debug_bundle_scenario": bundle_spec.scenario,
                },
            )
            for field_path, value in document_spec.fields.items()
        ]
        material_result = self._material_understanding_result(
            document=document,
            bundle_id=bundle_id,
            bundle_spec=bundle_spec,
            document_spec=document_spec,
            evidence_items=evidence_items,
        )
        artifact = dict(document.artifact_json or {})
        artifact["understanding_status"] = "completed"
        artifact["material_understanding_job"] = MaterialUnderstandingJob(
            job_id=f"debug-bundle-{document.document_id}",
            document_id=document.document_id,
            status="completed",
            trigger="debug_bundle",
            result=material_result,
        ).model_dump(mode="json", exclude_none=True)
        artifact["material_understanding_result"] = material_result.model_dump(
            mode="json"
        )
        artifact["evidence_cards"] = [
            item.model_dump(mode="json") for item in material_result.evidence_cards
        ]
        artifact["case_board_delta"] = self._case_board_delta(
            document=document,
            document_spec=document_spec,
            result=material_result,
        )
        document.artifact_json = artifact
        self.evidence.replace_document_result(
            document.document_id,
            [chunk],
            evidence_items,
        )
        self.db.add(document)
        self.db.flush()
        return (
            document,
            {
                "document_id": document.document_id,
                "filename": document.filename,
                "document_type": document_spec.document_type,
                "document_type_label": self._document_type_label(
                    document_spec.document_type
                ),
                "raw_text": document_spec.text,
                "fields": dict(document_spec.fields),
                "content_url": (
                    f"/v1/sessions/{session_id}/files/{document.document_id}/content"
                ),
            },
            len(evidence_items),
        )

    def _material_understanding_result(
        self,
        *,
        document: DocumentRecord,
        bundle_id: str,
        bundle_spec: DebugMaterialBundleSpec,
        document_spec: SyntheticDocumentSpec,
        evidence_items: list[EvidenceItem],
    ) -> MaterialUnderstandingResult:
        evidence_cards = [
            EvidenceCard(
                evidence_id=item.evidence_id,
                source_type="debug_material",
                document_id=document.document_id,
                page_number=1,
                excerpt=item.excerpt,
                claim_refs=[self._claim_id(document.document_id, item.field_path)],
                confidence=item.confidence,
                metadata={
                    "filename": document.filename,
                    "field_path": item.field_path,
                    "synthetic_bundle_id": bundle_id,
                    "debug_bundle_scenario": bundle_spec.scenario,
                },
            )
            for item in evidence_items
        ]
        claims = [
            CaseClaim(
                claim_id=self._claim_id(document.document_id, item.field_path),
                field_path=item.field_path,
                value=item.value,
                status="documented",
                supporting_evidence_ids=[item.evidence_id],
                confidence=item.confidence,
                metadata={
                    "document_id": document.document_id,
                    "filename": document.filename,
                    "synthetic_bundle_id": bundle_id,
                    "debug_bundle_scenario": bundle_spec.scenario,
                },
            )
            for item in evidence_items
        ]
        conflicts = self._material_conflicts(
            document=document,
            bundle_spec=bundle_spec,
            claims=claims,
            evidence_cards=evidence_cards,
        )
        proof_points = [
            ProofPoint(
                proof_point_id=f"proof-{document.document_id}-{index}",
                visa_family="unknown",
                question=f"请说明 {field_path} 如何支持你的签证计划。",
                status="supported",
                why_it_matters="调试材料包提供了一个可核验的案例事实。",
                claim_refs=[claim.claim_id],
                evidence_refs=list(claim.supporting_evidence_ids),
                metadata={"debug_material_bundle": True},
            )
            for index, (field_path, claim) in enumerate(
                zip(document_spec.fields.keys(), claims)
            )
        ]
        return MaterialUnderstandingResult(
            document_type_candidates=[
                DocumentTypeCandidate(
                    document_type=document_spec.document_type,
                    confidence=1.0,
                )
            ],
            evidence_cards=evidence_cards,
            extracted_claims=claims,
            proof_points=proof_points,
            conflicts=conflicts,
            suggested_followups=[
                InterviewNextMove(
                    move_type="ask",
                    question="材料已经加入案例理解。请继续说明它和你的签证计划有什么关系。",
                    reason="调试材料包已作为案例证据写入 Case Memory。",
                    claim_refs=[claim.claim_id for claim in claims[:3]],
                    evidence_refs=[item.evidence_id for item in evidence_cards[:3]],
                )
            ],
            confidence=1.0,
        )

    def _material_conflicts(
        self,
        *,
        document: DocumentRecord,
        bundle_spec: DebugMaterialBundleSpec,
        claims: list[CaseClaim],
        evidence_cards: list[EvidenceCard],
    ) -> list[CaseConflict]:
        conflicts: list[CaseConflict] = []
        for finding in bundle_spec.expected_findings:
            if finding.kind not in {
                "cross_document_conflict",
                "claim_vs_document_conflict",
            }:
                continue
            related_claim_ids = [
                claim.claim_id for claim in claims if claim.field_path == finding.field_path
            ]
            related_evidence_ids = [
                evidence.evidence_id
                for evidence in evidence_cards
                if finding.field_path
                in str(evidence.metadata.get("field_path") or "")
            ]
            if not related_claim_ids and not related_evidence_ids:
                continue
            conflicts.append(
                CaseConflict(
                    conflict_id=(
                        f"conflict-{document.document_id}-"
                        f"{finding.field_path.strip('/').replace('/', '-')}"
                    ),
                    claim_ids=related_claim_ids,
                    evidence_ids=related_evidence_ids,
                    summary=finding.description,
                    severity=finding.severity,
                    suggested_followup="Ask the applicant to clarify the material conflict.",
                )
            )
        return conflicts

    def _case_board_delta(
        self,
        *,
        document: DocumentRecord,
        document_spec: SyntheticDocumentSpec,
        result: MaterialUnderstandingResult,
    ) -> dict[str, Any]:
        return {
            "latest_material": {
                "document_id": document.document_id,
                "filename": document.filename,
                "understanding_status": "completed",
                "document_type": document_spec.document_type,
                "document_type_candidates": [
                    item.model_dump(mode="json")
                    for item in result.document_type_candidates
                ],
                "supported_claims": list(document_spec.fields.keys()),
                "confidence": result.confidence,
                "unknowns": [],
            },
            "evidence_cards": [
                item.model_dump(mode="json") for item in result.evidence_cards
            ],
            "claims": [item.model_dump(mode="json") for item in result.extracted_claims],
            "open_proof_points": [
                item.model_dump(mode="json") for item in result.proof_points
            ],
            "conflicts": [item.model_dump(mode="json") for item in result.conflicts],
            "next_move": result.suggested_followups[0].model_dump(mode="json"),
        }

    def _claim_id(self, document_id: str, field_path: str) -> str:
        normalized = field_path.strip("/").replace("/", "-").replace("_", "-")
        return f"claim-{document_id}-{normalized or 'unknown'}"

    def _write_synthetic_turns(
        self,
        record,
        *,
        bundle_id: str,
        bundle_spec: DebugMaterialBundleSpec,
    ) -> list[dict[str, Any]]:
        if not bundle_spec.synthetic_turns:
            return []

        profile = self._profile(record)
        profile.profile_version += 1
        profile.visa_intent["declared_family"] = record.declared_family
        turn_history = profile.ds160_view.setdefault("turn_history", [])
        if not isinstance(turn_history, list):
            turn_history = []
            profile.ds160_view["turn_history"] = turn_history
        field_claim_history = profile.ds160_view.setdefault("field_claim_history", {})
        if not isinstance(field_claim_history, dict):
            field_claim_history = {}
            profile.ds160_view["field_claim_history"] = field_claim_history

        payloads: list[dict[str, Any]] = []
        for turn_spec in bundle_spec.synthetic_turns:
            turn_record = self.turns.append_user_turn(
                session_id=record.session_id,
                content=turn_spec.content,
                source="debug_material_bundle",
                metadata_json={
                    "debug_material_bundle": True,
                    "synthetic_bundle_id": bundle_id,
                    "debug_bundle_scenario": bundle_spec.scenario,
                    "synthetic": True,
                },
                commit=False,
            )
            history_entry = {
                "turn_id": turn_record.turn_id,
                "turn_index": turn_record.turn_index,
                "role": turn_record.role,
                "content": turn_record.content,
                "source": turn_record.source,
            }
            turn_history.append(history_entry)
            for field_path, value in turn_spec.field_claims.items():
                self._apply_claim(profile, field_path, value, turn_record)
                field_history = field_claim_history.setdefault(field_path, [])
                if isinstance(field_history, list):
                    field_history.append(
                        {
                            "value": value,
                            "content": turn_spec.content,
                            "turn_id": turn_record.turn_id,
                            "turn_index": turn_record.turn_index,
                            "source": turn_record.source,
                        }
                    )
            payloads.append(
                {
                    "role": turn_spec.role,
                    "content": turn_spec.content,
                    "turn_id": turn_record.turn_id,
                    "field_claims": dict(turn_spec.field_claims),
                }
            )

        profile.ds160_view["turn_history"] = turn_history[-8:]
        record.profile_json = profile.model_dump(mode="json")
        self.db.add(record)
        self.db.flush()
        return payloads

    def _apply_claim(
        self,
        profile: ApplicantProfile,
        field_path: str,
        value: str,
        turn_record: SessionTurnRecord,
    ) -> None:
        binding = _CLAIM_FIELD_BINDINGS.get(field_path)
        if binding is None:
            return
        section, key = binding
        getattr(profile, section)[key] = value
        profile.field_states[field_path] = FieldStateRecord(state=FieldState.CLAIMED)
        profile.field_provenance[field_path] = FieldProvenanceRecord()
        profile.ds160_view["latest_user_message"] = turn_record.content
        profile.ds160_view["last_user_message"] = turn_record.content

    def _profile(self, record) -> ApplicantProfile:
        if record.profile_json:
            return ApplicantProfile.model_validate(record.profile_json)
        return ApplicantProfile.minimal(profile_id=f"profile-{record.session_id}")

    def _field_excerpt(self, text: str, field_path: str, value: str) -> str:
        field_name = field_path.rsplit("/", 1)[-1].replace("_", " ")
        for line in text.splitlines():
            normalized_line = line.casefold()
            if value.casefold() in normalized_line or field_name in normalized_line:
                return line[:240]
        return text[:240]

    def _safe_material_text(self, text: str) -> str:
        normalized_text = text.casefold()
        for marker in ORACLE_TEXT_MARKERS:
            if marker.casefold() in normalized_text:
                raise ValueError(f"synthetic material contains oracle marker: {marker}")
        return text

    def _document_type_label(self, document_type: str) -> str:
        return DOCUMENT_TYPE_LABELS.get(document_type, "材料待确认")
