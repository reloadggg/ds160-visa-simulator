# Runtime Debug Console Plan

状态：current debug observability plan；2026-06-06 文档刷新后按 native-only public runtime 理解。

## Objective

Make DS-160 runtime failures diagnosable from the frontend without SSH or manual trace downloads.

The console observes the current public runtime (`native_interviewer`) plus
compatibility/shadow/eval metadata. It must not make `legacy` or `graph` appear
as normal user-selectable public writers.

## Execution Phases

1. Version badge: show frontend version in the shell and keep it bumped before every server update.
2. Runtime snapshot: expose a gated read-only `GET /v1/sessions/{session_id}/debug/runtime` endpoint with redaction.
3. Debug console: add an in-app debug page for runtime metadata, material generation/import diagnostics, model/runtime errors, live events, and raw JSON.
4. Live progress: stream structured `debug_event` messages alongside existing message/material SSE events.
5. Export: copy one compact debug package containing frontend version, backend snapshot, recent live events, and latest material bundle result.
6. Follow-up diagnosis: use the console to distinguish missing seed, model failure, and material-refresh errors.

## Guardrails

- Do not expose API keys, cookies, tokens, passwords, or provider credentials.
- Do not put debug scenario labels or expected findings into model prompts.
- Keep normal interview UX separate from developer debug output.
- Debug visibility is gated by `ALLOW_RUNTIME_DEBUG=true` or the existing debug fill switch; production demos should keep debug material generation closed unless the demo window is explicitly controlled.
