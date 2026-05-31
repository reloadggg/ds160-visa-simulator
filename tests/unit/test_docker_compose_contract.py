from pathlib import Path

import yaml


def test_compose_uses_postgres_readiness_and_layered_app_healthcheck() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    services = compose["services"]

    api = services["ds160-api"]
    web = services["ds160-web"]
    worker = services["ds160-worker"]
    combined = services["ds160-agent2"]
    postgres = services["postgres"]
    nginx = services["nginx"]
    build_args = compose["x-ds160-build"]["args"]

    assert api["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert worker["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert worker["depends_on"]["ds160-api"]["condition"] == "service_healthy"
    assert nginx["depends_on"]["ds160-api"]["condition"] == "service_healthy"
    assert nginx["depends_on"]["ds160-web"]["condition"] == "service_healthy"
    assert combined["profiles"] == ["combined"]
    assert build_args["UV_HTTP_TIMEOUT"] == "${UV_HTTP_TIMEOUT:-180}"
    dockerfile = Path("Dockerfile").read_text()
    assert "ARG UV_HTTP_TIMEOUT=180" in dockerfile
    assert "UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT}" in dockerfile

    api_environment = api["environment"]
    worker_environment = worker["environment"]
    web_environment = web["environment"]
    assert api_environment["DS160_PROCESS"] == "api"
    assert api_environment["PARSE_WORKER_INLINE"] == "0"
    assert worker_environment["DS160_PROCESS"] == "worker"
    assert worker_environment["PARSE_WORKER_INLINE"] == "0"
    assert web_environment["DS160_PROCESS"] == "web"
    assert "postgresql+psycopg://ds160:ds160@postgres:5432/ds160" in (
        api_environment["DATABASE_URL"]
    )
    assert worker_environment["DATABASE_URL"] == api_environment["DATABASE_URL"]
    assert "COMPOSE_DATABASE_URL" in api_environment["DATABASE_URL"]
    assert not api_environment["DATABASE_URL"].startswith("${DATABASE_URL")
    assert api_environment["LOG_FORMAT"] == "${LOG_FORMAT:-json}"
    assert "sqlite" not in Path("docker-compose.yml").read_text().casefold()

    api_healthcheck = " ".join(api["healthcheck"]["test"])
    assert "/healthz" in api_healthcheck
    assert "127.0.0.1:8000" in api_healthcheck

    web_healthcheck = " ".join(web["healthcheck"]["test"])
    assert "127.0.0.1:3000" in web_healthcheck

    worker_healthcheck = " ".join(worker["healthcheck"]["test"])
    assert "select 1" in worker_healthcheck
    assert "DATABASE_URL" in worker_healthcheck
    assert "database_health" not in worker_healthcheck
    assert worker["healthcheck"]["timeout"] == "15s"

    postgres_healthcheck = " ".join(postgres["healthcheck"]["test"])
    assert "pg_isready" in postgres_healthcheck
    assert "POSTGRES_DB" in postgres_healthcheck

    volumes = compose["volumes"]
    assert "ds160-agent2-postgres" in volumes

    nginx_config = Path("deploy/nginx/ds160.conf").read_text()
    assert "proxy_pass http://ds160-api:8000" in nginx_config
    assert "proxy_pass http://ds160-web:3000" in nginx_config
    assert "ds160-agent2:8000" not in nginx_config

    start_script = Path("docker/start.sh").read_text()
    assert 'export HOSTNAME="$WEB_HOST"' in start_script
    assert 'export PORT="$WEB_PORT"' in start_script
    assert "server.js --hostname" not in start_script
