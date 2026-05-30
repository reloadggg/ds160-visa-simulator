from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.app_version import backend_version_payload
from app.core.settings import Settings
from app.workers.parse_worker import parse_worker_inline_enabled


def database_health(engine: Engine) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok",
        "dialect": engine.dialect.name,
    }
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        payload["status"] = "error"
        payload["error_type"] = exc.__class__.__name__
    return payload


def llm_health(app_settings: Settings) -> dict[str, Any]:
    return {
        "status": (
            "configured"
            if app_settings.openai_api_key and app_settings.openai_base_url
            else "not_configured"
        ),
        "provider": app_settings.llm_provider,
        "base_url_configured": bool(app_settings.openai_base_url),
        "api_key_configured": bool(app_settings.openai_api_key),
        "user_model_config_enabled": app_settings.allow_user_model_config,
    }


def worker_health(app: FastAPI) -> dict[str, Any]:
    inline_enabled = parse_worker_inline_enabled()
    task = getattr(app.state, "parse_worker_task", None)
    if not inline_enabled:
        status = "disabled"
    elif task is None:
        status = "not_started"
    elif task.done():
        status = "stopped"
    else:
        status = "running"
    return {
        "status": status,
        "inline_enabled": inline_enabled,
    }


def build_health_payload(
    *,
    app: FastAPI,
    engine: Engine,
    app_settings: Settings,
) -> dict[str, Any]:
    checks = {
        "app": {
            "status": "ok",
            **backend_version_payload(),
        },
        "database": database_health(engine),
        "llm": llm_health(app_settings),
        "worker": worker_health(app),
    }
    critical_ok = checks["database"]["status"] == "ok"
    worker_status = checks["worker"]["status"]
    if worker_status in {"not_started", "stopped"}:
        critical_ok = False
    return {
        "status": "ok" if critical_ok else "degraded",
        "checks": checks,
    }
