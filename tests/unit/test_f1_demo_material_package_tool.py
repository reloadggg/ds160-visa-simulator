from __future__ import annotations

import json
from pathlib import Path

import fitz
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord, SessionTurnRecord
from app.services.material_package_archive_service import MaterialPackageArchiveService
from scripts.f1_demo_material_package import (
    DEFAULT_TEMPLATE_ID,
    DEMO_TEMPLATE,
    PACKAGE_ID,
    PACKAGE_LABEL,
    REQUIRED_DOCUMENT_TYPES,
    apply_cleanup_plan,
    api_origin_for_base_url,
    build_cleanup_plan,
    list_template_ids,
    lookup_template,
    login_if_configured,
    main,
    publish_validated_archive,
    render_materials,
    validate_run_payload,
)
import scripts.f1_demo_material_package as f1_demo_tool


def _session_factory(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'demo-package.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _valid_run_payload() -> dict:
    documents = [
        {
            "filename": definition.filename,
            "status": "parsed",
            "artifact": {
                "document_type": definition.document_type,
                "understanding_status": "completed",
            },
        }
        for definition in DEMO_TEMPLATE.documents
    ]
    return {
        "uploads": [
            {
                "document_type": definition.document_type,
                "status_code": 202,
                "response": {},
            }
            for definition in DEMO_TEMPLATE.documents
        ],
        "message_turns": [
            {
                "turn_index": index,
                "status_code": 200,
                "response": {
                    "governor_decision": "continue_interview",
                    "assistant_message": f"Question {index}?",
                },
            }
            for index in range(1, 6)
        ],
        "export": {"documents": documents},
        "user_report": {"governor_decision": "continue_interview"},
        "internal_report": {
            "turn_decision": {"governor_decision": "continue_interview"},
            "interviewer_state": {"decision": "continue_interview"},
        },
    }


def _add_complete_validation_session(db, *, session_id: str = "sess-validated") -> None:
    db.add(SessionRecord(session_id=session_id, declared_family="f1"))
    for definition in DEMO_TEMPLATE.documents:
        document = DocumentRecord(
            document_id=f"doc-{definition.document_type}",
            session_id=session_id,
            filename=definition.filename,
            status="parsed",
            raw_bytes=b"%PDF-validated",
            raw_text=definition.body,
            artifact_json={
                "document_id": f"doc-{definition.document_type}",
                "session_id": session_id,
                "filename": definition.filename,
                "source_type": "pdf",
                "parser_name": "pymupdf",
                "status": "parsed",
                "document_type": definition.document_type,
                "understanding_status": "completed",
                "metadata": {"document_type": definition.document_type},
            },
        )
        db.add(document)
        db.add(
            DocumentChunkRecord(
                chunk_id=f"chunk-{definition.document_type}",
                document_id=document.document_id,
                session_id=session_id,
                ordinal=0,
                page_number=1,
                text=definition.body,
                metadata_json={},
            )
        )
        for index, (field_path, value) in enumerate(definition.expected_fields.items()):
            db.add(
                EvidenceItemRecord(
                    evidence_id=f"evi-{definition.document_type}-{index}",
                    session_id=session_id,
                    document_id=document.document_id,
                    chunk_id=f"chunk-{definition.document_type}",
                    evidence_type=definition.document_type,
                    field_path=field_path,
                    value=value,
                    excerpt=f"{field_path}: {value}",
                    metadata_json={},
                )
            )


def _add_existing_archive_package(db) -> None:
    db.add(SessionRecord(session_id="sess-existing-archive", declared_family="f1"))
    db.add(
        DocumentRecord(
            document_id="doc-existing-archive",
            session_id="sess-existing-archive",
            filename="existing.pdf",
            status="parsed",
            raw_bytes=b"%PDF-existing",
            raw_text="existing package document",
            artifact_json={
                "document_type": "passport_bio",
                "metadata": {
                    "debug_material_bundle": True,
                    "synthetic_bundle_id": PACKAGE_ID,
                    "debug_bundle_scenario_label": PACKAGE_LABEL,
                },
            },
        )
    )


def test_template_registry_lookup_and_default_template_are_f1_nyu_parent_package() -> None:
    assert list_template_ids() == (DEFAULT_TEMPLATE_ID,)

    default_template = lookup_template()
    explicit_template = lookup_template(DEFAULT_TEMPLATE_ID)

    assert default_template is DEMO_TEMPLATE
    assert explicit_template is DEMO_TEMPLATE
    assert default_template.visa_family == "f1"
    assert default_template.package_id == PACKAGE_ID
    assert default_template.label == PACKAGE_LABEL
    assert default_template.required_document_types == REQUIRED_DOCUMENT_TYPES


def test_unknown_template_id_fails_with_available_registry_ids(capsys) -> None:
    exit_code = main(["render", "--template-id", "missing-template", "--out", "unused"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "unknown template-id 'missing-template'" in captured.err
    assert DEFAULT_TEMPLATE_ID in captured.err


def test_render_materials_creates_six_pdf_documents_without_debug_or_placeholder_text(tmp_path: Path) -> None:
    manifest = render_materials(tmp_path)

    rendered = manifest["rendered_documents"]
    assert [item["document_type"] for item in rendered] == list(REQUIRED_DOCUMENT_TYPES)
    assert manifest["template"]["label"] == PACKAGE_LABEL
    assert "自洽" not in manifest["template"]["label"]

    for item in rendered:
        pdf_path = Path(item["path"])
        assert pdf_path.exists()
        assert pdf_path.suffix == ".pdf"
        pdf = fitz.open(pdf_path)
        try:
            text = "\n".join(page.get_text("text") for page in pdf)
        finally:
            pdf.close()
        lowered = text.lower()
        assert "placeholder" not in lowered
        assert "oracle" not in lowered
        assert "自洽" not in text
        assert "{{" not in text and "}}" not in text


def test_validation_payload_requires_real_uploads_completed_materials_and_five_turns() -> None:
    payload = _valid_run_payload()

    passed, defects, warnings = validate_run_payload(payload)

    assert passed is True
    assert defects == []
    assert warnings == []

    payload["message_turns"] = payload["message_turns"][:4]
    passed, defects, _ = validate_run_payload(payload)
    assert passed is False
    assert {item["code"] for item in defects} == {"not_enough_interview_turns"}


def test_validator_rejects_nested_internal_refusal() -> None:
    payload = _valid_run_payload()
    payload["internal_report"] = {
        "turn_decision": {"governor_decision": "simulated_refusal"},
        "interviewer_state": {"decision": "simulated_refusal"},
    }

    passed, defects, _ = validate_run_payload(payload)

    assert passed is False
    assert "report_terminal_risk_state" in {item["code"] for item in defects}


def test_validator_rejects_ab_repeated_template_replies() -> None:
    payload = _valid_run_payload()
    for index, turn in enumerate(payload["message_turns"]):
        turn["response"]["assistant_message"] = "Template A?" if index % 2 == 0 else "Template B?"

    passed, defects, _ = validate_run_payload(payload)

    assert passed is False
    assert "repeated_template_replies" in {item["code"] for item in defects}


def test_validator_rejects_stale_requested_documents_after_required_docs_completed() -> None:
    payload = _valid_run_payload()
    payload["message_turns"][0]["response"]["requested_documents"] = [
        {"document_type": "funding_proof"}
    ]
    payload["message_turns"][0]["response"]["remaining_required_documents"] = ["i20"]

    passed, defects, _ = validate_run_payload(payload)

    assert passed is False
    stale_defects = [item for item in defects if item["code"] == "stale_material_request"]
    assert stale_defects
    assert stale_defects[0]["document_types"] == ["funding_proof", "i20"]


def test_validator_allows_continue_interview_but_rejects_unresolved_required_evidence() -> None:
    payload = _valid_run_payload()
    payload["user_report"] = {
        "governor_decision": "continue_interview",
        "interview_result": "in_progress",
        "missing_evidence": ["funding_proof"],
    }
    payload["internal_report"] = {
        "turn_decision": {"governor_decision": "continue_interview"},
        "interviewer_state": {"decision": "continue_interview"},
        "runtime_view_state": {
            "governor_decision": "continue_interview",
            "current_key_proof": "funding_proof",
        },
    }

    passed, defects, _ = validate_run_payload(payload)

    assert passed is False
    unresolved = [
        item for item in defects if item["code"] == "unresolved_required_evidence"
    ]
    assert unresolved
    assert unresolved[0]["document_types"] == ["funding_proof"]
    assert "stale_material_request" not in {item["code"] for item in defects}


def test_validator_does_not_treat_ready_gate_or_prompt_ids_as_unresolved() -> None:
    payload = _valid_run_payload()
    payload["runtime_debug"] = {
        "gate_status": {
            "required_documents": [
                {
                    "document_type": "ds160",
                    "status": "ready",
                    "is_uploaded": True,
                    "is_parsed": True,
                }
            ]
        },
        "prompt_trace": {"prompt_pack_id": "ds160.native_interviewer"},
        "runtime_view_state": {
            "decision": "continue_interview",
            "advisory_context": {"missing_evidence": []},
        },
    }
    payload["internal_report"]["runtime_view_state"] = {
        "prompt_trace": {"prompt_pack_id": "ds160.native_interviewer"},
        "advisory_context": {"missing_evidence": []},
    }

    passed, defects, _ = validate_run_payload(payload)

    assert passed is True
    assert "unresolved_required_evidence" not in {item["code"] for item in defects}


def test_validator_only_marks_requested_docs_stale_after_completed_exports() -> None:
    payload = _valid_run_payload()
    payload["export"]["documents"] = [
        item
        for item in payload["export"]["documents"]
        if item["artifact"]["document_type"] != "funding_proof"
    ]
    payload["message_turns"][0]["response"]["requested_documents"] = [
        "funding_proof"
    ]
    payload["user_report"] = {
        "governor_decision": "continue_interview",
        "missing_evidence": ["funding_proof"],
    }

    passed, defects, _ = validate_run_payload(payload)

    assert passed is False
    codes = {item["code"] for item in defects}
    assert "missing_exported_document" in codes
    assert "unresolved_required_evidence" in codes
    assert "stale_material_request" not in codes


def test_api_origin_for_base_url_strips_api_path() -> None:
    assert api_origin_for_base_url("https://ds160.efastt.store/api") == "https://ds160.efastt.store"
    assert api_origin_for_base_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_login_if_configured_can_use_custom_login_path(monkeypatch) -> None:
    monkeypatch.setenv("MIGRATION_ACCESS_KEY", "redacted-test-key")
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        assert json.loads(request.content) == {"password": "redacted-test-key"}
        return httpx.Response(200, json={"authenticated": True})

    recorder = __import__(
        "scripts.f1_demo_material_package",
        fromlist=["ApiRecorder"],
    ).ApiRecorder()
    with httpx.Client(
        base_url="https://ds160.efastt.store/api",
        transport=httpx.MockTransport(handler),
    ) as client:
        login_if_configured(
            client,
            recorder,
            "MIGRATION_ACCESS_KEY",
            login_path="/v1/admin/login",
        )

    assert seen_paths == ["/api/v1/admin/login"]
    assert recorder.entries[0]["request"]["password"] == "<redacted>"


def test_cleanup_plan_excludes_imported_user_materials(tmp_path: Path) -> None:
    engine, factory = _session_factory(tmp_path)
    try:
        with factory() as db:
            db.add_all(
                [
                    SessionRecord(session_id="sess-source", declared_family="f1"),
                    SessionRecord(session_id="sess-user", declared_family="f1"),
                    DocumentRecord(
                        document_id="doc-source",
                        session_id="sess-source",
                        filename="source.pdf",
                        status="parsed",
                        raw_bytes=b"%PDF-source",
                        raw_text="source",
                        artifact_json={
                            "metadata": {
                                "debug_material_bundle": True,
                                "synthetic_bundle_id": "pkg-old",
                                "debug_bundle_scenario_label": "旧演示材料包",
                            }
                        },
                    ),
                    DocumentRecord(
                        document_id="doc-imported",
                        session_id="sess-user",
                        filename="imported.pdf",
                        status="parsed",
                        raw_bytes=b"%PDF-imported",
                        raw_text="imported",
                        artifact_json={
                            "metadata": {
                                "debug_material_bundle": True,
                                "synthetic_bundle_id": "pkg-import-1",
                                "material_package_import": True,
                                "archived_package_id": "pkg-old",
                            }
                        },
                    ),
                    DocumentChunkRecord(
                        chunk_id="chunk-source",
                        document_id="doc-source",
                        session_id="sess-source",
                        ordinal=0,
                        page_number=1,
                        text="source",
                        metadata_json={"synthetic_bundle_id": "pkg-old"},
                    ),
                    EvidenceItemRecord(
                        evidence_id="evi-source",
                        session_id="sess-source",
                        document_id="doc-source",
                        chunk_id="chunk-source",
                        evidence_type="ds160",
                        field_path="/identity/full_name",
                        value="Chen Wei",
                        excerpt="Full name: Chen Wei",
                        metadata_json={"synthetic_bundle_id": "pkg-old"},
                    ),
                    SessionTurnRecord(
                        turn_id="turn-source",
                        turn_index=0,
                        session_id="sess-source",
                        role="user",
                        content="synthetic source turn",
                        source="debug_material_bundle",
                        metadata_json={"synthetic_bundle_id": "pkg-old"},
                    ),
                ]
            )
            db.commit()

            plans = build_cleanup_plan(db, package_id="pkg-old")
            assert len(plans) == 1
            plan = plans[0]
            assert plan.document_ids == ("doc-source",)
            assert plan.chunk_ids == ("chunk-source",)
            assert plan.evidence_ids == ("evi-source",)
            assert plan.turn_ids == ("turn-source",)

            apply_cleanup_plan(db, plans)

            remaining_docs = {item.document_id for item in db.scalars(select(DocumentRecord))}
            assert remaining_docs == {"doc-imported"}
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_publish_validated_archive_creates_listable_source_package(tmp_path: Path) -> None:
    engine, factory = _session_factory(tmp_path)
    try:
        with factory() as db:
            _add_complete_validation_session(db)
            db.commit()

            result = publish_validated_archive(
                db,
                validation_artifact={
                    "session_id": "sess-validated",
                    "validation": {"passed": True},
                },
                package_id=PACKAGE_ID,
                label=PACKAGE_LABEL,
                replace=False,
            )

            assert result["package_id"] == PACKAGE_ID
            payload = MaterialPackageArchiveService(db).list_packages()
            packages = payload["packages"]
            assert len(packages) == 1
            assert packages[0]["package_id"] == PACKAGE_ID
            assert packages[0]["label"] == PACKAGE_LABEL
            assert packages[0]["status"] == "ready"
            assert packages[0]["document_count"] == len(REQUIRED_DOCUMENT_TYPES)
            assert "自洽" not in json.dumps(packages[0], ensure_ascii=False)
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_publish_rejects_incomplete_current_db_document(tmp_path: Path) -> None:
    engine, factory = _session_factory(tmp_path)
    try:
        with factory() as db:
            _add_complete_validation_session(db)
            db.flush()
            incomplete = db.get(DocumentRecord, "doc-funding_proof")
            assert incomplete is not None
            incomplete.artifact_json = {
                **dict(incomplete.artifact_json or {}),
                "understanding_status": "failed",
            }
            db.commit()

            with pytest.raises(RuntimeError, match="incomplete current DB documents"):
                publish_validated_archive(
                    db,
                    validation_artifact={
                        "session_id": "sess-validated",
                        "validation": {"passed": True},
                    },
                    package_id=PACKAGE_ID,
                    label=PACKAGE_LABEL,
                    replace=False,
                )
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_publish_replace_keeps_existing_package_when_validation_session_missing(
    tmp_path: Path,
) -> None:
    engine, factory = _session_factory(tmp_path)
    try:
        with factory() as db:
            _add_existing_archive_package(db)
            db.commit()

            with pytest.raises(RuntimeError, match="validation session not found"):
                publish_validated_archive(
                    db,
                    validation_artifact={
                        "session_id": "sess-missing-validation",
                        "validation": {"passed": True},
                    },
                    package_id=PACKAGE_ID,
                    label=PACKAGE_LABEL,
                    replace=True,
                )

            db.rollback()
            remaining = db.get(DocumentRecord, "doc-existing-archive")
            assert remaining is not None
            assert remaining.artifact_json["metadata"]["synthetic_bundle_id"] == PACKAGE_ID
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_publish_replace_copy_failure_keeps_existing_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory = _session_factory(tmp_path)
    try:
        with factory() as db:
            _add_existing_archive_package(db)
            _add_complete_validation_session(db)
            db.commit()

            def fail_copy(*args, **kwargs):
                raise RuntimeError("copy failed after cleanup staged")

            monkeypatch.setattr(
                f1_demo_tool,
                "_copy_validated_document_as_archive_source",
                fail_copy,
            )

            with pytest.raises(RuntimeError, match="copy failed"):
                publish_validated_archive(
                    db,
                    validation_artifact={
                        "session_id": "sess-validated",
                        "validation": {"passed": True},
                    },
                    package_id=PACKAGE_ID,
                    label=PACKAGE_LABEL,
                    replace=True,
                )

            remaining = db.get(DocumentRecord, "doc-existing-archive")
            assert remaining is not None
            assert remaining.artifact_json["metadata"]["synthetic_bundle_id"] == PACKAGE_ID
            archive_sessions = list(
                db.scalars(
                    select(SessionRecord).where(
                        SessionRecord.session_id != "sess-existing-archive",
                        SessionRecord.session_id != "sess-validated",
                    )
                )
            )
            assert archive_sessions == []
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
