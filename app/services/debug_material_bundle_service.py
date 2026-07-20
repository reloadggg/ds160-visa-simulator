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
    GeneratedMaterialBundleOutput,
    find_oracle_text_marker,
)
from app.services.case_memory_service import CaseMemoryService
from app.services.gate_runtime_service import GateRuntimeService
from app.services.material_generation_guard import MaterialGenerationGuard
from app.services.message_service import MessageService
from app.services.profile_recompute_service import ProfileRecomputeService
from app.services.runtime_errors import ModelRuntimeError


MaterialBundleSource = Literal["practice", "debug"]

DebugMaterialBundleScenario = Literal[
    "normal_f1_bundle",
    "normal_j1_bundle",
    "normal_b1_b2_bundle",
    "normal_h1b_bundle",
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


DEBUG_MATERIAL_BUNDLE_SCENARIOS: dict[str, str] = {
    "normal_f1_bundle": "自洽 F-1 基准材料包",
    "normal_j1_bundle": "自洽 J-1 交流访问材料包",
    "normal_b1_b2_bundle": "自洽 B-1/B-2 访问材料包",
    "normal_h1b_bundle": "自洽 H-1B 工作材料包",
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
    "ds2019": "DS-2019",
    "program_invitation": "项目邀请信",
    "sevis_fee_receipt": "SEVIS 缴费收据",
    "training_plan_ds7002": "DS-7002 培训计划",
    "insurance_proof": "保险证明",
    "itinerary_or_trip_purpose": "行程或访问目的说明",
    "invitation_letter": "邀请信",
    "employment_proof": "在职证明",
    "travel_history": "出入境记录",
    "family_ties_proof": "国内约束证明",
    "i797": "I-797 批准通知",
    "i129_petition": "I-129 申请材料",
    "employer_letter": "雇主证明信",
    "offer_letter": "Offer Letter",
    "lca": "LCA",
    "degree_certificate": "学历证明",
    "resume": "简历",
    "client_letter": "客户项目说明信",
}

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
        source: MaterialBundleSource = "debug",
        access_key_id: str | None = None,
        acquire_lock: bool = True,
    ) -> dict[str, Any]:
        final_payload: dict[str, Any] | None = None
        for event in self.create_bundle_events(
            session_id,
            scenario=scenario,
            include_synthetic_user_turns=include_synthetic_user_turns,
            seed_text=seed_text,
            generation_mode=generation_mode,
            include_accepted=False,
            source=source,
            access_key_id=access_key_id,
            acquire_lock=acquire_lock,
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
        source: MaterialBundleSource = "debug",
        access_key_id: str | None = None,
        acquire_lock: bool = True,
    ) -> Iterator[DebugMaterialBundleEvent]:
        if include_accepted:
            yield DebugMaterialBundleEvent("accepted", {"session_id": session_id})

        normalized_source: MaterialBundleSource = (
            "practice" if source == "practice" else "debug"
        )
        is_practice_material = normalized_source == "practice"
        meta_flag = (
            "practice_material_bundle" if is_practice_material else "debug_material_bundle"
        )
        refresh_reason_prefix = (
            "practice_material_bundle" if is_practice_material else "debug_material_bundle"
        )

        guard = MaterialGenerationGuard(self.db)
        # When acquire_lock is False the caller pre-acquired; we still release.
        lock_held = False
        materials_committed = False
        bundle_id = f"dbg-bundle-{uuid4().hex[:12]}"

        try:
            if acquire_lock:
                guard.acquire(
                    session_id,
                    access_key_id=access_key_id,
                    bundle_id=bundle_id,
                )
                lock_held = True
            else:
                lock_held = True

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
            if lock_held:
                guard.set_bundle_id(session_id, bundle_id)

            yield DebugMaterialBundleEvent(
                "debug_bundle_started",
                {
                    "session_id": record.session_id,
                    "bundle_id": bundle_id,
                    "scenario": bundle_spec.scenario,
                    "scenario_label": bundle_spec.scenario_label,
                    "document_count": len(bundle_spec.documents),
                    "generation_source": generation_metadata["source"],
                    "source": normalized_source,
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
                    source=normalized_source,
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
                source=normalized_source,
            )

            ProfileRecomputeService(self.db).recompute_session(
                record.session_id, save=False
            )
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

            # Bundle injects material_understanding without upsert_material_understanding.
            CaseMemoryService(self.db).rebuild_and_persist(record.session_id)
            self.db.commit()
            materials_committed = True
            yield DebugMaterialBundleEvent(
                "document_review_started",
                {"session_id": record.session_id, "bundle_id": bundle_id},
            )

            main_flow_response: dict[str, Any] = {}
            refresh_error: str | None = None
            try:
                main_flow_response = MessageService(self.db).refresh_after_material_change(
                    record.session_id,
                    reason=f"{refresh_reason_prefix}:{bundle_spec.scenario}",
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

            user_summary_zh = str(
                generation_metadata.get("user_summary_zh") or ""
            ).strip() or self._build_user_summary_zh_fallback(
                bundle_spec,
                seed_text=str(generation_metadata.get("seed_text_preview") or ""),
                source=normalized_source,
            )
            # Prefer structured Chinese brief for practice UI; hide oracle findings
            # from the top-level user-facing summary path.
            document_briefs = [
                {
                    "document_id": item.get("document_id"),
                    "document_type": item.get("document_type"),
                    "document_type_label": item.get("document_type_label")
                    or DOCUMENT_TYPE_LABELS.get(
                        str(item.get("document_type") or ""), "材料"
                    ),
                    "filename": item.get("filename"),
                    "highlights": self._field_highlights_zh(item.get("fields") or {}),
                }
                for item in created_documents
            ]
            final_payload: dict[str, Any] = {
                "session_id": record.session_id,
                "bundle_id": bundle_id,
                "scenario": bundle_spec.scenario,
                "scenario_label": bundle_spec.scenario_label,
                "documents": created_documents,
                "synthetic_turns": synthetic_turn_payloads,
                "user_summary_zh": user_summary_zh,
                "document_briefs_zh": document_briefs,
                "is_practice_material": is_practice_material,
                "source": normalized_source,
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
            # Debug retains oracle findings; practice omits them entirely.
            if not is_practice_material:
                final_payload["expected_findings"] = [
                    finding.__dict__ for finding in bundle_spec.expected_findings
                ]
            if lock_held:
                guard.complete(session_id)
                lock_held = False
            yield DebugMaterialBundleEvent("final", final_payload)
        except Exception:
            if materials_committed:
                self._best_effort_tombstone_bundle(
                    session_id,
                    bundle_id=bundle_id,
                    reason=f"{meta_flag}_generation_failed",
                )
            if lock_held:
                try:
                    guard.fail(session_id)
                except Exception:
                    pass
            raise

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
            requested_seed=requested_seed,
        )
        if not resolved_seed:
            raise ModelRuntimeError(
                detail="请先填写材料生成依据；系统不会生成或写入演示占位材料。",
                status_code=422,
            )
        if normalized_mode not in {"ai", "ai_if_seeded", "ai_if_available"}:
            raise ModelRuntimeError(
                detail="固定演示材料包已移除；请提供材料生成依据并使用 AI 生成。",
                status_code=422,
            )

        try:
            generated, trace = AIMaterialBundleGeneratorService(self.db).generate(
                record=record,
                scenario=scenario,  # type: ignore[arg-type]
                seed_text=resolved_seed,
                include_synthetic_user_turns=include_synthetic_user_turns,
            )
            bundle_spec = self._bundle_spec_from_generated_output(
                scenario,
                generated,
            )
            user_summary_zh = (generated.user_summary_zh or "").strip() or (
                self._build_user_summary_zh_fallback(bundle_spec, resolved_seed)
            )
            return bundle_spec, {
                "source": "ai",
                "mode": normalized_mode,
                "seed_text_present": True,
                "seed_source": seed_source,
                "request_seed_text_present": bool(requested_seed),
                "user_summary_zh": user_summary_zh,
                "seed_text_preview": resolved_seed[:200],
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
                error_category=exc.error_category,
                body=exc.body,
                missing_env_vars=exc.missing_env_vars,
            ) from exc

    def _resolve_generation_seed(
        self,
        *,
        requested_seed: str,
    ) -> tuple[str, str | None]:
        if requested_seed:
            return requested_seed, "request"
        return "", None

    def _build_user_summary_zh_fallback(
        self,
        bundle_spec: DebugMaterialBundleSpec,
        seed_text: str = "",
        *,
        source: MaterialBundleSource = "debug",
    ) -> str:
        if source == "practice":
            lines: list[str] = [
                f"已根据你的描述生成「{bundle_spec.scenario_label}」练习材料（虚构，仅供模拟面签）。"
            ]
        else:
            lines = [
                f"已根据你的描述生成「{bundle_spec.scenario_label}」调试材料包（虚构，仅供内部验证）。"
            ]
        if seed_text.strip():
            preview = seed_text.strip().replace("\n", " ")
            if len(preview) > 120:
                preview = preview[:120] + "…"
            lines.append(f"生成依据摘要：{preview}")
        type_labels: list[str] = []
        for doc in bundle_spec.documents:
            label = DOCUMENT_TYPE_LABELS.get(doc.document_type, doc.document_type)
            if label not in type_labels:
                type_labels.append(label)
        if type_labels:
            lines.append("包含材料：" + "、".join(type_labels) + "。")
        if source == "practice":
            lines.append(
                "请结合右侧「练习材料说明」与材料库查看要点；正式签证请使用真实证件。"
            )
        else:
            lines.append("请在调试台核对 expected_findings 与 case memory 注入结果。")
        return "\n".join(lines)

    def _field_highlights_zh(self, fields: dict[str, Any]) -> list[dict[str, str]]:
        label_map = {
            "/identity/full_name": "姓名",
            "/identity/passport_number": "护照号",
            "/identity/nationality": "国籍",
            "/education/school_name": "学校",
            "/education/program_name": "项目/专业",
            "/education/degree_level": "学位",
            "/funding/primary_source": "资金来源",
            "/funding/amount": "金额",
            "/funding/sponsor_name": "资助人",
            "/visa_intent/purpose": "赴美目的",
            "/employment/employer_name": "雇主",
            "/employment/job_title": "职位",
        }
        highlights: list[dict[str, str]] = []
        for path, label in label_map.items():
            value = fields.get(path)
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            highlights.append({"label": label, "value": text})
            if len(highlights) >= 4:
                break
        return highlights

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

    def _create_parsed_document(
        self,
        session_id: str,
        *,
        bundle_id: str,
        bundle_spec: DebugMaterialBundleSpec,
        document_spec: SyntheticDocumentSpec,
        generation_metadata: dict[str, Any],
        source: MaterialBundleSource = "debug",
    ) -> tuple[DocumentRecord, dict[str, Any], int]:
        is_practice = source == "practice"
        meta_flag = "practice_material_bundle" if is_practice else "debug_material_bundle"
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
            "parser_name": meta_flag,
            "status": "parsed",
            "page_count": 1,
            "metadata": {
                "debug_fill": not is_practice,
                meta_flag: True,
                "synthetic_bundle_id": bundle_id,
                "debug_bundle_scenario": bundle_spec.scenario,
                "debug_bundle_scenario_label": bundle_spec.scenario_label,
                "document_type": document_spec.document_type,
                "debug_generation": dict(generation_metadata),
                "material_bundle_source": source,
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
                "debug_fill": not is_practice,
                meta_flag: True,
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
                    "debug_fill": not is_practice,
                    meta_flag: True,
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
            source=source,
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
        source: MaterialBundleSource = "debug",
    ) -> MaterialUnderstandingResult:
        is_practice = source == "practice"
        meta_flag = "practice_material_bundle" if is_practice else "debug_material_bundle"
        # Domain EvidenceSourceType has no practice-specific value; metadata carries the split.
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
        # Practice must not inject oracle-driven conflicts from expected_findings.
        conflicts = (
            []
            if is_practice
            else self._material_conflicts(
                document=document,
                bundle_spec=bundle_spec,
                claims=claims,
                evidence_cards=evidence_cards,
            )
        )
        if is_practice:
            why_it_matters = "练习材料提供了一个可核验的案例事实。"
            next_move_question = (
                "练习材料已加入案例理解。请继续说明它和你的签证计划有什么关系。"
            )
            next_move_reason = "练习材料已作为案例证据写入 Case Memory。"
        else:
            why_it_matters = "调试材料包提供了一个可核验的案例事实。"
            next_move_question = (
                "材料已经加入案例理解。请继续说明它和你的签证计划有什么关系。"
            )
            next_move_reason = "调试材料包已作为案例证据写入 Case Memory。"
        proof_points = [
            ProofPoint(
                proof_point_id=f"proof-{document.document_id}-{index}",
                visa_family="unknown",
                question=f"请说明 {field_path} 如何支持你的签证计划。",
                status="supported",
                why_it_matters=why_it_matters,
                claim_refs=[claim.claim_id],
                evidence_refs=list(claim.supporting_evidence_ids),
                metadata={meta_flag: True},
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
                    question=next_move_question,
                    reason=next_move_reason,
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
            "proof_points": [
                item.model_dump(mode="json") for item in result.proof_points
            ],
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
        source: MaterialBundleSource = "debug",
    ) -> list[dict[str, Any]]:
        if not bundle_spec.synthetic_turns:
            return []

        is_practice = source == "practice"
        meta_flag = "practice_material_bundle" if is_practice else "debug_material_bundle"
        turn_source = meta_flag

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
                source=turn_source,
                metadata_json={
                    meta_flag: True,
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

    def _best_effort_tombstone_bundle(
        self,
        session_id: str,
        *,
        bundle_id: str,
        reason: str,
    ) -> None:
        """Tombstone docs for a failed generation after materials were committed."""
        try:
            self.db.rollback()
        except Exception:
            pass
        try:
            documents = self.documents.list_session_documents(session_id)
            document_ids: list[str] = []
            for document in documents:
                artifact = dict(document.artifact_json or {})
                metadata = dict(artifact.get("metadata") or {})
                if metadata.get("synthetic_bundle_id") != bundle_id:
                    continue
                if DocumentRepository.is_document_tombstoned(document):
                    continue
                document_ids.append(document.document_id)
            if document_ids:
                CaseMemoryService(self.db).tombstone_documents(
                    document_ids=document_ids,
                    reason=reason,
                )
            CaseMemoryService(self.db).rebuild_and_persist(session_id)
            self.db.commit()
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass

    def _field_excerpt(self, text: str, field_path: str, value: str) -> str:
        field_name = field_path.rsplit("/", 1)[-1].replace("_", " ")
        for line in text.splitlines():
            normalized_line = line.casefold()
            if value.casefold() in normalized_line or field_name in normalized_line:
                return line[:240]
        return text[:240]

    def _safe_material_text(self, text: str) -> str:
        marker = find_oracle_text_marker(text)
        if marker is not None:
            raise ValueError(f"synthetic material contains oracle marker: {marker}")
        return text

    def _document_type_label(self, document_type: str) -> str:
        return DOCUMENT_TYPE_LABELS.get(document_type, "材料待确认")
