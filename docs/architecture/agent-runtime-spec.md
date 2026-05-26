# Agent Runtime Spec

日期：2026-05-24
状态：v1 草案，可执行合同先行

## 目标

新 agent runtime 的目标不是增加 agent 数量，而是收敛主流程控制权：

- live turn 只有一个用户可见回复来源。
- 失败能安全停止，并带有可追踪原因。
- 每个最终回复都能回放 graph state、citation、agent output、guard result。

## 框架职责

```text
LangGraph       状态转移、条件分支、checkpoint、resume、replay
Pydantic AI     typed LLM call
LangChain       仅在 RAG adapter 内部提供检索组件
Postgres        session、checkpoint、knowledge metadata、audit
pgvector        vector search，不绕过权限和生命周期
```

当前代码必须真实依赖官方 `langgraph` package，而不是只采用 graph 风格命名：

- 依赖：`pyproject.toml` 中声明 `langgraph`。
- 执行：`DeterministicDS160TurnGraph` 使用 `langgraph.graph.StateGraph` 编译为 `CompiledStateGraph`。
- 入口：`GraphRuntimeAdapter` 返回内部调试字段 `graph_runtime_engine=langgraph` 和 `graph_runtime_engine_class=CompiledStateGraph`。
- 测试：`tests/unit/test_agent_runtime_graph.py` 与 `tests/unit/test_graph_runtime_adapter.py` 必须断言官方 LangGraph runtime 被使用。

## 主控权

`AdjudicationAgent` 是 live turn 唯一 user-facing writer。

允许写 `assistant_message` 的来源只有：

- `adjudication_agent`
- `deterministic_safe_fallback`

禁止：

- `MaterialReviewAgent` 写用户可见主回复。
- `GroundingGuard` 改写用户可见主回复。
- `ResponseProjector` 改写用户可见主回复。
- 前端生成 officer 主线话术。
- 多 agent handoff 链接管 live turn。

## Simplification Boundary

graph runtime 不迁移旧 agent-like 层级，只迁移业务合同。

保留：

- `MessageService` 作为 API transaction boundary。
- `GateRuntimeService` 作为材料门控状态机。
- `TurnRecord` 作为兼容 artifact。

替换：

- `InterviewerRuntimeService` 主控流程。
- `InterviewRuntimeService` 内的主控 agent 调用。
- `CapabilityOrchestrator` 的主流程工具编排。
- `InterviewerTurnProjectorService` 的回复改写能力。
- `RuntimeLedgerService` 对旧 trace 的事实源地位。

新 graph 的事实源是：

- `DS160GraphState`
- `GraphEvent`
- `GraphRunResult`
- `CitationBundle`

旧 API 字段只能由 `GraphResponseMapper` 从这些事实源投影，不得反向影响 graph state。

## OpenAI-Compatible Adapter Boundary

`/v1/chat/completions` 和 `/v1/responses` 是产品层 adapter，不是独立记忆层，也不是模型供应商代理。

允许：

- 接收 OpenAI-compatible 请求形状。
- 从 `metadata.session_id` 或 `previous_response_id` 找回本地 session。
- 将 public `user` / `assistant` 历史导入本地 transcript。
- 为本轮 user message 生成或接收 `client_message_id`，交给 `MessageService` 做幂等写入。
- 把 `MessageService` 的结果投影成 Chat Completions 或 Responses 风格响应。

禁止：

- 依赖上游 OpenAI-compatible provider 保存 DS-160 会话状态。
- 只把最后一条 user message 传给主流程而丢弃前文。
- 让 adapter 绕过 `MessageService` 直接写最终 assistant turn。
- 把 imported historical user turns 写成真实 `client_message_id`，导致历史导入占用本轮幂等语义。

本地记忆层由以下部分共同组成：

- `session_turns` 作为完整 public transcript 事实源。
- `SessionTranscriptService` 负责兼容入口历史导入和去重。
- Case Memory 负责材料事实、用户 claim、证据和冲突。
- `InterviewMemoryService` 负责 oral topic 是否已经回答。
- `GraphCaseStateBuilder` 将完整 transcript、Case Memory、Interview Memory 投影成 `case_state`。
- `GraphAdjudicationNode` 在 LLM 输出后对已回答 topic 的重复问题做确定性修复。

Prompt 侧不能无限塞入完整历史。`case_state.full_transcript` 是本地完整事实源，但进入 LLM prompt 前必须经过窗口或摘要边界；当前实现保留 tail window，同时保留 transcript counts，避免长会话或重复提交撑爆模型上下文。

## Retry Budget

默认预算：

```text
普通回合：
  AdjudicationAgent 最多 1 次

guard fail：
  同一个 AdjudicationAgent 修正最多 1 次

材料/冲突回合：
  MaterialReviewAgent 最多 1 次
  AdjudicationAgent 最多 1 次
  guard fail 后同一个 AdjudicationAgent 修正最多 1 次
```

预算耗尽后必须进入 deterministic safe fallback，不允许继续自动调用新 agent。

## Graph State

代码合同位于：

- `app/domain/agent_runtime.py`

核心模型：

- `DS160GraphState`
- `GraphRunResult`
- `RetryBudget`
- `GraphEvent`
- `GroundingCheckResult`

`DS160GraphState` 必须包含：

- `session_id`
- `run_id`
- `schema_version`
- `client_turn_id`
- `user_turn`
- `case_state`
- `retrieval_plan`
- `citation_bundle`
- `material_review`
- `adjudication_result`
- `guard_result`
- `final_response`
- `node_timings`
- `retry_budget`

## SSE Events

允许事件：

- `accepted`
- `state_built`
- `retrieval_started`
- `retrieval_completed`
- `material_review_completed`
- `adjudication_completed`
- `guard_completed`
- `retrying`
- `fallback_used`
- `final`
- `error`

规则：

- `final` 必须带 `final_response`。
- `error` 必须带 `error_code`。
- 每个 event 必须带 `run_id`、`sequence`、`schema_version`。

## Safe Fallback

fallback 不是伪装成签证官继续裁决，而是明确降低结论强度。

常见原因：

- `missing_policy_citation`
- `missing_case_evidence`
- `schema_invalid`
- `provider_error`
- `retrieval_error`
- `checkpoint_error`
- `guard_retry_exhausted`

fallback 输出必须包含：

- `assistant_message`
- `assistant_message_author=deterministic_safe_fallback`
- `guard_status=fallback_required`
- `incomplete_reason`
- `next_safe_action`

## 验收

- `tests/unit/test_agent_runtime_contracts.py` 必须通过。
- 任意 final response 只能有一个 `assistant_message_author`。
- 官方政策断言必须有 citation。
- 用户材料断言必须有 case evidence citation。
- guard fail 不能直接改写 `assistant_message`。
- retry 用尽后不继续自动调用新 agent。
