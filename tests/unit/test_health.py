import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.core.app_version import APP_VERSION
from app.core.health import build_health_payload, database_health
from app.core.settings import settings
from app.main import app


def test_livez_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/livez")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_returns_layered_status(monkeypatch) -> None:
    monkeypatch.setenv("PARSE_WORKER_INLINE", "0")
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["app"]["version"] == APP_VERSION
    assert payload["checks"]["database"]["status"] == "ok"
    assert payload["checks"]["database"]["dialect"]
    assert payload["checks"]["llm"]["status"] in {"configured", "not_configured"}
    assert payload["checks"]["worker"] == {
        "status": "disabled",
        "inline_enabled": False,
    }


def test_healthz_returns_503_when_critical_check_degraded(monkeypatch) -> None:
    def fake_health_payload(**kwargs):
        return {
            "status": "degraded",
            "checks": {
                "app": {"status": "ok"},
                "database": {"status": "error", "dialect": "sqlite"},
                "llm": {"status": "not_configured"},
                "worker": {"status": "disabled", "inline_enabled": False},
            },
        }

    monkeypatch.setattr("app.main.build_health_payload", fake_health_payload)
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_health_payload_degrades_when_inline_worker_not_started(monkeypatch) -> None:
    class AppState:
        parse_worker_task = None

    class HealthApp:
        state = AppState()

    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setenv("PARSE_WORKER_INLINE", "1")
    try:
        payload = build_health_payload(
            app=HealthApp(),
            engine=engine,
            app_settings=settings,
        )
    finally:
        engine.dispose()

    assert payload["status"] == "degraded"
    assert payload["checks"]["worker"] == {
        "status": "not_started",
        "inline_enabled": True,
    }


def test_database_health_reports_error_without_leaking_url() -> None:
    engine = create_engine("sqlite:////tmp/ds160-missing-dir/health.sqlite3")

    payload = database_health(engine)

    assert payload["status"] == "error"
    assert payload["dialect"] == "sqlite"
    assert "error_type" in payload
    assert "url" not in payload


def test_version_returns_build_metadata() -> None:
    client = TestClient(app)

    response = client.get("/version")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"]
    assert "git_sha" in payload
    assert "build_time" in payload


def test_backend_version_matches_frontend_package_version() -> None:
    package_json = json.loads(Path("web/package.json").read_text())

    assert APP_VERSION == package_json["version"]
