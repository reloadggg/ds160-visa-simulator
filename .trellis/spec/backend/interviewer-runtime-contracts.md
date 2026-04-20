# Interviewer Runtime Contracts

## Scenario: LLM-first turn runtime and shared consumer semantics

### 1. Scope / Trigger

- Trigger：修改 interviewer 主循环、turn decision schema、prompt pack、trace、OpenAI-compatible 输出、report 输出或 Chainlit 消费逻辑
- 关键文件：
  - `app/services/interview_runtime_service.py`
  - `app/services/interviewer_runtime_service.py`
  - `app/agents/question_agent.py`
  - `app/agents/schemas.py`
  - `app/domain/runtime.py`
  - `app/agents/model_factory.py`
  - `app/services/interviewer_prompt_registry.py`
  - `app/api/routers/openai_compat.py`
  - `app/services/report_service.py`
  - `app/services/message_service.py`
  - `chainlit_app.py`

### 2. Signatures

```python
QuestionAgentRunner.run(
    *,
    deps: AgentRuntimeDeps,
    dynamic_turn_context: dict[str, Any],
    user_message: str,
    boundary_decision: str,
) -> QuestionAgentRunResult

InterviewRuntimeService.build_question_action(
    session_id: str,
    profile: ApplicantProfile,
    score: ScoreState,
    governor_decision: str,
    trace_entries: list[RuntimeTraceEntry] | None = None,
    recent_turns: list[Any] | None = None,
) -> InterviewNextAction

InterviewerRuntimeService.run_turn(
    record: SessionRecord,
    message_text: str,
) -> dict
```

### 3. Contracts

#### 3.1 Prompt role contract

`app/domain/runtime.py::PromptRoleContract`

```json
{
  "system": "stable_policy",
  "dynamic_turn_context": "dynamic_turn_context",
  "tool_outputs": "tool_outputs",
  "user": "user"
}
```

规则：

- `system` 只放稳定 policy，不放当前用户事实
- `dynamic_turn_context` 放本轮 profile / score / recent turns / focus / gate 进度
- `tool_outputs` 只承载按需检索结果，不把所有工具内容预塞首屏
- `user` 只承载当前用户消息

#### 3.2 Turn decision output contract

`app/agents/schemas.py::InterviewNextAction`

```json
{
  "decision": "continue_interview | need_more_evidence | route_correction | high_risk_review | simulated_refusal",
  "assistant_message": "string",
  "requested_documents": ["at_most_one_document"],
  "focus_kind": "interview_question | required_document | route_correction | risk_review | refusal",
  "focus_document_type": "string | null",
  "focus_risk_code": "string | null",
  "reason": "string | null"
}
```

硬约束：

- `assistant_message` 不允许为空
- `requested_documents` 最多 1 个
- `requested_documents` 只允许在 `decision=need_more_evidence` 时出现
- `focus_kind` 缺失时按 `decision` 自动补默认值

#### 3.3 Advisory context contract

`app/domain/runtime.py::TurnAdvisoryContext`

```json
{
  "score_summary": {
    "category_fit": 0,
    "document_readiness": 0,
    "narrative_consistency": 0,
    "confidence": 0
  },
  "risk_codes": [],
  "missing_evidence": [],
  "risk_level": "none | low | medium | high",
  "missing_evidence_summary": "string | null"
}
```

规则：

- `score / risk / missing_evidence` 只作为 advisory context，不再直接决定主输出文案
- Governor 只保留边界裁决职责；主路径输出由 turn decision 驱动

#### 3.4 Trace contract

`app/domain/runtime.py::RuntimeTraceEntry`

关键字段：

```json
{
  "node_name": "turn_decision",
  "prompt_pack_id": "ds160.interviewer",
  "prompt_version": "v2",
  "provider": "openai",
  "model": "gpt-5.4",
  "tool_calls": [],
  "turn_decision": "continue_interview",
  "fallback_used": false,
  "retry_count": 0,
  "metadata": {
    "requested_documents": [],
    "focus_kind": "interview_question",
    "focus_document_type": null,
    "boundary_decision": "continue_interview",
    "reasoning_effort": "high"
  }
}
```

规则：

- 新节点名固定为 `turn_decision`，不要再写 `build_next_action`
- 任何 fallback / retry 都必须通过 trace 可见
- prompt trace 的来源是 `turn_decision` 节点，而不是另存一套平行字段

#### 3.5 Shared consumer contract

以下消费者必须消费同一套新语义：

- `MessageService` assistant turn metadata
  - `governor_decision`
  - `turn_decision`
  - `current_focus_kind`
  - `prompt_trace`
- `POST /v1/chat/completions`
  - `metadata.phase_state`
  - `metadata.governor_decision`
  - `metadata.requested_documents`
  - `metadata.turn_decision`
  - `metadata.prompt_trace`
- `ReportService.user_report/internal_report`
  - `turn_decision`
  - `advisory_context`
  - `prompt_trace`
- `chainlit_app.py`
  - 直接显示上传候选类型、支持主张与当前反馈

#### 3.6 Phase state contract

规则：

- 只要没有 `simulated_refusal`，`phase_state` 统一保持 `interview`
- `need_more_evidence` 不再把 session 重新编码成 `gate_review`
- `simulated_refusal` 是唯一把 `phase_state` 置为 `session_closed` 的 turn decision

### 4. Validation & Error Matrix

| Scenario | Input | Expected Behavior | Assertion Point |
|----------|-------|-------------------|-----------------|
| Agent 正常返回 | question agent 成功 | 使用 agent 输出；`fallback_used=false` | `runtime_trace_json` 最后一个 `turn_decision` 节点 |
| Agent 运行失败 | provider / schema / tool 失败 | 回退 `_fallback_question_action()`；trace 标记 fallback | `prompt_trace` 仍可见模型信息，`fallback_used=true` |
| score 仍提示缺材料 | `governor_decision=continue_interview` 且 fallback 路径有缺材料 | fallback 可恢复为 `need_more_evidence`，但这是兜底，不是主路径 | `tests/unit/test_interview_runtime_service.py` |
| 拒签结果 | `decision=simulated_refusal` | 对外文案必须走公共拒签 copy，不暴露内部推理 | `assistant_message` 与 `current_focus.reason` |
| 多消费者对齐 | messages / openai_compat / report | 都消费 `turn_decision + advisory_context + prompt_trace` | integration/live tests |

### 5. Good/Base/Bad Cases

#### Good

- 模型返回 `continue_interview`，`requested_documents=[]`，report / openai / message metadata 全部包含同一份 `turn_decision`
- 模型返回 `need_more_evidence`，只请求 1 份材料，并在 `focus_document_type` 中显式指出当前关键证明

#### Base

- provider 不可用时，fallback 仍返回单焦点输出，并在 trace 中记录 `fallback_used=true`
- live 模型在解析后可能进入 `continue_interview`，也可能推进到下一份关键材料；测试只断言“已推进，不再卡在旧材料”

#### Bad

- 继续断言 runtime trace 节点名为 `build_next_action`
- 把 `score` 或 `missing_evidence` 直接渲染成主输出而不经过 `turn decision`
- 让 `openai_compat` 继续暴露旧的 `gate_review` 语义
- 在主路径继续依赖旧 response templates 生成最终输出

### 6. Tests Required

- `tests/unit/test_interview_runtime_service.py`
  - 断言 fallback 行为、trace 节点、requested document 限制
- `tests/unit/test_interviewer_runtime_service.py`
  - 断言 `current_focus`、`interviewer_state_json`、`advisory_context`、`prompt_trace`
- `tests/integration/test_messages_api.py`
  - 断言 message 主链与 DB 持久化语义
- `tests/integration/test_interview_runtime_trace.py`
  - 断言 trace 历史按 `turn_decision` 追加
- `tests/integration/test_openai_compat.py`
  - 断言 OpenAI-compatible metadata
- `tests/integration/live/test_live_messages_api.py`
  - 断言真实模型执行 question agent 主路径
- `tests/integration/live/test_live_openai_compat.py`
  - 断言 live 场景下 metadata 与后续推进信号

### 7. Wrong vs Correct

#### Wrong

```python
assert record.runtime_trace_json[-1]["node_name"] == "build_next_action"
assert payload["metadata"]["phase_state"] == "gate_review"
assert payload["governor_decision"] == "continue_interview"  # 把 live 模型波动写死
```

#### Correct

```python
assert record.runtime_trace_json[-1]["node_name"] == "turn_decision"
assert payload["metadata"]["phase_state"] == "interview"
assert payload["metadata"]["turn_decision"]
assert payload["metadata"]["prompt_trace"]
assert payload["governor_decision"] in {"continue_interview", "need_more_evidence"}
```
