from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
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
        assert payload["graph_runtime_engine"] == "langgraph"
        assert payload["graph_runtime_engine_class"] == "CompiledStateGraph"
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
        assert payload["graph_runtime_engine"] == "langgraph"
        assert payload["graph_runtime_engine_class"] == "CompiledStateGraph"
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
        assert payload["graph_runtime_engine"] == "langgraph"
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
