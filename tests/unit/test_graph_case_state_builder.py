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
    assert case_state["case_brief"]["known_documented_facts"] == [
        {
            "field_path": "/education/school_name",
            "label": "学校",
            "value": "Example University",
            "document_ids": ["doc-i20"],
            "document_filenames": ["i20.txt"],
            "evidence_refs": ["evi-school"],
        }
    ]
    assert case_state["case_brief"]["known_documented_field_paths"] == [
        "/education/school_name"
    ]
    assert case_state["case_brief"]["recent_assistant_questions"] == [
        {
            "turn_id": "turn-assistant-1",
            "turn_index": 2,
            "question": "What program will you study?",
        }
    ]
    assert case_state["case_brief"]["latest_assistant_question"] == (
        "What program will you study?"
    )
    assert case_state["history_summary"]["turn_count"] == 2
    assert case_state["history_summary"]["assistant_turn_count"] == 1
    assert case_state["history_summary"]["prior_decisions"] == [
        "continue_interview"
    ]


def test_graph_case_state_builder_projects_case_memory_from_document_artifacts() -> None:
    record = SessionRecord(
        session_id="sess-case-memory",
        declared_family="f1",
        gate_status_json={"status": "ready_for_interview"},
    )
    documents = [
        DocumentRecord(
            document_id="doc-i20",
            session_id=record.session_id,
            filename="i20.pdf",
            status="parsed",
            artifact_json={
                "document_type": "i20",
                "understanding_status": "completed",
                "case_board_delta": {
                    "latest_material": {
                        "document_id": "doc-i20",
                        "filename": "i20.pdf",
                        "understanding_status": "completed",
                    }
                },
                "material_understanding_result": {
                    "document_type_candidates": [
                        {"document_type": "i20", "confidence": 0.91}
                    ],
                    "evidence_cards": [
                        {
                            "evidence_id": "ev-school",
                            "source_type": "uploaded_file",
                            "document_id": "doc-i20",
                            "excerpt": "School Name: Example University",
                            "claim_refs": ["claim-school"],
                            "confidence": 0.93,
                        }
                    ],
                    "extracted_claims": [
                        {
                            "claim_id": "claim-school",
                            "field_path": "/education/school_name",
                            "value": "Example University",
                            "status": "documented",
                            "supporting_evidence_ids": ["ev-school"],
                            "confidence": 0.93,
                        }
                    ],
                    "proof_points": [],
                    "conflicts": [],
                    "unknowns": [],
                    "suggested_followups": [],
                    "confidence": 0.91,
                },
            },
        )
    ]

    case_state = GraphCaseStateBuilder().build(record, [], documents=documents)

    assert case_state["case_memory"]["claims"][0]["field_path"] == (
        "/education/school_name"
    )
    assert case_state["case_memory"]["evidence_cards"][0]["evidence_id"] == (
        "ev-school"
    )
    assert case_state["case_board"]["latest_material"]["document_id"] == "doc-i20"


def test_graph_case_state_builder_projects_material_next_move() -> None:
    record = SessionRecord(
        session_id="sess-case-next-move",
        declared_family="f1",
        gate_status_json={"status": "ready_for_interview"},
    )
    documents = [
        DocumentRecord(
            document_id="doc-i20",
            session_id=record.session_id,
            filename="i20.pdf",
            status="parsed",
            artifact_json={
                "document_type": "i20",
                "case_board_delta": {
                    "latest_material": {
                        "document_id": "doc-i20",
                        "filename": "i20.pdf",
                        "understanding_status": "completed",
                    }
                },
                "material_understanding_result": {
                    "document_type_candidates": [],
                    "evidence_cards": [
                        {
                            "evidence_id": "ev-school",
                            "source_type": "uploaded_file",
                            "document_id": "doc-i20",
                            "excerpt": "School Name: Example University",
                            "claim_refs": ["claim-school"],
                            "confidence": 0.93,
                        }
                    ],
                    "extracted_claims": [
                        {
                            "claim_id": "claim-school",
                            "field_path": "/education/school_name",
                            "value": "Example University",
                            "status": "documented",
                            "supporting_evidence_ids": ["ev-school"],
                            "confidence": 0.93,
                        }
                    ],
                    "proof_points": [],
                    "conflicts": [],
                    "unknowns": [],
                    "suggested_followups": [
                        {
                            "move_type": "ask",
                            "question": "Why did you choose Example University?",
                            "reason": "The uploaded I-20 proves the school; motivation is the next interview topic.",
                            "claim_refs": ["claim-school"],
                            "evidence_refs": ["ev-school"],
                        }
                    ],
                    "confidence": 0.91,
                },
            },
        )
    ]

    case_state = GraphCaseStateBuilder().build(record, [], documents=documents)

    assert case_state["case_memory"]["next_move"] == {
        "move_type": "ask",
        "question": "Why did you choose Example University?",
        "reason": (
            "The uploaded I-20 proves the school; motivation is the next "
            "interview topic."
        ),
        "claim_refs": ["claim-school"],
        "evidence_refs": ["ev-school"],
    }
    assert case_state["case_board"]["next_move"] == case_state["case_memory"]["next_move"]


def test_graph_case_state_builder_includes_user_claims_and_conflicts() -> None:
    record = SessionRecord(
        session_id="sess-case-conflict",
        declared_family="f1",
        gate_status_json={"status": "ready_for_interview"},
    )
    turns = [
        SessionTurnRecord(
            turn_id="turn-user-1",
            turn_index=1,
            session_id=record.session_id,
            role="user",
            content="I will pay for the program myself.",
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
                        "excerpt": "I will pay for the program myself.",
                        "claim_refs": ["claim-user-funding"],
                        "confidence": 0.72,
                    }
                ],
            },
        )
    ]
    documents = [
        DocumentRecord(
            document_id="doc-bank",
            session_id=record.session_id,
            filename="bank.pdf",
            status="parsed",
            artifact_json={
                "document_type": "funding_proof",
                "material_understanding_result": {
                    "evidence_cards": [
                        {
                            "evidence_id": "ev-bank",
                            "source_type": "uploaded_file",
                            "document_id": "doc-bank",
                            "excerpt": "Sponsor: parents",
                            "claim_refs": ["claim-bank-funding"],
                            "confidence": 0.9,
                        }
                    ],
                    "extracted_claims": [
                        {
                            "claim_id": "claim-bank-funding",
                            "field_path": "/funding/primary_source",
                            "value": "parents",
                            "status": "documented",
                            "supporting_evidence_ids": ["ev-bank"],
                            "confidence": 0.9,
                        }
                    ],
                    "proof_points": [],
                    "conflicts": [],
                    "unknowns": [],
                    "suggested_followups": [],
                    "confidence": 0.9,
                },
            },
        )
    ]

    case_state = GraphCaseStateBuilder().build(record, turns, documents=documents)

    claims_by_value = {
        claim["value"]: claim for claim in case_state["case_memory"]["claims"]
    }
    assert claims_by_value["self"]["status"] == "contradicted"
    assert claims_by_value["parents"]["status"] == "contradicted"
    assert case_state["case_memory"]["conflicts"][0]["conflict_id"] == (
        "conflict-funding-primary-source"
    )


def test_graph_case_state_builder_excludes_tombstoned_case_memory_document() -> None:
    record = SessionRecord(
        session_id="sess-case-tombstone",
        declared_family="f1",
        gate_status_json={"status": "ready_for_interview"},
    )
    documents = [
        DocumentRecord(
            document_id="doc-i20",
            session_id=record.session_id,
            filename="i20.pdf",
            status="tombstoned",
            artifact_json={
                "document_type": "i20",
                "case_memory_tombstone": {
                    "status": "tombstoned",
                    "reason": "document_removed",
                },
                "case_board_delta": {
                    "latest_material": {
                        "document_id": "doc-i20",
                        "filename": "i20.pdf",
                        "understanding_status": "completed",
                    }
                },
                "material_understanding_result": {
                    "evidence_cards": [
                        {
                            "evidence_id": "ev-school",
                            "source_type": "uploaded_file",
                            "document_id": "doc-i20",
                            "excerpt": "School Name: Example University",
                            "claim_refs": ["claim-school"],
                            "confidence": 0.93,
                        }
                    ],
                    "extracted_claims": [
                        {
                            "claim_id": "claim-school",
                            "field_path": "/education/school_name",
                            "value": "Example University",
                            "status": "documented",
                            "supporting_evidence_ids": ["ev-school"],
                            "confidence": 0.93,
                        }
                    ],
                    "proof_points": [],
                    "conflicts": [],
                    "unknowns": [],
                    "suggested_followups": [],
                    "confidence": 0.93,
                },
            },
        )
    ]

    case_state = GraphCaseStateBuilder().build(record, [], documents=documents)

    assert case_state["case_memory"] == {
        "claims": [],
        "evidence_cards": [],
        "proof_points": [],
        "conflicts": [],
    }
    assert "latest_material" not in case_state["case_board"]


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


def test_graph_case_state_builder_uses_profile_document_snapshot_and_material_reference() -> None:
    record = SessionRecord(
        session_id="sess-profile-snapshot",
        phase_state="interview",
        declared_family="f1",
        profile_json={
            "ds160_view": {
                "document_evidence_snapshot": {
                    "/education/program_name": {
                        "value": "Master of Example Analytics",
                        "state": "documented",
                        "evidence_refs": ["evi-program"],
                    }
                }
            }
        },
    )
    turns = [
        SimpleNamespace(
            turn_id="turn-assistant-program",
            turn_index=1,
            session_id=record.session_id,
            role="assistant",
            content="你本科读的是什么专业？",
            source="graph_runtime_adapter",
            metadata_json={},
        ),
        SimpleNamespace(
            turn_id="turn-user-materials",
            turn_index=2,
            session_id=record.session_id,
            role="user",
            content="我提供的资料里面都有",
            source="user_message",
            metadata_json={},
        ),
    ]

    case_state = GraphCaseStateBuilder().build(record, turns)

    assert case_state["case_brief"]["known_documented_facts"] == [
        {
            "field_path": "/education/program_name",
            "label": "项目",
            "value": "Master of Example Analytics",
            "document_ids": [],
            "document_filenames": [],
            "evidence_refs": ["evi-program"],
        }
    ]
    assert case_state["case_brief"]["latest_assistant_question"] == (
        "你本科读的是什么专业？"
    )
    assert case_state["case_brief"]["latest_user_referred_to_materials"] is True


def test_graph_case_state_builder_does_not_depend_on_legacy_orchestrators() -> None:
    builder = GraphCaseStateBuilder()

    assert not hasattr(builder, "capability_orchestrator")
    assert not hasattr(builder, "turn_projector")


def test_graph_case_state_builder_sanitizes_debug_material_metadata() -> None:
    record = SessionRecord(
        session_id="sess-debug-sanitize",
        declared_family="f1",
        gate_status_json={"status": "ready_for_interview"},
    )
    documents = [
        DocumentRecord(
            document_id="doc-debug",
            session_id=record.session_id,
            filename="debug_i20.txt",
            status="parsed",
            artifact_json={
                "document_type": "i20",
                "source_type": "text",
                "material_understanding_result": {
                    "evidence_cards": [
                        {
                            "evidence_id": "ev-debug",
                            "source_type": "debug_material",
                            "excerpt": "School Name: Example University",
                            "metadata": {
                                "synthetic_bundle_id": "dbg-bundle-secret",
                                "debug_bundle_scenario": "school_mismatch_bundle",
                            },
                        }
                    ],
                    "extracted_claims": [],
                },
                "case_board_delta": {
                    "latest_material": {
                        "document_id": "doc-debug",
                        "filename": "debug_i20.txt",
                        "understanding_status": "completed",
                    },
                    "evidence_cards": [
                        {
                            "evidence_id": "ev-debug",
                            "source_type": "debug_material",
                            "excerpt": "School Name: Example University",
                            "metadata": {
                                "debug_bundle_scenario_label": "学校材料冲突包",
                            },
                        }
                    ],
                },
                "metadata": {
                    "debug_material_bundle": True,
                    "synthetic_bundle_id": "dbg-bundle-secret",
                    "debug_bundle_scenario": "school_mismatch_bundle",
                    "debug_bundle_scenario_label": "学校材料冲突包",
                },
                "document_assessment": {
                    "document_type": "i20",
                    "document_type_candidates": ["i20"],
                    "relevance": "high",
                    "supported_claims": ["/education/school_name"],
                    "confidence": 1.0,
                    "relevant": True,
                    "counts_toward_gate": True,
                },
            },
        )
    ]
    chunks = [
        DocumentChunkRecord(
            chunk_id="chunk-debug",
            document_id="doc-debug",
            session_id=record.session_id,
            ordinal=0,
            page_number=1,
            text="School Name: Example University",
            metadata_json={
                "synthetic_bundle_id": "dbg-bundle-secret",
                "debug_bundle_scenario": "school_mismatch_bundle",
            },
        )
    ]
    evidence = [
        EvidenceItemRecord(
            evidence_id="evi-debug",
            session_id=record.session_id,
            document_id="doc-debug",
            chunk_id="chunk-debug",
            evidence_type="i20",
            field_path="/education/school_name",
            value="Example University",
            excerpt="School Name: Example University",
            confidence=1.0,
            metadata_json={
                "synthetic_bundle_id": "dbg-bundle-secret",
                "debug_bundle_scenario": "school_mismatch_bundle",
            },
        )
    ]

    case_state = GraphCaseStateBuilder().build(
        record,
        [],
        documents=documents,
        document_chunks=chunks,
        evidence_items=evidence,
    )

    assert case_state["documents"][0]["artifact"] == {
            "source_type": "text",
            "document_type": "i20",
            "case_board_delta": {
                "latest_material": {
                    "document_id": "doc-debug",
                    "filename": "debug_i20.txt",
                    "understanding_status": "completed",
                },
                "evidence_cards": [
                    {
                        "evidence_id": "ev-debug",
                        "source_type": "debug_material",
                        "excerpt": "School Name: Example University",
                    }
                ],
            },
            "material_understanding_result": {
                "evidence_cards": [
                    {
                        "evidence_id": "ev-debug",
                        "source_type": "debug_material",
                        "excerpt": "School Name: Example University",
                    }
                ]
            },
            "metadata": {"debug_material_bundle": True},
            "document_assessment": {
                "document_type": "i20",
            "document_type_candidates": ["i20"],
            "relevance": "high",
            "supported_claims": ["/education/school_name"],
            "confidence": 1.0,
            "relevant": True,
            "counts_toward_gate": True,
        },
    }
    serialized = str(case_state)
    assert "dbg-bundle-secret" not in serialized
    assert "school_mismatch_bundle" not in serialized
    assert "学校材料冲突包" not in serialized
