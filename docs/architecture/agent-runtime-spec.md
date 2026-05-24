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
