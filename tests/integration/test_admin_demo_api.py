from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import (
    AccessKeyRecord,
    AccessKeySessionRecord,
    CaseMemorySnapshotRecord,
    DocumentRecord,
    JobRecord,
    SessionRecord,
    SessionTurnRecord,
)
from app.db.session import get_db
from app.domain.case_memory import (
    CaseClaim,
    EvidenceCard,
    MaterialUnderstandingJob,
    MaterialUnderstandingResult,
)
from app.main import app
from app.services.access_key_service import hash_secret
from app.services.case_memory_service import CaseMemoryService
from app.services.material_package_archive_service import VALIDATED_ARCHIVE_SOURCE_REASON


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-demo-api.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(settings_module.settings, "app_auth_password", "admin-pass")
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings_module.settings, "app_auth_csrf_protection", False)

    def override_get_db() -> Generator[Session, None, None]:
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.state.auth_session_factory = db_session_factory
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.auth_session_factory = None


def test_admin_key_quota_and_session_ownership_flow(client: TestClient) -> None:
    login_response = client.post("/v1/admin/login", json={"password": "admin-pass"})
    assert login_response.status_code == 200

    created = client.post(
        "/v1/admin/access-keys",
        json={"label": "demo customer", "usage_limit": 1},
    )
    assert created.status_code == 200
    key_payload = created.json()
    plaintext_key = key_payload["key"]
    key_id = key_payload["record"]["key_id"]
    assert plaintext_key.startswith(f"ds160_{key_id}_")
    assert key_payload["record"]["usage_count"] == 0
    assert key_payload["record"]["masked_key_preview"] == f"ds160_{key_id}_••••"
    assert key_payload["record"]["secret_available"] is True
    assert "key" not in key_payload["record"]
    assert "key_display_value" not in key_payload["record"]

    client.post("/v1/admin/logout")
    user_login = client.post("/v1/auth/login", json={"password": plaintext_key})
    assert user_login.status_code == 200
    assert user_login.json()["history_namespace"] == f"key_{key_id}"
    assert user_login.json()["access_key_quota"] == {
        "key_id": key_id,
        "label": "demo customer",
        "usage_limit": 1,
        "usage_count": 0,
        "remaining_uses": 1,
        "can_create_session": True,
        "expires_at": None,
        "revoked": False,
        "revoked_at": None,
    }
    assert plaintext_key not in user_login.text
    user_reveal = client.get(f"/v1/admin/access-keys/{key_id}/secret")
    assert user_reveal.status_code == 401

    first_session = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert first_session.status_code == 201
    session_id = first_session.json()["session_id"]

    second_session = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert second_session.status_code == 403
    assert "quota exhausted" in second_session.json()["detail"]

    session_list = client.get("/v1/sessions")
    assert session_list.status_code == 200
    assert [item["session_id"] for item in session_list.json()["sessions"]] == [
        session_id
    ]
    quota_status = client.get("/v1/auth/me")
    assert quota_status.status_code == 200
    assert quota_status.json()["access_key_quota"]["usage_count"] == 1
    assert quota_status.json()["access_key_quota"]["remaining_uses"] == 0
    assert quota_status.json()["access_key_quota"]["can_create_session"] is False
    assert plaintext_key not in quota_status.text

    client.post("/v1/auth/logout")
    client.post("/v1/admin/login", json={"password": "admin-pass"})
    keys = client.get("/v1/admin/access-keys")
    assert keys.status_code == 200
    [record] = keys.json()["keys"]
    assert record["usage_count"] == 1
    assert record["remaining_uses"] == 0
    assert record["masked_key_preview"] == f"ds160_{key_id}_••••"
    assert record["secret_available"] is True
    assert "key" not in record
    assert "key_display_value" not in record
    assert plaintext_key not in keys.text

    revealed = client.get(f"/v1/admin/access-keys/{key_id}/secret")
    assert revealed.status_code == 200
    assert revealed.json() == {
        "key_id": key_id,
        "key": plaintext_key,
        "available": True,
    }

    disabled = client.patch(
        f"/v1/admin/access-keys/{key_id}",
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["record"]["enabled"] is False

    client.post("/v1/admin/logout")
    disabled_login = client.post("/v1/auth/login", json={"password": plaintext_key})
    assert disabled_login.status_code == 200
    disabled_session_list = client.get("/v1/sessions")
    assert disabled_session_list.status_code == 200
    assert [item["session_id"] for item in disabled_session_list.json()["sessions"]] == [
        session_id
    ]
    disabled_create = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert disabled_create.status_code == 403
    assert "revoked" in disabled_create.json()["detail"]

    client.post("/v1/auth/logout")
    client.post("/v1/admin/login", json={"password": "admin-pass"})
    increased = client.patch(
        f"/v1/admin/access-keys/{key_id}",
        json={"enabled": True, "usage_limit": 2},
    )
    assert increased.status_code == 200
    assert increased.json()["record"]["remaining_uses"] == 1

    client.post("/v1/admin/logout")
    assert client.post("/v1/auth/login", json={"password": plaintext_key}).status_code == 200
    resumed_create = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert resumed_create.status_code == 201


def test_user_can_clear_own_session_history_without_removing_current_session(
    client: TestClient,
    db_session_factory,
) -> None:
    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    first_key = client.post(
        "/v1/admin/access-keys",
        json={"label": "first customer", "usage_limit": 3},
    ).json()["key"]
    second_key = client.post(
        "/v1/admin/access-keys",
        json={"label": "second customer", "usage_limit": 1},
    ).json()["key"]

    client.post("/v1/admin/logout")
    assert client.post("/v1/auth/login", json={"password": first_key}).status_code == 200
    old_session = client.post("/v1/sessions", json={"declared_family": "f1"}).json()[
        "session_id"
    ]
    current_session = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
    ).json()["session_id"]

    client.post("/v1/auth/logout")
    assert client.post("/v1/auth/login", json={"password": second_key}).status_code == 200
    second_session = client.post("/v1/sessions", json={"declared_family": "f1"}).json()[
        "session_id"
    ]

    client.post("/v1/auth/logout")
    assert client.post("/v1/auth/login", json={"password": first_key}).status_code == 200

    with db_session_factory() as db:
        db.add(
            SessionTurnRecord(
                turn_id="turn-clear-old",
                turn_index=0,
                session_id=old_session,
                role="user",
                content="hello",
                source="test",
                metadata_json={},
                client_message_id="client-clear-old",
            )
        )
        db.add(
            DocumentRecord(
                document_id="doc-clear-old",
                session_id=old_session,
                filename="old.pdf",
                status="parsed",
                artifact_json={},
                raw_bytes=b"pdf",
                raw_text="old document",
            )
        )
        db.add(
            JobRecord(
                job_id="job-clear-old",
                session_id=old_session,
                kind="case_understanding",
                status="done",
                payload_json={},
            )
        )
        db.add(
            DocumentChunkRecord(
                chunk_id="chunk-clear-old",
                document_id="doc-clear-old",
                session_id=old_session,
                ordinal=0,
                page_number=1,
                text="old chunk",
                metadata_json={},
            )
        )
        db.add(
            EvidenceItemRecord(
                evidence_id="evidence-clear-old",
                session_id=old_session,
                document_id="doc-clear-old",
                chunk_id="chunk-clear-old",
                evidence_type="identity",
                field_path="/identity/name",
                value="old",
                excerpt="old chunk",
                confidence=1.0,
                metadata_json={},
            )
        )
        db.add(
            CaseMemorySnapshotRecord(
                session_id=old_session,
                snapshot_json={"session_id": old_session},
            )
        )
        db.commit()

    clear_response = client.delete(
        f"/v1/sessions?exclude_session_id={current_session}"
    )
    assert clear_response.status_code == 200
    assert clear_response.json() == {
        "deleted_count": 1,
        "remaining_session_id": current_session,
    }
    session_list = client.get("/v1/sessions")
    assert session_list.status_code == 200
    assert [item["session_id"] for item in session_list.json()["sessions"]] == [
        current_session
    ]

    with db_session_factory() as db:
        assert db.get(SessionRecord, old_session) is None
        assert db.get(SessionTurnRecord, "turn-clear-old") is None
        assert db.get(DocumentRecord, "doc-clear-old") is None
        assert db.get(JobRecord, "job-clear-old") is None
        assert db.get(DocumentChunkRecord, "chunk-clear-old") is None
        assert db.get(EvidenceItemRecord, "evidence-clear-old") is None
        assert db.get(CaseMemorySnapshotRecord, old_session) is None
        assert db.get(SessionRecord, current_session) is not None
        assert db.get(SessionRecord, second_session) is not None
        assert (
            db.query(AccessKeySessionRecord)
            .filter(AccessKeySessionRecord.session_id == old_session)
            .one_or_none()
            is None
        )

    client.post("/v1/auth/logout")
    assert client.post("/v1/auth/login", json={"password": second_key}).status_code == 200
    assert client.get("/v1/sessions").json()["sessions"][0]["session_id"] == second_session


def _material_result(document_id: str, claim_id: str, value: str) -> MaterialUnderstandingResult:
    evidence_id = f"ev-{claim_id}"
    return MaterialUnderstandingResult(
        evidence_cards=[
            EvidenceCard(
                evidence_id=evidence_id,
                source_type="uploaded_file",
                document_id=document_id,
                excerpt=value,
                claim_refs=[claim_id],
                confidence=0.92,
            )
        ],
        extracted_claims=[
            CaseClaim(
                claim_id=claim_id,
                field_path="/education/school_name",
                value=value,
                status="documented",
                supporting_evidence_ids=[evidence_id],
                confidence=0.92,
            )
        ],
        confidence=0.92,
    )


def test_current_key_material_cleanup_tombstones_owned_uploads_only(
    client: TestClient,
    db_session_factory,
) -> None:
    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    first_key_payload = client.post(
        "/v1/admin/access-keys",
        json={"label": "first cleanup customer", "usage_limit": 2},
    ).json()
    second_key_payload = client.post(
        "/v1/admin/access-keys",
        json={"label": "second cleanup customer", "usage_limit": 1},
    ).json()
    first_key = first_key_payload["key"]
    second_key = second_key_payload["key"]

    client.post("/v1/admin/logout")
    assert client.post("/v1/auth/login", json={"password": first_key}).status_code == 200
    first_current_session = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
    ).json()["session_id"]
    first_old_session = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
    ).json()["session_id"]

    client.post("/v1/auth/logout")
    assert client.post("/v1/auth/login", json={"password": second_key}).status_code == 200
    second_session = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
    ).json()["session_id"]

    with db_session_factory() as db:
        documents = [
            DocumentRecord(
                document_id="doc-first-current-upload",
                session_id=first_current_session,
                filename="current-i20.pdf",
                status="parsed",
                artifact_json={"document_type": "i20"},
                raw_bytes=b"i20",
                raw_text="Example University",
            ),
            DocumentRecord(
                document_id="doc-first-old-upload",
                session_id=first_old_session,
                filename="old-bank.pdf",
                status="parsed",
                artifact_json={"document_type": "funding_proof"},
                raw_bytes=b"bank",
                raw_text="Bank statement",
            ),
            DocumentRecord(
                document_id="doc-first-template-source",
                session_id=first_current_session,
                filename="validated-template-i20.pdf",
                status="parsed",
                artifact_json={
                    "document_type": "i20",
                    "metadata": {
                        "debug_material_bundle": True,
                        "source_validation_session_id": "sess-validation",
                        "archive_source_reason": VALIDATED_ARCHIVE_SOURCE_REASON,
                        "validation_status": "passed",
                    },
                },
                raw_bytes=b"template",
                raw_text="Template source",
            ),
            DocumentRecord(
                document_id="doc-second-upload",
                session_id=second_session,
                filename="second-i20.pdf",
                status="parsed",
                artifact_json={"document_type": "i20"},
                raw_bytes=b"second",
                raw_text="Other University",
            ),
        ]
        db.add_all(documents)
        db.commit()
        service = CaseMemoryService(db)
        before = service.upsert_material_understanding(
            document_id="doc-first-current-upload",
            job=MaterialUnderstandingJob(
                job_id="job-first-current-upload",
                document_id="doc-first-current-upload",
                status="completed",
                result=_material_result(
                    "doc-first-current-upload",
                    "claim-first-school",
                    "Example University",
                ),
            ),
        )
        assert before.claims
        db.commit()

    client.post("/v1/auth/logout")
    assert client.post("/v1/auth/login", json={"password": first_key}).status_code == 200
    response = client.delete("/v1/materials/current-key")

    assert response.status_code == 200
    assert response.json() == {
        "key_id": first_key_payload["record"]["key_id"],
        "session_count": 2,
        "cleared_document_count": 2,
        "skipped_template_count": 1,
        "affected_session_ids": sorted([first_current_session, first_old_session]),
    }
    exported_documents = client.get(
        f"/v1/sessions/{first_current_session}/reports/export"
    ).json()["documents"]
    exported_document_ids = {item["document_id"] for item in exported_documents}
    assert "doc-first-current-upload" not in exported_document_ids
    assert "doc-first-template-source" in exported_document_ids

    with db_session_factory() as db:
        assert db.get(DocumentRecord, "doc-first-current-upload").status == "tombstoned"
        assert db.get(DocumentRecord, "doc-first-old-upload").status == "tombstoned"
        template_document = db.get(DocumentRecord, "doc-first-template-source")
        assert template_document is not None
        assert template_document.status == "parsed"
        assert db.get(DocumentRecord, "doc-second-upload").status == "parsed"
        assert db.get(SessionRecord, first_current_session) is not None
        assert db.get(SessionRecord, first_old_session) is not None
        assert db.get(SessionRecord, second_session) is not None
        assert db.get(CaseMemorySnapshotRecord, first_current_session) is not None
        snapshot = db.get(CaseMemorySnapshotRecord, first_current_session)
        assert snapshot is not None
        assert snapshot.snapshot_json["claims"] == []
        assert (
            db.query(AccessKeySessionRecord)
            .filter(AccessKeySessionRecord.session_id == first_current_session)
            .one_or_none()
            is not None
        )

    client.post("/v1/auth/logout")
    assert client.post("/v1/auth/login", json={"password": second_key}).status_code == 200
    second_list = client.get("/v1/sessions")
    assert [item["session_id"] for item in second_list.json()["sessions"]] == [
        second_session
    ]


def test_admin_can_clear_materials_for_selected_access_key(
    client: TestClient,
    db_session_factory,
) -> None:
    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    key_payload = client.post(
        "/v1/admin/access-keys",
        json={"label": "admin cleanup target", "usage_limit": 1},
    ).json()
    key_id = key_payload["record"]["key_id"]
    plaintext_key = key_payload["key"]

    client.post("/v1/admin/logout")
    assert client.post("/v1/auth/login", json={"password": plaintext_key}).status_code == 200
    session_id = client.post("/v1/sessions", json={"declared_family": "f1"}).json()[
        "session_id"
    ]

    with db_session_factory() as db:
        db.add(
            DocumentRecord(
                document_id="doc-admin-cleanup",
                session_id=session_id,
                filename="admin-cleanup.pdf",
                status="parsed",
                artifact_json={"document_type": "passport_bio"},
                raw_bytes=b"passport",
                raw_text="Passport",
            )
        )
        db.commit()

    client.post("/v1/auth/logout")
    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    assert client.delete("/v1/materials/current-key").status_code == 403
    response = client.delete(f"/v1/admin/access-keys/{key_id}/materials")

    assert response.status_code == 200
    assert response.json() == {
        "key_id": key_id,
        "session_count": 1,
        "cleared_document_count": 1,
        "skipped_template_count": 0,
        "affected_session_ids": [session_id],
    }

    missing = client.delete("/v1/admin/access-keys/missing-key/materials")
    assert missing.status_code == 404

    with db_session_factory() as db:
        document = db.get(DocumentRecord, "doc-admin-cleanup")
        assert document is not None
        assert document.status == "tombstoned"
        assert db.get(SessionRecord, session_id) is not None


def test_access_key_secret_storage_list_masking_legacy_and_filters(
    client: TestClient,
    db_session_factory,
) -> None:
    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200

    active_response = client.post(
        "/v1/admin/access-keys",
        json={"label": "Alpha Customer", "usage_limit": 3},
    )
    assert active_response.status_code == 200
    active_payload = active_response.json()
    active_key = active_payload["key"]
    active_id = active_payload["record"]["key_id"]

    disabled_response = client.post(
        "/v1/admin/access-keys",
        json={"label": "Beta Disabled", "usage_limit": 3, "enabled": False},
    )
    assert disabled_response.status_code == 200
    disabled_id = disabled_response.json()["record"]["key_id"]

    expired_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    expired_response = client.post(
        "/v1/admin/access-keys",
        json={
            "label": "Gamma Expired",
            "usage_limit": 3,
            "expires_at": expired_at,
        },
    )
    assert expired_response.status_code == 200
    expired_id = expired_response.json()["record"]["key_id"]

    with db_session_factory() as db:
        active_record = db.get(AccessKeyRecord, active_id)
        assert active_record is not None
        assert active_record.key_display_value == active_key
        assert active_record.key_hash == hash_secret(active_key)

    listed = client.get("/v1/admin/access-keys")
    assert listed.status_code == 200
    listed_payload = listed.json()
    assert active_key not in listed.text
    assert all("key" not in record for record in listed_payload["keys"])
    assert all("key_display_value" not in record for record in listed_payload["keys"])
    active_list_record = next(
        record for record in listed_payload["keys"] if record["key_id"] == active_id
    )
    assert active_list_record["masked_key_preview"] == f"ds160_{active_id}_••••"
    assert active_list_record["secret_available"] is True

    reveal = client.get(f"/v1/admin/access-keys/{active_id}/secret")
    assert reveal.status_code == 200
    assert reveal.json()["key"] == active_key

    with db_session_factory() as db:
        legacy_record = db.get(AccessKeyRecord, active_id)
        assert legacy_record is not None
        legacy_record.key_display_value = None
        db.add(legacy_record)
        db.commit()

    legacy_reveal = client.get(f"/v1/admin/access-keys/{active_id}/secret")
    assert legacy_reveal.status_code == 200
    assert legacy_reveal.json() == {
        "key_id": active_id,
        "key": None,
        "available": False,
        "detail": "该访问密钥是在密钥持久化启用前创建的，明文不可找回。",
    }
    legacy_list = client.get(f"/v1/admin/access-keys?q={active_id}")
    assert legacy_list.status_code == 200
    assert legacy_list.json()["keys"][0]["secret_available"] is False

    label_filter = client.get("/v1/admin/access-keys?q=alpha")
    assert label_filter.status_code == 200
    assert [record["key_id"] for record in label_filter.json()["keys"]] == [active_id]

    id_filter = client.get(f"/v1/admin/access-keys?q={disabled_id}")
    assert id_filter.status_code == 200
    assert [record["key_id"] for record in id_filter.json()["keys"]] == [disabled_id]

    disabled_filter = client.get("/v1/admin/access-keys?status=disabled")
    assert disabled_filter.status_code == 200
    assert [record["key_id"] for record in disabled_filter.json()["keys"]] == [
        disabled_id
    ]

    expired_filter = client.get("/v1/admin/access-keys?expired=true")
    assert expired_filter.status_code == 200
    assert [record["key_id"] for record in expired_filter.json()["keys"]] == [
        expired_id
    ]

    active_filter = client.get("/v1/admin/access-keys?expired=false&status=enabled")
    assert active_filter.status_code == 200
    assert [record["key_id"] for record in active_filter.json()["keys"]] == [
        active_id
    ]


def test_expired_key_can_login_view_history_but_not_create_session(
    client: TestClient,
) -> None:
    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    created = client.post(
        "/v1/admin/access-keys",
        json={"label": "expiring customer", "usage_limit": 2},
    )
    assert created.status_code == 200
    plaintext_key = created.json()["key"]
    key_id = created.json()["record"]["key_id"]

    client.post("/v1/admin/logout")
    login = client.post("/v1/auth/login", json={"password": plaintext_key})
    assert login.status_code == 200
    first_session = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert first_session.status_code == 201
    session_id = first_session.json()["session_id"]

    client.post("/v1/auth/logout")
    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    expired_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    expired = client.patch(
        f"/v1/admin/access-keys/{key_id}",
        json={"expires_at": expired_at},
    )
    assert expired.status_code == 200
    assert expired.json()["record"]["can_create_session"] is False

    client.post("/v1/admin/logout")
    expired_login = client.post("/v1/auth/login", json={"password": plaintext_key})
    assert expired_login.status_code == 200
    session_list = client.get("/v1/sessions")
    assert session_list.status_code == 200
    assert [item["session_id"] for item in session_list.json()["sessions"]] == [
        session_id
    ]

    blocked = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "access key is expired"


def test_user_model_config_requires_admin_toggle(
    client: TestClient,
) -> None:
    config = client.get("/v1/app-config").json()
    assert config["user_model_config_enabled"] is False

    client.post("/v1/admin/login", json={"password": "admin-pass"})
    updated = client.patch(
        "/v1/admin/settings",
        json={"user_model_config_enabled": True},
    )
    assert updated.status_code == 200
    assert updated.json()["user_model_config_enabled"] is True
    assert client.get("/v1/app-config").json()["user_model_config_enabled"] is False


def test_wx_entry_config_defaults_closed_and_follows_admin_toggle(
    client: TestClient,
) -> None:
    config = client.get("/v1/app-config").json()
    assert config["wx_entry_enabled"] is False

    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    admin_settings = client.get("/v1/admin/settings")
    assert admin_settings.status_code == 200
    assert admin_settings.json()["wx_entry_enabled"] is False

    enabled = client.patch(
        "/v1/admin/settings",
        json={"wx_entry_enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["wx_entry_enabled"] is True
    assert client.get("/v1/app-config").json()["wx_entry_enabled"] is True

    disabled = client.patch(
        "/v1/admin/settings",
        json={"wx_entry_enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["wx_entry_enabled"] is False
    assert client.get("/v1/app-config").json()["wx_entry_enabled"] is False


def test_admin_runtime_model_config_preserves_key_and_fetches_models(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeModelListResponse:
        def model_dump(self, *, mode: str) -> dict[str, Any]:
            captured["mode"] = mode
            return {
                "data": [
                    {"id": "admin-model"},
                    {"id": "admin-model"},
                    {"id": "other-model"},
                ]
            }

    class FakeModels:
        def list(self) -> FakeModelListResponse:
            captured["list_called"] = True
            return FakeModelListResponse()

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs
            self.models = FakeModels()

    monkeypatch.setattr("app.services.admin_model_config_service.OpenAI", FakeOpenAI)

    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    saved = client.patch(
        "/v1/admin/settings",
        json={
            "model_base_url": "https://admin-models.example.test",
            "model_api_key": "admin-secret-key",
            "model_name": "admin-model",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["model_base_url"] == "https://admin-models.example.test/v1"
    assert saved.json()["model_api_key_configured"] is True
    assert "model_api_key" not in saved.json()
    assert "admin-secret-key" not in saved.text

    omitted = client.patch(
        "/v1/admin/settings",
        json={"show_github_link": True},
    )
    assert omitted.status_code == 200
    assert omitted.json()["model_api_key_configured"] is True
    assert "admin-secret-key" not in omitted.text

    null_preserved = client.patch(
        "/v1/admin/settings",
        json={"model_api_key": None},
    )
    assert null_preserved.status_code == 200
    assert null_preserved.json()["model_api_key_configured"] is True
    assert "admin-secret-key" not in null_preserved.text

    preserved = client.patch(
        "/v1/admin/settings",
        json={"model_api_key": "", "model_name": "other-model"},
    )
    assert preserved.status_code == 200
    assert preserved.json()["model_api_key_configured"] is True
    assert "admin-secret-key" not in preserved.text

    response = client.post("/v1/admin/model-config/models", json={})
    assert response.status_code == 200
    assert captured["mode"] == "json"
    assert captured["list_called"] is True
    assert captured["kwargs"] == {
        "api_key": "admin-secret-key",
        "base_url": "https://admin-models.example.test/v1",
        "timeout": settings_module.settings.openai_timeout_seconds,
        "max_retries": 0,
        "default_headers": {"User-Agent": "curl/8.5.0"},
    }
    assert response.json() == {
        "models": [
            {"id": "admin-model", "label": "admin-model"},
            {"id": "other-model", "label": "other-model"},
        ],
        "source": "admin",
        "base_url": "https://admin-models.example.test/v1",
    }
    assert "admin-secret-key" not in response.text


def test_admin_model_list_uses_saved_base_url_and_key_before_model_is_selected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeModelListResponse:
        def model_dump(self, *, mode: str) -> dict[str, Any]:
            captured["mode"] = mode
            return {"data": [{"id": "model-from-saved-admin-config"}]}

    class FakeModels:
        def list(self) -> FakeModelListResponse:
            captured["list_called"] = True
            return FakeModelListResponse()

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs
            self.models = FakeModels()

    monkeypatch.setattr("app.services.admin_model_config_service.OpenAI", FakeOpenAI)

    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    saved = client.patch(
        "/v1/admin/settings",
        json={
            "model_base_url": "https://admin-models.example.test",
            "model_api_key": "admin-secret-key",
        },
    )
    assert saved.status_code == 200

    response = client.post("/v1/admin/model-config/models", json={})

    assert response.status_code == 200
    assert captured["kwargs"]["api_key"] == "admin-secret-key"
    assert captured["kwargs"]["base_url"] == "https://admin-models.example.test/v1"
    assert response.json() == {
        "models": [
            {
                "id": "model-from-saved-admin-config",
                "label": "model-from-saved-admin-config",
            }
        ],
        "source": "admin",
        "base_url": "https://admin-models.example.test/v1",
    }
    assert "admin-secret-key" not in response.text


def test_admin_runtime_model_test_uses_long_benign_prompt(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeCompletions:
        def create(self, **kwargs: Any) -> SimpleNamespace:
            captured["completion_kwargs"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="OK")),
                ]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("app.services.admin_model_config_service.OpenAI", FakeOpenAI)

    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    saved = client.patch(
        "/v1/admin/settings",
        json={
            "model_base_url": "https://admin-models.example.test/v1",
            "model_api_key": "admin-secret-key",
            "model_name": "admin-model",
        },
    )
    assert saved.status_code == 200

    response = client.post("/v1/admin/model-config/test", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["model"] == "admin-model"
    assert payload["provider"] == "openai_compatible"
    assert payload["source"] == "admin"
    assert payload["detail"] == "OK"
    assert payload["upstream"] == {"status_code": 200}
    assert "admin-secret-key" not in response.text
    assert captured["client_kwargs"]["api_key"] == "admin-secret-key"
    assert captured["completion_kwargs"]["model"] == "admin-model"
    user_prompt = captured["completion_kwargs"]["messages"][1]["content"]
    assert len(user_prompt.split()) > 200
    assert "Reply with exactly OK" in user_prompt


def test_admin_runtime_model_test_failure_is_structured_without_key(
    client: TestClient,
) -> None:
    assert client.post("/v1/admin/login", json={"password": "admin-pass"}).status_code == 200
    saved = client.patch(
        "/v1/admin/settings",
        json={
            "model_base_url": "https://admin-models.example.test/v1",
            "model_api_key": "admin-secret-key",
            "model_name": "",
        },
    )
    assert saved.status_code == 200

    response = client.post("/v1/admin/model-config/test", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["provider"] == "openai_compatible"
    assert payload["upstream"]["error_category"] == "model_config"
    assert payload["upstream"]["upstream_code"] == "missing_model_config"
    assert "MODEL_NAME" in payload["upstream"]["missing_env_vars"]
    assert "admin-secret-key" not in response.text


def test_rag_status_is_never_public_in_app_config(client: TestClient) -> None:
    assert client.get("/v1/app-config").json()["rag_status_user_visible"] is False

    client.post("/v1/admin/login", json={"password": "admin-pass"})
    updated = client.patch(
        "/v1/admin/settings",
        json={"rag_status_user_visible": True},
    )
    assert updated.status_code == 200
    assert updated.json()["rag_status_user_visible"] is True
    assert client.get("/v1/app-config").json()["rag_status_user_visible"] is False


def test_access_key_cannot_read_another_key_session(client: TestClient) -> None:
    client.post("/v1/admin/login", json={"password": "admin-pass"})
    first_key = client.post(
        "/v1/admin/access-keys",
        json={"label": "first", "usage_limit": 1},
    ).json()["key"]
    second_key = client.post(
        "/v1/admin/access-keys",
        json={"label": "second", "usage_limit": 1},
    ).json()["key"]

    client.post("/v1/admin/logout")
    assert (
        client.post("/v1/auth/login", json={"password": first_key}).status_code
        == 200
    )
    session_id = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
    ).json()["session_id"]

    client.post("/v1/auth/logout")
    assert (
        client.post("/v1/auth/login", json={"password": second_key}).status_code
        == 200
    )
    response = client.get(f"/v1/sessions/{session_id}/messages")

    assert response.status_code == 403
    assert "not available" in response.json()["detail"]


def test_terminal_session_rejects_more_messages_and_uploads(
    client: TestClient,
    db_session_factory,
) -> None:
    client.post("/v1/admin/login", json={"password": "admin-pass"})
    access_key = client.post(
        "/v1/admin/access-keys",
        json={"label": "terminal", "usage_limit": 1},
    ).json()["key"]

    client.post("/v1/admin/logout")
    client.post("/v1/auth/login", json={"password": access_key})
    session_id = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
    ).json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.phase_state = "completed"
        db.add(record)
        db.commit()

    message_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Can I continue?"},
    )
    assert message_response.status_code == 409
    assert "已结束" in message_response.json()["detail"]

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={"file": ("i20.txt", b"SEVIS ID: N1234567890", "text/plain")},
    )
    assert upload_response.status_code == 409
    assert "已结束" in upload_response.json()["detail"]


def test_admin_login_audit_api_shows_exact_ips_and_counts(client: TestClient) -> None:
    failed_admin = client.post(
        "/v1/admin/login",
        json={"password": "wrong"},
        headers={"CF-Connecting-IP": "203.0.113.10"},
    )
    assert failed_admin.status_code == 401

    login = client.post(
        "/v1/admin/login",
        json={"password": "admin-pass"},
        headers={"CF-Connecting-IP": "203.0.113.10", "CF-IPCountry": "US"},
    )
    assert login.status_code == 200

    response = client.get("/v1/admin/login-audit")
    assert response.status_code == 200
    payload = response.json()

    assert [event["client_ip"] for event in payload["events"][:2]] == [
        "203.0.113.10",
        "203.0.113.10",
    ]
    assert payload["events"][0]["outcome"] == "success"
    assert payload["events"][0]["client_ip_source"] == "cf-connecting-ip"
    assert payload["events"][0]["cf_country"] == "US"
    [stat] = [
        item for item in payload["ip_stats"] if item["client_ip"] == "203.0.113.10"
    ]
    assert stat["total_count"] == 2
    assert stat["success_count"] == 1
    assert stat["failure_count"] == 1


def test_user_session_cannot_read_admin_login_audit(client: TestClient) -> None:
    client.post("/v1/admin/login", json={"password": "admin-pass"})
    created = client.post(
        "/v1/admin/access-keys",
        json={"label": "audit", "usage_limit": 1},
    )
    access_key = created.json()["key"]
    client.post("/v1/admin/logout")

    assert client.post("/v1/auth/login", json={"password": access_key}).status_code == 200
    response = client.get("/v1/admin/login-audit")

    assert response.status_code == 401
