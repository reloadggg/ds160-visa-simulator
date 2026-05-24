#!/usr/bin/env bash
set -euo pipefail

uv run python -m compileall app
uv run pytest \
  tests/unit/test_graph_replay_eval.py \
  tests/unit/test_cli_main.py \
  tests/unit/test_graph_knowledge_plane_service.py \
  tests/unit/test_graph_runtime_adapter.py \
  tests/unit/test_graph_adjudication_node.py \
  tests/integration/test_messages_api.py \
  tests/integration/test_openai_compat.py \
  tests/integration/test_sessions_api.py \
  -q
uv run python -m app.cli.main eval-graph-corpus --fixture-dir fixtures/graph_replay
