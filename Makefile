.PHONY: dev stop status logs api web

dev:
	./scripts/dev-up.sh

stop:
	./scripts/dev-down.sh

status:
	./scripts/dev-status.sh

logs:
	./scripts/dev-logs.sh

api:
	uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

web:
	cd web && pnpm dev --hostname 127.0.0.1 --port 3000
