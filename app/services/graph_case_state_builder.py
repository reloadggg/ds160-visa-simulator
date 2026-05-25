from __future__ import annotations

from typing import Any

from app.db.models import SessionRecord
from app.domain.document_types import normalize_document_type


INTERNAL_DEBUG_METADATA_KEYS = {
    "expected_findings",
    "synthetic_bundle_id",
    "debug_bundle_scenario",
    "debug_bundle_scenario_label",
    "scenario_label",
    "debug_fill_scenario",
    "debug_fill_scenario_label",
}

PUBLIC_FIELD_LABELS = {
    "/education/school_name": "学校",
    "/education/program_name": "项目",
    "/education/first_year_cost": "第一年费用",
    "/education/sevis_id": "SEVIS ID",
    "/funding/primary_source": "资金来源",
    "/funding/available_funds": "可用资金",
    "/funding/sponsor_relationship": "资助关系",
    "/identity/full_name": "申请人姓名",
    "/identity/nationality": "国籍",
    "/identity/passport_number": "护照号码",
    "/family/parent_names": "父母姓名",
    "/visa_intent/travel_purpose": "赴美目的",
}

USER_MATERIAL_REFERENCE_MARKERS = (
    "资料",
    "材料",
    "文件",
    "里面都有",
    "都在里面",
    "已经提供",
    "已提供",
)
CASE_MEMORY_USER_CLAIMS_KEY = "case_memory_claims"
CASE_MEMORY_USER_EVIDENCE_KEY = "case_memory_evidence_cards"
CASE_MEMORY_TOMBSTONE_KEY = "case_memory_tombstone"


class GraphCaseStateBuilder:
    """Build the graph case snapshot without calling models or mutating records."""

    def __init__(
        self,
        *,
        max_recent_turns: int = 6,
        max_history_items: int = 20,
        max_text_excerpt_chars: int = 500,
    ) -> None:
        self.max_recent_turns = max(max_recent_turns, 0)
        self.max_history_items = max(max_history_items, 0)
        self.max_text_excerpt_chars = max(max_text_excerpt_chars, 0)

    def build(
        self,
        record: SessionRecord,
        turns: list[Any],
        *,
        documents: list[Any] | None = None,
        evidence_items: list[Any] | None = None,
        document_chunks: list[Any] | None = None,
    ) -> dict[str, Any]:
        normalized_turns = self._normalize_turns(turns)
        recent_turns = normalized_turns[-self.max_recent_turns :] if self.max_recent_turns else []
        normalized_documents = self._normalize_documents(documents or [])
        normalized_evidence = self._normalize_evidence_items(evidence_items or [])
        normalized_chunks = self._normalize_document_chunks(document_chunks or [])
        gate_status = self._payload(getattr(record, "gate_status_json", None))

        return {
            "schema_version": "graph_case_state.v1",
            "session": {
                "session_id": record.session_id,
                "phase_state": record.phase_state,
                "declared_family": record.declared_family,
                "current_governor_decision": record.current_governor_decision,
            },
            "profile_json": self._payload(getattr(record, "profile_json", None)),
            "route_candidates": self._list_payload(
                getattr(record, "route_candidates_json", None)
            ),
            "gate_status": gate_status,
            "gate_progress": self._build_gate_progress(gate_status),
            "current_focus": self._payload(getattr(record, "current_focus_json", None)),
            "interviewer_state": self._payload(
                getattr(record, "interviewer_state_json", None)
            ),
            "recent_turns": recent_turns,
            "history_summary": self._build_history_summary(normalized_turns),
            "documents": normalized_documents,
            "document_chunks": normalized_chunks,
            "evidence_items": normalized_evidence,
            "case_memory": self._build_case_memory_snapshot(
                normalized_documents,
                normalized_turns,
            ),
            "case_board": self._build_case_board(
                normalized_documents,
                normalized_turns,
            ),
            "evidence_digest": self._build_evidence_digest(
                documents=normalized_documents,
                evidence_items=normalized_evidence,
            ),
            "case_brief": self._build_case_brief(
                record=record,
                turns=normalized_turns,
                documents=normalized_documents,
                evidence_items=normalized_evidence,
            ),
            "runtime_trace_tail": self._tail_payloads(
                getattr(record, "runtime_trace_json", None)
            ),
            "score_history_tail": self._tail_payloads(
                getattr(record, "score_history_json", None)
            ),
            "governor_history_tail": self._tail_payloads(
                getattr(record, "governor_history_json", None)
            ),
        }

    def _normalize_turns(self, turns: list[Any]) -> list[dict[str, Any]]:
        normalized = [
            {
                "turn_id": self._string_or_none(getattr(turn, "turn_id", None)),
                "turn_index": getattr(turn, "turn_index", None),
                "session_id": self._string_or_none(getattr(turn, "session_id", None)),
                "role": self._string_or_none(getattr(turn, "role", None)),
                "source": self._string_or_none(getattr(turn, "source", None)),
                "content": self._string_or_none(getattr(turn, "content", None)) or "",
                "metadata": self._normalize_turn_metadata(
                    getattr(turn, "metadata_json", None)
                ),
            }
            for turn in turns
        ]
        return sorted(
            normalized,
            key=lambda item: (
                item["turn_index"] if isinstance(item["turn_index"], int) else 0,
                item["turn_id"] or "",
            ),
        )

    def _normalize_turn_metadata(
        self,
        metadata_json: Any,
    ) -> dict[str, Any]:
        metadata = self._payload(metadata_json)
        turn_record = self._payload(metadata.get("turn_record"))
        return {
            "phase_state": self._string_or_none(metadata.get("phase_state")),
            "governor_decision": self._string_or_none(
                metadata.get("governor_decision")
            ),
            "turn_decision": self._string_or_none(metadata.get("turn_decision"))
            or self._string_or_none(turn_record.get("decision")),
            "requested_documents": self._normalize_document_types(
                metadata.get("requested_documents")
                or turn_record.get("requested_documents")
                or []
            ),
            "current_focus_kind": self._string_or_none(
                metadata.get("current_focus_kind")
            )
            or self._string_or_none(self._payload(turn_record.get("focus")).get("kind")),
            "turn_record": turn_record,
            "runtime_view_state": self._payload(metadata.get("runtime_view_state")),
            "prompt_trace": self._payload(metadata.get("prompt_trace")),
            CASE_MEMORY_USER_CLAIMS_KEY: self._list_payload(
                metadata.get(CASE_MEMORY_USER_CLAIMS_KEY)
            ),
            CASE_MEMORY_USER_EVIDENCE_KEY: self._list_payload(
                metadata.get(CASE_MEMORY_USER_EVIDENCE_KEY)
            ),
        }

    def _normalize_documents(self, documents: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for document in documents:
            artifact = self._payload(getattr(document, "artifact_json", None))
            document_type = (
                normalize_document_type(artifact.get("document_type"))
                or self._string_or_none(artifact.get("document_type"))
            )
            normalized.append(
                {
                    "document_id": self._string_or_none(
                        getattr(document, "document_id", None)
                    ),
                    "session_id": self._string_or_none(
                        getattr(document, "session_id", None)
                    ),
                    "filename": self._string_or_none(
                        getattr(document, "filename", None)
                    )
                    or "",
                    "status": self._string_or_none(getattr(document, "status", None)),
                    "document_type": document_type,
                    "artifact": self._public_document_artifact(artifact),
                }
            )
        return sorted(
            normalized,
            key=lambda item: (item["filename"], item["document_id"] or ""),
        )

    def _build_case_memory_snapshot(
        self,
        documents: list[dict[str, Any]],
        turns: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        claims_by_id: dict[str, dict[str, Any]] = {}
        evidence_by_id: dict[str, dict[str, Any]] = {}
        proof_by_id: dict[str, dict[str, Any]] = {}
        conflicts_by_id: dict[str, dict[str, Any]] = {}
        next_move: dict[str, Any] = {}

        for document in documents:
            if document.get("status") in {"deleted", "tombstoned"}:
                continue
            artifact = self._payload(document.get("artifact"))
            tombstone = self._payload(artifact.get(CASE_MEMORY_TOMBSTONE_KEY))
            if tombstone.get("status") == "tombstoned":
                continue
            result = self._payload(artifact.get("material_understanding_result"))
            for evidence in self._list_payload(result.get("evidence_cards")):
                evidence_id = self._string_or_none(evidence.get("evidence_id"))
                if evidence_id:
                    evidence_by_id[evidence_id] = evidence
            for claim in self._list_payload(result.get("extracted_claims")):
                claim_id = self._string_or_none(claim.get("claim_id"))
                if claim_id:
                    claims_by_id[claim_id] = claim
            for proof_point in self._list_payload(result.get("proof_points")):
                proof_point_id = self._string_or_none(
                    proof_point.get("proof_point_id")
                )
                if proof_point_id:
                    proof_by_id[proof_point_id] = proof_point
            for conflict in self._list_payload(result.get("conflicts")):
                conflict_id = self._string_or_none(conflict.get("conflict_id"))
                if conflict_id:
                    conflicts_by_id[conflict_id] = conflict
            if not next_move:
                suggested_followups = self._list_payload(
                    result.get("suggested_followups")
                )
                if suggested_followups:
                    next_move = suggested_followups[0]

        for turn in turns or []:
            metadata = self._payload(turn.get("metadata"))
            for evidence in self._list_payload(
                metadata.get(CASE_MEMORY_USER_EVIDENCE_KEY)
            ):
                evidence_id = self._string_or_none(evidence.get("evidence_id"))
                if evidence_id:
                    evidence_by_id[evidence_id] = evidence
            for claim in self._list_payload(metadata.get(CASE_MEMORY_USER_CLAIMS_KEY)):
                claim_id = self._string_or_none(claim.get("claim_id"))
                if claim_id:
                    claims_by_id[claim_id] = claim

        claims_by_id, generated_conflicts = self._apply_case_memory_conflicts(
            claims_by_id,
            evidence_by_id,
        )
        for conflict in generated_conflicts:
            conflict_id = self._string_or_none(conflict.get("conflict_id"))
            if conflict_id:
                conflicts_by_id.setdefault(conflict_id, conflict)
        if conflicts_by_id:
            next_move = self._next_move_from_conflict(
                sorted(conflicts_by_id.values(), key=lambda item: item.get("conflict_id") or "")[0]
            )

        snapshot = {
            "claims": list(claims_by_id.values())[-self.max_history_items :],
            "evidence_cards": list(evidence_by_id.values())[-self.max_history_items :],
            "proof_points": list(proof_by_id.values())[-self.max_history_items :],
            "conflicts": list(conflicts_by_id.values())[-self.max_history_items :],
        }
        if next_move:
            snapshot["next_move"] = next_move
        return snapshot

    def _build_case_board(
        self,
        documents: list[dict[str, Any]],
        turns: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        case_memory = self._build_case_memory_snapshot(documents, turns)
        latest_material: dict[str, Any] = {}
        for document in reversed(documents):
            if document.get("status") in {"deleted", "tombstoned"}:
                continue
            artifact = self._payload(document.get("artifact"))
            tombstone = self._payload(artifact.get(CASE_MEMORY_TOMBSTONE_KEY))
            if tombstone.get("status") == "tombstoned":
                continue
            delta = self._payload(artifact.get("case_board_delta"))
            material = self._payload(delta.get("latest_material"))
            if material:
                latest_material = material
                break
        return self._drop_empty_values(
            {
                "schema_version": "case_board.v1",
                "latest_material": latest_material,
                **case_memory,
            }
        )

    def _normalize_document_chunks(self, chunks: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for chunk in chunks:
            text = self._string_or_none(getattr(chunk, "text", None)) or ""
            normalized.append(
                {
                    "chunk_id": self._string_or_none(getattr(chunk, "chunk_id", None)),
                    "document_id": self._string_or_none(
                        getattr(chunk, "document_id", None)
                    ),
                    "session_id": self._string_or_none(
                        getattr(chunk, "session_id", None)
                    ),
                    "ordinal": getattr(chunk, "ordinal", None),
                    "page_number": getattr(chunk, "page_number", None),
                    "text_excerpt": self._excerpt(text),
                    "text_length": len(text),
                    "metadata": self._sanitize_metadata(
                        self._payload(getattr(chunk, "metadata_json", None))
                    ),
                }
            )
        return sorted(
            normalized,
            key=lambda item: (
                item["document_id"] or "",
                item["ordinal"] if isinstance(item["ordinal"], int) else 0,
                item["chunk_id"] or "",
            ),
        )

    def _normalize_evidence_items(
        self,
        evidence_items: list[Any],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in evidence_items:
            normalized.append(
                {
                    "evidence_id": self._string_or_none(
                        getattr(item, "evidence_id", None)
                    ),
                    "session_id": self._string_or_none(
                        getattr(item, "session_id", None)
                    ),
                    "document_id": self._string_or_none(
                        getattr(item, "document_id", None)
                    ),
                    "chunk_id": self._string_or_none(getattr(item, "chunk_id", None)),
                    "evidence_type": self._string_or_none(
                        getattr(item, "evidence_type", None)
                    ),
                    "field_path": self._string_or_none(
                        getattr(item, "field_path", None)
                    ),
                    "value": self._string_or_none(getattr(item, "value", None)),
                    "excerpt": self._excerpt(
                        self._string_or_none(getattr(item, "excerpt", None)) or ""
                    ),
                    "confidence": getattr(item, "confidence", None),
                    "metadata": self._sanitize_metadata(
                        self._payload(getattr(item, "metadata_json", None))
                    ),
                }
            )
        return sorted(
            normalized,
            key=lambda item: (
                item["field_path"] or "",
                item["evidence_id"] or "",
            ),
        )

    def _build_gate_progress(self, gate_status: dict[str, Any]) -> dict[str, Any]:
        documents: list[dict[str, Any]] = []
        ready_count = 0
        uploaded_count = 0
        missing_count = 0

        for item in self._list_payload(gate_status.get("required_documents")):
            document = {
                "document_type": self._string_or_none(item.get("document_type")),
                "status": self._string_or_none(item.get("status")) or "missing",
                "is_uploaded": bool(item.get("is_uploaded", False)),
                "is_parsed": bool(item.get("is_parsed", False)),
                "meets_minimum_fields": bool(
                    item.get("meets_minimum_fields", False)
                ),
            }
            documents.append(document)
            if document["status"] == "ready":
                ready_count += 1
            elif document["status"] == "uploaded":
                uploaded_count += 1
            else:
                missing_count += 1

        return {
            "overall_status": self._string_or_none(gate_status.get("status")),
            "ready_count": ready_count,
            "uploaded_count": uploaded_count,
            "missing_count": missing_count,
            "documents": documents,
        }

    def _build_history_summary(
        self,
        turns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prior_decisions: list[str] = []
        prior_requested_documents: list[str] = []
        prior_question_topics: list[str] = []

        for turn in turns:
            metadata = self._payload(turn.get("metadata"))
            turn_record = self._payload(metadata.get("turn_record"))
            decision = self._string_or_none(metadata.get("turn_decision")) or self._string_or_none(
                turn_record.get("decision")
            )
            if decision:
                prior_decisions.append(decision)
            for document_type in self._normalize_document_types(
                metadata.get("requested_documents")
                or turn_record.get("requested_documents")
                or []
            ):
                if document_type not in prior_requested_documents:
                    prior_requested_documents.append(document_type)
            focus = self._payload(turn_record.get("focus"))
            question = self._string_or_none(focus.get("question")) or self._string_or_none(
                turn.get("content")
            )
            question_topic = self._question_topic(question)
            if question_topic and question_topic not in prior_question_topics:
                prior_question_topics.append(question_topic)

        return {
            "turn_count": len(turns),
            "user_turn_count": sum(1 for turn in turns if turn.get("role") == "user"),
            "assistant_turn_count": sum(
                1 for turn in turns if turn.get("role") == "assistant"
            ),
            "prior_decisions": prior_decisions[-self.max_history_items :],
            "prior_requested_documents": prior_requested_documents[
                -self.max_history_items :
            ],
            "prior_question_topics": prior_question_topics[-self.max_history_items :],
        }

    def _question_topic(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if any(
            marker in normalized
            for marker in (
                "第一年费用",
                "学费",
                "生活费",
                "资金",
                "资助",
                "父亲",
                "母亲",
                "父母",
                "fund",
                "sponsor",
                "tuition",
                "pay",
            )
        ):
            return "funding"
        if any(
            marker in normalized
            for marker in (
                "毕业后",
                "回国",
                "工作",
                "岗位",
                "任教",
                "career",
                "job",
                "return home",
                "post-graduation",
            )
        ):
            return "post_study_plan"
        if any(
            marker in normalized
            for marker in (
                "为什么选择",
                "为什么不在国内",
                "学校",
                "项目",
                "专业",
                "program",
                "school",
                "major",
            )
        ):
            return "program_school"
        if any(
            marker in normalized
            for marker in (
                "本科",
                "成绩",
                "语言",
                "经历",
                "academic",
                "gpa",
                "toefl",
                "ielts",
                "experience",
            )
        ):
            return "academic_preparation"
        return None

    def _build_evidence_digest(
        self,
        *,
        documents: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        uploaded_documents = [
            {
                "document_id": document.get("document_id"),
                "filename": document.get("filename"),
                "status": document.get("status"),
                "document_type": document.get("document_type"),
            }
            for document in documents
        ]
        documented_field_paths: list[str] = []
        evidence_refs: list[str] = []
        supported_claims: list[str] = []
        for item in evidence_items:
            field_path = self._string_or_none(item.get("field_path"))
            if field_path and field_path not in documented_field_paths:
                documented_field_paths.append(field_path)
            evidence_id = self._string_or_none(item.get("evidence_id"))
            if evidence_id:
                evidence_refs.append(evidence_id)
            if field_path and item.get("value") is not None:
                supported_claims.append(f"{field_path}={item['value']}")

        return {
            "uploaded_document_count": len(documents),
            "uploaded_documents": uploaded_documents,
            "documented_field_paths": documented_field_paths,
            "evidence_refs": evidence_refs,
            "supported_claims": supported_claims[-self.max_history_items :],
        }

    def _build_case_brief(
        self,
        *,
        record: SessionRecord,
        turns: list[dict[str, Any]],
        documents: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        recent_assistant_questions = self._recent_assistant_questions(turns)
        latest_user_message = self._latest_user_message(turns)
        known_documented_facts = self._build_known_documented_facts(
            profile_json=self._payload(getattr(record, "profile_json", None)),
            documents=documents,
            evidence_items=evidence_items,
        )
        known_field_paths = [
            fact["field_path"]
            for fact in known_documented_facts
            if isinstance(fact.get("field_path"), str)
        ]

        return self._drop_empty_values(
            {
                "phase_state": self._string_or_none(record.phase_state),
                "declared_family": self._string_or_none(record.declared_family),
                "known_documented_facts": known_documented_facts,
                "known_documented_field_paths": known_field_paths,
                "recent_assistant_questions": recent_assistant_questions,
                "latest_assistant_question": (
                    recent_assistant_questions[-1]["question"]
                    if recent_assistant_questions
                    else None
                ),
                "latest_user_referred_to_materials": (
                    self._mentions_provided_materials(latest_user_message)
                ),
            }
        )

    def _build_known_documented_facts(
        self,
        *,
        profile_json: dict[str, Any],
        documents: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        document_lookup = {
            document.get("document_id"): document
            for document in documents
            if self._string_or_none(document.get("document_id"))
        }
        facts_by_key: dict[tuple[str, str], dict[str, Any]] = {}

        for item in evidence_items:
            field_path = self._string_or_none(item.get("field_path"))
            value = self._string_or_none(item.get("value"))
            if field_path is None or value is None:
                continue

            key = (field_path, value)
            document_id = self._string_or_none(item.get("document_id"))
            evidence_id = self._string_or_none(item.get("evidence_id"))
            fact = facts_by_key.setdefault(
                key,
                {
                    "field_path": field_path,
                    "label": PUBLIC_FIELD_LABELS.get(field_path, field_path),
                    "value": value,
                    "document_ids": [],
                    "document_filenames": [],
                    "evidence_refs": [],
                },
            )
            if document_id and document_id not in fact["document_ids"]:
                fact["document_ids"].append(document_id)
                document = document_lookup.get(document_id)
                filename = self._string_or_none(
                    document.get("filename") if isinstance(document, dict) else None
                )
                if filename and filename not in fact["document_filenames"]:
                    fact["document_filenames"].append(filename)
            if evidence_id and evidence_id not in fact["evidence_refs"]:
                fact["evidence_refs"].append(evidence_id)

        ds160_view = self._payload(profile_json.get("ds160_view"))
        snapshots = self._payload(ds160_view.get("document_evidence_snapshot"))
        for field_path, snapshot_value in snapshots.items():
            if not isinstance(field_path, str):
                continue
            snapshot = self._payload(snapshot_value)
            value = self._string_or_none(snapshot.get("value"))
            if value is None:
                continue
            key = (field_path, value)
            evidence_refs = [
                item
                for item in self._string_list(snapshot.get("evidence_refs"))
                if item
            ]
            fact = facts_by_key.setdefault(
                key,
                {
                    "field_path": field_path,
                    "label": PUBLIC_FIELD_LABELS.get(field_path, field_path),
                    "value": value,
                    "document_ids": [],
                    "document_filenames": [],
                    "evidence_refs": [],
                },
            )
            for evidence_ref in evidence_refs:
                if evidence_ref not in fact["evidence_refs"]:
                    fact["evidence_refs"].append(evidence_ref)

        return list(facts_by_key.values())[-self.max_history_items :]

    def _recent_assistant_questions(
        self,
        turns: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        questions: list[dict[str, Any]] = []
        for turn in turns:
            if turn.get("role") != "assistant":
                continue
            metadata = self._payload(turn.get("metadata"))
            turn_record = self._payload(metadata.get("turn_record"))
            focus = self._payload(turn_record.get("focus"))
            question = self._string_or_none(focus.get("question")) or self._string_or_none(
                turn.get("content")
            )
            if question is None:
                continue
            questions.append(
                self._drop_empty_values(
                    {
                        "turn_id": self._string_or_none(turn.get("turn_id")),
                        "turn_index": turn.get("turn_index"),
                        "question": question,
                    }
                )
            )
        return questions[-self.max_recent_turns :]

    def _latest_user_message(self, turns: list[dict[str, Any]]) -> str | None:
        for turn in reversed(turns):
            if turn.get("role") != "user":
                continue
            return self._string_or_none(turn.get("content"))
        return None

    def _mentions_provided_materials(self, message: str | None) -> bool:
        if message is None:
            return False
        return any(marker in message for marker in USER_MATERIAL_REFERENCE_MARKERS)

    def _tail_payloads(self, value: Any) -> list[dict[str, Any]]:
        return self._list_payload(value)[-self.max_history_items :]

    def _public_document_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        assessment = self._payload(artifact.get("document_assessment"))
        metadata = self._payload(artifact.get("metadata"))
        public_metadata: dict[str, Any] = {}
        if bool(metadata.get("debug_material_bundle")):
            public_metadata["debug_material_bundle"] = True
        if bool(metadata.get("debug_fill")):
            public_metadata["debug_fill"] = True

        payload: dict[str, Any] = {
            "status": self._string_or_none(artifact.get("status")),
            "source_type": self._string_or_none(artifact.get("source_type")),
            "document_type": (
                normalize_document_type(artifact.get("document_type"))
                or self._string_or_none(artifact.get("document_type"))
            ),
            "document_type_candidates": self._normalize_document_types(
                artifact.get("document_type_candidates") or []
            ),
            "relevance": self._string_or_none(artifact.get("relevance")),
            "supported_claims": self._string_list(artifact.get("supported_claims")),
            "understanding_status": self._string_or_none(
                artifact.get("understanding_status")
            ),
            "case_board_delta": self._sanitize_public_payload(
                self._payload(artifact.get("case_board_delta"))
            ),
            "material_understanding_result": self._sanitize_public_payload(
                self._payload(artifact.get("material_understanding_result"))
            ),
            "material_understanding_job": self._sanitize_public_payload(
                self._payload(artifact.get("material_understanding_job"))
            ),
            CASE_MEMORY_TOMBSTONE_KEY: self._payload(
                artifact.get(CASE_MEMORY_TOMBSTONE_KEY)
            ),
            "counts_toward_gate": artifact.get("counts_toward_gate"),
            "metadata": public_metadata,
        }
        if assessment:
            payload["document_assessment"] = {
                "document_type": (
                    normalize_document_type(assessment.get("document_type"))
                    or self._string_or_none(assessment.get("document_type"))
                ),
                "document_type_candidates": self._normalize_document_types(
                    assessment.get("document_type_candidates") or []
                ),
                "relevance": self._string_or_none(assessment.get("relevance")),
                "supported_claims": self._string_list(
                    assessment.get("supported_claims")
                ),
                "confidence": assessment.get("confidence"),
                "relevant": assessment.get("relevant"),
                "counts_toward_gate": assessment.get("counts_toward_gate"),
            }
        return self._drop_empty_values(payload)

    def _sanitize_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return self._drop_empty_values(
            {
                key: value
                for key, value in metadata.items()
                if key not in INTERNAL_DEBUG_METADATA_KEYS
            }
        )

    def _sanitize_public_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            return self._drop_empty_values(
                {
                    key: self._sanitize_public_payload(item)
                    for key, item in value.items()
                    if key not in INTERNAL_DEBUG_METADATA_KEYS
                }
            )
        if isinstance(value, list):
            return [
                item
                for item in (
                    self._sanitize_public_payload(item) for item in value
                )
                if item not in (None, "", [], {})
            ]
        return value

    def _normalize_document_types(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            document_type = normalize_document_type(item) or item.strip()
            if document_type and document_type not in normalized:
                normalized.append(document_type)
        return normalized

    def _apply_case_memory_conflicts(
        self,
        claims_by_id: dict[str, dict[str, Any]],
        evidence_by_id: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for claim in claims_by_id.values():
            field_path = self._string_or_none(claim.get("field_path"))
            value = self._string_or_none(claim.get("value"))
            if field_path is None or value is None:
                continue
            grouped.setdefault(field_path, []).append(claim)

        updated = {claim_id: dict(claim) for claim_id, claim in claims_by_id.items()}
        conflicts: list[dict[str, Any]] = []
        for field_path, claims in grouped.items():
            distinct_values = {
                self._normalize_claim_value(str(claim.get("value"))): str(
                    claim.get("value")
                )
                for claim in claims
                if self._string_or_none(claim.get("value")) is not None
            }
            if len(distinct_values) <= 1:
                continue
            evidence_ids = self._evidence_ids_for_claims(claims, evidence_by_id)
            conflict_id = self._field_conflict_id(field_path)
            for claim in claims:
                claim_id = self._string_or_none(claim.get("claim_id"))
                if claim_id is None:
                    continue
                own_evidence = set(self._string_list(claim.get("supporting_evidence_ids")))
                own_evidence.update(self._evidence_ids_for_claim(claim_id, evidence_by_id))
                conflicting = [
                    evidence_id
                    for evidence_id in evidence_ids
                    if evidence_id not in own_evidence
                ]
                if not conflicting:
                    continue
                payload = dict(updated[claim_id])
                payload["status"] = "contradicted"
                payload["conflicting_evidence_ids"] = self._dedupe_strings(
                    [
                        *self._string_list(payload.get("conflicting_evidence_ids")),
                        *own_evidence,
                        *conflicting,
                    ]
                )
                updated[claim_id] = payload
            conflicts.append(
                {
                    "conflict_id": conflict_id,
                    "claim_ids": [
                        claim_id
                        for claim_id in (
                            self._string_or_none(claim.get("claim_id"))
                            for claim in claims
                        )
                        if claim_id is not None
                    ],
                    "evidence_ids": evidence_ids,
                    "summary": (
                        f"{field_path} has conflicting values: "
                        f"{', '.join(distinct_values.values())}."
                    ),
                    "severity": "medium",
                    "suggested_followup": (
                        "Ask the applicant to reconcile the stated answer with "
                        "the uploaded evidence."
                    ),
                }
            )
        return updated, conflicts

    def _next_move_from_conflict(self, conflict: dict[str, Any]) -> dict[str, Any]:
        return {
            "move_type": "clarify_conflict",
            "question": self._string_or_none(conflict.get("suggested_followup"))
            or "Please clarify the inconsistency between your answer and the uploaded evidence.",
            "reason": self._string_or_none(conflict.get("summary"))
            or "Case memory contains conflicting claims.",
            "claim_refs": self._string_list(conflict.get("claim_ids")),
            "evidence_refs": self._string_list(conflict.get("evidence_ids")),
        }

    def _evidence_ids_for_claims(
        self,
        claims: list[dict[str, Any]],
        evidence_by_id: dict[str, dict[str, Any]],
    ) -> list[str]:
        evidence_ids: list[str] = []
        for claim in claims:
            claim_id = self._string_or_none(claim.get("claim_id"))
            evidence_ids.extend(self._string_list(claim.get("supporting_evidence_ids")))
            evidence_ids.extend(self._string_list(claim.get("conflicting_evidence_ids")))
            if claim_id is not None:
                evidence_ids.extend(self._evidence_ids_for_claim(claim_id, evidence_by_id))
        return self._dedupe_strings(evidence_ids)

    def _evidence_ids_for_claim(
        self,
        claim_id: str,
        evidence_by_id: dict[str, dict[str, Any]],
    ) -> list[str]:
        return [
            evidence_id
            for evidence_id, evidence in evidence_by_id.items()
            if claim_id in self._string_list(evidence.get("claim_refs"))
        ]

    def _field_conflict_id(self, field_path: str) -> str:
        normalized = field_path.strip("/").replace("/", "-").replace("_", "-")
        return f"conflict-{normalized or 'unknown'}"

    def _normalize_claim_value(self, value: str) -> str:
        return " ".join(value.strip().casefold().split())

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    def _excerpt(self, value: str) -> str:
        if self.max_text_excerpt_chars <= 0:
            return ""
        return value[: self.max_text_excerpt_chars]

    def _drop_empty_values(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if value not in (None, "", [], {})
        }

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = self._string_or_none(item)
            if text is not None and text not in normalized:
                normalized.append(text)
        return normalized

    def _payload(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _list_payload(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
