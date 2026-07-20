"""Rate limits and in-flight lock for practice/debug material generation.

Session lock + session rate history live in ``SessionRecord.interviewer_state_json``
under ``material_generation`` so they survive worker restarts and work on SQLite
without migrations. Stale ``running`` status auto-expires after
``settings.material_generation_lock_ttl_seconds``.

Access-key sliding windows are process-local; multi-worker deployments need a
shared store (e.g. Redis) to enforce key limits across processes.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.repositories.session_repo import SessionRepository


class MaterialGenerationInProgressError(Exception):
    """Raised when a session already has material generation in flight."""

    def __init__(
        self,
        detail: str = "material generation already in progress for this session",
    ) -> None:
        super().__init__(detail)
        self.detail = detail


class MaterialGenerationRateLimitError(Exception):
    """Raised when session or access-key generation rate is exceeded."""

    def __init__(self, detail: str = "material generation rate limit exceeded") -> None:
        super().__init__(detail)
        self.detail = detail


_KEY_STARTS: dict[str, deque[float]] = defaultdict(deque)
_KEY_LOCK = Lock()

_STATE_KEY = "material_generation"


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _prune_iso_list(
    values: list[Any],
    *,
    now: datetime,
    window_seconds: int,
) -> list[str]:
    cutoff = now - timedelta(seconds=max(window_seconds, 1))
    kept: list[str] = []
    for raw in values:
        parsed = _parse_iso(raw)
        if parsed is None:
            continue
        if parsed >= cutoff:
            kept.append(parsed.isoformat())
    return kept


class MaterialGenerationGuard:
    """Acquire/release per-session generation lock and enforce rate limits."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.sessions = SessionRepository(db)

    def acquire(
        self,
        session_id: str,
        *,
        access_key_id: str | None = None,
        bundle_id: str | None = None,
    ) -> None:
        """Lock the session for generation and record rate-limit starts.

        Commits the session row so concurrent requests (other connections)
        observe ``running`` even when SQLite ``FOR UPDATE`` is a no-op.
        """
        self._assert_access_key_rate(access_key_id)

        record = self.sessions.get_for_update(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")

        now = _utc_now()
        state = dict(record.interviewer_state_json or {})
        mg = dict(state.get(_STATE_KEY) or {})

        if self._is_running(mg, now=now):
            raise MaterialGenerationInProgressError()

        window = settings.material_generation_session_window_seconds
        limit = settings.material_generation_session_limit
        recent = _prune_iso_list(
            list(mg.get("recent_starts") or []),
            now=now,
            window_seconds=window,
        )
        if len(recent) >= limit:
            raise MaterialGenerationRateLimitError(
                detail=(
                    "session material generation rate limit exceeded "
                    f"({limit} per {window}s)"
                )
            )

        recent.append(now.isoformat())
        keep = max(limit * 2, 20)
        mg["status"] = "running"
        mg["started_at"] = now.isoformat()
        mg["bundle_id"] = bundle_id
        mg["recent_starts"] = recent[-keep:]
        state[_STATE_KEY] = mg
        record.interviewer_state_json = state
        self.db.add(record)
        self.db.commit()

        self._record_access_key_start(access_key_id)

    def set_bundle_id(self, session_id: str, bundle_id: str) -> None:
        """Attach the active bundle id to the running generation marker."""
        record = self.sessions.get(session_id)
        if record is None:
            return
        state = dict(record.interviewer_state_json or {})
        mg = dict(state.get(_STATE_KEY) or {})
        if mg.get("status") != "running":
            return
        mg["bundle_id"] = bundle_id
        state[_STATE_KEY] = mg
        record.interviewer_state_json = state
        self.db.add(record)
        self.db.commit()

    def complete(self, session_id: str) -> None:
        """Mark generation completed and clear the in-flight flag."""
        self._set_terminal(session_id, status="completed")

    def fail(self, session_id: str) -> None:
        """Clear the in-flight flag after a failed generation."""
        self._set_terminal(session_id, status="failed")

    def get_status(self, session_id: str) -> dict[str, Any]:
        record = self.sessions.get(session_id)
        if record is None:
            return {}
        state = dict(record.interviewer_state_json or {})
        return dict(state.get(_STATE_KEY) or {})

    def _set_terminal(self, session_id: str, *, status: str) -> None:
        record = self.sessions.get(session_id)
        if record is None:
            return
        state = dict(record.interviewer_state_json or {})
        mg = dict(state.get(_STATE_KEY) or {})
        mg["status"] = status
        mg["finished_at"] = _utc_now().isoformat()
        # Keep bundle_id / recent_starts for audit + rate history; clear running.
        state[_STATE_KEY] = mg
        record.interviewer_state_json = state
        self.db.add(record)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()

    @staticmethod
    def _is_running(mg: dict[str, Any], *, now: datetime) -> bool:
        if mg.get("status") != "running":
            return False
        started = _parse_iso(mg.get("started_at"))
        if started is None:
            return True
        ttl = max(int(settings.material_generation_lock_ttl_seconds), 1)
        return (now - started) < timedelta(seconds=ttl)

    def _assert_access_key_rate(self, access_key_id: str | None) -> None:
        if not access_key_id:
            return
        limit = settings.material_generation_access_key_limit
        window = settings.material_generation_access_key_window_seconds
        now_ts = _utc_now().timestamp()
        cutoff = now_ts - max(window, 1)
        with _KEY_LOCK:
            bucket = _KEY_STARTS[access_key_id]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                raise MaterialGenerationRateLimitError(
                    detail=(
                        "access key material generation rate limit exceeded "
                        f"({limit} per {window}s)"
                    )
                )

    def _record_access_key_start(self, access_key_id: str | None) -> None:
        if not access_key_id:
            return
        now_ts = _utc_now().timestamp()
        window = settings.material_generation_access_key_window_seconds
        cutoff = now_ts - max(window, 1)
        with _KEY_LOCK:
            bucket = _KEY_STARTS[access_key_id]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            bucket.append(now_ts)


def reset_access_key_rate_limits_for_tests() -> None:
    """Clear process-local access-key rate buckets (tests only)."""
    with _KEY_LOCK:
        _KEY_STARTS.clear()
