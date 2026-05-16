from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class UserModelConfig:
    base_url: str
    api_key: str
    model: str


_runtime_config: ContextVar[UserModelConfig | None] = ContextVar(
    "user_model_runtime_config",
    default=None,
)


def normalize_openai_base_url(raw_base_url: str) -> str:
    parsed = urlsplit(raw_base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL 必须是有效的 http 或 https 地址。")
    if parsed.username or parsed.password:
        raise ValueError("Base URL 不能包含用户名或密码。")
    if parsed.query or parsed.fragment:
        raise ValueError("Base URL 不能包含 query 或 fragment。")

    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def current_user_model_config() -> UserModelConfig | None:
    return _runtime_config.get()


@contextmanager
def user_model_runtime(config: UserModelConfig | None) -> Iterator[None]:
    token = _runtime_config.set(config)
    try:
        yield
    finally:
        _runtime_config.reset(token)
