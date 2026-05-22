# Runtime Contracts

本文记录当前主线运行时合同，重点是 Gate、LLM 面试官 Agent、Governor、上传和报告之间的职责边界。

## Gate 与 LLM 的边界

Gate 不是面谈主脑，也不负责决定下一句面试官回复。

当前合同：

- `GateRuntimeService` 只负责签证家族选择、材料包进度和 `gate_progress`。
- `MessageService.handle_user_turn()` 只在 `gate_status == family_not_selected` 时返回 Gate response。
- `pending_documents`、`waiting_for_parse`、最低字段未齐等状态仍必须进入 `InterviewerRuntimeService`。
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

用户报告和内部报告以 runtime view / interviewer state 为主：

- 不因 `phase_state == gate_review` 自动输出“补件审核中”。
- 不用 Gate primary document 覆盖 `current_key_proof`。
- 不因 `funding.primary_source == parents` 且没有 evidence refs 自动追加 `funding_proof`。
- `missing_evidence` 来自面试官状态中的 `requested_documents`、`remaining_required_documents`、`current_key_proof` 或当前 focus。

## OpenAI-Compatible 合同

`POST /v1/chat/completions` 的 metadata 使用同一套 runtime view 合并规则：

- `metadata.phase_state` 非拒签时为 `interview`。
- `metadata.requested_documents` 不从 Gate 回填。
- `metadata.turn_decision`、`metadata.prompt_trace`、`metadata.runtime_view_state` 与 `/messages` 保持一致。

非 live 测试必须 stub turn decision，不依赖真实模型配置。

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
- `tests/e2e/test_simulation_flow.py`
- `tests/unit/test_interviewer_turn_projector_service.py`
- `tests/unit/test_interviewer_runtime_service.py`
- `tests/unit/test_gate_runtime_service.py`
- `tests/unit/test_report_service.py`

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
return interviewer_runtime.run_turn(record, message_text)
```

Wrong:

```python
requested_documents = gate_support["requested_documents"]
```

Correct:

```python
requested_documents = action.requested_documents if action.decision == "need_more_evidence" else []
```
