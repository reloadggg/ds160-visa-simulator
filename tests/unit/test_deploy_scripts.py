from pathlib import Path


def test_production_cutover_script_has_explicit_safety_gates() -> None:
    script = Path("scripts/production-split-postgres-cutover.sh").read_text()

    assert "CONFIRM_PRODUCTION_CUTOVER" in script
    assert "I_UNDERSTAND_PRODUCTION_CUTOVER" in script
    assert "RUN_WRITE_MIGRATION=1" in script
    assert "Refusing to run a partial production cutover." in script
    assert "TRUNCATE_TARGET=1" in script
    assert "SKIP_DOCKER_BUILD=1" in script
    assert "ALLOW_DIRTY_WORKTREE=1" in script
    assert "MIGRATION_TIMEOUT_SECONDS=600" in script
    assert "ROLLBACK_ON_FAILURE=1" in script
    assert "set -x" not in script


def test_production_cutover_script_runs_dry_run_before_write() -> None:
    script = Path("scripts/production-split-postgres-cutover.sh").read_text()

    postgres_position = script.index('start_postgres_service "$backup_dir"')
    dry_run_position = script.index('run_migration "$backup_dir" "dry-run"')
    write_position = script.index('run_migration "$backup_dir" "write"')
    split_call_position = script.index("start_split_services", write_position)

    assert postgres_position < dry_run_position
    assert dry_run_position < write_position
    assert write_position < split_call_position
    assert "docker compose up -d --build postgres" in script
    assert "docker compose up -d --build ds160-api ds160-web ds160-worker" in script
    assert "docker compose up -d postgres" in script
    assert "docker compose up -d ds160-api ds160-web ds160-worker" in script
    assert "docker compose run" in script
    assert 'timeout "$MIGRATION_TIMEOUT_SECONDS" docker compose run' in script
    assert 'sqlite:////backup/app.sqlite3.backup' in script
    assert "docker cp" not in script
    assert "docker compose up -d nginx" in script


def test_production_cutover_script_captures_release_evidence_without_printing_env() -> None:
    script = Path("scripts/production-split-postgres-cutover.sh").read_text()

    assert "env-presence.txt" in script
    assert "env.backup" in script
    assert 'chmod 600 "$backup_dir/env.backup"' in script
    assert "cat .env" not in script
    assert "docker compose config" in script


def test_production_cutover_script_attempts_combined_rollback_on_failure() -> None:
    script = Path("scripts/production-split-postgres-cutover.sh").read_text()

    assert "trap attempt_rollback_on_failure ERR" in script
    assert "docker compose stop ds160-worker ds160-api ds160-web postgres" in script
    assert "docker start ds160-agent2" in script
    assert "compose-after-rollback-attempt.txt" in script


def test_production_recover_combined_script_restarts_existing_container_without_build() -> None:
    script = Path("scripts/production-recover-combined.sh").read_text()

    assert "docker start ds160-agent2" in script
    assert "docker compose stop ds160-worker ds160-api ds160-web postgres" in script
    assert "--build" not in script
    assert "cat .env" not in script
    assert "combined-recovery" in script
    assert "https://127.0.0.1:18000/healthz" in script
