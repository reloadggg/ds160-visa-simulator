import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest
from pydantic import ValidationError
from sqlalchemy import JSON, Float, Text

from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.domain.evidence import (
    DocumentArtifact,
    DocumentChunk,
    DocumentSourceType,
    EvidenceItem,
)


def test_document_artifact_exposes_phase1_contract_fields() -> None:
    artifact = DocumentArtifact(
        document_id="doc-1",
        session_id="sess-1",
        filename="funding_proof.pdf",
        source_type=DocumentSourceType.PDF,
        parser_name="pymupdf",
        status="parsed",
        page_count=1,
    )

    assert {source_type.value for source_type in DocumentSourceType} == {
        "text",
        "pdf",
        "docx",
        "image",
        "unknown",
    }
    assert artifact.document_id == "doc-1"
    assert artifact.session_id == "sess-1"
    assert artifact.filename == "funding_proof.pdf"
    assert artifact.source_type is DocumentSourceType.PDF
    assert artifact.parser_name == "pymupdf"
    assert artifact.status == "parsed"
    assert artifact.page_count == 1
    assert artifact.metadata == {}


def test_document_chunk_exposes_minimal_contract_defaults() -> None:
    chunk = DocumentChunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        session_id="sess-1",
        ordinal=0,
        text="Parent sponsor bank statement",
    )

    assert chunk.chunk_id == "chunk-1"
    assert chunk.document_id == "doc-1"
    assert chunk.session_id == "sess-1"
    assert chunk.ordinal == 0
    assert chunk.page_number is None
    assert chunk.text == "Parent sponsor bank statement"
    assert chunk.metadata == {}


def test_evidence_item_to_ref_preserves_core_fields() -> None:
    chunk = DocumentChunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        session_id="sess-1",
        ordinal=0,
        text="Parent sponsor bank statement",
    )
    item = EvidenceItem(
        evidence_id="evi-1",
        session_id="sess-1",
        document_id="doc-1",
        chunk_id="chunk-1",
        evidence_type="funding_proof",
        field_path="/funding/primary_source",
        value="parents",
        excerpt=chunk.text,
    )

    ref = item.to_ref()

    assert item.confidence == 1.0
    assert item.metadata == {}
    assert ref.evidence_id == "evi-1"
    assert ref.document_id == "doc-1"
    assert ref.chunk_id == "chunk-1"
    assert ref.excerpt == "Parent sponsor bank statement"


def test_evidence_item_confidence_validates_bounds() -> None:
    with pytest.raises(ValidationError):
        EvidenceItem(
            evidence_id="evi-low",
            session_id="sess-1",
            document_id="doc-1",
            chunk_id="chunk-1",
            evidence_type="funding_proof",
            field_path="/funding/primary_source",
            excerpt="Parent sponsor bank statement",
            confidence=-0.1,
        )

    with pytest.raises(ValidationError):
        EvidenceItem(
            evidence_id="evi-high",
            session_id="sess-1",
            document_id="doc-1",
            chunk_id="chunk-1",
            evidence_type="funding_proof",
            field_path="/funding/primary_source",
            excerpt="Parent sponsor bank statement",
            confidence=1.1,
        )


def test_evidence_records_define_expected_tables_and_columns() -> None:
    assert DocumentChunkRecord.__tablename__ == "document_chunks"
    assert EvidenceItemRecord.__tablename__ == "evidence_items"

    chunk_columns = DocumentChunkRecord.__table__.c
    assert chunk_columns.chunk_id.primary_key is True
    assert chunk_columns.document_id.index is True
    assert chunk_columns.session_id.index is True
    assert chunk_columns.page_number.nullable is True
    assert isinstance(chunk_columns.text.type, Text)
    assert isinstance(chunk_columns.metadata_json.type, JSON)
    assert chunk_columns.metadata_json.default is not None
    assert callable(chunk_columns.metadata_json.default.arg)
    assert chunk_columns.metadata_json.default.arg.__name__ == "dict"

    evidence_columns = EvidenceItemRecord.__table__.c
    assert evidence_columns.evidence_id.primary_key is True
    assert evidence_columns.session_id.index is True
    assert evidence_columns.document_id.index is True
    assert evidence_columns.chunk_id.index is True
    assert evidence_columns.evidence_type.index is True
    assert evidence_columns.field_path.index is True
    assert evidence_columns.value.nullable is True
    assert isinstance(evidence_columns.excerpt.type, Text)
    assert isinstance(evidence_columns.confidence.type, Float)
    assert evidence_columns.confidence.default is not None
    assert evidence_columns.confidence.default.arg == 1.0
    assert isinstance(evidence_columns.metadata_json.type, JSON)
    assert evidence_columns.metadata_json.default is not None
    assert callable(evidence_columns.metadata_json.default.arg)
    assert evidence_columns.metadata_json.default.arg.__name__ == "dict"


def test_app_main_create_all_includes_evidence_tables(tmp_path: pytest.TempPathFactory) -> None:
    db_path = tmp_path / "isolated-schema.db"
    script = textwrap.dedent(
        """
        import json
        import sys

        from sqlalchemy import create_engine, inspect

        import app.db.session as db_session

        temp_engine = create_engine(f"sqlite:///{sys.argv[1]}")
        db_session.engine = temp_engine

        import app.main  # noqa: F401

        print(json.dumps(sorted(inspect(temp_engine).get_table_names())))
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script, str(db_path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ.copy(),
    )
    table_names = set(json.loads(result.stdout))

    assert "document_chunks" in table_names
    assert "evidence_items" in table_names
