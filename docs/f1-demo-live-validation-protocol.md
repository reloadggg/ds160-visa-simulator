# F-1 demo live validation protocol

This protocol is the acceptance checklist for the F-1 pass-oriented demo material
package. It is intentionally live-style: a pass must come from real API calls and
uploaded PDFs, not from direct debug-bundle persistence or hand-written success
claims.

## Current executable contract

Run from `ds160-visa-simulator/`.

```bash
# 1) Render the stable six-PDF F-1 package and manifest.
uv run python scripts/f1_demo_material_package.py render \
  --out artifacts/f1_demo_materials_smoke

# 2) Start the backend with the repository .env. Runtime debug is required for
#    the full acceptance snapshot; material-package archive list/import is gated
#    by debug material support.
ALLOW_RUNTIME_DEBUG=true ALLOW_DEBUG_FILL=true \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3) Validate through real backend APIs. Use --auth-password-env to reference a
#    secret by env var name; the runner must not write the secret value.
uv run python scripts/f1_demo_material_package.py validate \
  --base-url http://127.0.0.1:8000 \
  --artifact-dir artifacts/f1_demo_validation/manual-$(date -u +%Y%m%dT%H%M%SZ) \
  --auth-password-env APP_AUTH_PASSWORD \
  --timeout-seconds 180 \
  --poll-seconds 2 \
  --drain-local-worker

# 4) Publish only a passed run.json, after a DB backup or explicit external
#    backup confirmation.
uv run python scripts/f1_demo_material_package.py publish \
  --artifact artifacts/f1_demo_validation/<run-dir>/run.json \
  --backup-sqlite \
  --replace
```

For a non-SQLite target, replace `--backup-sqlite` with `--backup-confirmed`
after taking an external backup such as `pg_dump`. Do not use `--force` unless
recovering under a separate written incident note.

## Exact API sequence that `validate` must cover

The current runner in `scripts/f1_demo_material_package.py` records the following
request order into `api-log.json` and embeds the same log in `run.json`:

1. `POST /v1/sessions` with `declared_family=f1`.
2. Render PDFs from the template definition into `<artifact-dir>/materials/`.
3. `POST /v1/sessions/{session_id}/files` once per required PDF:
   - `ds160`
   - `passport_bio`
   - `i20`
   - `admission_letter`
   - `funding_proof`
   - `relationship_proof_between_applicant_and_sponsors`
4. Poll `GET /v1/sessions/{session_id}/reports/export` until all required
   documents are `parsed` and `understanding_status=completed`; local runs may
   also use `--drain-local-worker` to drain `case_understanding` jobs.
5. Send at least five applicant answers through
   `POST /v1/sessions/{session_id}/messages`, using only facts present in the
   generated template/profile.
6. Capture runtime and report state:
   - `GET /v1/sessions/{session_id}/debug/runtime`
   - `GET /v1/sessions/{session_id}/reports/user`
   - `GET /v1/sessions/{session_id}/reports/internal`
   - `GET /v1/sessions/{session_id}/reports/export`
   - `GET /v1/sessions/{session_id}/messages`
7. Validate `run.json` for zero defects: no upload/worker failure, no
   `main_flow_refresh_error`, no terminal risk/refusal decision, no stale
   material request after completed uploads, no repeated template replies, no
   unresolved required evidence, and no user/internal report drift.
8. Promote only the passed validation session via `publish`, then smoke the
   existing archive surface:
   - `GET /v1/material-packages`
   - `POST /v1/sessions/{fresh_session_id}/material-packages/{package_id}/import`
   - `GET /v1/sessions/{fresh_session_id}/reports/export`

## Manual list/import/export smoke after publish

The runner currently automates `render`, `validate`, and `publish`; list/import
smoke is still a manual API check. If local auth is enabled, log in first and
store cookies without printing the password value.

```bash
BASE_URL=http://127.0.0.1:8000 \
PACKAGE_ID=demo-f1-parent-sponsored-nyu-mscs-v1 \
SMOKE_DIR=artifacts/f1_demo_validation/import-smoke-$(date -u +%Y%m%dT%H%M%SZ) \
uv run python - <<'PY'
import json
import os
from pathlib import Path
import httpx

base_url = os.environ["BASE_URL"].rstrip("/")
package_id = os.environ["PACKAGE_ID"]
smoke_dir = Path(os.environ["SMOKE_DIR"])
smoke_dir.mkdir(parents=True, exist_ok=True)

with httpx.Client(base_url=base_url, timeout=120.0) as client:
    password = os.getenv("APP_AUTH_PASSWORD")
    if password:
        response = client.post("/v1/auth/login", json={"password": password})
        response.raise_for_status()

    packages = client.get("/v1/material-packages")
    packages.raise_for_status()
    (smoke_dir / "material-packages-smoke.json").write_text(
        json.dumps(packages.json(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    session = client.post("/v1/sessions", json={"declared_family": "f1"})
    session.raise_for_status()
    session_id = session.json()["session_id"]

    imported = client.post(f"/v1/sessions/{session_id}/material-packages/{package_id}/import")
    imported.raise_for_status()
    (smoke_dir / "import-smoke.json").write_text(
        json.dumps(imported.json(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    exported = client.get(f"/v1/sessions/{session_id}/reports/export")
    exported.raise_for_status()
    (smoke_dir / "import-export-smoke.json").write_text(
        json.dumps(exported.json(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

print(json.dumps({"status": "smoke-recorded", "session_id": session_id, "smoke_dir": str(smoke_dir)}, ensure_ascii=False))
PY
```

Review those three smoke files for the package id, six ready documents, and a
coherent export. The smoke script keeps cookies in memory only; never write or
commit cookie jars or other auth material.

## Required artifacts and secret handling

A valid run directory must contain at least:

- `run.json`: schema `ds160.f1_demo_validation_run.v1`, template metadata,
  session id, material manifest, upload results, worker export snapshot, five
  message turns, runtime debug snapshot, user/internal/export reports,
  transcript, embedded `api_log`, and `validation` summary.
- `api-log.json`: each API method/path/status plus request summary and response.
- `materials/manifest.json` plus the six generated PDFs.

Secrets must not appear in either JSON file. Use `--auth-password-env` so the
runner records the env var name and `<redacted>` instead of the password. Do not
place API keys, passwords, bearer tokens, cookies, or realistic credentials in
artifact directories. Before sharing an artifact, run a focused scan such as:

```bash
rg -n 'sk-|Bearer |APP_AUTH_PASSWORD|MIGRATION_ACCESS_KEY|password|cookie|set-cookie' \
  artifacts/f1_demo_validation/<run-dir>/run.json \
  artifacts/f1_demo_validation/<run-dir>/api-log.json
```

A hit for the literal request field name `password` is only acceptable when its
value is `<redacted>`; any real secret value fails the artifact review.

## Existing local evidence in this workspace

This workspace already contains live-style artifacts. The strongest current
local evidence is:

- `artifacts/f1_demo_validation_live3/run.json`
- `artifacts/f1_demo_validation_live3/api-log.json`

That run reports `validation.status=passed`, six `POST /files` responses with
HTTP `202`, five `POST /messages` responses with HTTP `200`, runtime debug and
user/internal/export reports collected, and zero recorded defects/warnings.
Older directories such as `artifacts/f1_demo_validation_live/` and
`artifacts/f1_demo_validation_live2/` are failed attempts and must not be cited
as passed evidence.

If a future branch or clean checkout does not contain a passed `run.json` and
matching `api-log.json`, do not claim the template is validated. Regenerate it
with the commands above, keep the artifact secret-free, and only publish from a
passed validation session.
