"""Unit tests for trusted-proxy IP resolution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import settings as settings_module
from app.core.simple_auth import request_metadata


class _FakeRequest:
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        client_host: str | None = "10.0.0.5",
    ) -> None:
        self.headers = headers or {}
        self.client = SimpleNamespace(host=client_host) if client_host else None


def test_prefers_cf_connecting_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module.settings, "trust_x_forwarded_for", False)
    metadata = request_metadata(
        _FakeRequest(
            headers={
                "cf-connecting-ip": "203.0.113.9",
                "x-forwarded-for": "1.2.3.4, 10.0.0.1",
            }
        )
    )
    assert metadata.client_ip == "203.0.113.9"
    assert metadata.client_ip_source == "cf-connecting-ip"


def test_ignores_xff_when_trust_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module.settings, "trust_x_forwarded_for", False)
    metadata = request_metadata(
        _FakeRequest(
            headers={
                "x-forwarded-for": "1.2.3.4, 10.0.0.1",
                "x-real-ip": "9.9.9.9",
            }
        )
    )
    assert metadata.client_ip == "10.0.0.5"
    assert metadata.client_ip_source == "direct"


def test_uses_rightmost_xff_when_trust_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module.settings, "trust_x_forwarded_for", True)
    metadata = request_metadata(
        _FakeRequest(
            headers={"x-forwarded-for": "1.2.3.4, 198.51.100.20, 10.0.0.1"}
        )
    )
    assert metadata.client_ip == "10.0.0.1"
    assert metadata.client_ip_source == "x-forwarded-for"


def test_falls_back_to_x_real_ip_when_trust_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "trust_x_forwarded_for", True)
    metadata = request_metadata(
        _FakeRequest(headers={"x-real-ip": "198.51.100.77"})
    )
    assert metadata.client_ip == "198.51.100.77"
    assert metadata.client_ip_source == "x-real-ip"
