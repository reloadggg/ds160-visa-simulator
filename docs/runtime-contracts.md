# Runtime Contracts

本文记录当前主线运行时合同，重点是 Gate、native public interviewer runtime、Governor、上传和报告之间的职责边界。

## Public Runtime Boundary

当前公开 writer 只有一个：`native_interviewer` / `NativeInterviewerRuntimeService`。`/messages` 和 public material refresh 都必须写入 `runtime_execution.public_runtime=native_interviewer`、`execution_runtime=native_interviewer_runtime`。

`graph`、`graph_shadow`、`graph_canary` 只保留为 LangGraph-backed shadow/eval/replay 或 compatibility labels；它们不能成为公开 writer。`legacy` 只作为历史/兼容语义存在，不能作为 public writer 或 fail-open fallback。

## Gate 与 Native Interviewer 的边界

Gate 不是面谈主脑，也不负责决定下一句面试官回复。

当前合同：

- `GateRuntimeService` 只负责签证家族选择、材料包进度和 `gate_progress`。
- `MessageService.handle_user_turn()` 只在 `gate_status == family_not_selected` 时返回 Gate response。
- `pending_documents`、`waiting_for_parse`、最低字段未齐等状态仍必须进入 `NativeInterviewerRuntimeService`。
- 非拒签场景下 `phase_state` 保持 `interview`。
- `simulated_refusal` 是唯一把 `phase_state` 置为 `session_closed` 的 turn decision。

## 主线材料请求来源

主线 `requested_documents` 只能来自面试官 Agent 的显式 turn decision：

```json
{
  "decision": "need_more_evidence",
  "requested_documents": ["funding_proof"],
  "focus_kind": "required_document",
  "focus_document_type": "funding_proof"
}
```

以下信息只能作为 advisory/support，不能回填为主线材料请求：

- Gate primary document
- `score.missing_evidence`
- document review 的 `recommended_next_step`
- governor requested documents
- 上一轮 `current_focus_json`

## 上传响应合同

`POST /v1/sessions/{session_id}/files` 仍返回 Gate 辅助进度，但顶层主线字段只反映面试官 focus：

```json
{
  "requested_documents": [],
  "remaining_required_documents": [],
  "gate_progress": {
    "overall_status": "waiting_for_parse",
    "documents": []
  },
  "main_flow_feedback": {
    "status": "helpful",
    "current_focus_document_type": "funding_proof"
  }
}
```

如果当前 `current_focus_json.owner == interviewer_runtime_service` 且 `kind == required_document`，上传响应的顶层 `requested_documents` 可以返回该面试官 focus。没有面试官 focus 时，不允许用 Gate primary document 填充顶层请求材料。

## 报告合同

用户报告和内部报告以 native runtime view / interviewer state 为主：

- 不因 `phase_state == gate_review` 自动输出“补件审核中”。
- 不用 Gate primary document 覆盖 `current_key_proof`。
- 不因 `funding.primary_source == parents` 且没有 evidence refs 自动追加 `funding_proof`。
- `missing_evidence` 来自面试官状态中的 `requested_documents`、`remaining_required_documents`、`current_key_proof` 或当前 focus。
- 用户报告顶层包含 `requested_documents`（与 turn 响应对齐；优先 runtime/interviewer 显式列表，否则从 current focus / remaining / missing 投影）。

## Case Board 字段

- 证明点 **canonical**：`proof_points`。
- 过渡别名：`open_proof_points` 与 `proof_points` 同内容输出，供旧客户端兼容一个版本周期。
- 写材料路径（package import、debug fill/bundle、understanding upsert、tombstone）在变更后必须 `CaseMemoryService.rebuild_and_persist`（或等价 rebuild），避免 sticky snapshot。

## OpenAI-Compatible 合同

`POST /v1/chat/completions` 与 `POST /v1/responses` 的 metadata 使用同一套 runtime view 合并规则：

- `metadata.phase_state` 非拒签时为 `interview`。
- `metadata.requested_documents` 不从 Gate 回填。
- `metadata.turn_decision`、`metadata.prompt_trace`、`metadata.runtime_view_state` 与 `/messages` 保持一致。

### Authz / ownership（breaking）

- Access-key 用户复用 `metadata.session_id` 时必须拥有该 session；跨 key → `403`。
- 新建 session 走 `create_session_with_quota`（扣次失败 → `403`，不留下孤儿 session）。
- Machine bearer（`APP_COMPAT_API_KEY`）仅 compat 两路径；对该路径 session 写为 admin-equivalent，**仅可信后端**。

非 live 测试必须 stub turn decision，不依赖真实模型配置。

## Practice materials 合同

- Gate：**仅** `practice_materials_enabled`。`practice OFF + debug ON` → practice API `403`（非 practice OR debug 并集）。
- Request：`include_synthetic_user_turns` 默认 `false`；`seed_text` max = `material_generation_seed_max_chars`（默认 4000）→ 超长 `422`。
- Response：`source:"practice"`、`is_practice_material:true`；**省略** `expected_findings`。Debug 路径保留 oracle，且 `is_practice_material:false`。
- Guard：同 session in-flight → `409`（detail 含 `material generation already in progress`）；滑动窗口超限 → `429`。
- Generation：chunked（plan + per-document + summary），`max_tokens` 与连接重试见 `AI_MATERIAL_*` env；长单次整包 JSON 在不稳定网关上易 `APIConnectionError` → 对外 503。

## Admin model channels 合同

- 运行时可配置多条 OpenAI-compatible **渠道**，存于 `admin_settings` JSON：`model_channels[]` + `active_model_channel_id`。
- 激活渠道驱动 `effective_model_config()`（`source=admin` 时覆盖 env）；并镜像到兼容字段 `model_base_url` / `model_api_key` / `model_name` / `model_streaming_enabled`。
- 旧客户端 PATCH 扁平 `model_*`：写入/更新激活渠道（无渠道时创建「默认渠道」）。
- 仅有旧扁平三元组、无 `model_channels` 时，读 settings 会迁移并持久化一条默认渠道（id 稳定）。
- API：`GET/POST /v1/admin/model-channels`、`PATCH/DELETE .../{id}`、`POST .../{id}/activate`；响应不回显 `api_key`。
- 后台 UI（`/admin` → 模型渠道）支持列表、新建/编辑/删除、设为运行时、拉模型列表与连通性测试。

## Client IP 信任

- `TRUST_X_FORWARDED_FOR` / `trust_x_forwarded_for` 默认 `false`：忽略 `CF-Connecting-IP` 与 XFF/X-Real-IP，用直连 peer。
- `true` 时：`CF-Connecting-IP` → 右端 XFF → X-Real-IP → peer。Cloudflare 生产须开 trust **并**锁定 origin 到 CF IP。

## Material understanding readiness

- `MATERIAL_UNDERSTANDING_REQUIRED` 默认 `true`：gate readiness 要求 understanding completed（或 skipped_legacy）。
- 离线 demo 可设 `false`，允许 parsed/legacy evidence 满足 readiness。

## 回归测试

默认回归命令：

```bash
uv run pytest -q -m "not live_llm"
```

关键断言位置：

- `tests/integration/test_messages_api.py`
- `tests/integration/test_files_api.py`
- `tests/integration/test_openai_compat.py`
- `tests/integration/test_reports_api.py`
- `tests/integration/test_sessions_api.py`
- `tests/integration/test_practice_material_bundles_api.py`
- `tests/integration/test_admin_demo_api.py`（含 model channels CRUD）
- `tests/e2e/test_simulation_flow.py`
- `tests/unit/test_interviewer_turn_projector_service.py`
- `tests/unit/test_interviewer_runtime_service.py`
- `tests/unit/test_gate_runtime_service.py`
- `tests/unit/test_report_service.py`
- `tests/unit/test_admin_model_channels.py`
- `tests/unit/test_material_generation_guard.py`
- `tests/unit/test_ai_material_bundle_generator_service.py`

## Wrong vs Correct

Wrong:

```python
if record.gate_status_json["status"] != "ready_for_interview":
    return gate_runtime.build_gate_response(record)
```

Correct:

```python
if record.gate_status_json["status"] == "family_not_selected":
    return gate_runtime.build_gate_response(record)
return native_interviewer_runtime.run_turn(record, message_text)
```

Wrong:

```python
requested_documents = gate_support["requested_documents"]
```

Correct:

```python
requested_documents = action.requested_documents if action.decision == "need_more_evidence" else []
```
