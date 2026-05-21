from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.db.base import Base
from app.db.session import get_db
from app.domain.rag import PolicyKnowledgeIngestResult
from app.main import app
from app.services.visa_policy_ingest_service import (
    PolicyKnowledgeIngestService,
    PolicyKnowledgeParseError,
)


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'rag-api.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings_module.settings, "rag_enabled", False)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_rag_status_reports_disabled(client: TestClient) -> None:
    response = client.get("/v1/rag/status")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["skip_reason"] == "disabled"


def test_rag_file_upload_returns_ingest_result(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_ingest_upload(self, **kwargs):
        assert kwargs["filename"] == "policy.md"
        assert kwargs["source_type"] == "third_party_reference"
        return PolicyKnowledgeIngestResult(
            status="indexed",
            source_id="src-1",
            source_type="third_party_reference",
            title="policy.md",
            collection_name="us_visa_third_party_reference_v1",
            chunk_count=1,
        )

    monkeypatch.setattr(
        PolicyKnowledgeIngestService,
        "ingest_upload",
        fake_ingest_upload,
    )

    response = client.post(
        "/v1/rag/files",
        files={"file": ("policy.md", b"case text", "text/markdown")},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "indexed"
    assert response.json()["chunk_count"] == 1


def test_rag_file_upload_ignores_client_source_type(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_source_types: list[str] = []

    def fake_ingest_upload(self, **kwargs):
        captured_source_types.append(kwargs["source_type"])
        return PolicyKnowledgeIngestResult(
            status="indexed",
            source_id="src-1",
            source_type=kwargs["source_type"],
            title="policy.md",
            collection_name="us_visa_third_party_reference_v1",
            chunk_count=1,
        )

    monkeypatch.setattr(
        PolicyKnowledgeIngestService,
        "ingest_upload",
        fake_ingest_upload,
    )

    response = client.post(
        "/v1/rag/files",
        files={"file": ("policy.md", b"case text", "text/markdown")},
        data={"source_type": "federal_official"},
    )

    assert response.status_code == 202
    assert captured_source_types == ["third_party_reference"]


def test_rag_file_upload_rejects_oversized_payload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "rag_upload_max_size_mb", 1)

    response = client.post(
        "/v1/rag/files",
        files={"file": ("large.md", b"x" * (1024 * 1024 + 1), "text/markdown")},
    )

    assert response.status_code == 413


def test_rag_file_upload_returns_422_for_parse_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_ingest_upload(self, **kwargs):
        raise PolicyKnowledgeParseError("Uploaded policy file could not be parsed")

    monkeypatch.setattr(
        PolicyKnowledgeIngestService,
        "ingest_upload",
        fake_ingest_upload,
    )

    response = client.post(
        "/v1/rag/files",
        files={"file": ("broken.pdf", b"%PDF-broken", "application/pdf")},
    )

    assert response.status_code == 422
