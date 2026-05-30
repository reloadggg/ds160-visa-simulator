from pathlib import Path


def test_production_cutover_script_has_explicit_safety_gates() -> None:
    script = Path("scripts/production-split-postgres-cutover.sh").read_text()

    assert "CONFIRM_PRODUCTION_CUTOVER" in script
    assert "I_UNDERSTAND_PRODUCTION_CUTOVER" in script
    assert "RUN_WRITE_MIGRATION=1" in script
    assert "Refusing to run a partial production cutover." in script
    assert "TRUNCATE_TARGET=1" in script
    assert "ALLOW_DIRTY_WORKTREE=1" in script
    assert "set -x" not in script


def test_production_cutover_script_runs_dry_run_before_write() -> None:
    script = Path("scripts/production-split-postgres-cutover.sh").read_text()

    dry_run_position = script.index('run_migration "$backup_dir" "dry-run"')
    write_position = script.index('run_migration "$backup_dir" "write"')

    assert dry_run_position < write_position
    assert "docker compose up -d --build postgres ds160-api ds160-web ds160-worker" in script
    assert "docker compose exec -T ds160-api" in script
    assert "docker compose up -d nginx" in script


def test_production_cutover_script_captures_release_evidence_without_printing_env() -> None:
    script = Path("scripts/production-split-postgres-cutover.sh").read_text()

    assert "env-presence.txt" in script
    assert "env.backup" in script
    assert 'chmod 600 "$backup_dir/env.backup"' in script
    assert "cat .env" not in script
    assert "docker compose config" in script
