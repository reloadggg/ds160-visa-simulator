# Agent Runtime Cutover Plan

日期：2026-05-24
状态：dated cutover plan；2026-06-06 文档刷新后仅作为历史/设计背景保留
目标：记录当时从旧 agent-like 主流程收敛 runtime 的计划；当前公开主线已经收敛为 native-only

> **Current status**：当前公开 writer 只有 `native_interviewer` / `NativeInterviewerRuntimeService`。`graph`、`graph_shadow`、`graph_canary` 只保留为 replay/eval、shadow/兼容 metadata 或未来单独验证过的 public promotion 语境；`legacy` 不再是部署 runbook 里的公开 fallback。需要回滚时回滚镜像、提交或配置备份，不通过重新打开 legacy public runtime 掩盖问题。

## 目标

本计划原本用于收敛“多个 runtime/agent-like 层都能写用户可见回复”的复杂度。读这份文档时请按历史 cutover plan 理解：

- 当前 `MessageService` 公开主流程已由 native interviewer 承担。
- 前端和 OpenAI-compatible API 保持现有响应字段兼容。
- 出现 schema、citation、provider、checkpoint 问题时必须能安全失败、可观测，并通过发布回滚恢复。
- 线上部署不再把 `legacy` 当成普通运行时回滚开关。

## 不再接受的旧模式

- 多个 agent-like service 都能影响用户可见主回复。
- guard / projector / material review 改写 `assistant_message`。
- agent 自己随意调 retriever / tool，然后把 trace 散落到多个字段。
- 失败后继续自动绕圈调用更多 agent。
- 用户问“哪里不一致”时只回复模板，不说明具体 what。

## 简化原则

这次迁移使用官方 `langgraph` Python package 承载图执行，但不是把旧 `InterviewerRuntimeService -> InterviewRuntimeService -> CapabilityOrchestrator -> Projector -> RuntimeLedger` 链路搬进 LangGraph。

当前实现边界：

- 图执行器：`app/services/agent_runtime_graph.py::DeterministicDS160TurnGraph` 内部编译官方 `langgraph.graph.StateGraph`，运行时产物是 `CompiledStateGraph`。
- 业务合同：仍由 `DS160GraphState`、`GraphRunResult`、`GraphEvent`、`CitationBundle` 冻结，避免把业务语义绑死在框架私有对象上。
- 兼容层：`GraphRuntimeAdapter` 和 `GraphResponseMapper` 保持旧 API 字段兼容；框架替换不要求前端同步改造。
- 后续扩展：checkpoint / resume / replay / conditional edges 应优先使用 LangGraph 原生能力，不再扩展自研 graph executor。

新主流程只保留 5 个概念：

```text
TurnGraph       唯一流程控制器
CaseState       profile / evidence / recent turns 的统一快照
KnowledgePlane  policy / case evidence / product rubric 检索与 citation
Adjudicator     唯一可写用户主回复的 typed LLM 节点
ResponseMapper  只做旧 API 兼容映射，不改写语义
```

所有旧层按以下规则处理：

| 旧组件 | 新定位 | 处理 |
| --- | --- | --- |
| `MessageService` | API transaction boundary | 保留，但只负责 user turn / assistant turn 持久化、runtime 选择、回滚 |
| `GateRuntimeService` | pre-interview document gate | 暂时保留；后续只输出 gate state，不写面试官语气 |
| `InterviewerRuntimeService` | historical legacy runtime | 不再作为 public writer；剩余引用仅用于兼容/删除窗口背景 |
| `InterviewRuntimeService` | legacy analysis + LLM caller | 拆出可复用 pure functions 后删除主控职责 |
| `CapabilityOrchestrator` | legacy scattered tool planner | 不迁移；能力拆成 graph nodes 和 `KnowledgePlane` |
| `InterviewerTurnProjectorService` | legacy projector | 不迁移为 graph 节点；仅抽出 `ResponseMapper` 兼容字段 |
| `RuntimeViewContractService` | compatibility mapper | 短期保留；长期由 graph response mapper 直接生成 |
| `RuntimeLedgerService` | read model builder | 短期保留 reports 兼容；长期改读 graph events |
| `TurnRecord` | turn artifact | 保留字段合同，但由 graph 一次性生成 |
| `GovernorService` / boundary logic | deterministic guard + next action policy | 合并进 graph guard / policy node |
| `RiskWatchService` | deterministic risk rule library | 保留为 pure rule helper，不拥有流程 |

## 复杂度削减清单

### 必须删掉或合并

1. `CapabilityOrchestrator` 里由 agent-like 动态决定工具输出的主流程职责。
   - 正确做法：graph 节点先规划 retrieval / material review，再把结果作为 citation bundle 输入给 adjudicator。
2. `InterviewerTurnProjectorService` 对 `assistant_message` 的二次改写能力。
   - 正确做法：`ResponseMapper` 只把 `GraphRunResult` 映射为旧字段，不能改文字。
3. `current_governor_decision` 与 `turn_decision.decision` 双写漂移。
   - 正确做法：graph 的 `final_response.decision` 是唯一决策源；旧字段只是投影。
4. `prompt_trace`、`runtime_trace_json`、`runtime_view_state`、`turn_record` 平行表达同一件事。
   - 正确做法：graph event 是事实源，旧字段从 graph event 派生。
5. 材料上传后直接触发旧 runtime 主回复的隐式副作用。
   - 正确做法：parse worker 先提交 evidence / case state；后置 material refresh 只能经 `MessageService` 的 runtime selector；当前公开默认由 `NativeInterviewerRuntimeService.run_material_change(...)` 刷新，只有未来真实 LangGraph public promotion 才改走 `GraphRuntimeAdapter.run_material_change(...)`。
   - refresh 失败不能把已完成 parse job 回滚为 failed。

### 可以先保留但冻结

1. `InterviewerRuntimeService`
   - 已按 `docs/architecture/legacy-runtime-deprecation-decision.md` 冻结并降级为历史/兼容语义。
   - 不再作为 current deployment rollback path；native 失败不能 fail-open 到 legacy。
   - 后续删除窗口只处理剩余代码/配置兼容面，不代表 legacy 可以继续写 public response。
2. `RuntimeLedgerService`
   - 继续服务报告和前端兼容，等 graph events 完整后替换。
3. `GateRuntimeService`
   - 保留材料门控，不进入 agent graph 的主回复生成。
4. SQLite 存储
   - 部署上线前可继续用；Postgres/pgvector 是 RAG 和 checkpoint 扩展阶段，不阻塞主流程替换。

### 暂不做

1. 不把 LangChain Agent 当主控。
2. 不把所有旧 unit test 重写成 graph test。
3. 不马上删除旧 runtime 文件。
4. 不在用户材料删除语义完成前把用户材料放入 pgvector。
5. 不强迫前端一次性改成 graph-native UI。

## Runtime 模式

历史上该计划讨论过以下配置标签：

```text
AGENT_RUNTIME=native_interviewer | graph_shadow | graph_canary | graph | legacy
AGENT_RUNTIME_CANARY_PERCENT=0..100
AGENT_RUNTIME_TRACE_ENABLED=true | false
```

当前语义：

- `native_interviewer`：唯一公开主流程，由 `NativeInterviewerRuntimeService` 写用户可见回复和 public material refresh。
- `graph_shadow`：历史兼容配置标签；公开请求仍只运行 native interviewer，不再同步旁路运行 graph。
- `graph_canary` / `graph`：历史兼容标签或未来 promotion 讨论入口；当前公开回复仍走 native interviewer，`selected_public_runtime` / `runtime_execution` 必须暴露真实执行路径。
- `legacy`：历史/兼容语义，不是当前公开 writer，也不是 deployment rollback runbook。

当前上线默认：

```text
AGENT_RUNTIME=native_interviewer
AGENT_RUNTIME_TYPED_ADJUDICATION_ENABLED=true
```

默认不静默回 legacy；`AGENT_RUNTIME_FAIL_OPEN_TO_LEGACY` 已从运行时 settings 中移除，native 失败应返回错误或由调用方显式处理。需要临时恢复服务时，按部署文档回滚到上一版已验证镜像、配置备份或代码提交。`AGENT_RUNTIME=graph` 不再作为新部署默认值；如果后续要让 LangGraph 成为公开 writer，必须单独完成 replay + live smoke + provider 指标验证。

## 兼容输出合同

无论 runtime 模式是什么，`MessageService.handle_user_turn(...)` 必须返回现有字段：

```json
{
  "assistant_message": "string",
  "governor_decision": "continue_interview | need_more_evidence | route_correction | high_risk_review | simulated_refusal",
  "requested_documents": [],
  "remaining_required_documents": [],
  "gate_progress": {},
  "turn_decision": {},
  "document_review": {},
  "prompt_trace": {},
  "runtime_view_state": {},
  "turn_record": {}
}
```

graph 可追加但不能替换的字段：

```json
{
  "agent_runtime": "graph",
  "graph_run_id": "run-...",
  "graph_trace": {
    "schema_version": "agent-runtime.v1",
    "event_count": 0,
    "guard_status": "passed",
    "used_citation_ids": []
  }
}
```

前端在替换期只依赖旧字段；`graph_trace` 只作为 debug / observability 数据。

兼容字段语义：

- `requested_documents` / `remaining_required_documents` 是旧消费者兼容投影，不是 Case Memory 的事实源。
- 当 response、material refresh 或 runtime view fallback 显式包含上述字段时，该字段就是本次投影的权威结果；空数组表示没有材料缺口，不允许再从旧 `runtime_view_state`、`interviewer_state_json`、`current_focus.document_type` 或 Gate 历史状态补回。
- 只有字段缺失时，才允许为了旧 API 消费者读取上一层 runtime/interviewer state 作为 fallback。

## 主流程替换阶段

### Phase A0 - Simplification Spike

目标：先切出最小可替换主流程，避免把 5000 行旧 runtime 复杂度搬进 graph。

任务：

1. 画出当前 live turn 数据流：
   - `MessageService`
   - `GateRuntimeService`
   - `InterviewerRuntimeService`
   - `InterviewRuntimeService`
   - `CapabilityOrchestrator`
   - `InterviewerTurnProjectorService`
   - `RuntimeViewContractService`
   - `RuntimeLedgerService`
2. 标记每个字段的唯一来源：
   - `assistant_message`
   - `decision`
   - `requested_documents`
   - `current_focus`
   - `prompt_trace`
   - `runtime_view_state`
3. 新建 `GraphResponseMapper` 合同：
   - 输入只允许 `DS160GraphState + GraphRunResult + GraphEvent[]`
   - 输出旧 API 字段
   - 不允许改写 `assistant_message`
4. 新建 `GraphCaseStateBuilder` 合同：
   - 从 `SessionRecord + turns + documents + evidence_items` 构造 case state
   - 不调用 LLM
5. 给旧复杂层贴上冻结边界：
   - legacy-only
   - graph-shared pure helper
   - delete-after-cutover

验收：

- 新计划明确哪些旧类不迁移。
- `GraphResponseMapper` 单测覆盖旧字段兼容。
- `GraphCaseStateBuilder` 单测覆盖材料/历史 turn 输入。
- 没有新增 agent-like orchestration。

停止条件：

- 如果 mapper 需要调用旧 projector 才能生成字段，先重做 mapper，不进入 Phase A。

### Phase A - Graph Adapter 接入，不写用户回复

目标：让 `MessageService` 能选择 runtime，但当前公开 writer 固定为 native interviewer，legacy 只做显式回滚路径；公开请求不再执行 graph shadow。

任务：

1. 新增 `AgentRuntimeMode` settings。
2. 新增 `GraphRuntimeAdapter.run_turn(record, message_text) -> MessageServiceResponse`。
3. 在 `MessageService.handle_user_turn()` 中加入选择器：
   - gate `family_not_selected` 仍走 gate。
   - `legacy` 走 `InterviewerRuntimeService`，只用于人工显式回滚。
   - `graph_shadow` / `graph_canary` / `graph` 公开请求只映射到 native public response。
4. graph replay / eval 保留在离线测试和显式工具路径中，不在用户请求中自动并发运行。
5. `GraphRuntimeAdapter` 不依赖 `CapabilityOrchestrator` 或 `InterviewerTurnProjectorService`。

验收：

- 现有前端字段不变。
- graph shadow 不新增第二个 assistant turn。
- graph shadow 不触发真实额外用户可见消息或额外 LLM 调用。
- native public response 失败不 fail-open 到 legacy。
- `uv run pytest -q -m "not live_llm"` 通过。

停止条件：

- 如果 shadow 会写第二条 assistant turn，立即停止。

### Phase B - Deterministic Graph 公开接管评估

目标：如果要让 LangGraph 成为公开 writer，先用 deterministic graph 接管一小类可控场景，证明主流程 wiring 正确。

范围：

- 不调用真实 LLM。
- 使用 deterministic fallback / fake adjudicator。
- 只覆盖 replay fixture 场景和 debug 场景。

任务：

1. `GraphRuntimeAdapter` 将 `GraphRunResult` 映射为旧响应字段。
2. `final_response.assistant_message_author` 写入 assistant turn metadata。
3. `GraphEvent` 写入 trace payload。
4. 新增 `POST /v1/sessions/{session_id}/runtime-traces/{run_id}` 或等价 debug endpoint。
5. `messages/stream` 保持 `accepted -> analyzing -> final/error` 兼容，同时可附加 graph event 摘要。
6. parse worker 默认只更新材料状态；自动 material refresh 通过当前公开 runtime 显式执行，不再在 worker 里隐式启动旧主流程。
7. debug fill / debug material bundle 的 material refresh 在 native / graph-compatible mode 下走 native interviewer；debug 场景名进入 runtime 前脱敏，避免把 `school_mismatch_bundle` 等 oracle 信号暴露给模型或 trace。

验收：

- `MessageService` graph mode 能创建 user turn + assistant turn。
- OpenAI-compatible API metadata 仍有 `turn_decision` / `prompt_trace`。
- Next.js workbench 无需改 UI 即可显示回复。
- replay fixture CLI 全部通过。
- 一键补资料、材料包、parse worker 三条 material-change 入口在 graph mode 下不会调用 legacy refresh。
- graph material refresh 只更新 session state / `material_refresh` metadata，不写用户可见 assistant turn。
- material bundle 的 `expected_findings`、scenario、bundle id 不进入 graph prompt/event/document review context。

停止条件：

- 如果 graph response 无法映射到旧 `runtime_view_state`，不进入 Phase C。

### Phase C - Typed AdjudicationAgent 接入

目标：graph 内部用 Pydantic AI typed call 替换旧 turn decision agent-like 编排。

任务：

1. `AdjudicationNode` 调用 Pydantic AI，但只接收：
   - `DS160GraphState`
   - `CitationBundle`
   - 当前 user message
2. 禁止 agent 直接读 DB。
3. retriever / material review 由 graph node 预先执行。
4. agent 输出必须先转成 `GraphRunResult`，再过 deterministic guard。
5. guard fail 时同一个 `AdjudicationNode` 最多 retry 一次。
6. retry 仍失败走 deterministic safe fallback。
7. 不复用 `CapabilityOrchestrator` 的主控逻辑。

验收：

- 任意 final response author 只能是 `adjudication_agent` 或 `deterministic_safe_fallback`。
- 单轮 LLM 调用数可在 trace 中看到。
- provider error 不产生 500，返回安全错误或 fallback。
- live LLM 测试只断言稳定合同。

停止条件：

- 如果 MaterialReviewAgent / guard / projector 能写主回复，立即回退。

### Phase D - RAG Knowledge Plane 接入

目标：让 graph 的 retrieval plan 和 citation bundle 替代旧散落式政策工具输出。

任务：

1. official policy 仍可先用现有 Chroma adapter，但输出必须升级为 `CitationRef`。
2. case evidence 先从已有 document chunks / evidence_items 生成 citation，不先上用户材料 vector storage。
3. product rubric 只进入 product guidance，不得支撑 official policy claim。
4. policy claim 无 official citation 时走 `unable_to_confirm`。
5. case conflict 无 case evidence citation 时要求补证或澄清。
6. 旧 `tool_outputs.policy_knowledge_retrieval` 只作为兼容输入，最终替换为 `CitationBundle`。

验收：

- 用户材料断言可回放到 `document_id/chunk_id/span/hash`。
- policy citation 不再只是 URL 级。
- 删除材料后相关 case evidence 不可被 graph 检索。

停止条件：

- 如果 case evidence citation 不能删除或失效，不接 pgvector 用户材料索引。

### Phase E - Replay Corpus 扩容与行为门禁

目标：证明新主流程不是“字段能跑”，而是业务逻辑能跑。

最低 corpus：

- 10 类场景，每类 3 条，共 30 条 fixture。
- 必含：学校冲突、资金不足、无 citation 政策问题、材料删除后检索、provider 失败。

机器门禁：

- 连续 10 轮不 500。
- 不连续重复同一模板超过 2 次。
- “哪里不一致”必须有具体 what。
- 高风险必须有 what / why / next。
- policy claim 必须有 official citation。
- case conflict claim 必须有 case evidence citation。
- 无证据必须降级，不得编造。

停止条件：

- replay 只能测字段、不能测行为时，不允许 canary。

### Phase F - 未来 LangGraph public promotion canary

目标：仅当后续明确要让 LangGraph 成为公开 writer 时，才做小流量真实替换，并保留快速回滚。当前线上默认仍是 `native_interviewer`。

步骤：

1. 部署时保持：
   - `AGENT_RUNTIME=native_interviewer`
2. 观察 24 小时或至少 30 个真实/测试 session：
   - 500 rate
   - provider error rate
   - guard fail rate
   - retry exhausted rate
   - missing citation rate
   - material refresh error rate
3. 切 `graph_canary`：
   - 10% -> 25% -> 50% -> 100%
4. 每一档至少跑：
   - smoke message
   - file upload + parse worker
   - stream message
   - OpenAI-compatible request
   - replay corpus

回滚：

```bash
# 回滚到上一版已验证镜像/提交/配置备份；不要用 legacy public runtime 掩盖 native 或 graph promotion 错误。
git checkout <previous-verified-sha>
docker compose up -d --build ds160-api ds160-web ds160-worker
```

停止条件：

- 任一档出现新增 500、重复模板、无法解释冲突、citation 缺失率异常，回滚到上一版已验证发布。

### Phase G - 默认 native interviewer，移除 legacy public rollback 语义

目标：线上默认 `native_interviewer`；legacy 只作为历史/兼容删除窗口背景，不作为 current rollback path。LangGraph 只在 shadow/eval 或单独 public promotion 任务中接管。

任务：

1. 默认 `.env.example` / compose 设为 `AGENT_RUNTIME=native_interviewer`。
2. legacy 代码冻结，不再新增能力。
3. 剩余 `AGENT_RUNTIME=legacy` 相关配置/测试/文档只作为删除窗口兼容面处理；边界以 `docs/architecture/legacy-runtime-deprecation-decision.md` 为准。
4. 删除旧 runtime 前必须有完整 replay + live smoke 证据。
5. 删除或降级旧复杂组件：
   - `CapabilityOrchestrator` 主流程职责删除。
   - `InterviewerTurnProjectorService` 删除或改成 thin mapper。
   - `InterviewerRuntimeService` 删除 live path。
   - `RuntimeLedgerService` 改读 graph events。

停止条件：

- 如果 native/graph trace 不能定位单轮失败原因，不删除 legacy。

## 部署前 Checklist

- 先运行 `uv run python -m app.cli.main release-preflight`，确认 legacy freeze / deletion 的必跑项没有被误判为完成；已在本次发布窗口完成的证据必须用 `--replay-corpus-passed`、`--focused-tests-passed`、`--live-smoke-passed`、`--docker-smoke-passed` 显式传入。`live-smoke` 指 focused live smoke，不等同于全量 live conversation / OpenAI API 套件。
- `uv run python -m compileall app`
- `uv run pytest -q -m "not live_llm"`
- `uv run python -m app.cli.main eval-graph-fixture --fixture fixtures/graph_replay/school_mismatch_where.json`
- replay corpus 全量通过
- `cd web && pnpm lint && pnpm type-check`
- Docker build 通过
- 本地或服务器 `/healthz` 通过
- `/api/v1/sessions` 创建会话通过
- `/api/v1/sessions/{session_id}/messages` 普通消息通过
- `/api/v1/sessions/{session_id}/messages/stream` SSE 通过
- `/api/v1/chat/completions` 兼容接口通过
- 文件上传 + parse worker + material refresh 通过

## 当前状态与下一步

Phase A0 已落地：

- `GraphCaseStateBuilder` 已能从 session / turns / documents / chunks / evidence 构造稳定 case state。
- `GraphResponseMapper` 已能把 `DS160GraphState + GraphEvent[]` 投影为旧 API 响应字段，并保留 graph trace。
- focused tests、`compileall`、非 live 全量测试和 graph replay fixture 已通过。

Phase A / B 当前已落地：

- `AGENT_RUNTIME=legacy|native_interviewer|graph_shadow|graph_canary|graph` selector 已接入 `MessageService.handle_user_turn(...)`。
- 官方 `langgraph.graph.StateGraph` 已编译为 `CompiledStateGraph` 并由 `GraphRuntimeAdapter` 调用。
- 普通用户消息和 material refresh 的 `graph_shadow` 不再触发第二套 graph shadow 调用；该配置值只作为历史兼容标签保留。
- 当前公开默认由 `NativeInterviewerRuntimeService` 写 assistant turn；`graph` / `graph_canary` 仍作为历史兼容标签映射到 native interviewer，响应必须暴露 `selected_public_runtime`。
- material-change refresh 当前由 native interviewer 公开主链路处理；native 失败不再自动 fallback 到 legacy。
- parse worker 在解析 job 完成后触发 refresh；refresh 异常只记录日志，不反向污染 completed job。

下一批实现进入 Phase C / D：

1. 为 `AdjudicationNode` 开启真实 typed LLM 前补 live smoke：普通消息、材料包、parse worker、OpenAI-compatible。
2. 把 `GraphKnowledgePlaneService` 的 policy/case citation 扩到 replay corpus，验证 policy claim 和 case conflict 均可溯源。
3. 扩容 replay corpus 到至少 30 条行为 fixture，覆盖“哪里不一致”、资金不足、学校冲突、provider 失败。
4. canary 前通过 replay / 显式评测记录指标：500 rate、provider error rate、guard fail rate、missing citation rate。
5. 只有 replay + live smoke 均通过后，才把 `AGENT_RUNTIME_TYPED_ADJUDICATION_ENABLED=true` 纳入 canary。
