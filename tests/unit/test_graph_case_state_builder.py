from types import SimpleNamespace

from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord, SessionTurnRecord
from app.services.graph_case_state_builder import GraphCaseStateBuilder


def test_graph_case_state_builder_normalizes_session_turns_and_materials() -> None:
    record = SessionRecord(
        session_id="sess-graph-case",
        phase_state="interview",
        declared_family="f1",
        current_governor_decision="continue_interview",
        profile_json={
            "profile_id": "profile-sess-graph-case",
            "education": {"school_name": "Example University"},
        },
        route_candidates_json=[{"family": "f1", "confidence": 0.91}],
        gate_status_json={
            "status": "ready_for_interview",
            "required_documents": [
                {
                    "document_type": "I-20",
                    "status": "ready",
                    "is_uploaded": True,
                    "is_parsed": True,
                    "meets_minimum_fields": True,
                }
            ],
        },
        runtime_trace_json=[{"node_name": "turn_decision"}],
        score_history_json=[
            {
                "category_fit": 80,
                "document_readiness": 90,
                "narrative_consistency": 75,
                "confidence": 70,
                "missing_evidence": [],
                "risk_flags": [],
            }
        ],
        governor_history_json=[{"decision": "continue_interview"}],
        interviewer_state_json={
            "advisory_context": {"risk_codes": [], "risk_level": "none"}
        },
        current_focus_json={
            "kind": "interview_question",
            "question": "What school will you attend?",
        },
    )
    turns = [
        SessionTurnRecord(
            turn_id="turn-user-1",
            turn_index=1,
            session_id=record.session_id,
            role="user",
            content="I will attend Example University.",
            source="user_message",
            metadata_json={"phase_state": "interview"},
        ),
        SessionTurnRecord(
            turn_id="turn-assistant-1",
            turn_index=2,
            session_id=record.session_id,
            role="assistant",
            content="What program will you study?",
            source="interviewer_runtime_service",
            metadata_json={
                "turn_record": {
                    "decision": "continue_interview",
                    "requested_documents": [],
                    "focus": {
                        "kind": "interview_question",
                        "question": "What program will you study?",
                    },
                },
                "prompt_trace": {"model": "gpt-5.4"},
            },
        ),
    ]
    documents = [
        DocumentRecord(
            document_id="doc-i20",
            session_id=record.session_id,
            filename="i20.txt",
            status="parsed",
            artifact_json={"document_type": "I-20", "source_type": "text"},
        )
    ]
    chunks = [
        DocumentChunkRecord(
            chunk_id="chunk-i20-school",
            document_id="doc-i20",
            session_id=record.session_id,
            ordinal=0,
            page_number=1,
            text="School Name: Example University",
            metadata_json={"parser": "plain_text"},
        )
    ]
    evidence = [
        EvidenceItemRecord(
            evidence_id="evi-school",
            session_id=record.session_id,
            document_id="doc-i20",
            chunk_id="chunk-i20-school",
            evidence_type="i20",
            field_path="/education/school_name",
            value="Example University",
            excerpt="School Name: Example University",
            confidence=0.98,
            metadata_json={"source": "parser"},
        )
    ]

    case_state = GraphCaseStateBuilder().build(
        record,
        turns,
        documents=documents,
        document_chunks=chunks,
        evidence_items=evidence,
    )

    assert case_state["schema_version"] == "graph_case_state.v1"
    assert case_state["session"] == {
        "session_id": "sess-graph-case",
        "phase_state": "interview",
        "declared_family": "f1",
        "current_governor_decision": "continue_interview",
    }
    assert case_state["profile_json"]["education"]["school_name"] == (
        "Example University"
    )
    assert case_state["gate_progress"]["overall_status"] == "ready_for_interview"
    assert case_state["gate_progress"]["ready_count"] == 1
    assert case_state["recent_turns"][1]["metadata"]["turn_decision"] == (
        "continue_interview"
    )
    assert case_state["recent_turns"][1]["metadata"]["prompt_trace"] == {
        "model": "gpt-5.4"
    }
    assert case_state["documents"][0]["document_type"] == "i20"
    assert case_state["document_chunks"][0]["text_excerpt"] == (
        "School Name: Example University"
    )
    assert case_state["evidence_items"][0]["field_path"] == (
        "/education/school_name"
    )
    assert case_state["evidence_digest"]["uploaded_document_count"] == 1
    assert case_state["evidence_digest"]["documented_field_paths"] == [
        "/education/school_name"
    ]
    assert case_state["evidence_digest"]["evidence_refs"] == ["evi-school"]
    assert case_state["history_summary"]["turn_count"] == 2
    assert case_state["history_summary"]["assistant_turn_count"] == 1
    assert case_state["history_summary"]["prior_decisions"] == [
        "continue_interview"
    ]


def test_graph_case_state_builder_keeps_recent_turn_window_stable() -> None:
    record = SessionRecord(
        session_id="sess-window",
        declared_family="f1",
        gate_status_json={"status": "ready_for_interview"},
    )
    turns = [
        SimpleNamespace(
            turn_id=f"turn-{index}",
            turn_index=index,
            session_id="sess-window",
            role="user" if index % 2 else "assistant",
            content=f"message-{index}",
            source="test",
            metadata_json={},
        )
        for index in range(1, 9)
    ]

    case_state = GraphCaseStateBuilder(max_recent_turns=3).build(record, turns)

    assert [turn["turn_id"] for turn in case_state["recent_turns"]] == [
        "turn-6",
        "turn-7",
        "turn-8",
    ]
    assert case_state["history_summary"]["turn_count"] == 8


def test_graph_case_state_builder_does_not_depend_on_legacy_orchestrators() -> None:
    builder = GraphCaseStateBuilder()

    assert not hasattr(builder, "capability_orchestrator")
    assert not hasattr(builder, "turn_projector")
