from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.domain.agent_runtime import DS160GraphState
from app.domain.case_memory import (
    CaseClaim,
    EvidenceCard,
    InterviewNextMove,
    MaterialUnderstandingResult,
)
from app.domain.runtime import build_initial_gate_status
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.graph_runtime_adapter import GraphRuntimeAdapter


def test_graph_runtime_adapter_builds_legacy_compatible_shadow_response(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'graph-runtime-adapter.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            record = SessionRecord(
                session_id="sess-graph-adapter",
                phase_state="interview",
                declared_family="f1",
                current_governor_decision="continue_interview",
                gate_status_json=build_initial_gate_status(
                    declared_family="f1",
                    required_documents=["i20"],
                    scenario_key="student",
                ),
                profile_json={"profile_id": "profile-sess-graph-adapter"},
                runtime_trace_json=[],
                score_history_json=[],
                governor_history_json=[],
                interviewer_state_json={},
                current_focus_json={},
            )
            db.add(record)
            db.add(
                DocumentRecord(
                    document_id="doc-i20",
                    session_id=record.session_id,
                    filename="i20.txt",
                    status="parsed",
                    artifact_json={"document_type": "i20", "source_type": "text"},
                )
            )
            db.add(
                DocumentChunkRecord(
                    chunk_id="chunk-i20",
                    document_id="doc-i20",
                    session_id=record.session_id,
                    ordinal=0,
                    page_number=1,
                    text="School Name: Example University",
                    metadata_json={},
                )
            )
            db.add(
                EvidenceItemRecord(
                    evidence_id="evi-school",
                    session_id=record.session_id,
                    document_id="doc-i20",
                    chunk_id="chunk-i20",
                    evidence_type="i20",
                    field_path="/education/school_name",
                    value="Example University",
                    excerpt="School Name: Example University",
                    confidence=1.0,
                    metadata_json={},
                )
            )
            user_turn = SessionTurnRepository(db).append_user_turn(
                session_id=record.session_id,
                content="I will study computer science.",
                source="user_message",
                commit=False,
            )
            db.flush()

            payload = GraphRuntimeAdapter(db).run_turn(
                record,
                "I will study computer science.",
                user_turn=user_turn,
            )

        assert payload["agent_runtime"] == "graph"
        assert payload["selected_public_runtime"] == "experimental_graph"
        assert payload["runtime_role"] == "experimental"
        assert payload["canonical"] is False
        assert payload["graph_runtime_engine"] == "langgraph"
        assert payload["graph_runtime_engine_class"] == "CompiledStateGraph"
        assert payload["runtime_execution"] == {
            "schema_version": "runtime.execution.v1",
            "configured_runtime": "graph",
            "requested_public_runtime": "experimental_graph",
            "public_runtime": "experimental_graph",
            "execution_runtime": "graph_runtime_adapter",
            "runtime_engine": "langgraph",
            "runtime_engine_class": "CompiledStateGraph",
            "source": "user_turn",
            "runtime_role": "experimental",
            "canonical": False,
        }
        assert payload["assistant_message"]
        assert payload["turn_decision"]["decision"] == "continue_interview"
        assert payload["runtime_view_state"]["decision"] == "continue_interview"
        assert payload["turn_record"]["user_turn_id"] == user_turn.turn_id
        assert payload["graph_trace"]["event_count"] > 0
        assert payload["prompt_trace"]["graph_run_id"] == payload["graph_run_id"]
        assert payload["graph_trace"]["citation_count"] == 1
        assert payload["document_review"]["knowledge_plane"]["case_evidence"][
            "candidate_count"
        ] == 1
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_graph_runtime_adapter_typed_adjudication_missing_model_falls_back(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.graph_runtime_adapter.settings.agent_runtime_typed_adjudication_enabled",
        True,
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'graph-runtime-typed-fallback.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            record = SessionRecord(
                session_id="sess-graph-typed-fallback",
                phase_state="interview",
                declared_family="f1",
                current_governor_decision="continue_interview",
                gate_status_json=build_initial_gate_status(
                    declared_family="f1",
                    required_documents=[],
                    scenario_key="student",
                ),
                profile_json={"profile_id": "profile-sess-graph-typed-fallback"},
                runtime_trace_json=[],
                score_history_json=[],
                governor_history_json=[],
                interviewer_state_json={},
                current_focus_json={},
            )
            db.add(record)
            user_turn = SessionTurnRepository(db).append_user_turn(
                session_id=record.session_id,
                content="I will study computer science.",
                source="user_message",
                commit=False,
            )
            db.flush()

            payload = GraphRuntimeAdapter(db).run_turn(
                record,
                "I will study computer science.",
                user_turn=user_turn,
            )

        assert payload["agent_runtime"] == "graph"
        assert payload["selected_public_runtime"] == "experimental_graph"
        assert payload["runtime_role"] == "experimental"
        assert payload["canonical"] is False
        assert payload["graph_runtime_engine"] == "langgraph"
        assert payload["graph_runtime_engine_class"] == "CompiledStateGraph"
        assert payload["runtime_execution"]["runtime_role"] == "experimental"
        assert payload["runtime_execution"]["canonical"] is False
        assert payload["turn_decision"]["assistant_message_author"] == (
            "deterministic_safe_fallback"
        )
        assert payload["turn_decision"]["guard_status"] == "fallback_required"
        adjudication_event = next(
            event
            for event in payload["graph_events"]
            if event["event_type"] == "adjudication_completed"
        )
        assert adjudication_event["payload"]["fallback_used"] is True
        assert adjudication_event["payload"]["fallback_reason"] == "model_unavailable"
        assert adjudication_event["payload"]["llm_calls_used"] == 0
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_graph_runtime_adapter_runs_material_change_without_user_turn(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'graph-runtime-material-change.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            record = SessionRecord(
                session_id="sess-graph-material-change",
                phase_state="interview",
                declared_family="f1",
                current_governor_decision="continue_interview",
                gate_status_json=build_initial_gate_status(
                    declared_family="f1",
                    required_documents=[],
                    scenario_key="student",
                ),
                profile_json={"profile_id": "profile-sess-graph-material-change"},
                runtime_trace_json=[],
                score_history_json=[],
                governor_history_json=[],
                interviewer_state_json={},
                current_focus_json={},
            )
            db.add(record)
            db.flush()

            payload = GraphRuntimeAdapter(db).run_material_change(
                record,
                reason="debug_fill:i20",
            )

        assert payload["agent_runtime"] == "graph"
        assert payload["selected_public_runtime"] == "experimental_graph"
        assert payload["runtime_role"] == "experimental"
        assert payload["canonical"] is False
        assert payload["graph_runtime_engine"] == "langgraph"
        assert payload["runtime_execution"]["source"] == "material_change"
        assert payload["runtime_execution"]["runtime_role"] == "experimental"
        assert payload["runtime_execution"]["canonical"] is False
        assert payload["turn_record"].get("user_turn_id") is None
        assert payload["turn_record"]["user_input"] == "debug_fill:i20"
        assert payload["prompt_trace"]["graph_trigger"] == "material_change"
        assert payload["prompt_trace"]["material_change_reason"] == "debug_fill:i20"
        assert payload["graph_trace"]["trigger"] == "material_change"
        assert payload["graph_trace"]["material_change_reason"] == "debug_fill:i20"
        accepted_event = payload["graph_events"][0]
        assert accepted_event["payload"]["trigger"] == "material_change"
        assert accepted_event["payload"]["material_change_reason"] == "debug_fill:i20"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_graph_runtime_adapter_fallback_uses_case_board_next_move(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'graph-runtime-case-board-next-move.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    material_result = MaterialUnderstandingResult(
        evidence_cards=[
            EvidenceCard(
                evidence_id="ev-i20-school",
                source_type="uploaded_file",
                document_id="doc-i20",
                excerpt="School Name: Example University",
                claim_refs=["claim-school"],
                confidence=0.91,
            )
        ],
        extracted_claims=[
            CaseClaim(
                claim_id="claim-school",
                field_path="/education/school_name",
                value="Example University",
                status="documented",
                supporting_evidence_ids=["ev-i20-school"],
                confidence=0.91,
            )
        ],
        suggested_followups=[
            InterviewNextMove(
                move_type="ask",
                question="I-20 显示 Example University。为什么选择这个项目？",
                reason="学校和项目已经由材料支持，下一步核验动机。",
                claim_refs=["claim-school"],
                evidence_refs=["ev-i20-school"],
            )
        ],
        confidence=0.91,
    )

    try:
        with testing_session_local() as db:
            record = SessionRecord(
                session_id="sess-case-board-next-move",
                phase_state="interview",
                declared_family="f1",
                current_governor_decision="continue_interview",
                gate_status_json=build_initial_gate_status(
                    declared_family="f1",
                    required_documents=["funding_proof"],
                    scenario_key="case-board-next-move",
                ),
                profile_json={"profile_id": "profile-sess-case-board-next-move"},
                runtime_trace_json=[],
                score_history_json=[],
                governor_history_json=[],
                interviewer_state_json={},
                current_focus_json={},
            )
            db.add(record)
            db.add(
                DocumentRecord(
                    document_id="doc-i20",
                    session_id=record.session_id,
                    filename="i20.png",
                    status="parsed",
                    artifact_json={
                        "document_type": "i20",
                        "material_understanding_result": material_result.model_dump(
                            mode="json"
                        ),
                        "case_board_delta": {
                            "latest_material": {
                                "document_id": "doc-i20",
                                "filename": "i20.png",
                                "understanding_status": "completed",
                            }
                        },
                    },
                )
            )
            user_turn = SessionTurnRepository(db).append_user_turn(
                session_id=record.session_id,
                content="I uploaded my I-20.",
                source="user_message",
                commit=False,
            )
            db.flush()

            payload = GraphRuntimeAdapter(db).run_turn(
                record,
                "I uploaded my I-20.",
                user_turn=user_turn,
            )

        assert payload["assistant_message"] == (
            "I-20 显示 Example University。为什么选择这个项目？"
        )
        assert payload["requested_documents"] == []
        assert payload["turn_decision"]["decision"] == "continue_interview"
        adjudication_event = next(
            event
            for event in payload["graph_events"]
            if event["event_type"] == "adjudication_completed"
        )
        assert adjudication_event["payload"]["case_memory_fallback"] is True
        assert adjudication_event["payload"]["fallback_reason"] == "case_board_next_move"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_graph_runtime_adapter_fallback_reads_open_proof_points_from_case_board(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'graph-runtime-open-proof.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            node = GraphRuntimeAdapter(db)._case_memory_fallback_adjudication_node()
            state = DS160GraphState(
                session_id="sess-graph-open-proof",
                run_id="run-open-proof",
                case_state={
                    "case_board": {
                        "schema_version": "case_board.v1",
                        "claims": [],
                        "evidence_cards": [],
                        "open_proof_points": [
                            {
                                "proof_point_id": "proof-funding-source",
                                "question": "Who will pay for your first year?",
                                "status": "partial",
                            }
                        ],
                        "conflicts": [],
                    },
                    "case_memory": {},
                },
            )

            updated = node(state)

        assert updated.adjudication_result is not None
        assert updated.adjudication_result["assistant_message"] == (
            "Who will pay for your first year?"
        )
        assert updated.adjudication_result["metadata"]["fallback_reason"] == (
            "case_memory_proof_point"
        )
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_graph_runtime_adapter_fallback_clarifies_case_memory_conflict(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'graph-runtime-case-memory-conflict.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    material_result = MaterialUnderstandingResult(
        evidence_cards=[
            EvidenceCard(
                evidence_id="ev-bank-parents",
                source_type="uploaded_file",
                document_id="doc-bank",
                excerpt="Sponsor: parents",
                claim_refs=["claim-material-funding"],
                confidence=0.9,
            )
        ],
        extracted_claims=[
            CaseClaim(
                claim_id="claim-material-funding",
                field_path="/funding/primary_source",
                value="parents",
                status="documented",
                supporting_evidence_ids=["ev-bank-parents"],
                confidence=0.9,
            )
        ],
        confidence=0.9,
    )

    try:
        with testing_session_local() as db:
            record = SessionRecord(
                session_id="sess-case-memory-conflict",
                phase_state="interview",
                declared_family="f1",
                current_governor_decision="continue_interview",
                gate_status_json=build_initial_gate_status(
                    declared_family="f1",
                    required_documents=[],
                    scenario_key="case-memory-conflict",
                ),
                profile_json={"profile_id": "profile-sess-case-memory-conflict"},
                runtime_trace_json=[],
                score_history_json=[],
                governor_history_json=[],
                interviewer_state_json={},
                current_focus_json={},
            )
            db.add(record)
            db.add(
                DocumentRecord(
                    document_id="doc-bank",
                    session_id=record.session_id,
                    filename="bank.pdf",
                    status="parsed",
                    artifact_json={
                        "material_understanding_result": material_result.model_dump(
                            mode="json"
                        )
                    },
                )
            )
            user_turn = SessionTurnRepository(db).append_user_turn(
                session_id=record.session_id,
                content="I am self-funded.",
                source="user_message",
                metadata_json={
                    "case_memory_claims": [
                        {
                            "claim_id": "claim-user-funding",
                            "field_path": "/funding/primary_source",
                            "value": "self",
                            "status": "stated",
                            "confidence": 0.72,
                        }
                    ],
                    "case_memory_evidence_cards": [
                        {
                            "evidence_id": "ev-user-funding",
                            "source_type": "user_turn",
                            "excerpt": "I am self-funded.",
                            "claim_refs": ["claim-user-funding"],
                            "confidence": 0.72,
                        }
                    ],
                },
                commit=False,
            )
            db.flush()

            payload = GraphRuntimeAdapter(db).run_turn(
                record,
                "I am self-funded.",
                user_turn=user_turn,
            )

        assert payload["governor_decision"] == "high_risk_review"
        assert payload["turn_decision"]["next_safe_action"] == "ask_clarification"
        assert "不一致" in payload["assistant_message"] or "差异" in payload[
            "assistant_message"
        ]
        adjudication_event = next(
            event
            for event in payload["graph_events"]
            if event["event_type"] == "adjudication_completed"
        )
        assert adjudication_event["payload"]["case_memory_fallback"] is True
        assert adjudication_event["payload"]["fallback_reason"] == "case_memory_conflict"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
