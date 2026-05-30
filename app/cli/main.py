from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import create_engine, delete, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging_config import configure_logging
from app.core.settings import settings
from app.db import evidence_models as _evidence_models
from app.db import models as _models
from app.db.base import Base
from app.db.session import DATABASE_URL, SessionLocal, connect_args_for_database_url
from app.evals.graph_replay_eval import GraphReplayEvaluator, GraphReplayFixture
from app.evals.replay_runner import ReplayRunner
from app.workers.parse_worker import (
    drain_parse_jobs,
    parse_worker_poll_interval_seconds,
)

logger = logging.getLogger(__name__)

LIVE_LLM_SMOKE_COMMAND = (
    "RUN_LIVE_LLM_TESTS=1 OPENAI_BASE_URL=... OPENAI_API_KEY=... "
    "uv run pytest "
    "tests/integration/live/test_infrastructure.py "
    "tests/integration/live/test_live_llm_client.py "
    "tests/integration/live/test_live_model_config_api.py "
    "tests/integration/live/test_live_extractor_service.py "
    "tests/integration/live/test_live_scoring_service.py "
    "-q -m live_llm -vv --maxfail=1"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ds160-agent-cli")
    parser.add_argument(
        "--db-url",
        default=DATABASE_URL,
        help="数据库连接串，默认使用应用主库。",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect-turn")
    inspect_parser.add_argument("--session-id", required=True)
    inspect_parser.add_argument("--turn-id", required=True)

    replay_parser = subparsers.add_parser("replay-session")
    replay_parser.add_argument("--session-id", required=True)

    graph_fixture_parser = subparsers.add_parser("eval-graph-fixture")
    graph_fixture_parser.add_argument("--fixture", required=True)
    graph_fixture_parser.add_argument("--max-repeated-template", type=int, default=2)

    graph_corpus_parser = subparsers.add_parser("eval-graph-corpus")
    graph_corpus_parser.add_argument("--fixture-dir", default="fixtures/graph_replay")
    graph_corpus_parser.add_argument("--max-repeated-template", type=int, default=2)

    migration_parser = subparsers.add_parser("migrate-sqlite-to-postgres")
    migration_parser.add_argument("--source-url", required=True)
    migration_parser.add_argument("--target-url", required=True)
    migration_parser.add_argument(
        "--truncate-target",
        action="store_true",
        help="复制前清空目标表；只应在已备份且维护窗口内使用。",
    )
    migration_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计源库/目标库行数，不写入目标库。",
    )
    release_parser = subparsers.add_parser("release-preflight")
    release_parser.add_argument(
        "--target",
        choices=["legacy-freeze"],
        default="legacy-freeze",
        help="要评估的发布门禁目标。",
    )
    release_parser.add_argument(
        "--replay-corpus-passed",
        action="store_true",
        help="标记 graph replay corpus 已在本次发布窗口通过。",
    )
    release_parser.add_argument(
        "--focused-tests-passed",
        action="store_true",
        help="标记 focused non-live runtime tests 已在本次发布窗口通过。",
    )
    release_parser.add_argument(
        "--live-smoke-passed",
        action="store_true",
        help="标记 live LLM smoke 已在本次发布窗口通过。",
    )
    release_parser.add_argument(
        "--docker-smoke-passed",
        action="store_true",
        help="标记 Docker/Postgres smoke 已在本次发布窗口通过。",
    )
    worker_parser = subparsers.add_parser("run-parse-worker")
    worker_parser.add_argument(
        "--once",
        action="store_true",
        help="只 drain 一次当前队列后退出，用于本地检查。",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "eval-graph-fixture":
        result = GraphReplayEvaluator().evaluate_fixture_file(
            args.fixture,
            max_repeated_template=args.max_repeated_template,
        )
        print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.passed else 1
    if args.command == "eval-graph-corpus":
        fixture_dir = Path(args.fixture_dir)
        evaluator = GraphReplayEvaluator()
        results = []
        for path in sorted(fixture_dir.glob("*.json")):
            fixture = GraphReplayFixture.from_file(path)
            result = evaluator.evaluate(
                fixture_id=fixture.fixture_id,
                state=fixture.state,
                events=fixture.events,
                max_repeated_template=args.max_repeated_template,
            )
            expected_passed = bool(fixture.expected.get("should_pass", True))
            expected_failed_checks = set(fixture.expected.get("failed_checks") or [])
            actual_failed_checks = {check.name for check in result.failed_checks}
            matched_expectation = result.passed == expected_passed
            if expected_failed_checks:
                matched_expectation = matched_expectation and expected_failed_checks <= actual_failed_checks
            results.append(
                {
                    **result.model_dump(),
                    "fixture_path": str(path),
                    "expected_passed": expected_passed,
                    "matched_expectation": matched_expectation,
                }
            )
        payload = {
            "fixture_dir": str(fixture_dir),
            "fixture_count": len(results),
            "passed": all(result["matched_expectation"] for result in results),
            "results": results,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload["passed"] else 1
    if args.command == "migrate-sqlite-to-postgres":
        payload = migrate_sqlite_to_database(
            source_url=args.source_url,
            target_url=args.target_url,
            truncate_target=args.truncate_target,
            dry_run=args.dry_run,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload["ok"] else 1
    if args.command == "release-preflight":
        payload = build_release_preflight(
            target=args.target,
            replay_corpus_passed=args.replay_corpus_passed,
            focused_tests_passed=args.focused_tests_passed,
            live_smoke_passed=args.live_smoke_passed,
            docker_smoke_passed=args.docker_smoke_passed,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload["ok"] else 1
    if args.command == "run-parse-worker":
        return run_parse_worker_loop(once=args.once)

    session_factory = _build_session_factory(args.db_url)

    with session_factory() as db:
        runner = ReplayRunner(db)
        if args.command == "inspect-turn":
            payload = runner.inspect_turn(args.session_id, args.turn_id)
        else:
            payload = runner.replay_session(args.session_id)

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run_parse_worker_loop(*, once: bool = False) -> int:
    configure_logging(level=settings.log_level, log_format=settings.log_format)
    stop_requested = False

    def request_stop(signum, frame) -> None:
        nonlocal stop_requested
        stop_requested = True
        logger.info(
            "parse worker shutdown requested",
            extra={"signal": signal.Signals(signum).name},
        )

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    logger.info("parse worker started", extra={"mode": "once" if once else "loop"})
    while not stop_requested:
        try:
            processed_any_job = drain_parse_jobs(SessionLocal)
        except Exception:
            logger.exception("parse worker loop iteration failed")
            if once:
                return 1
            time.sleep(parse_worker_poll_interval_seconds())
            continue
        if once:
            logger.info(
                "parse worker once completed",
                extra={"processed_any_job": processed_any_job},
            )
            return 0
        if processed_any_job:
            continue
        time.sleep(parse_worker_poll_interval_seconds())
    logger.info("parse worker stopped")
    return 0


def _build_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(
        database_url,
        connect_args=connect_args_for_database_url(database_url),
    )
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def migrate_sqlite_to_database(
    *,
    source_url: str,
    target_url: str,
    truncate_target: bool = False,
    dry_run: bool = False,
) -> dict:
    if not source_url.startswith("sqlite"):
        return {
            "ok": False,
            "error": "source_url must be a sqlite SQLAlchemy URL",
        }
    if source_url == target_url:
        return {
            "ok": False,
            "error": "source_url and target_url must be different",
        }

    source_engine = create_engine(
        source_url,
        connect_args=connect_args_for_database_url(source_url),
    )
    target_engine = create_engine(
        target_url,
        connect_args=connect_args_for_database_url(target_url),
    )
    tables = list(Base.metadata.sorted_tables)

    if not dry_run:
        Base.metadata.create_all(bind=target_engine)

    source_counts = _table_counts(source_engine, tables)
    target_before_counts = _table_counts(target_engine, tables)
    copied_counts = {table.name: 0 for table in tables}

    if not dry_run:
        source_table_names = set(inspect(source_engine).get_table_names())
        with source_engine.connect() as source_connection:
            with target_engine.begin() as target_connection:
                if truncate_target:
                    for table in reversed(tables):
                        target_connection.execute(delete(table))
                for table in tables:
                    if table.name not in source_table_names:
                        continue
                    rows = [
                        dict(row)
                        for row in source_connection.execute(
                            select(table),
                        ).mappings()
                    ]
                    if rows:
                        target_connection.execute(table.insert(), rows)
                    copied_counts[table.name] = len(rows)

    target_after_counts = (
        target_before_counts if dry_run else _table_counts(target_engine, tables)
    )
    return {
        "ok": True,
        "dry_run": dry_run,
        "truncate_target": truncate_target,
        "source_url": _redact_database_url(source_url),
        "target_url": _redact_database_url(target_url),
        "source_counts": source_counts,
        "target_before_counts": target_before_counts,
        "target_after_counts": target_after_counts,
        "copied_counts": copied_counts,
    }


def _table_counts(engine, tables) -> dict[str, int]:
    table_names = set(inspect(engine).get_table_names())
    counts = {}
    with engine.connect() as connection:
        for table in tables:
            if table.name not in table_names:
                counts[table.name] = 0
                continue
            counts[table.name] = int(
                connection.execute(select(func.count()).select_from(table)).scalar()
                or 0
            )
    return counts


def _redact_database_url(database_url: str) -> str:
    if "@" not in database_url:
        return database_url
    prefix, suffix = database_url.rsplit("@", 1)
    scheme, _, auth = prefix.partition("://")
    if ":" not in auth:
        return database_url
    user, _, _password = auth.partition(":")
    return f"{scheme}://{user}:***@{suffix}"


def build_release_preflight(
    *,
    target: str = "legacy-freeze",
    replay_corpus_passed: bool = False,
    focused_tests_passed: bool = False,
    live_smoke_passed: bool = False,
    docker_smoke_passed: bool = False,
    dotenv_path: str | Path = ".env",
) -> dict:
    if target != "legacy-freeze":
        return {
            "ok": False,
            "target": target,
            "error": "unsupported release preflight target",
        }

    fixture_dir = Path("fixtures/graph_replay")
    fixture_count = len(list(fixture_dir.glob("*.json"))) if fixture_dir.exists() else 0
    if docker_smoke_passed:
        docker_status = "passed"
        docker_evidence = {"asserted_by_flag": True}
        docker_reason = None
    else:
        docker_status, docker_evidence, docker_reason = _docker_preflight_status()
    live_env_ready, live_env_evidence, live_env_reason = _live_llm_env_status(
        Path(dotenv_path)
    )
    rollback_runbook = Path("docs/architecture/postgres-migration-runbook.md")
    legacy_decision = Path("docs/architecture/legacy-runtime-deprecation-decision.md")
    release_report = Path("docs/implementation/runtime-cleanup-progress-report.md")

    checks = [
        _preflight_check(
            "graph_replay_corpus",
            status="passed"
            if replay_corpus_passed
            else "pending"
            if fixture_count
            else "blocked",
            required=True,
            command=(
                "uv run python -m app.cli.main eval-graph-corpus "
                "--fixture-dir fixtures/graph_replay"
            ),
            evidence={
                "fixture_count": fixture_count,
                **({"asserted_by_flag": True} if replay_corpus_passed else {}),
            },
            reason=None if fixture_count else "fixtures/graph_replay has no JSON fixtures",
        ),
        _preflight_check(
            "focused_non_live_runtime_tests",
            status="passed" if focused_tests_passed else "pending",
            required=True,
            command=(
                "uv run pytest -q tests/integration/test_messages_api.py "
                "tests/integration/test_openai_compat.py "
                "tests/integration/test_sessions_api.py "
                "tests/integration/test_debug_material_bundles_api.py "
                "tests/integration/test_parse_worker.py "
                "tests/unit/test_graph_replay_eval.py tests/unit/test_health.py "
                '-m "not live_llm"'
            ),
            evidence={"asserted_by_flag": True} if focused_tests_passed else None,
        ),
        _preflight_check(
            "live_llm_smoke",
            status="passed"
            if live_smoke_passed
            else "pending"
            if live_env_ready
            else "blocked",
            required=True,
            command=LIVE_LLM_SMOKE_COMMAND,
            evidence={"asserted_by_flag": True} if live_smoke_passed else live_env_evidence,
            reason=None if live_smoke_passed or live_env_ready else live_env_reason,
        ),
        _preflight_check(
            "docker_compose_postgres_smoke",
            status="passed" if docker_smoke_passed else docker_status,
            required=True,
            command=(
                "docker compose config --quiet "
                "&& docker compose up -d postgres ds160-api ds160-web ds160-worker "
                "&& docker compose exec -T ds160-api python -c "
                "\"import urllib.request; "
                "urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).read(); "
                "urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=5).read()\""
            ),
            evidence=docker_evidence,
            reason=None if docker_smoke_passed else docker_reason,
        ),
        _preflight_check(
            "rollback_runbook",
            status="documented" if rollback_runbook.exists() else "blocked",
            required=True,
            path=str(rollback_runbook),
            reason=None if rollback_runbook.exists() else "rollback runbook is missing",
        ),
        _preflight_check(
            "legacy_deprecation_decision",
            status="documented" if legacy_decision.exists() else "blocked",
            required=True,
            path=str(legacy_decision),
            reason=(
                None
                if legacy_decision.exists()
                else "legacy runtime deprecation decision is missing"
            ),
        ),
        _preflight_check(
            "implementation_report",
            status="documented" if release_report.exists() else "blocked",
            required=True,
            path=str(release_report),
            reason=None if release_report.exists() else "implementation report is missing",
        ),
    ]
    blocking = [
        check
        for check in checks
        if check["required"] and check["status"] not in {"documented", "passed"}
    ]
    return {
        "ok": not blocking,
        "target": target,
        "checks": checks,
        "blocking_check_ids": [check["id"] for check in blocking],
        "next_action": (
            "All required release preflight gates are documented or explicitly marked "
            "passed for this release window."
            if not blocking
            else "Run every pending command and re-run this preflight with evidence "
            "recorded before freezing or deleting legacy runtime."
        ),
    }


def _live_llm_env_status(dotenv_path: Path) -> tuple[bool, dict, str | None]:
    required_keys = ("RUN_LIVE_LLM_TESTS", "OPENAI_BASE_URL", "OPENAI_API_KEY")
    process_presence = {key: bool(os.getenv(key)) for key in required_keys}
    dotenv_presence, dotenv_error = _read_dotenv_key_presence(dotenv_path, required_keys)
    effective_presence = {
        key: process_presence[key] or dotenv_presence.get(key, False)
        for key in required_keys
    }
    evidence = {
        "required_keys": list(required_keys),
        "process_env_present": process_presence,
        "dotenv": {
            "path": str(dotenv_path),
            "exists": dotenv_path.exists(),
            "key_present": dotenv_presence,
        },
        "effective_present": effective_presence,
    }
    if dotenv_error:
        evidence["dotenv"]["error"] = dotenv_error
    missing = [key for key, present in effective_presence.items() if not present]
    if missing:
        return False, evidence, "missing required live env keys: " + ", ".join(missing)
    return True, evidence, None


def _read_dotenv_key_presence(
    dotenv_path: Path,
    required_keys: tuple[str, ...],
) -> tuple[dict[str, bool], str | None]:
    presence = {key: False for key in required_keys}
    if not dotenv_path.exists():
        return presence, None
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return presence, str(exc)

    required_key_set = set(required_keys)
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if key not in required_key_set:
            continue
        value = raw_value.strip().strip("'\"")
        presence[key] = bool(value)
    return presence, None


def _docker_preflight_status() -> tuple[str, dict, str | None]:
    candidates = _docker_cli_candidates()
    evidence = {"docker_candidates": candidates, "docker_path": None}
    if not candidates:
        return "blocked", evidence, "docker executable was not found"

    checks: dict[str, dict] = {}
    evidence["checks"] = checks

    selected_docker_path = None
    selected_version_check = None
    candidate_checks = []
    for candidate in candidates:
        version_check = _run_preflight_command([candidate, "--version"], timeout=5)
        candidate_checks.append(
            {
                "path": candidate,
                "docker_version": version_check,
            }
        )
        if version_check["ok"]:
            selected_docker_path = candidate
            selected_version_check = version_check
            break
    evidence["candidate_checks"] = candidate_checks

    if not selected_docker_path or not selected_version_check:
        last_check = candidate_checks[-1]["docker_version"]
        reason = last_check.get("output") or "docker --version failed"
        return "blocked", evidence, reason

    evidence["docker_path"] = selected_docker_path
    checks["docker_version"] = selected_version_check

    compose_check = _run_preflight_command(
        [selected_docker_path, "compose", "config", "--quiet"],
        timeout=10,
    )
    checks["compose_config"] = compose_check
    if not compose_check["ok"]:
        reason = compose_check.get("output") or "docker compose config failed"
        return "blocked", evidence, f"docker compose config failed: {reason}"

    daemon_check = _run_preflight_command(
        [selected_docker_path, "info", "--format", "{{.ServerVersion}}"],
        timeout=5,
    )
    checks["docker_daemon"] = daemon_check
    if not daemon_check["ok"]:
        reason = daemon_check.get("output") or "docker info failed"
        return "blocked", evidence, f"Docker daemon is not reachable: {reason}"

    return "pending", evidence, None


def _docker_cli_candidates() -> list[str]:
    candidates = []
    for executable in ("docker", "docker.exe"):
        path = shutil.which(executable)
        if path and path not in candidates:
            candidates.append(path)
    return candidates


def _run_preflight_command(args: list[str], *, timeout: int) -> dict:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "timed_out": True,
            "timeout_seconds": timeout,
            "output": _safe_output_summary(
                f"{_command_label(args)} timed out after {timeout}s"
            ),
        }
    except OSError as exc:
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "output": _safe_output_summary(str(exc)),
        }

    output = _safe_output_summary(result.stdout or result.stderr or "")
    payload = {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
    }
    if output:
        payload["output"] = output
    return payload


def _command_label(args: list[str]) -> str:
    executable = Path(args[0]).name
    return " ".join([executable, *args[1:]])


def _safe_output_summary(output: str, *, limit: int = 240) -> str:
    import re

    summary = next((line.strip() for line in output.splitlines() if line.strip()), "")
    summary = " ".join(summary.split())
    if not summary:
        return ""
    summary = re.sub(
        r"([A-Za-z][A-Za-z0-9+.-]*://[^:\s/@]+):[^@\s]+@",
        r"\1:***@",
        summary,
    )
    summary = re.sub(
        r"(?i)(api[_-]?key|authorization|token|password|secret)=\S+",
        r"\1=***",
        summary,
    )
    if len(summary) > limit:
        return summary[: limit - 3] + "..."
    return summary


def _preflight_check(
    check_id: str,
    *,
    status: str,
    required: bool,
    command: str | None = None,
    path: str | None = None,
    evidence: dict | None = None,
    reason: str | None = None,
) -> dict:
    payload = {
        "id": check_id,
        "status": status,
        "required": required,
    }
    if command is not None:
        payload["command"] = command
    if path is not None:
        payload["path"] = path
    if evidence:
        payload["evidence"] = evidence
    if reason:
        payload["reason"] = reason
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
