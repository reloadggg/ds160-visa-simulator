from __future__ import annotations

from app.core.settings import settings


def openai_compat_default_headers() -> dict[str, str]:
    user_agent = settings.openai_compat_user_agent.strip()
    if not user_agent:
        return {}
    return {"User-Agent": user_agent}
