import json
import subprocess
from types import SimpleNamespace

from app.cli.main import build_release_preflight, main
from app.db.base import Base
from app.db.models import DocumentRecord, SessionRecord

from sqlalchemy import create_engine, func, select


class _FakeRunner:
    def inspect_turn(self, session_id: str, turn_id: str) -> dict:
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "role": "assistant",
        }

    def replay_session(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "turn_count": 2,
            "turns": [],
        }


class _FakeSession:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


def test_cli_main_inspect_turn_outputs_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.main.ReplayRunner", lambda db: _FakeRunner())
    monkeypatch.setattr("app.cli.main._build_session_factory", lambda url: _FakeSession)

    exit_code = main(
        [
            "--db-url",
            "sqlite:///fake.db",
            "inspect-turn",
            "--session-id",
            "sess-1",
            "--turn-id",
            "turn-2",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert '"session_id": "sess-1"' in captured
    assert '"turn_id": "turn-2"' in captured


def test_cli_main_replay_session_outputs_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.main.ReplayRunner", lambda db: _FakeRunner())
    monkeypatch.setattr("app.cli.main._build_session_factory", lambda url: _FakeSession)

    exit_code = main(
        [
            "replay-session",
            "--session-id",
            "sess-2",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert '"session_id": "sess-2"' in captured
    assert '"turn_count": 2' in captured


def test_cli_main_eval_graph_fixture_outputs_result(capsys) -> None:
    exit_code = main(
        [
            "eval-graph-fixture",
            "--fixture",
            "fixtures/graph_replay/school_mismatch_where.json",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert '"fixture_id": "school-mismatch-where"' in captured
    assert '"passed": true' in captured


def test_cli_main_eval_graph_corpus_matches_expected_failures(capsys) -> None:
    exit_code = main(
        [
            "eval-graph-corpus",
            "--fixture-dir",
            "fixtures/graph_replay",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert '"fixture_count": 13' in captured
    assert '"fixture_id": "no-material-chat-starts"' in captured
    assert '"fixture_id": "purpose-answer-advances"' in captured
    assert '"fixture_id": "complete-interview-success-path"' in captured
    assert '"fixture_id": "refuse-fabrication-request"' in captured
    assert '"fixture_id": "repeated-template-failure"' in captured
    assert '"expected_passed": false' in captured
    assert '"matched_expectation": true' in captured


def test_cli_main_run_parse_worker_once_drains_queue(monkeypatch) -> None:
    calls: list[object] = []

    def fake_drain(session_factory) -> bool:
        calls.append(session_factory)
        return False

    monkeypatch.setattr("app.cli.main.drain_parse_jobs", fake_drain)

    exit_code = main(["run-parse-worker", "--once"])

    assert exit_code == 0
    assert len(calls) == 1


def test_cli_main_migrate_sqlite_to_postgres_copies_tables(tmp_path, capsys) -> None:
    source_url = f"sqlite:///{tmp_path / 'source.sqlite3'}"
    target_url = f"sqlite:///{tmp_path / 'target.sqlite3'}"
    source_engine = create_engine(
        source_url,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=source_engine)

    with source_engine.begin() as connection:
        connection.execute(
            SessionRecord.__table__.insert(),
            {
                "session_id": "sess-migrate",
                "phase_state": "interview",
                "declared_family": "F1",
                "current_governor_decision": "continue",
                "profile_json": {"given_name": "Ada"},
                "route_candidates_json": [],
                "gate_status_json": {},
                "runtime_trace_json": [],
                "score_history_json": [],
                "governor_history_json": [],
                "interviewer_state_json": {"selected_public_runtime": "native_interviewer"},
                "current_focus_json": {},
            },
        )
        connection.execute(
            DocumentRecord.__table__.insert(),
            {
                "document_id": "doc-migrate",
                "session_id": "sess-migrate",
                "filename": "i20.txt",
                "status": "parsed",
                "artifact_json": {"document_type": "i20"},
                "raw_bytes": b"i20",
                "raw_text": "I-20",
            },
        )

    exit_code = main(
        [
            "migrate-sqlite-to-postgres",
            "--source-url",
            source_url,
            "--target-url",
            target_url,
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert '"sessions": 1' in captured
    assert '"documents": 1' in captured
    assert '"ok": true' in captured

    target_engine = create_engine(
        target_url,
        connect_args={"check_same_thread": False},
    )
    with target_engine.connect() as connection:
        session_count = connection.execute(
            select(func.count()).select_from(SessionRecord),
        ).scalar_one()
        document_count = connection.execute(
            select(func.count()).select_from(DocumentRecord),
        ).scalar_one()

    assert session_count == 1
    assert document_count == 1


def test_release_preflight_reports_required_legacy_freeze_gates(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("app.cli.main.shutil.which", lambda name: None)
    monkeypatch.delenv("RUN_LIVE_LLM_TESTS", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    payload = build_release_preflight(
        target="legacy-freeze",
        dotenv_path=tmp_path / "missing.env",
    )

    assert payload["ok"] is False
    assert payload["target"] == "legacy-freeze"
    assert "graph_replay_corpus" in {
        check["id"] for check in payload["checks"]
    }
    assert "legacy_deprecation_decision" in {
        check["id"] for check in payload["checks"]
    }
    assert "live_llm_smoke" in payload["blocking_check_ids"]
    assert "docker_compose_postgres_smoke" in payload["blocking_check_ids"]


def test_release_preflight_blocks_docker_when_command_is_unusable(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("app.cli.main.shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        "app.cli.main.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="Docker WSL integration is disabled\nmore details",
            stderr="",
        ),
    )

    payload = build_release_preflight(
        target="legacy-freeze",
        dotenv_path=tmp_path / "missing.env",
    )
    docker_check = next(
        check
        for check in payload["checks"]
        if check["id"] == "docker_compose_postgres_smoke"
    )

    assert docker_check["status"] == "blocked"
    assert docker_check["reason"] == "Docker WSL integration is disabled"


def test_release_preflight_blocks_when_compose_config_is_invalid(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("app.cli.main.shutil.which", lambda name: "/usr/bin/docker")

    def fake_run(args, **kwargs):
        if args[1:] == ["--version"]:
            return SimpleNamespace(
                returncode=0,
                stdout="Docker version 29.2.1",
                stderr="",
            )
        if args[1:] == ["compose", "config", "--quiet"]:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="invalid compose OPENAI_API_KEY=super-secret-key",
            )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr("app.cli.main.subprocess.run", fake_run)

    payload = build_release_preflight(
        target="legacy-freeze",
        dotenv_path=tmp_path / "missing.env",
    )
    docker_check = next(
        check
        for check in payload["checks"]
        if check["id"] == "docker_compose_postgres_smoke"
    )

    assert docker_check["status"] == "blocked"
    assert docker_check["reason"].startswith("docker compose config failed")
    assert "OPENAI_API_KEY=***" in json.dumps(docker_check)
    assert "super-secret-key" not in json.dumps(payload)


def test_release_preflight_blocks_when_docker_daemon_is_unreachable(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("app.cli.main.shutil.which", lambda name: "/usr/bin/docker")

    def fake_run(args, **kwargs):
        if args[1:] == ["--version"]:
            return SimpleNamespace(
                returncode=0,
                stdout="Docker version 29.2.1",
                stderr="",
            )
        if args[1:] == ["compose", "config", "--quiet"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[1:] == ["info", "--format", "{{.ServerVersion}}"]:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr("app.cli.main.subprocess.run", fake_run)

    payload = build_release_preflight(
        target="legacy-freeze",
        dotenv_path=tmp_path / "missing.env",
    )
    docker_check = next(
        check
        for check in payload["checks"]
        if check["id"] == "docker_compose_postgres_smoke"
    )

    assert docker_check["status"] == "blocked"
    assert docker_check["reason"].startswith("Docker daemon is not reachable")
    assert docker_check["evidence"]["checks"]["compose_config"]["ok"] is True
    assert docker_check["evidence"]["checks"]["docker_daemon"]["timed_out"] is True


def test_release_preflight_uses_docker_exe_when_wsl_shim_is_unusable(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_which(name: str):
        if name == "docker":
            return "/usr/bin/docker"
        if name == "docker.exe":
            return "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"
        return None

    monkeypatch.setattr("app.cli.main.shutil.which", fake_which)

    def fake_run(args, **kwargs):
        if args[0] == "/usr/bin/docker":
            return SimpleNamespace(
                returncode=1,
                stdout="The command 'docker' could not be found in this WSL 2 distro.",
                stderr="",
            )
        if args[0].endswith("docker.exe") and args[1:] == ["--version"]:
            return SimpleNamespace(
                returncode=0,
                stdout="Docker version 29.2.1",
                stderr="",
            )
        if args[0].endswith("docker.exe") and args[1:] == [
            "compose",
            "config",
            "--quiet",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0].endswith("docker.exe") and args[1:] == [
            "info",
            "--format",
            "{{.ServerVersion}}",
        ]:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr("app.cli.main.subprocess.run", fake_run)

    payload = build_release_preflight(
        target="legacy-freeze",
        dotenv_path=tmp_path / "missing.env",
    )
    docker_check = next(
        check
        for check in payload["checks"]
        if check["id"] == "docker_compose_postgres_smoke"
    )

    assert docker_check["status"] == "blocked"
    assert docker_check["evidence"]["docker_path"].endswith("docker.exe")
    first_candidate_version = docker_check["evidence"]["candidate_checks"][0][
        "docker_version"
    ]
    assert first_candidate_version["ok"] is False
    assert docker_check["evidence"]["checks"]["compose_config"]["ok"] is True
    assert docker_check["evidence"]["checks"]["docker_daemon"]["timed_out"] is True


def test_release_preflight_keeps_docker_smoke_pending_when_docker_is_ready(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("app.cli.main.shutil.which", lambda name: "/usr/bin/docker")

    def fake_run(args, **kwargs):
        if args[1:] == ["--version"]:
            return SimpleNamespace(
                returncode=0,
                stdout="Docker version 29.2.1",
                stderr="",
            )
        if args[1:] == ["compose", "config", "--quiet"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[1:] == ["info", "--format", "{{.ServerVersion}}"]:
            return SimpleNamespace(returncode=0, stdout="29.2.1", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr("app.cli.main.subprocess.run", fake_run)

    payload = build_release_preflight(
        target="legacy-freeze",
        dotenv_path=tmp_path / "missing.env",
    )
    docker_check = next(
        check
        for check in payload["checks"]
        if check["id"] == "docker_compose_postgres_smoke"
    )

    assert docker_check["status"] == "pending"
    assert "reason" not in docker_check
    assert "docker compose up -d postgres ds160-api ds160-web ds160-worker" in (
        docker_check["command"]
    )
    assert "docker compose exec -T ds160-api" in docker_check["command"]
    assert "docker compose up -d postgres ds160-agent2" not in docker_check["command"]
    assert docker_check["evidence"]["checks"]["docker_version"]["ok"] is True
    assert docker_check["evidence"]["checks"]["compose_config"]["ok"] is True
    assert docker_check["evidence"]["checks"]["docker_daemon"]["ok"] is True


def test_cli_main_release_preflight_outputs_blocking_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.main.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "app.cli.main._read_dotenv_key_presence",
        lambda dotenv_path, required_keys: (
            {key: False for key in required_keys},
            None,
        ),
    )
    monkeypatch.delenv("RUN_LIVE_LLM_TESTS", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = main(["release-preflight"])

    captured = capsys.readouterr().out
    assert exit_code == 1
    assert '"target": "legacy-freeze"' in captured
    assert '"id": "docker_compose_postgres_smoke"' in captured
    assert '"blocking_check_ids"' in captured


def test_release_preflight_passes_when_all_evidence_flags_are_set(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("app.cli.main.shutil.which", lambda name: None)
    monkeypatch.delenv("RUN_LIVE_LLM_TESTS", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    payload = build_release_preflight(
        target="legacy-freeze",
        replay_corpus_passed=True,
        focused_tests_passed=True,
        live_smoke_passed=True,
        docker_smoke_passed=True,
        dotenv_path=tmp_path / "missing.env",
    )

    assert payload["ok"] is True
    assert payload["blocking_check_ids"] == []
    assert {check["status"] for check in payload["checks"]} <= {
        "documented",
        "passed",
    }


def test_release_preflight_requires_legacy_deprecation_decision(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("app.cli.main.shutil.which", lambda name: None)
    monkeypatch.setattr("app.cli.main.Path.exists", lambda self: False)
    monkeypatch.delenv("RUN_LIVE_LLM_TESTS", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    payload = build_release_preflight(
        target="legacy-freeze",
        replay_corpus_passed=True,
        focused_tests_passed=True,
        live_smoke_passed=True,
        docker_smoke_passed=True,
        dotenv_path=tmp_path / "missing.env",
    )
    decision_check = next(
        check
        for check in payload["checks"]
        if check["id"] == "legacy_deprecation_decision"
    )

    assert payload["ok"] is False
    assert decision_check["status"] == "blocked"
    assert "legacy_deprecation_decision" in payload["blocking_check_ids"]


def test_release_preflight_reads_live_env_presence_from_dotenv(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("app.cli.main.shutil.which", lambda name: None)
    monkeypatch.delenv("RUN_LIVE_LLM_TESTS", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            [
                "RUN_LIVE_LLM_TESTS=1",
                "OPENAI_BASE_URL=https://example.test/v1",
                "OPENAI_API_KEY=super-secret-key",
            ]
        ),
        encoding="utf-8",
    )

    payload = build_release_preflight(
        target="legacy-freeze",
        dotenv_path=dotenv_path,
    )
    live_check = next(
        check for check in payload["checks"] if check["id"] == "live_llm_smoke"
    )

    assert live_check["status"] == "pending"
    assert live_check.get("reason") is None
    assert live_check["evidence"]["dotenv"]["exists"] is True
    assert live_check["evidence"]["effective_present"] == {
        "RUN_LIVE_LLM_TESTS": True,
        "OPENAI_BASE_URL": True,
        "OPENAI_API_KEY": True,
    }
    assert "super-secret-key" not in json.dumps(payload)
