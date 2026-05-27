from __future__ import annotations

import os


APP_VERSION = "0.1.1"
APP_GIT_SHA = os.getenv("APP_GIT_SHA") or os.getenv("GIT_SHA")
APP_BUILD_TIME = os.getenv("APP_BUILD_TIME") or os.getenv("BUILD_TIME")


def backend_version_payload() -> dict[str, str | None]:
    return {
        "version": APP_VERSION,
        "git_sha": APP_GIT_SHA,
        "build_time": APP_BUILD_TIME,
    }
