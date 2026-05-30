# Postgres Migration Runbook

## Scope

This project supports two database modes:

- Local single-process development: SQLite, default `sqlite:///./app.sqlite3`.
- Docker Compose deployment: Postgres, default `postgresql+psycopg://ds160:ds160@postgres:5432/ds160`.
  The default service topology is split into `ds160-api`, `ds160-web`,
  `ds160-worker`, `postgres`, and `nginx`.

The current repository does not use Alembic. SQLAlchemy `Base.metadata.create_all(...)`
is still the schema bootstrap mechanism. SQLite-only compatibility bootstraps in
`app/main.py` must not run against Postgres.

The Compose contract is covered by `tests/unit/test_docker_compose_contract.py`.
It checks that API and worker wait for Postgres readiness, nginx waits for API
and Web readiness, the API healthcheck uses `/healthz`, worker readiness probes
database connectivity, and the default Compose `DATABASE_URL` points at the
internal Postgres service.

Compose intentionally uses `COMPOSE_DATABASE_URL` as the host-side override
variable and writes it into the container as `DATABASE_URL`. This prevents a
local development `.env` value such as `DATABASE_URL=sqlite:///./app.sqlite3`
from silently overriding the Compose Postgres default.

## Preflight

1. Run the structured release preflight when preparing a legacy freeze or
   runtime cleanup release:

```bash
uv run python -m app.cli.main release-preflight \
  --replay-corpus-passed \
  --focused-tests-passed \
  --live-smoke-passed
```

This command checks Docker CLI candidates, validates `docker compose config --quiet`,
and probes Docker daemon readiness without printing the full Compose render.

2. Confirm the running deployment target:

```bash
docker compose config | sed -n '/DATABASE_URL/p'
```

3. Confirm the app can import and create metadata with the configured driver:

```bash
uv run python - <<'PY'
from app.db.base import Base
from app.db import evidence_models as _evidence_models
from app.db.session import engine

Base.metadata.create_all(bind=engine)
print(engine.dialect.name)
PY
```

4. Confirm application health after startup. In the default Compose topology,
   `ds160-api` exposes API ports only to the Compose network, so probe the app
   from inside the API container unless nginx is also started:

```bash
docker compose exec -T ds160-api python - <<'PY'
import urllib.request

for path in ("healthz", "livez", "version"):
    body = urllib.request.urlopen(
        f"http://127.0.0.1:8000/{path}",
        timeout=5,
    ).read().decode()
    print(path, body)
PY
```

`/livez` only proves the API process responds. `/healthz` is the layered readiness
surface and should include `checks.database`, `checks.worker`, and `checks.llm`.
When a critical readiness check is degraded, `/healthz` returns HTTP 503 so
Compose and curl smoke checks fail instead of treating a degraded app as healthy.
In the split Compose topology, `checks.worker.inline_enabled=false` means the API
process is not running an inline worker; confirm the separate `ds160-worker`
service through `docker compose ps` and worker logs.

## Fresh Compose Deployment

For a new server with no SQLite data to preserve:

```bash
APP_AUTH_PASSWORD='change-me' \
OPENAI_BASE_URL='https://your-openai-compatible-endpoint/v1' \
OPENAI_API_KEY='your-api-key' \
NEXT_PUBLIC_GIT_SHA="$(git rev-parse --short HEAD)" \
APP_GIT_SHA="$(git rev-parse --short HEAD)" \
NEXT_PUBLIC_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
APP_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
LOG_FORMAT=json \
docker compose up -d --build postgres ds160-api ds160-web ds160-worker
```

Do not set `DATABASE_URL` for Compose. Compose will point the app at the
internal `postgres` service. If an external database is intentional, set
`COMPOSE_DATABASE_URL`; the container will still receive it as `DATABASE_URL`.

Start nginx only after valid certificates are available under `deploy/certs`.
The old combined app container remains available for compatibility only:

```bash
docker compose --profile combined up -d ds160-agent2
```

## Existing SQLite Data

Before replacing a SQLite-backed deployment, take a cold backup. If the old
deployment used the compatibility combined container, stop that container first:

```bash
docker compose --profile combined stop ds160-agent2
docker run --rm -v ds160-agent2-data:/data -v "$PWD":/backup alpine \
  sh -lc 'cp /data/app.sqlite3 /backup/app.sqlite3.backup'
```

For a split deployment already using Postgres, do not copy `/data/app.sqlite3`
as a source of truth.

For an existing deployment that must preserve runtime data, use the checked-in
CLI migration helper during a maintenance window:

```bash
uv run python -m app.cli.main migrate-sqlite-to-postgres \
  --source-url sqlite:////absolute/path/to/app.sqlite3 \
  --target-url "$POSTGRES_DATABASE_URL"
```

Rules:

- Fresh deployment: keep the SQLite backup for audit only; do not migrate empty demo data.
- Existing deployment: run the helper against a backed-up SQLite file, not a live writable SQLite file.
- Non-empty target: only pass `--truncate-target` after taking a target backup and confirming the maintenance window.
- Dry run: pass `--dry-run` first to inspect source and target table counts without writes.

Do not point multiple production containers at the old SQLite file.

The guarded production helper wraps the backup, split Compose startup, dry-run,
write migration, and smoke checks in one maintenance-window command:

```bash
CONFIRM_PRODUCTION_CUTOVER=I_UNDERSTAND_PRODUCTION_CUTOVER \
RUN_WRITE_MIGRATION=1 \
scripts/production-split-postgres-cutover.sh
```

The helper refuses to run without explicit confirmation and `RUN_WRITE_MIGRATION=1`
because it stops the old combined container before starting split services. Use
the manual dry-run command below when you only want migration counts.

If the Compose Postgres service is not published to the host, copy the cold
SQLite backup into the app container and run the dry-run against the internal
Compose hostname:

```bash
docker cp app.sqlite3 ds160-api:/tmp/app.sqlite3
docker compose exec -T ds160-api /app/.venv/bin/python -m app.cli.main \
  migrate-sqlite-to-postgres \
  --source-url sqlite:////tmp/app.sqlite3 \
  --target-url postgresql+psycopg://ds160:ds160@postgres:5432/ds160 \
  --dry-run
docker compose exec -T ds160-api rm -f /tmp/app.sqlite3
```

## Smoke Tests

After cutover:

```bash
docker compose exec -T ds160-api python - <<'PY'
import urllib.request

for path in ("healthz", "livez", "version"):
    body = urllib.request.urlopen(
        f"http://127.0.0.1:8000/{path}",
        timeout=5,
    ).read().decode()
    print(path, body)
PY
```

Then create one UI session, send one message, upload or generate one debug material
bundle, and open the runtime debug console. The debug snapshot should show the
current backend version and runtime metadata without requiring SSH log access.

For Compose, also confirm:

```bash
docker compose ps
docker compose logs --tail=100 ds160-api ds160-worker ds160-web
```

`ds160-api`, `ds160-worker`, and `ds160-web` should report healthy or running
status. API and worker JSON logs should preserve correlation fields such as
`session_id`, `document_id`, `turn_id`, or `run_id` when the emitting code
supplies them.

Nginx is a separate edge smoke. The checked-in compose file mounts
`./deploy/certs` into nginx and expects `origin.crt` / `origin.key`. Those
files are intentionally gitignored. Do not start the nginx service until valid
local or origin certificates are present:

```bash
test -s deploy/certs/origin.crt
test -s deploy/certs/origin.key
docker compose up -d nginx
curl -k -fsS https://127.0.0.1:18000/healthz -H 'Host: ds160.efastt.store'
```

When running Docker Desktop from WSL through `docker.exe`, bind mounts under
the WSL distro may fail before nginx starts. In that case, validate the same
edge config with a temporary nginx container and `docker cp`:

```bash
docker create --name ds160-nginx-local-smoke \
  --network ds160_pr_default \
  -p 18000:18000 \
  nginx:1.27-alpine
docker cp deploy/nginx/ds160.conf \
  ds160-nginx-local-smoke:/etc/nginx/conf.d/default.conf
docker cp deploy/certs ds160-nginx-local-smoke:/etc/nginx/certs
docker start ds160-nginx-local-smoke
curl -k -fsS https://127.0.0.1:18000/healthz -H 'Host: ds160.efastt.store'
curl -k -fsS https://127.0.0.1:18000/api/version -H 'Host: ds160.efastt.store'
```

## Rollback

For Compose, rollback means restoring the previous image and either:

- Keeping Postgres if the app smoke tests pass on the previous image.
- Restoring the SQLite volume only if that deployment was still SQLite-backed.

Never overwrite a newer Postgres volume with stale SQLite data during rollback.
