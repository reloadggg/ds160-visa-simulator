from app.domain.runtime import (
    build_initial_gate_status,
    empty_governor_history,
    empty_runtime_trace,
    empty_score_history,
)


def test_build_initial_gate_status_with_required_documents() -> None:
    gate_status = build_initial_gate_status(
        declared_family="j1",
        scenario_key="institution_funded",
        required_documents=["ds160", "passport_bio"],
    )

    assert gate_status["declared_family"] == "j1"
    assert gate_status["scenario_key"] == "institution_funded"
    assert gate_status["status"] == "pending_documents"
    assert gate_status["required_documents"] == [
        {
            "document_type": "ds160",
            "status": "missing",
            "is_uploaded": False,
            "is_parsed": False,
            "meets_minimum_fields": False,
        },
        {
            "document_type": "passport_bio",
            "status": "missing",
            "is_uploaded": False,
            "is_parsed": False,
            "meets_minimum_fields": False,
        },
    ]


def test_build_initial_gate_status_without_family_returns_stable_empty_shape() -> None:
    gate_status = build_initial_gate_status(
        declared_family=None,
        required_documents=[],
    )

    assert gate_status == {
        "declared_family": None,
        "scenario_key": None,
        "status": "family_not_selected",
        "required_documents": [],
    }


def test_runtime_history_helpers_return_empty_lists() -> None:
    assert empty_runtime_trace() == []
    assert empty_score_history() == []
    assert empty_governor_history() == []
