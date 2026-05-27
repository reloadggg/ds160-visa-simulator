from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy.orm import Session

from app.agents.adjudication_agent import AdjudicationAgentRunner
from app.agents.model_factory import AgentModelFactory
from app.agents.schemas import AgentRuntimeDeps, InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, GovernorDecision, ScoreState
from app.domain.runtime import (
    GovernorHistoryEntry,
    PromptRoleContract,
    RiskFlagHistoryEntry,
    RuntimeTraceEntry,
    ScoreHistoryEntry,
    TurnAdvisoryContext,
)
from app.repositories.session_repo import SessionRepository
from app.repositories.document_repo import DocumentRepository
from app.services.advisory_review_service import AdvisoryReviewService
from app.services.capability_orchestrator import CapabilityOrchestrator
from app.services.consistency_service import ConsistencyService
from app.services.ds160_context_engine import DS160ContextEngine
from app.services.ds160_memory_manager import DS160MemoryManager
from app.services.evidence_service import EvidenceService
from app.services.extractor_service import ExtractorService
from app.services.retrieval_service import RetrievalService
from app.services.runtime_errors import (
    ModelRuntimeError,
    ModelUnavailableError,
    ProviderAPIError,
)
from app.services.scoring_service import ScoringService
from app.services.session_read_model_service import SessionReadModelService
from app.services.visa_policy_retrieval_service import VisaPolicyRetrievalService

TURN_DECISION_PROVIDER_MAX_RETRIES = 1


@dataclass
class InterviewTurnAnalysis:
    profile: ApplicantProfile
    trace_entries: list[RuntimeTraceEntry]
    score: ScoreState
    findings: list[dict[str, Any]]


class InterviewRuntimeService:
    def __init__(self, db: Session | Any) -> None:
        self.db = db
        self.model_factory = AgentModelFactory()
        self.session_repo = SessionRepository(db)
        self.document_repo = DocumentRepository(db)
        self.session_read_model = SessionReadModelService(db)
        self.extractor = ExtractorService(db)
        self.consistency = ConsistencyService()
        self.scoring = ScoringService(db)
        self.advisory_review = AdvisoryReviewService()
        self.memory_manager = DS160MemoryManager()
        self.context_engine = DS160ContextEngine()
        self.capability_orchestrator = CapabilityOrchestrator(db)
        self._last_capability_trace_entries: list[RuntimeTraceEntry] = []
        self._last_capability_tool_outputs: dict[str, Any] = {}

    def analyze_turn(
        self,
        record: SessionRecord,
        message_text: str,
        recent_turns: list[Any] | None = None,
    ) -> InterviewTurnAnalysis:
        profile = self._load_profile(record.session_id, record.profile_json)
        trace_entries: list[RuntimeTraceEntry] = []

        trace_entries.append(self._receive_input())
        profile = self._extract_claims(
            record,
            profile,
            message_text,
            trace_entries,
            recent_turns=recent_turns,
        )
        self._resolve_evidence(profile, trace_entries)
        findings = self._consistency_check(profile, trace_entries)
        score = self._score_case(profile, findings, trace_entries)

        return InterviewTurnAnalysis(
            profile=profile,
            trace_entries=trace_entries,
            score=score,
            findings=findings,
        )

    def analyze_material_change(
        self,
        record: SessionRecord,
        *,
        reason: str,
    ) -> InterviewTurnAnalysis:
        profile = self._load_profile(record.session_id, record.profile_json)
        trace_entries: list[RuntimeTraceEntry] = [
            RuntimeTraceEntry(
                node_name="material_changed",
                summary=reason,
            )
        ]

        self._resolve_evidence(profile, trace_entries)
        findings = self._consistency_check(profile, trace_entries)
        score = self._score_case(profile, findings, trace_entries)

        return InterviewTurnAnalysis(
            profile=profile,
            trace_entries=trace_entries,
            score=score,
            findings=findings,
        )

    def _receive_input(self) -> RuntimeTraceEntry:
        return RuntimeTraceEntry(
            node_name="receive_input",
            summary="user_message_received",
        )

    def _extract_claims(
        self,
        record: SessionRecord,
        profile: ApplicantProfile,
        message_text: str,
        trace_entries: list[RuntimeTraceEntry],
        *,
        recent_turns: list[Any] | None = None,
    ) -> ApplicantProfile:
        profile.profile_version += 1
        profile.visa_intent["declared_family"] = record.declared_family
        previous_profile = profile.model_copy(deep=True)
        updated_profile = self.extractor.apply_message(
            profile,
            message_text,
            recent_turns=recent_turns,
        )
        updated_profile = self._preserve_gate_ready_fields(previous_profile, updated_profile)
        trace_entries.append(
            RuntimeTraceEntry(
                node_name="extract_claims",
                summary=f"profile_version={updated_profile.profile_version}",
            )
        )
        return updated_profile

    def _resolve_evidence(
        self,
        profile: ApplicantProfile,
        trace_entries: list[RuntimeTraceEntry],
    ) -> None:
        documented_refs = {
            evidence_ref
            for provenance in profile.field_provenance.values()
            for evidence_ref in provenance.evidence_refs
        }
        trace_entries.append(
            RuntimeTraceEntry(
                node_name="resolve_evidence",
                summary=f"documented_refs={len(documented_refs)}",
            )
        )

    def _consistency_check(
        self,
        profile: ApplicantProfile,
        trace_entries: list[RuntimeTraceEntry],
    ) -> list[dict[str, Any]]:
        findings = self.consistency.evaluate(profile)
        trace_entries.append(
            RuntimeTraceEntry(
                node_name="consistency_check",
                summary=f"findings={len(findings)}",
            )
        )
        return findings

    def _score_case(
        self,
        profile: ApplicantProfile,
        findings: list[dict[str, Any]],
        trace_entries: list[RuntimeTraceEntry],
    ) -> ScoreState:
        score = self.scoring.propose(profile, findings, scoring_stage="interview_turn")
        trace_entries.append(
            RuntimeTraceEntry(
                node_name="score_case",
                summary=self._score_summary(score),
            )
        )
        return score

    def build_question_action(
        self,
        session_id: str,
        profile: ApplicantProfile,
        score: ScoreState,
        governor_decision: str,
        trace_entries: list[RuntimeTraceEntry] | None = None,
        recent_turns: list[Any] | None = None,
    ) -> InterviewNextAction:
        self._last_capability_trace_entries = []
        self._last_capability_tool_outputs = {}
        action, runtime_trace = self._question_action(
            session_id,
            profile,
            score,
            governor_decision,
            recent_turns=recent_turns,
        )
        if trace_entries is not None:
            trace_entries.extend(self._last_capability_trace_entries)
            trace_entries.append(runtime_trace)
        self._last_capability_trace_entries = []
        return action

    def _build_score_history_entry(self, score: ScoreState) -> ScoreHistoryEntry:
        return ScoreHistoryEntry(
            scoring_stage=score.scoring_stage,
            category_fit=score.category_fit,
            document_readiness=score.document_readiness,
            narrative_consistency=score.narrative_consistency,
            confidence=score.confidence,
            missing_evidence=list(score.missing_evidence),
            risk_flags=[
                RiskFlagHistoryEntry(
                    code=item.code,
                    severity=item.severity,
                    status=item.status,
                    evidence_refs=list(item.evidence_refs),
                )
                for item in score.risk_flags
            ],
            summary=self._score_summary(score),
        )

    def _build_governor_history_entry(self, decision: str) -> GovernorHistoryEntry:
        return GovernorHistoryEntry(
            decision=decision,
            summary=f"decision={decision}",
        )

    def _score_summary(self, score: ScoreState) -> str:
        return f"missing={len(score.missing_evidence)} risk_flags={len(score.risk_flags)}"

    def _load_profile(self, session_id: str, profile_json: dict) -> ApplicantProfile:
        if profile_json:
            return ApplicantProfile.model_validate(profile_json)
        return ApplicantProfile.minimal(profile_id=f"profile-{session_id}")

    def _preserve_gate_ready_fields(
        self,
        previous_profile: ApplicantProfile,
        updated_profile: ApplicantProfile,
    ) -> ApplicantProfile:
        field_path = "/funding/primary_source"
        previous_state = previous_profile.field_states.get(field_path)
        updated_state = updated_profile.field_states.get(field_path)
        if previous_state is None or updated_state is None:
            return updated_profile

        if previous_state.state not in {"documented", "confirmed"}:
            return updated_profile
        if updated_state.state not in {"claimed", "unknown"}:
            return updated_profile

        previous_provenance = previous_profile.field_provenance.get(field_path)
        if previous_provenance is None or not previous_provenance.evidence_refs:
            return updated_profile

        updated_profile.field_states[field_path] = previous_state.model_copy(deep=True)
        updated_profile.field_provenance[field_path] = previous_provenance.model_copy(
            deep=True
        )
        if "primary_source" in previous_profile.funding:
            updated_profile.funding["primary_source"] = previous_profile.funding["primary_source"]
        return updated_profile

    def _question_action(
        self,
        session_id: str,
        profile: ApplicantProfile,
        score: ScoreState,
        governor_decision: str,
        recent_turns: list[Any] | None = None,
    ) -> tuple[InterviewNextAction, RuntimeTraceEntry]:
        self._last_capability_trace_entries = []
        self._last_capability_tool_outputs = {}
        declared_family = profile.visa_intent.get("declared_family")
        model, runtime = self._build_turn_decision_agent_runtime(declared_family)
        self._raise_if_question_model_unavailable(
            runtime=runtime,
            governor_decision=governor_decision,
            score=score,
        )
        latest_user_message = self._latest_user_message(recent_turns)
        dynamic_turn_context = self._build_dynamic_turn_context(
            session_id=session_id,
            profile=profile,
            score=score,
            governor_decision=governor_decision,
            recent_turns=recent_turns,
            latest_user_message=latest_user_message,
            declared_family=declared_family,
        )
        capability_result = self.capability_orchestrator.orchestrate(
            session_id=session_id,
            governor_decision=governor_decision,
            latest_user_message=latest_user_message,
            dynamic_turn_context=dynamic_turn_context,
            score=score,
        )
        dynamic_turn_context["capability_plan"] = list(
            capability_result.capability_plan
        )
        reply_guidance = self._build_reply_guidance(
            capability_result.tool_outputs
        )
        if reply_guidance is not None:
            dynamic_turn_context["reply_guidance"] = reply_guidance
        self._last_capability_tool_outputs = dict(capability_result.tool_outputs)
        if model is None:
            raise ModelUnavailableError(
                detail=runtime.get("model_unavailable_detail")
                or "当前后端未配置可用的对话模型，无法生成面签问答。",
                provider=runtime.get("provider"),
                model=runtime.get("model"),
                missing_env_vars=list(
                    runtime.get("model_unavailable_missing_env_vars") or []
                ),
            )
        runner = AdjudicationAgentRunner(
            model=model,
            instructions=runtime.get("instructions")
            or self.model_factory.build_instructions(
                "adjudication_agent",
                declared_family=declared_family,
            ),
        )
        retry_count = 0
        while True:
            try:
                run_result = runner.run(
                    deps=self._build_agent_deps(session_id),
                    dynamic_turn_context=dynamic_turn_context,
                    tool_outputs=capability_result.tool_outputs,
                    user_message=latest_user_message,
                    boundary_decision=governor_decision,
                )
                break
            except Exception as exc:
                normalized = self._normalize_turn_decision_error(
                    exc,
                    runtime=runtime,
                )
                if not self._should_retry_turn_decision_provider_error(
                    normalized,
                    retry_count=retry_count,
                ):
                    raise normalized from exc
                retry_count += 1

        try:
            action = self._finalize_question_action(
                governor_decision,
                score,
                run_result.output,
                capability_tool_outputs=capability_result.tool_outputs,
            )
            self._last_capability_trace_entries = [
                RuntimeTraceEntry(
                    node_name="governor_decide",
                    summary=f"decision={governor_decision}",
                ),
                *list(capability_result.trace_entries),
            ]
            return action, self._build_turn_decision_trace(
                runtime=runtime,
                action=action,
                fallback_used=False,
                tool_calls=run_result.tool_calls,
                retry_count=retry_count + run_result.retry_count,
                provider=run_result.provider or runtime.get("provider"),
                model=run_result.model or runtime.get("model"),
                boundary_decision=governor_decision,
                capability_tool_outputs=capability_result.tool_outputs,
            )
        except Exception as exc:
            raise self._normalize_turn_decision_error(
                exc,
                runtime=runtime,
            ) from exc

    def _build_reply_guidance(
        self,
        capability_tool_outputs: dict[str, Any],
    ) -> dict[str, Any] | None:
        document_review = (
            capability_tool_outputs.get("document_review")
            if isinstance(capability_tool_outputs, dict)
            else None
        )
        if not isinstance(document_review, dict):
            return None

        conflict = self._active_document_review_conflict(document_review)
        if conflict is None:
            return None

        return {
            "priority": "clarify_active_material_conflict",
            "source": "document_review",
            "recommended_next_step": self._runtime_text(
                document_review.get("recommended_next_step")
            )
            or "clarify_conflict",
            "active_conflict_to_clarify": self._safe_conflict_reply_context(
                conflict
            ),
            "assistant_message_guidance": [
                "用自然面签口吻承接已收到的材料，点出冲突主题，并要求申请人解释或澄清。",
                "不要照抄固定模板；根据用户语言、最近对话和冲突主题自行组织一到两句。",
                "不要暴露内部字段名、材料编号、证据编号、审核状态、审核摘要或内部风险编号。",
            ],
        }

    def _active_document_review_conflict(
        self,
        document_review: dict[str, Any],
    ) -> dict[str, Any] | None:
        cross_document_conflicts = [
            item
            for item in document_review.get("cross_document_conflicts", []) or []
            if isinstance(item, dict)
        ]
        claim_conflicts = [
            item
            for item in document_review.get("claim_conflicts", []) or []
            if isinstance(item, dict)
        ]
        conflicts = [*cross_document_conflicts, *claim_conflicts]
        high_conflict = next(
            (
                item
                for item in conflicts
                if self._is_confirmed_high_reply_conflict(item)
            ),
            None,
        )
        if high_conflict is not None:
            return high_conflict

        review_status = self._runtime_text(document_review.get("review_status"))
        recommended_next_step = self._runtime_text(
            document_review.get("recommended_next_step")
        )
        if review_status not in {"needs_clarification", "high_risk"} and (
            recommended_next_step not in {"clarify_conflict", "high_risk_review"}
        ):
            return None
        return next(iter(conflicts), None)

    def _is_confirmed_high_reply_conflict(
        self,
        conflict: dict[str, Any],
    ) -> bool:
        if conflict.get("severity") != "high":
            return False
        summary = (self._runtime_text(conflict.get("summary")) or "").casefold()
        has_material_anchor = bool(
            self._normalized_string_list(conflict.get("document_ids"))
            or self._normalized_string_list(conflict.get("evidence_refs"))
        )
        if self._looks_like_unverified_missing_evidence(summary):
            return has_material_anchor
        conflict_type = self._runtime_text(conflict.get("conflict_type"))
        if conflict_type in {"document_vs_document", "claim_vs_document"}:
            return True
        if conflict_type != "missing_verification":
            return False
        if not self._normalized_string_list(conflict.get("document_ids")):
            return False
        field_paths = set(self._normalized_string_list(conflict.get("field_paths")))
        return {
            "/education/first_year_cost",
            "/funding/available_funds",
        }.issubset(field_paths)

    def _looks_like_unverified_missing_evidence(self, summary: str) -> bool:
        if not summary:
            return False
        missing_markers = (
            "no funding proof",
            "no sponsor",
            "not provided",
            "has not been provided",
            "missing",
            "unverified",
            "not verified",
            "awaiting",
            "缺少",
            "未提供",
            "未提交",
            "未验证",
            "待验证",
            "待补",
        )
        return any(marker in summary for marker in missing_markers)

    def _safe_conflict_reply_context(
        self,
        conflict: dict[str, Any],
    ) -> dict[str, Any]:
        conflict_type = (
            self._runtime_text(conflict.get("conflict_type"))
            or "material_conflict"
        )
        field_paths = set(self._normalized_string_list(conflict.get("field_paths")))
        return {
            "conflict_type": conflict_type,
            "severity": self._runtime_text(conflict.get("severity")) or "unknown",
            "subject": self._conflict_reply_subject(
                conflict_type=conflict_type,
                field_paths=field_paths,
            ),
            "field_labels": self._field_labels_for_reply(field_paths),
            "material_labels": self._material_labels_for_reply(
                conflict_type=conflict_type,
                field_paths=field_paths,
            ),
        }

    def _conflict_reply_subject(
        self,
        *,
        conflict_type: str,
        field_paths: set[str],
    ) -> str:
        if {
            "/education/first_year_cost",
            "/funding/available_funds",
        }.issubset(field_paths):
            return "资金证明金额与 I-20 第一年度费用"
        if "/education/school_name" in field_paths or "/education/program_name" in field_paths:
            if conflict_type == "claim_vs_document":
                return "口头说明的学校/项目信息与 I-20/录取信材料"
            return "I-20 与录取信里的学校/项目信息"
        if "/identity/passport_number" in field_paths:
            if conflict_type == "claim_vs_document":
                return "口头说明的护照信息与护照材料"
            return "DS-160 与护照首页里的护照号码"
        if "/identity/full_name" in field_paths:
            if conflict_type == "claim_vs_document":
                return "口头身份说明与材料上的姓名"
            return "多份身份材料上的姓名"
        if "/funding/primary_source" in field_paths:
            if conflict_type == "claim_vs_document":
                return "口头说明的资金来源与资金证明"
            return "资金证明和其他资助材料里的资金来源"
        if "/funding/sponsor_relationship" in field_paths or "/family/parent_names" in field_paths:
            if conflict_type == "claim_vs_document":
                return "口头说明的资助人关系与亲属关系材料"
            return "资金材料和亲属关系材料里的资助人关系"
        if conflict_type == "claim_vs_document":
            return "口头说明与已提交材料"
        return "已提交材料之间的关键信息"

    def _field_labels_for_reply(self, field_paths: set[str]) -> list[str]:
        labels_by_path = {
            "/identity/full_name": "姓名",
            "/identity/passport_number": "护照号码",
            "/identity/nationality": "国籍",
            "/education/school_name": "学校名称",
            "/education/program_name": "项目/专业名称",
            "/education/sevis_id": "SEVIS 编号",
            "/education/first_year_cost": "I-20 第一年度费用",
            "/funding/primary_source": "资金来源",
            "/funding/available_funds": "资金证明金额",
            "/funding/sponsor_relationship": "资助人关系",
            "/family/parent_names": "父母/资助人姓名",
        }
        labels = [
            label
            for path, label in labels_by_path.items()
            if path in field_paths
        ]
        return labels or ["关键信息"]

    def _material_labels_for_reply(
        self,
        *,
        conflict_type: str,
        field_paths: set[str],
    ) -> list[str]:
        if {
            "/education/first_year_cost",
            "/funding/available_funds",
        }.issubset(field_paths):
            return ["I-20", "资金证明"]
        labels: list[str] = []
        if conflict_type == "claim_vs_document":
            labels.append("口头说明")
        if "/education/school_name" in field_paths or "/education/program_name" in field_paths:
            labels.extend(["I-20", "录取信"])
        if "/identity/passport_number" in field_paths:
            labels.extend(["DS-160 确认页", "护照首页"])
        if "/identity/full_name" in field_paths:
            labels.append("身份材料")
        if "/funding/primary_source" in field_paths or "/funding/available_funds" in field_paths:
            labels.append("资金证明")
        if "/funding/sponsor_relationship" in field_paths or "/family/parent_names" in field_paths:
            labels.append("亲属关系证明")
        return self._dedupe_preserve_order(labels) or ["已提交材料"]

    def _dedupe_preserve_order(self, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            if value in normalized:
                continue
            normalized.append(value)
        return normalized

    def _should_retry_turn_decision_provider_error(
        self,
        error: ModelRuntimeError,
        *,
        retry_count: int,
    ) -> bool:
        if retry_count >= TURN_DECISION_PROVIDER_MAX_RETRIES:
            return False
        if isinstance(error, ProviderAPIError):
            return error.status_code in {500, 502, 503, 504}
        return error.status_code == 503 and error.upstream_code != "missing_model_config"

    def _build_turn_decision_agent_runtime(
        self,
        declared_family: str | None,
    ) -> tuple[Any | None, dict[str, Any]]:
        try:
            return self.model_factory.build(
                "adjudication_agent",
                "interview_turn",
                declared_family=declared_family,
            )
        except TypeError:
            return self.model_factory.build("adjudication_agent", "interview_turn")

    def _raise_if_question_model_unavailable(
        self,
        *,
        runtime: dict[str, Any],
        governor_decision: str,
        score: ScoreState,
    ) -> None:
        del governor_decision, score
        if runtime.get("model_unavailable_reason") != "missing_openai_config":
            return
        raise ModelUnavailableError(
            detail=runtime.get("model_unavailable_detail")
            or "当前后端未配置可用的对话模型，无法生成面签问答。",
            provider=runtime.get("provider"),
            model=runtime.get("model"),
            missing_env_vars=list(
                runtime.get("model_unavailable_missing_env_vars") or []
            ),
        )

    def _normalize_turn_decision_error(
        self,
        exc: Exception,
        *,
        runtime: dict[str, Any],
    ) -> ModelRuntimeError:
        if isinstance(exc, ModelRuntimeError):
            return exc

        provider = self._runtime_text(runtime.get("provider"))
        model = self._runtime_text(runtime.get("model"))

        if isinstance(exc, ModelHTTPError):
            status_code = exc.status_code
            upstream_code = self._model_error_code(exc.body)
            return ProviderAPIError(
                detail=self._model_http_error_detail(
                    status_code,
                    upstream_code=upstream_code,
                ),
                status_code=status_code,
                provider=provider,
                model=model or exc.model_name,
                upstream_code=upstream_code,
                body=exc.body,
            )

        return ModelRuntimeError(
            detail="当前对话模型运行失败，请稍后重试。",
            status_code=503,
            provider=provider,
            model=model,
        )

    def _model_http_error_detail(
        self,
        status_code: int,
        *,
        upstream_code: str | None,
    ) -> str:
        normalized_code = (upstream_code or "").upper()
        if status_code == 401:
            return "当前对话模型认证失败，API Key 可能已失效或被禁用。"
        if status_code == 429:
            if normalized_code in {
                "API_KEY_QUOTA_EXHAUSTED",
                "INSUFFICIENT_QUOTA",
                "QUOTA_EXCEEDED",
            }:
                return "当前对话模型额度已耗尽，请稍后重试或更换可用配置。"
            return "当前对话模型请求过于频繁，请稍后重试。"
        if status_code == 503:
            return "当前对话模型暂时不可用，请稍后重试。"
        if 500 <= status_code < 600:
            return "当前对话模型服务暂时异常，请稍后重试。"
        return "当前对话模型运行失败，请稍后重试。"

    def _model_error_code(self, body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        code = body.get("code")
        return self._runtime_text(code)

    def _runtime_text(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _normalized_string_list(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized = [
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip()
        ]
        return sorted(set(normalized))

    def _build_agent_deps(self, session_id: str) -> AgentRuntimeDeps:
        return AgentRuntimeDeps(
            session_id=session_id,
            retrieval=RetrievalService(self.db),
            evidence=EvidenceService(self.db),
            policy_retrieval=VisaPolicyRetrievalService(),
        )

    def _finalize_question_action(
        self,
        governor_decision: str,
        score: ScoreState,
        action: InterviewNextAction,
        capability_tool_outputs: dict[str, Any] | None = None,
    ) -> InterviewNextAction:
        del governor_decision, score, capability_tool_outputs
        requested_documents = (
            self._coerce_requested_documents(action.requested_documents)
            if action.decision == GovernorDecision.NEED_MORE_EVIDENCE.value
            else []
        )
        focus_document_type = action.focus_document_type
        if requested_documents and action.focus_kind == "required_document":
            focus_document_type = requested_documents[0]
        return InterviewNextAction(
            decision=action.decision,
            assistant_message=action.assistant_message,
            requested_documents=requested_documents,
            focus_kind=action.focus_kind,
            focus_document_type=focus_document_type,
            focus_risk_code=action.focus_risk_code,
            reason=action.reason,
        )

    def _coerce_requested_documents(
        self,
        *document_groups: list[str] | None,
    ) -> list[str]:
        for document_group in document_groups:
            if not document_group:
                continue
            for item in document_group:
                document_type = item.strip()
                if document_type:
                    return [document_type]
        return []

    def _latest_user_message(self, recent_turns: list[Any] | None) -> str:
        if recent_turns is None:
            return ""
        for turn in reversed(recent_turns):
            if getattr(turn, "role", None) != "user":
                continue
            content = getattr(turn, "content", "")
            if isinstance(content, str):
                return content
        return ""

    def _build_dynamic_turn_context(
        self,
        *,
        session_id: str,
        profile: ApplicantProfile,
        score: ScoreState,
        governor_decision: str,
        recent_turns: list[Any] | None,
        latest_user_message: str,
        declared_family: str | None,
    ) -> dict[str, Any]:
        record = self.session_repo.get(session_id)
        phase_state = getattr(record, "phase_state", None) or "interview"
        gate_progress = (
            dict(getattr(record, "gate_status_json", {}) or {})
            if record is not None
            else {}
        )
        read_model = None
        documents = []
        if record is not None:
            read_model = self.session_read_model.build_from_record(
                record,
                turns=recent_turns,
            )
            documents = self.document_repo.list_session_documents(session_id)
        advisory_context = self._build_advisory_context(score)
        memory_bundle = self.memory_manager.build(
            profile=profile,
            score=score,
            advisory_context=advisory_context,
            read_model=read_model,
            declared_family=declared_family,
            phase_state=phase_state,
            boundary_decision=governor_decision,
            documents=documents,
            gate_progress=gate_progress,
        )
        snapshot = self.context_engine.build_dynamic_turn_context(
            session_id=session_id,
            declared_family=declared_family,
            phase_state=phase_state,
            latest_user_message=latest_user_message,
            profile=profile,
            advisory_context=advisory_context,
            gate_progress=gate_progress,
            recent_turns=recent_turns,
            memory_bundle=memory_bundle,
            capability_plan=[],
            prompt_roles=PromptRoleContract(),
        )
        return snapshot.model_dump(mode="json")

    def _build_advisory_context(self, score: ScoreState) -> TurnAdvisoryContext:
        return self.advisory_review.build_context(score)

    def _risk_level_from_score(self, score: ScoreState) -> InterviewRiskLevel:
        return self.advisory_review.derive_risk_level(score)

    def _build_turn_decision_trace(
        self,
        *,
        runtime: dict[str, Any],
        action: InterviewNextAction,
        fallback_used: bool,
        tool_calls: list[dict[str, Any]],
        retry_count: int,
        provider: str | None,
        model: str | None,
        boundary_decision: str,
        capability_tool_outputs: dict[str, Any],
    ) -> RuntimeTraceEntry:
        document_review = (
            capability_tool_outputs.get("document_review", {})
            if isinstance(capability_tool_outputs, dict)
            else {}
        )
        policy_knowledge = (
            capability_tool_outputs.get("policy_knowledge_retrieval", {})
            if isinstance(capability_tool_outputs, dict)
            else {}
        )
        policy_citations = list(policy_knowledge.get("citations", []) or [])
        return RuntimeTraceEntry(
            node_name="turn_decision",
            summary=f"decision={action.decision}",
            prompt_pack_id=runtime.get("prompt_pack_id"),
            prompt_version=runtime.get("prompt_version"),
            provider=provider,
            model=model,
            tool_calls=tool_calls,
            turn_decision=action.decision,
            fallback_used=fallback_used,
            retry_count=retry_count,
            metadata={
                "requested_documents": list(action.requested_documents),
                "focus_kind": action.focus_kind,
                "focus_document_type": action.focus_document_type,
                "boundary_decision": boundary_decision,
                "decision_source": "adjudication_agent",
                "reasoning_effort": runtime.get("reasoning_effort"),
                "remaining_required_documents": list(
                    document_review.get("remaining_required_documents", []) or []
                ),
                "document_review_status": document_review.get("review_status"),
                "policy_citations": policy_citations,
                "policy_knowledge_status": (
                    "skipped"
                    if policy_knowledge.get("skipped")
                    else "completed"
                    if policy_knowledge
                    else "not_requested"
                ),
                "policy_knowledge_skip_reason": policy_knowledge.get("skip_reason"),
            },
        )
