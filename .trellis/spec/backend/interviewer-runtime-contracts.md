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
    tool_outputs: dict[str, Any] | None = None,
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

CapabilityOrchestrator.orchestrate(
    *,
    session_id: str,
    governor_decision: str,
    latest_user_message: str,
    dynamic_turn_context: dict[str, Any],
    score: ScoreState,
) -> CapabilityOrchestrationResult
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
- `tool_outputs` 可以承载两类补充信息：
  - 模型按需调用工具后的返回结果
  - runtime 在进入 question agent 前预编排的 capability outputs
- `user` 只承载当前用户消息

#### 3.1.1 Test-double contract for question agent

- 任何 monkeypatch / spy / test double 只要替换 `QuestionAgentRunner.run(...)`，都必须接受 `tool_outputs` 关键字参数。
- 如果测试替身漏掉 `tool_outputs`，`InterviewRuntimeService._question_action()` 会在调用 test double 时触发 `TypeError`，然后被 runtime 当成 agent 失败并走 fallback。
- 这类失败属于测试桩签名漂移，不应被误判成真实业务回归。

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
| 测试桩漏 `tool_outputs` | monkeypatch 的 `QuestionAgentRunner.run` 签名过旧 | 会触发假 fallback；应先修测试替身签名 | live / unit monkeypatches |
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

```python
def tracked_run(self, *, deps, dynamic_turn_context, user_message, boundary_decision):
    ...
```

#### Correct

```python
def tracked_run(
    self,
    *,
    deps,
    dynamic_turn_context,
    tool_outputs=None,
    user_message,
    boundary_decision,
):
    ...
```

## Scenario: Phase 1 minimal agent-kernel seams

### 1. Scope / Trigger

- Trigger：修改 turn record、assistant turn metadata、BoundaryPolicy / AdvisoryReview / RiskWatch / ScoreStateBuilder、replay/inspect CLI 或相关测试
- Trigger：修改 turn projector / output projection / interviewer state projection / phase state projection 时也适用本节
- 关键文件：
  - `app/platform/turn_record.py`
  - `app/services/message_service.py`
  - `app/services/interviewer_runtime_service.py`
  - `app/services/interviewer_turn_projector_service.py`
  - `app/services/boundary_policy_service.py`
  - `app/services/advisory_review_service.py`
  - `app/services/risk_watch_service.py`
  - `app/services/score_state_builder.py`
  - `app/evals/replay_runner.py`
  - `app/cli/main.py`

### 2. Signatures

```python
TurnRecord.create(
    *,
    session_id: str,
    user_turn_id: str | None,
    user_input: str,
    decision: str,
    assistant_message: str,
    requested_documents: list[str],
    focus: dict[str, Any] | None,
    trace_refs: list[str],
    artifacts: list[dict[str, Any]] | None = None,
    advisory_summary: dict[str, Any] | None = None,
) -> TurnRecord

TurnRecord.with_assistant_turn(assistant_turn_id: str) -> TurnRecord

BoundaryPolicyService.decide(
    profile: ApplicantProfile,
    score: ScoreState,
    early_term_candidate: dict | None,
    review_signal: RiskFlag | None = None,
) -> dict

AdvisoryReviewService.build_context(score: ScoreState) -> TurnAdvisoryContext

RiskWatchService.apply_risk_watch_signals(
    record: SessionRecord,
    profile: ApplicantProfile,
    score: ScoreState,
    history_turns: list[Any],
    message_text: str,
) -> None

RiskWatchService.high_risk_review_signal(
    profile: ApplicantProfile,
    score: ScoreState,
) -> RiskFlag | None

ReplayRunner.inspect_turn(session_id: str, turn_id: str) -> dict
ReplayRunner.replay_session(session_id: str) -> dict

InterviewerTurnProjectorService.project(
    *,
    record: SessionRecord,
    message_text: str,
    action: InterviewNextAction,
    score: ScoreState,
    governor_decision: str,
    governor_requested_documents: list[str],
    trace_entries: list[RuntimeTraceEntry],
    history_turn_count: int,
    history_turns: list[Any],
) -> InterviewerTurnProjection
```

CLI contract：

```bash
ds160-agent-cli inspect-turn --session-id <sid> --turn-id <tid>
ds160-agent-cli replay-session --session-id <sid>
```

### 3. Contracts

#### 3.0 Extractor normalization contract for undecided funding

- `ExtractorService` 对 `/funding/primary_source` 的 agent 输出必须做后端归一化。
- 当模型返回的 `value` 只是占位词，例如 `undecided`、`unknown`、`not decided`、`tbd`、`to be determined`，不允许把它当成 `claimed` 真值写入 `profile.funding["primary_source"]`。
- 这类值必须被收敛成：
  - `field_states["/funding/primary_source"].state == "unknown"`
  - `profile.funding` 中不写入 `primary_source`
  - `field_provenance["/funding/primary_source"].evidence_refs == []`

#### 3.1 TurnRecord payload contract

`app/platform/turn_record.py::TurnRecord`

```json
{
  "turn_id": "assistant_turn_id_or_latest_user_turn_id",
  "session_id": "sess-...",
  "user_turn_id": "turn-user-...",
  "assistant_turn_id": "turn-assistant-...",
  "user_input": "string",
  "decision": "continue_interview | need_more_evidence | high_risk_review | simulated_refusal | route_correction",
  "assistant_message": "string",
  "requested_documents": [],
  "focus": {},
  "trace_refs": [],
  "artifacts": [],
  "advisory_summary": {}
}
```

规则：

- `TurnRecord.create()` 阶段允许 `turn_id == user_turn_id`
- assistant turn 落库后，必须通过 `with_assistant_turn()` 把：
  - `turn_id`
  - `assistant_turn_id`
  统一切到 assistant turn id
- `trace_refs` 必须保持原顺序，不允许在 replay/持久化时重排
- `artifacts` 只记录对本轮决策有意义的结构化产物，例如 `requested_document`

#### 3.2 Assistant turn metadata contract

assistant turn 落库后，`SessionTurnRecord.metadata_json` 至少包含：

```json
{
  "phase_state": "interview | session_closed",
  "governor_decision": "string | null",
  "turn_decision": "string | null",
  "current_focus_kind": "string | null",
  "prompt_trace": {},
  "turn_record": {}
}
```

规则：

- `turn_record` 只挂在 assistant turn metadata，不挂在 user turn
- response 中如果存在 `turn_record`，`MessageService` 必须在 assistant turn id 可用后写回最终版本
- `prompt_trace` 与 `turn_record.trace_refs` 必须描述同一轮执行，不允许跨轮复用旧 trace

#### 3.3 Boundary / Advisory / Risk contract

规则：

- `BoundaryPolicyService` 只负责边界裁决与高风险升级，不负责生成主回复文案
- 若 `GovernorService` 未给出 `simulated_refusal`，且 `review_signal` 非空，`BoundaryPolicyService` 必须把决策提升为 `high_risk_review`
- `AdvisoryReviewService` 只从 `ScoreState` 构造 `TurnAdvisoryContext`，不读 session record，不拼 UI 文案
- `InterviewerTurnProjectorService` 负责把：
  - `action + score + governor_requested_documents + trace`
  投影成：
  - `response`
  - `current_focus`
  - `interviewer_state`
  - `phase_state`
  - `turn_record`
- `InterviewerRuntimeService.run_turn()` 应保持 orchestration 角色，不应重新回流这些 projection 细节
- `RiskWatchService` 只负责：
  - 更新 `profile.ds160_view["risk_watch"]`
  - 根据回避回答/关键证明缺失次数上调 risk flag
  - 为高风险升级提供 `review_signal`
- `ScoreStateBuilder` 负责：
  - `ScoreProposal -> ScoreState`
  - fallback score 构造
  - findings guard
  - profile evidence reconciliation
  不要把这些规则重新塞回 `ScoringService`

#### 3.4 Replay / Inspect contract

`ReplayRunner.inspect_turn()` 返回：

```json
{
  "turn_id": "turn-...",
  "turn_index": 2,
  "session_id": "sess-...",
  "role": "assistant",
  "content": "string",
  "source": "interviewer_runtime_service",
  "metadata": {},
  "turn_record": {}
}
```

`ReplayRunner.replay_session()` 返回：

```json
{
  "session_id": "sess-...",
  "phase_state": "interview",
  "turn_count": 2,
  "score_evals": [],
  "turns": []
}
```

规则：

- `inspect_turn()` 在 turn 不存在或 session 不匹配时必须抛 `LookupError`
- `replay_session()` 在 session 不存在时必须抛 `LookupError`
- 只有当 `metadata.turn_record` 是字典时，返回 payload 才附带 `turn_record`
- CLI 必须打印 JSON，不输出混杂日志文本

### 4. Validation & Error Matrix

| Scenario | Input | Expected Behavior | Assertion Point |
|----------|-------|-------------------|-----------------|
| run_turn 刚结束、assistant turn 未落库 | response 带 `turn_record` | `turn_id` 允许先等于最新 user turn id | `tests/unit/test_interviewer_runtime_service.py` |
| assistant turn 已落库 | `MessageService._append_assistant_turn()` | `turn_record.turn_id == assistant_turn_id` 且 `assistant_turn_id` 已写入 | `tests/integration/test_messages_api.py` |
| review signal 触发 | `review_signal` 非空且 governor 不是 refusal | 边界决策升级为 `high_risk_review` | `tests/unit/test_boundary_policy_service.py` |
| 风险观察累计 2 次 | evasive 或 missing proof 连续出现 | upsert 高风险 flag，并带 `msg:<turn_id>` 引用 | `tests/unit/test_risk_watch_service.py` |
| replay 指向错误 turn | `turn_id` 不存在或 session 不匹配 | 抛 `LookupError` | `tests/unit/test_replay_runner.py` |
| replay 读取 assistant turn | metadata 有 `turn_record` | 返回 payload 附带 `turn_record` | `tests/unit/test_replay_runner.py` |

### 5. Good/Base/Bad Cases

#### Good

- `run_turn()` 返回的 `turn_record.trace_refs` 与 runtime trace 节点顺序一致
- assistant turn metadata 同时带 `prompt_trace` 与最终版 `turn_record`
- `BoundaryPolicyService` 只做决策提升，不再拼建议上下文
- CLI `inspect-turn` / `replay-session` 都返回纯 JSON 结构，适合二次消费

#### Base

- 还未拿到 assistant turn id 时，`TurnRecord` 先以 `user_turn_id` 占位
- 没有 `metadata.turn_record` 的 turn 仍可 replay，只是不附带 `turn_record`

#### Bad

- 在 `ScoringService` 中重新复制 `ScoreStateBuilder` 的 findings guard 逻辑
- 在 user turn metadata 上持久化 `turn_record`
- replay 结果把 `turn_record` 平铺进 `metadata` 外又丢失原 `metadata`
- CLI 混入解释性文本，破坏 JSON 可机读性

### 6. Tests Required

- `tests/unit/test_turn_record.py`
  - 断言 `TurnRecord` 在 assistant turn 前后切换 id 的规则
- `tests/unit/test_boundary_policy_service.py`
  - 断言 review signal 的升级语义
- `tests/unit/test_advisory_review_service.py`
  - 断言 advisory context 只由 `ScoreState` 派生
- `tests/unit/test_risk_watch_service.py`
  - 断言计数器递增、risk flag upsert、evidence ref 来源
- `tests/unit/test_score_state_builder.py`
  - 断言 proposal/fallback/findings guard/reconcile 规则
- `tests/unit/test_replay_runner.py`
  - 断言 inspect/replay 输出与 `LookupError`
- `tests/unit/test_cli_main.py`
  - 断言 CLI 参数解析与 JSON 输出
- `tests/unit/test_interviewer_turn_projector_service.py`
  - 断言 projector 的 response/current_focus/interviewer_state/turn_record 合同
- `tests/integration/test_messages_api.py`
  - 断言 assistant turn metadata 中的最终版 `turn_record`

### 7. Wrong vs Correct

#### Wrong

```python
assistant_turn.metadata_json = {
    "turn_record": response["turn_record"],
}
```

```python
score = ScoringService().score_profile(...)
score.risk_flags.append(...)
score.missing_evidence.append("funding_proof")
```

#### Correct

```python
finalized = TurnRecord.model_validate(response["turn_record"]).with_assistant_turn(
    assistant_turn.turn_id
)
assistant_turn.metadata_json = {
    **(assistant_turn.metadata_json or {}),
    "turn_record": finalized.model_dump(mode="json", exclude_none=True),
}
```

```python
score = self.score_builder.from_proposal(...)
review_signal = self.risk_watch_service.high_risk_review_signal(profile, score)
decision = self.boundary_policy.decide(profile, score, early_term_candidate, review_signal)
```

## Scenario: Phase 2 runtime ledger read model

### 1. Scope / Trigger

- Trigger：修改 `app/platform/runtime_ledger.py`、`app/services/runtime_ledger_service.py`、`app/evals/replay_runner.py`、`app/services/report_service.py`、`app/api/routers/reports.py`
- Trigger：让 replay / CLI / reports internal / 后续调试消费者开始共享同一套运行时读取模型时适用

### 2. Signatures

```python
RuntimeLedgerService.build_session_ledger(session_id: str) -> SessionLedger
RuntimeLedgerService.build_from_record(
    record: SessionRecord,
    *,
    turns: list[Any] | None = None,
) -> SessionLedger
RuntimeLedgerService.latest_view_state(
    ledger: SessionLedger,
    *,
    fallback_governor_decision: str | None = None,
) -> RuntimeViewState
RuntimeLedgerService.events_for_turn(
    ledger: SessionLedger,
    turn_id: str,
) -> list[dict[str, Any]]
```

### 3. Contracts

#### 3.1 Ledger shape

`SessionLedger`：

```json
{
  "session_id": "sess-...",
  "phase_state": "interview",
  "declared_family": "f1",
  "current_governor_decision": "continue_interview",
  "current_focus": {},
  "interviewer_state": {},
  "turns": [],
  "events": []
}
```

`TurnLedger`：

```json
{
  "turn_id": "turn-...",
  "turn_index": 2,
  "session_id": "sess-...",
  "role": "assistant",
  "content": "string",
  "source": "interviewer_runtime_service",
  "metadata": {},
  "turn_record": {},
  "event_ids": []
}
```

`LedgerEvent`：

```json
{
  "event_id": "turn-...:trace:0:0",
  "session_id": "sess-...",
  "turn_id": "turn-...",
  "turn_index": 2,
  "event_type": "trace | capability | scorer | boundary | advisory",
  "source": "runtime_trace | score_history | governor_history | turn_record",
  "name": "turn_decision",
  "payload": {}
}
```

`RuntimeViewState`：

```json
{
  "source_turn_id": "turn-... | null",
  "decision": "continue_interview",
  "governor_decision": "continue_interview",
  "public_status": "continue_interview | verify_key_issue | waiting_key_proof | high_risk_review | simulated_refusal | null",
  "risk_level": "none | medium | high | null",
  "current_focus": {},
  "current_key_question": "string | null",
  "current_key_proof": "string | null",
  "current_risk_code": "string | null",
  "requested_documents": [],
  "allowed_next_actions": [],
  "advisory_context": {},
  "prompt_trace": {}
}
```

`SessionReadModel`：

```json
{
  "session_id": "sess-...",
  "phase_state": "interview",
  "declared_family": "f1",
  "current_governor_decision": "continue_interview",
  "runtime_ledger": {},
  "runtime_view_state": {}
}
```

#### 3.2 Projection rules

- `runtime_trace_json` 必须按 `turn_decision` 节点切组，再顺序对齐 assistant turn
- 每条 trace 先产生 `trace` 事件；若该 trace 含非空 `tool_calls`，同批次再派生 `capability` 事件
- `score_history_json` 顺序映射为 `scorer` 事件
- `governor_history_json` 顺序映射为 `boundary` 事件
- assistant turn `metadata_json.turn_record.advisory_summary` 顺序映射为 `advisory` 事件
- 当历史批次数多于 assistant turn 数时，超出的事件必须保留为 session 级 orphan：
  - `turn_id = null`
  - `turn_index = null`
  - `event_id` 前缀固定为 `session-orphan:`
- `latest_view_state()` 的规则：
  - 有 assistant turn 时，优先用最新 assistant turn 的 `turn_record.focus / requested_documents / advisory_summary`
  - `governor_decision` 优先取最新 `boundary` 事件，否则回退 `session.current_governor_decision`
  - `prompt_trace` 优先取最新 `turn_decision` trace 事件
  - 无 assistant turn 时，`source_turn_id` 必须为 `null`，且不要把仅靠 fallback 推导出的 `public_status / allowed_next_actions` 冒充成真实 turn state
- `SessionReadModelService.build()` / `build_from_record()` 是 `runtime_ledger + runtime_view_state` 的唯一装配入口
- `RuntimeViewContractService` 负责统一消费者的字段合并规则：
  - `payload(..., anchored_only=True)` 可用于隐藏没有真实 assistant turn 锚点的半成品视图
  - `governor_decision / requested_documents / turn_decision / prompt_trace` 必须复用同一套 merge 逻辑，避免 `/messages`、`/chat/completions`、assistant turn metadata 各自漂移

#### 3.3 Consumer contract

- `ReplayRunner.inspect_turn()` / `replay_session()` 必须以 ledger 为主读取源，不再直接解析 ORM turn metadata + `score_history_json`
- replay 输出仍保留：
  - `session_id`
  - `phase_state`
  - `turn_count`
  - `score_evals`
  - `turns`
- replay 可以增量暴露 `events`，但不能删除旧字段
- `ReportService.user_report()` 必须开始优先消费 `runtime_view_state`
- 当 `runtime_view_state.source_turn_id` 存在时，`user_report` 应以它覆盖过期的 `interviewer_state_json`
- 当 `runtime_view_state.source_turn_id` 不存在时，`user_report` 只能把它当作弱提示，不得覆盖 gate 阶段的旧状态判断
- `ReportService.internal_report()` / `GET /reports/internal` 必须开始暴露 `runtime_ledger`
- `ReportService.internal_report()` / `GET /reports/internal` 应同时暴露 `runtime_view_state`
- internal report 仍需保留兼容镜像字段：
  - `runtime_trace`
  - `score_history`
  - `governor_history`
- `SessionReadModelService` 必须为 `reports`、`openai_compat`、`MessageService` 等消费者提供同一份读模型，不允许各处手工重复拼 `runtime_ledger + latest_view_state`
- `POST /v1/chat/completions`：
  - `metadata.runtime_view_state` 必须暴露统一视图
  - 顶层 `metadata.governor_decision / requested_documents` 仍优先保留实时 `MessageService.handle_user_turn()` 的兼容语义
  - `metadata.turn_decision / prompt_trace` 必须通过 `RuntimeViewContractService` 与 `runtime_view_state` 对齐
- `POST /v1/sessions/{session_id}/messages`：
  - 返回体必须直接带 `runtime_view_state`
  - `turn_decision / prompt_trace` 必须通过 `RuntimeViewContractService` 与读模型对齐
- `MessageService._append_assistant_turn()` 生成的 assistant turn metadata：
  - 仍必须保留最终版 `turn_record`
  - 应同步写入归一化后的 `requested_documents / prompt_trace`
  - 当 `runtime_view_state.source_turn_id == assistant_turn_id` 时，应同时写入 `runtime_view_state`
- `chainlit_app._format_internal_report()` 必须先输出 `runtime_view_state` 摘要，再附完整 JSON，避免调试视图重新退回旧字段

### 4. Validation & Error Matrix

| Scenario | Input | Expected Behavior | Assertion Point |
|----------|-------|-------------------|-----------------|
| 单轮 trace + tool call | `runtime_trace_json` 含 `tool_calls` | 生成 `trace + capability` 两类事件 | `tests/unit/test_runtime_ledger_service.py` |
| 多轮 assistant turn | 两组 `turn_decision` + 两个 assistant turn | 事件按批次对齐到各自 turn | `tests/unit/test_runtime_ledger_service.py` |
| orphan 历史 | 两批历史但只有一个 assistant turn | 第二批事件保留为 `session-orphan:*` | `tests/unit/test_runtime_ledger_service.py` |
| runtime view state | 最新 assistant turn 含 focus / requested_documents / advisory | 产出稳定 `runtime_view_state` | `tests/unit/test_runtime_ledger_service.py` |
| session read model | session record + session turns | 产出统一 `SessionReadModel`，同时包含 `runtime_ledger / runtime_view_state` | `tests/unit/test_session_read_model_service.py` |
| replay inspect | assistant turn 含 `turn_record` | 返回旧字段，同时新增 `events` | `tests/unit/test_replay_runner.py` |
| reports internal | 仅 session 历史，无 assistant turn | 返回 `runtime_ledger` / `runtime_view_state`，并保留旧 `runtime_trace/score_history/governor_history` | `tests/integration/test_reports_api.py` |
| reports user | `interviewer_state_json` 为空但 ledger 有 assistant turn | 用户报告仍能从 `runtime_view_state` 推导关键问题/动作 | `tests/integration/test_reports_api.py` |
| messages response | interview turn 已落库 | `/messages` 返回 `runtime_view_state`，assistant turn metadata 与之对齐 | `tests/integration/test_messages_api.py` |
| openai metadata | `MessageService` 被 monkeypatch 或暂无真实 assistant turn | `runtime_view_state` 可为空，但顶层兼容字段不丢失 | `tests/integration/test_openai_compat.py` |
| chainlit internal debug | 内部报告含 `runtime_view_state` | 先展示摘要，再输出完整 JSON | `tests/unit/test_chainlit_app.py` |

### 5. Tests Required

- `tests/unit/test_runtime_ledger_service.py`
  - 断言分组、事件类型、orphan 规则、payload helper、`latest_view_state`
- `tests/unit/test_session_read_model_service.py`
  - 断言统一读模型会同时带 `runtime_ledger / runtime_view_state`
- `tests/unit/test_report_service.py`
  - 断言 `user_report/internal_report` 对 `runtime_view_state` 的优先级
- `tests/unit/test_replay_runner.py`
  - 断言 replay/inspect 已带 ledger 事件且兼容旧字段
- `tests/unit/test_cli_main.py`
  - 断言 CLI 继续输出纯 JSON
- `tests/unit/test_chainlit_app.py`
  - 断言内部报告优先显示 `runtime_view_state` 摘要
- `tests/integration/test_messages_api.py`
  - 断言 `/messages` 与 assistant turn metadata 已消费统一 runtime view contract
- `tests/integration/test_openai_compat.py`
  - 断言 OpenAI-compatible metadata 中 `runtime_view_state` 暴露及其兼容回退行为
- `tests/integration/test_reports_api.py`
  - 断言 `runtime_ledger / runtime_view_state` 暴露，以及 user/internal report 的兼容行为

## Scenario: Phase 3 structured context engine and memory strata

### 1. Scope / Trigger

- Trigger：修改 `app/services/interview_runtime_service.py`、`app/services/ds160_context_engine.py`、`app/services/ds160_memory_manager.py`、`app/domain/runtime.py`、`app/interviewer_prompts/base.yaml`
- Trigger：修改 `dynamic_turn_context` 形状、recent turn 压缩策略、case brief / focus thread / evidence digest / memory strata 合同时适用

### 2. Signatures

```python
DS160MemoryManager.build(
    *,
    profile: ApplicantProfile,
    score: ScoreState,
    advisory_context: TurnAdvisoryContext,
    read_model: SessionReadModel | None,
    declared_family: str | None,
    phase_state: str,
    boundary_decision: str,
    documents: list[Any] | None = None,
) -> DS160MemoryBundle

DS160ContextEngine.build_dynamic_turn_context(
    *,
    session_id: str,
    declared_family: str | None,
    phase_state: str,
    latest_user_message: str,
    profile: ApplicantProfile,
    advisory_context: TurnAdvisoryContext,
    gate_progress: dict[str, Any] | None,
    recent_turns: list[Any] | None,
    memory_bundle: DS160MemoryBundle,
    prompt_roles: PromptRoleContract | None = None,
) -> TurnContextSnapshot
```

### 3. Contracts

#### 3.1 Dynamic turn context shape

`dynamic_turn_context` 在保留旧字段的同时，新增以下结构化字段：

```json
{
  "case_brief": {
    "declared_family": "f1",
    "phase_state": "interview",
    "boundary_decision": "continue_interview",
    "last_turn_decision": "need_more_evidence",
    "profile_version": 3,
    "travel_purpose": "study",
    "school_name": "Test University",
    "funding_source": "parents"
  },
  "focus_thread": {
    "current_focus": {},
    "last_turn_decision": "need_more_evidence",
    "public_status": "waiting_key_proof | verify_key_issue | null",
    "current_key_question": "string | null",
    "current_key_proof": "string | null",
    "current_risk_code": "string | null",
    "requested_documents": [],
    "allowed_next_actions": []
  },
  "evidence_digest": {
    "missing_evidence": [],
    "requested_documents": [],
    "current_focus_document_type": "string | null",
    "documented_field_paths": [],
    "evidence_refs": [],
    "supported_claims": [],
    "active_main_flow_feedback": {},
    "uploaded_document_count": 0,
    "uploaded_documents": []
  },
  "memory_strata": {
    "facts_memory": {},
    "working_memory": {},
    "evidence_memory": {},
    "derived_memory": {},
    "audit_memory": {}
  },
  "history_summary": {
    "summarized_turn_count": 0,
    "summarized_user_turn_count": 0,
    "summarized_assistant_turn_count": 0,
    "prior_decisions": [],
    "prior_requested_documents": []
  },
  "compression": {
    "strategy": "recent_turns_tail+history_summary",
    "recent_turn_window": 6,
    "retained_turn_count": 0,
    "summarized_turn_count": 0
  }
}
```

#### 3.2 Rules

- `dynamic_turn_context.current_focus` 仍保留，避免打破现有 prompt/runtime 消费路径
- `case_brief` 表达稳定案件基线，不应直接塞完整 `profile_snapshot`
- `focus_thread` 必须优先来自 `runtime_view_state / current_focus` 的上一轮稳定视图，而不是重新猜测
- `evidence_digest` 至少包含：
  - 当前缺失材料
  - 上一轮请求材料
  - 当前主线材料类型
  - 已 documented/confirmed 的字段路径
  - 已知 evidence refs
  - 当前上传材料聚合出的 `supported_claims`
  - 与当前主线最相关的一条 `active_main_flow_feedback`
  - 已上传材料列表与数量，便于主对话感知新证据是否已经进入上下文
- `memory_strata` 必须显式区分：
  - `facts_memory`
  - `working_memory`
  - `evidence_memory`
  - `derived_memory`
  - `audit_memory`
- `recent_turns` 只保留最近 `6` 条原始 turn
- 被裁剪掉的更早 turn 不能直接丢失，必须体现在 `history_summary + compression`
- `boundary_decision` 必须反映当前回合入口的 governor/boundary 决策，不允许退回上一轮旧值

### 4. Validation & Error Matrix

| Scenario | Input | Expected Behavior | Assertion Point |
|----------|-------|-------------------|-----------------|
| 有上一轮读模型 | `runtime_view_state` 含 current focus / requested docs | `focus_thread` 与 `current_focus` 对齐上一轮主线 | `tests/unit/test_interview_runtime_service.py` |
| 有 documented 字段 | `profile.field_states/provenance` 含 documented + evidence refs | `evidence_digest` 带 documented paths + refs | `tests/unit/test_interview_runtime_service.py` |
| 有已上传材料 | session documents 含 `document_assessment/main_flow_feedback` | `evidence_digest` 聚合 `supported_claims/uploaded_documents/active_main_flow_feedback` | `tests/unit/test_interview_runtime_service.py` |
| turn 超过 6 条 | `recent_turns` > 6 | 只保留最近 6 条，旧 turn 进入 `history_summary` | `tests/unit/test_interview_runtime_service.py` |
| 主链集成 | `/messages`、`/chat/completions` 正常跑通 | 新 context 字段不会破坏现有 API 合同 | `tests/integration/test_messages_api.py`, `tests/integration/test_openai_compat.py` |

### 5. Tests Required

- `tests/unit/test_interview_runtime_service.py`
  - 断言结构化 `case_brief / focus_thread / evidence_digest / memory_strata`
  - 断言上传材料反馈会进入 `evidence_digest`
  - 断言 `recent_turns_tail + history_summary` 压缩行为
- `tests/integration/test_files_api.py`
  - 断言上传链路仍持续暴露 `document_assessment/main_flow_feedback`
- `tests/integration/test_messages_api.py`
  - 断言消息主链不因 Phase 3 context 扩展而回退
- `tests/integration/test_openai_compat.py`
  - 断言 OpenAI-compatible 主链保持兼容

## Scenario: Phase 4 selective capability orchestration

### 1. Scope / Trigger

- Trigger：修改 `app/services/capability_orchestrator.py`、`app/services/interview_runtime_service.py`、`app/agents/question_agent.py`、`app/services/interviewer_turn_projector_service.py`、`app/services/runtime_ledger_service.py`
- Trigger：修改 capability plan、`tool_outputs` 预编排语义、capability trace 节点或 turn artifacts 合同时适用

### 2. Contracts

#### 2.1 Capability plan contract

`dynamic_turn_context.capability_plan` 必须是 runtime 预先给 question agent 的受控能力计划，最小结构：

```json
[
  {
    "capability_name": "document_assessment | evidence_retrieval | consistency_review",
    "status": "completed | skipped",
    "governor_decision": "continue_interview",
    "reason": "string",
    "summary": "string"
  }
]
```

规则：

- capability plan 只表达 runtime 已经决定是否调用/展开的能力，不是开放式 agent 委派
- `status=completed` 表示对应输出已经进入 `tool_outputs`
- `status=skipped` 必须给出可追溯原因，避免主循环再次隐式分支

#### 2.2 Tool outputs contract

`QuestionAgentRunner.run(..., tool_outputs=...)` 中的 `tool_outputs` 至少允许承载：

```json
{
  "document_assessment": {},
  "evidence_retrieval": {},
  "consistency_review": {}
}
```

规则：

- `document_assessment` 以 `evidence_digest` 为只读来源，输出当前主线材料、活跃反馈和相关上传材料
- `evidence_retrieval` 只允许基于当前 focus / supported claims / latest user message 做小范围检索，默认 `limit <= 3`
- `consistency_review` 只总结当前 risk / missing evidence / top risk flags，不重新生成第二套评分状态
- 主循环仍保持单一 interviewer agent；这些输出只是受控能力层的上下文输入

#### 2.3 Trace and replay contract

能力编排必须写入 runtime trace，并使用固定节点名：

```json
{
  "node_name": "decide_capability | resolve_capability",
  "summary": "string",
  "metadata": {
    "capability_plan": [],
    "artifacts": []
  }
}
```

规则：

- `decide_capability` 记录本轮 capability plan
- `resolve_capability` 记录最终落地的 capability artifacts
- `RuntimeLedgerService` 必须把这两个节点投影为 `CAPABILITY` 事件，即使没有模型 `tool_calls`
- `TurnRecord.artifacts` 必须吸收 `resolve_capability.metadata.artifacts`，保证 replay / inspect 可见

### 3. Validation & Error Matrix

| Scenario | Input | Expected Behavior | Assertion Point |
|----------|-------|-------------------|-----------------|
| 有上传材料摘要 | `evidence_digest` 含 uploaded documents / feedback | `document_assessment` completed，并进入 `tool_outputs` | `tests/unit/test_capability_orchestrator.py` |
| 有当前主线锚点 | `current_focus_document_type` 或最新用户消息存在 | `evidence_retrieval` 产生 query 与 hits 摘要 | `tests/unit/test_capability_orchestrator.py` |
| 有风险或缺口 | `risk_codes / missing_evidence / risk_flags` 非空 | `consistency_review` completed | `tests/unit/test_capability_orchestrator.py` |
| capability trace 无 tool_calls | 只有 `decide_capability / resolve_capability` | ledger 仍投影出 capability events | `tests/unit/test_runtime_ledger_service.py` |
| turn record 收口 | trace 含 capability artifacts | `turn_record.artifacts` 同时包含 requested documents 与 capability artifacts | `tests/unit/test_interviewer_turn_projector_service.py` |
| 主链集成 | `/messages`、`/chat/completions`、`/reports` 正常跑通 | 新 capability 节点不会破坏外部合同 | integration tests |

### 4. Tests Required

- `tests/unit/test_capability_orchestrator.py`
  - 断言 capability plan / tool_outputs / trace / artifacts
- `tests/unit/test_interview_runtime_service.py`
  - 断言 capability trace 会在 `turn_decision` 前落盘
- `tests/unit/test_interviewer_turn_projector_service.py`
  - 断言 capability artifacts 进入 turn record
- `tests/unit/test_runtime_ledger_service.py`
  - 断言 capability trace 节点可被 replay/read-model 投影
- `tests/integration/test_messages_api.py`
  - 断言主链 trace_refs 与 runtime trace 包含 capability 节点
- `tests/integration/test_interview_runtime_trace.py`
  - 断言每轮 runtime trace 新增 capability 节点且按 turn 对齐
