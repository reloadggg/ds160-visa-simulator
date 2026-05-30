from types import SimpleNamespace

from app.services.debug_fill_service import DebugFillService


def test_target_document_type_keeps_empty_remaining_documents_authoritative() -> None:
    service = DebugFillService.__new__(DebugFillService)
    record = SimpleNamespace(
        current_focus_json={},
        interviewer_state_json={
            "remaining_required_documents": [],
            "requested_documents": ["admission_letter"],
        },
        gate_status_json={
            "required_documents": [
                {"document_type": "i20", "status": "missing"},
            ],
        },
    )

    assert service._target_document_type(record) == "i20"


def test_target_document_type_uses_requested_documents_when_remaining_is_missing() -> None:
    service = DebugFillService.__new__(DebugFillService)
    record = SimpleNamespace(
        current_focus_json={},
        interviewer_state_json={
            "requested_documents": ["admission_letter"],
        },
        gate_status_json={
            "required_documents": [
                {"document_type": "i20", "status": "missing"},
            ],
        },
    )

    assert service._target_document_type(record) == "admission_letter"
