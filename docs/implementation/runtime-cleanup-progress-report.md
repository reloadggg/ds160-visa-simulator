# Runtime Cleanup Progress Report

日期：2026-05-29（2026-05-30 继续更新）
状态：阶段实施报告，非最终完成报告

## 本阶段目标

本阶段围绕原任务清单中的 P0/P1 风险点先做可验证落地：

- 收口公开 runtime 命名和真实执行路径，避免继续出现“显示 graph、实际跑 native/legacy、debug 看不清”的问题。
- 默认关闭静默 legacy fallback，除非显式启用 fail-open 或直接切 `AGENT_RUNTIME=legacy`。
- 防止前端重新自造签证官消息，把非对话状态移入 activity stream。
- 增加完整成功路径 replay，覆盖建档、自然问答、上传理解、冲突澄清和可解释复盘。

## 已完成

### 本轮补充：L1 Compose 服务拆分

已落地：

- 默认 `docker-compose.yml` 现在按职责拆分为 `ds160-api`、`ds160-web`、
  `ds160-worker`、`postgres` 和 `nginx`。
- `docker/start.sh` 新增 `DS160_PROCESS=api|web|worker|combined`，同一镜像可按
  服务职责启动；旧 `combined` 模式只作为兼容入口保留在 Compose profile。
- Next standalone server 通过 `HOSTNAME` / `PORT` 环境变量绑定，避免
  `server.js --hostname ...` 参数被忽略后 Web healthcheck 长期停在 starting。
- `ds160-api` 只运行 FastAPI，`PARSE_WORKER_INLINE=0`；`ds160-worker` 通过
  `python -m app.cli.main run-parse-worker` 独立消费材料理解任务。
- `nginx` upstream 已拆分：`/healthz` 与 `/api/` 指向 `ds160-api:8000`，
  根路径指向 `ds160-web:3000`。
- `release-preflight` 的 Docker smoke 提示命令改为启动
  `postgres ds160-api ds160-web ds160-worker`，不再提示默认启动旧单容器。
- README 与 Postgres migration runbook 已改成默认 split topology；旧
  `ds160-agent2` 只作为 `--profile combined` 兼容模式说明。
- 本地 Docker Desktop split Compose smoke 已通过：`postgres`、`ds160-api`、
  `ds160-web`、`ds160-worker` 均 healthy；Web 容器内 `/` 返回 200；API 容器内
  `/healthz` 返回 `status=ok` 且 database dialect 为 `postgresql`；worker 日志
  显示 parse worker 以 loop 模式启动。
- `deploy/README.md` 已同步服务器启动/更新流程：拉取
  `refactor/agent-runtime-graph`、注入 git sha/build time、按 split services
  重建、再启动 nginx 并验证 `/healthz` 与 `/api/version`。Runtime 说明也从
  “graph 接管主流程”修正为 `native_interviewer` 公开主流程、graph 仅兼容/shadow。

### 本轮补充：远程生产只读审计

已确认：

- 服务器 `/opt/ds160-agent2` 当前分支是 `refactor/agent-runtime-graph`，工作树干净。
- 服务器 HEAD 为 `ef4dd76`，当前远端分支 HEAD 为 `c299f7c`；生产代码落后。
- 当前远程 Compose 仍只有 `ds160-agent2` 与 `nginx`，尚未切到
  `ds160-api` / `ds160-web` / `ds160-worker` / `postgres` split topology。
- 当前生产数据库 dialect 为 `sqlite`，表计数为：
  sessions=40、session_turns=276、documents=110、document_chunks=109、
  evidence_items=333、jobs=4、auth_sessions=19、case_memory_snapshots=0。
- 远程 `.env` 中 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`APP_AUTH_PASSWORD` 存在；
  `COMPOSE_DATABASE_URL`、`APP_GIT_SHA`、`APP_BUILD_TIME`、`NEXT_PUBLIC_GIT_SHA`、
  `NEXT_PUBLIC_BUILD_TIME` 缺失。
- 服务器本机 `/healthz` 和公网 `https://ds160.efastt.store/healthz` 均返回
  `status=ok`；`/api/version` 仍被当前生产鉴权挡住，直接 `/version` 是旧前端
  404，这与服务器代码落后相符。

### 本轮补充：删除 `gate_parse` 历史兼容路径

已落地：

- 远程生产 jobs 表只剩 `case_understanding`，状态分布为 completed=1、failed=3；
  无 `gate_parse` 历史队列。
- `ParseWorker.run_once()` 不再 fallback claim `gate_parse`，只消费
  `CASE_UNDERSTANDING_JOB_KIND`。
- `GateRuntimeService` 的 waiting-for-parse 判断只承认 `case_understanding` job。
- 单元测试中的 legacy `gate_parse` 夹具已改为 `case_understanding`。
- `docs/API.md`、`gate-decommission-inventory.md` 和本报告已同步删除旧 fallback
  说法。

### 本轮补充：生产 cutover 脚本

已落地：

- 新增 `scripts/production-split-postgres-cutover.sh`，用于维护窗口内执行
  SQLite -> Postgres + split Compose cutover。
- 脚本默认拒绝运行，必须显式设置
  `CONFIRM_PRODUCTION_CUTOVER=I_UNDERSTAND_PRODUCTION_CUTOVER` 和
  `RUN_WRITE_MIGRATION=1`。
- 脚本会拒绝 dirty server worktree，备份 `.env` 与 SQLite，保存 env key presence、
  git head、compose 状态和 migration JSON 证据。
- 迁移顺序固定为：备份旧 combined volume -> 可选快进代码 -> 注入 build metadata
  env -> 启动 `postgres ds160-api ds160-web ds160-worker` -> copy SQLite 到
  `ds160-api` -> migration dry-run -> migration write -> 删除临时 SQLite ->
  启动 nginx -> 本机和公网 smoke。
- 新增 `tests/unit/test_deploy_scripts.py`，锁住显式确认、dirty worktree 保护、
  dry-run-before-write 和不打印 `.env` 的约束。
- 本地验证：`bash -n scripts/production-split-postgres-cutover.sh` 通过；未设置确认变量时脚本以 exit 64 拒绝执行。

关键文件：

- `docker-compose.yml`
- `docker/start.sh`
- `deploy/nginx/ds160.conf`
- `app/cli/main.py`
- `app/workers/parse_worker.py`
- `app/services/gate_runtime_service.py`
- `tests/unit/test_docker_compose_contract.py`
- `tests/unit/test_cli_main.py`
- `tests/unit/test_gate_runtime_service.py`
- `tests/unit/test_deploy_scripts.py`
- `scripts/production-split-postgres-cutover.sh`
- `.trellis/spec/backend/database-guidelines.md`
- `README.md`
- `deploy/README.md`
- `docs/architecture/postgres-migration-runbook.md`
- `docs/implementation/runtime-cleanup-task-audit.md`

### 本轮补充：Legacy runtime deprecation 决策

已落地：

- 新增 `docs/architecture/legacy-runtime-deprecation-decision.md`。
- 决定不在远程生产 cutover 前直接删除 `InterviewerRuntimeService`；生产仍是旧 combined service + SQLite，直接删除会放大回滚风险。
- legacy 只保留为显式 `AGENT_RUNTIME=legacy` 或显式 `AGENT_RUNTIME_FAIL_OPEN_TO_LEGACY=true` fallback。
- legacy 冻结为一个发布周期的回滚开关：不得新增产品能力、提示词策略、材料理解策略、Case Board 语义或 user-facing writer 行为。
- 删除窗口前必须完成 production split Compose + Postgres cutover、replay、focused tests、focused live smoke、Docker/Postgres smoke、生产 health/version/public smoke 和 `release-preflight`。
- `release-preflight` 已新增 `legacy_deprecation_decision` 文档门禁，防止后续删除或冻结 legacy 时缺少正式决策记录。

关键文件：

- `docs/architecture/legacy-runtime-deprecation-decision.md`
- `.trellis/spec/backend/interviewer-runtime-contracts.md`
- `docs/architecture/agent-runtime-cutover-plan.md`
- `app/cli/main.py`
- `tests/unit/test_cli_main.py`

### 本轮补充：原始 A-L 清单审计、角色口径和文档合同收口

已落地：

- 新增 `docs/implementation/runtime-cleanup-task-audit.md`，把最初 A-L 任务逐项映射到当前代码证据、完成状态和剩余可执行清单。
- 前端 transcript role 从 `officer` 收敛为 `assistant | user | system`；旧本地 history 中的 `officer` 只在恢复时兼容归一化为 `assistant`，UI 仍展示为“签证官”。
- `README.md` 与 `docs/API.md` 移除用户文档里的“薄弱证明点”旧口径，统一为“待核实事实 / 证据冲突 / 补强证据”语义。
- `debug-material-bundle` 合同修正过期 graph refresh 约束：当前 `native_interviewer`、`graph` 兼容别名、`graph_canary`、`graph_shadow` 的公开 material refresh 都走 native interviewer；真实 LangGraph public promotion 后才允许 `graph` 走 `GraphRuntimeAdapter.run_material_change(...)`。

关键文件：

- `docs/implementation/runtime-cleanup-task-audit.md`
- `web/lib/api/types.ts`
- `web/lib/message-source-policy.ts`
- `web/hooks/use-session-workbench.ts`
- `web/tests/message-source-contract.test.mjs`
- `.trellis/spec/frontend/state-management.md`
- `.trellis/spec/backend/debug-material-bundle-contracts.md`
- `.trellis/spec/backend/multimodal-upload-contracts.md`
- `README.md`
- `docs/API.md`

### T1/T2：Runtime fallback 与执行元数据收口

已落地：

- `AGENT_RUNTIME` 默认改为 `native_interviewer`。
- `AGENT_RUNTIME=graph` / `graph_canary` 当前作为兼容标签映射到 native interviewer 公开主链路。
- `AGENT_RUNTIME_FAIL_OPEN_TO_LEGACY` 默认改为 `false`。
- 新增 `runtime_execution` 合同，贯通：
  - `POST /v1/sessions/{session_id}/messages`
  - `/messages/stream` final payload
  - OpenAI-compatible metadata
  - assistant turn metadata
  - `record.interviewer_state_json`
  - `last_material_refresh`
  - runtime trace endpoint
  - runtime debug snapshot
- fallback 显式开启时，`runtime_execution.public_runtime="legacy"` 且记录 `fallback_runtime/error_type/error_message`。

关键文件：

- `app/services/message_service.py`
- `app/services/runtime_debug_snapshot_service.py`
- `app/api/routers/openai_compat.py`
- `app/api/routers/sessions.py`
- `app/core/settings.py`
- `.env.example`
- `docker-compose.yml`

### T16 补充：Legacy runtime freeze 与 graph_shadow 收口

已落地：

- `AGENT_RUNTIME=graph_shadow` 不再把 legacy runtime 作为公开 writer。
- 普通消息与 material refresh 的公开结果均来自 `NativeInterviewerRuntimeService`。
- `GraphRuntimeAdapter` 在 `graph_shadow` 下只作为 shadow/eval trace 来源，shadow 成功或失败都不能替换、回滚或阻断 native public response。
- legacy runtime 只剩两类入口：
  - 显式 `AGENT_RUNTIME=legacy`。
  - native/graph-compatible public runtime 失败，且 `AGENT_RUNTIME_FAIL_OPEN_TO_LEGACY=true` 时的显式 fail-open fallback。
- `runtime_execution` 在 `graph_shadow` 下固定暴露：
  - `configured_runtime="graph_shadow"`
  - `requested_public_runtime="native_interviewer"`
  - `public_runtime="native_interviewer"`
  - `execution_runtime="native_interviewer_runtime"`
  - `shadow_runtime="graph_shadow"`
  - `compatibility_runtime_label="graph_shadow"`
- shadow failure 只写 `graph_shadow.status="error"`，不会再因为 fail-open 关闭而把用户消息整体变成失败。

关键文件：

- `app/services/message_service.py`
- `.trellis/spec/backend/interviewer-runtime-contracts.md`
- `docs/architecture/agent-runtime-cutover-plan.md`
- `tests/integration/test_messages_api.py`
- `tests/integration/test_sessions_api.py`

### T4：前端消息来源测试

已落地：

- 新增 `web/lib/message-source-policy.ts`。
- SSE `accepted/analyzing/debug_event/final` 不直接产生聊天主线消息。
- assistant 消息只从最终后端 `MessageResponse.assistant_message` 转换，前端不再使用 `officer` 作为 transcript role。
- 空 assistant text 不生成空白 assistant 气泡。
- mock 模式不再随机造签证官追问，只记录 activity event。

关键文件：

- `web/lib/message-source-policy.ts`
- `web/tests/message-source-contract.test.mjs`
- `web/hooks/use-session-workbench.ts`
- `web/package.json`

### T5：真实用户成功路径 replay

已落地：

- 新增 `complete_interview_success_path.json`。
- 新增 evaluator 检查 `success_path_review`。
- 必跑 replay 清单从 12 个 fixture 增至 13 个。
- replay 覆盖：
  - 多轮用户回答
  - 上传并解析材料
  - Case Memory claims/evidence/proof point
  - funding source 冲突与 resolution
  - Case Board next move
  - post-interview review summary / next steps

关键文件：

- `app/evals/graph_replay_eval.py`
- `fixtures/graph_replay/complete_interview_success_path.json`
- `tests/unit/test_graph_replay_eval.py`
- `tests/unit/test_cli_main.py`
- `docs/architecture/replay-eval-spec.md`

### T3：Gate 残余职责审计

已落地：

- `GateRuntimeService.build_gate_response()` 不再对用户展示“材料门控阶段”，只在 `family_not_selected` 时要求先选择签证家族。
- `GateRuntimeService.build_gate_support()` 的辅助文案从“等待解析/最缺材料/仍待补”改为“案例理解正在更新，可以继续对话”和“可补强材料”。
- debug fill 的 normal 场景标签不再表达“补齐一套材料”，改为当前缺口参考材料。
- 新增集成测试证明 `AGENT_RUNTIME=native_interviewer` 下，`pending_documents` 与 `waiting_for_parse` 都会继续进入 native interviewer，不会落回 Gate 或 legacy runtime。
- 旧 Gate 阻断语义扫描确认剩余命中只存在于 forbidden marker、负例 fixture 或合同文档说明里。

关键文件：

- `app/services/gate_runtime_service.py`
- `app/services/debug_fill_service.py`
- `tests/integration/test_messages_api.py`
- `tests/unit/test_gate_runtime_service.py`

### T6/T7：Case Memory / Evidence Graph 查询层切片

已落地：

- `CaseMemoryService.get_snapshot()` 读取 `case_memory_snapshots` 一等投影，不需要重新扫描 document artifact。
- `CaseMemoryService.get_or_build_snapshot()` 在没有投影时才回退重建，并保持旧数据兼容。
- `CaseMemoryService.query_evidence_graph()` 支持按 `field_path` 查询 claims、evidence cards、proof points、conflicts 和确定性 edges。
- `GraphCaseStateBuilder.build()` 支持显式接收 `case_memory_snapshot` 与 `evidence_graph`。
- `NativeInterviewerRuntimeService` 与 `GraphRuntimeAdapter` 在构建 case state 时显式注入 Case Memory 快照和 Evidence Graph 查询结果。
- OpenAI-compatible metadata 直接暴露 `case_board` 与 `evidence_graph`，外部消费者不需要从旧 artifact 或 gate 状态推断事实。
- Runtime debug snapshot 直接暴露 `case_board` 与 `evidence_graph`，前端调试台显示 claims/evidence/proof/conflict/edges 摘要。
- 单测证明 Case Board 优先读取持久化快照，Evidence Graph 可按字段过滤，runtime case state builder 不再必须从 stale artifact 推断事实。
- runtime 注入一等 Case Memory / Evidence Graph 前会经过 public-safe 脱敏，防止 debug bundle 的 `synthetic_bundle_id`、scenario label、expected oracle 泄露到 prompt。
- legacy `InterviewRuntimeService._build_dynamic_turn_context()` 也注入 public-safe `case_board` / `evidence_graph`，`DS160MemoryManager` 的 evidence memory 暴露 Case Board 计数和 Evidence Graph edge 计数。
- `CapabilityOrchestrator` 的 document review fallback 可直接消费 Case Board conflicts/proof points；即使没有旧 document artifact context，也能从 Case Memory 生成高风险冲突复核结论。
- public-safe 投影只移除 debug oracle / scenario metadata，不再删除空数组和 `null` 字段，避免 Case Board / Evidence Graph API shape 漂移。
- `/reports/export` 和 post-interview review context 的 `documents[].artifact` 统一走 public-safe artifact 脱敏，不再导出 `expected_findings`、debug bundle id、scenario label 等测试 oracle。
- `InterviewReviewService` 生成复盘上下文时显式传入 public Case Board，复盘的 user/internal report 不再因为没传 `case_board` 而退回 profile/gate 事实源。
- `GraphCaseStateBuilder` fallback 脱敏测试补强 `expected_findings` 覆盖，确认 document artifact、chunk metadata、evidence metadata、case memory snapshot 和 evidence graph 都不会携带 debug oracle 进入 graph case state。
- 前端 Analysis Panel 新增 Case Board presentation policy：有 `report.case_board` 时优先展示后端一等 Case Board facts/evidence/conflicts/next move，只有 Case Board 为空时才回退本地材料列表，避免刷新或历史恢复后重新显示 0 个事实。
- 前端材料 fallback 明确遵守 `uploadedMaterials` 新材料在前的顺序，避免 Case Board 为空时把旧材料误判为最近材料。
- 前端上传-only activity 摘要改为读取 `case_board_delta.evidence_cards` 优先，避免后端只返回 Case Board delta 时漏报证据片段数量。
- `/files` 上传响应新增 `case_board_refresh` 合同，显式携带
  `message_policy="case_board_timeline_only"`、材料理解状态和 debug timeline
  scope；前端映射为 `caseBoardRefresh`，用于材料库 / activity / debug
  timeline，不能生成 assistant 或 system 对话气泡。
- replay fixture 中 `case_board.next_move` 引用的 claim/evidence 现在必须能在同一个 Case Board 投影内解析，避免测试继续依赖 `case_memory` fallback 却让前端主面板显示 0 个事实/证据。
- graph mapper、native runtime 与 report service 共享 Case Board missing-evidence 投影：只要 Case Board 已有 claims/evidence/proof/conflict 状态，`missing_evidence` 就从 unresolved proof points 派生，旧 score/interviewer missing 字段只能在没有 Case Board 状态时 fallback。
- 前端 mock/demo 数据改为 Case Board-first：mock transcript 不再包含 system checklist，mock report 带可解析的 claims/evidence/proof/next_move，兼容字段不再把 `requested_documents=1` 当主路径。
- 前端 legacy 兼容字段的用户可见文案不再使用“关键证明/缺少关键证明材料”口径，统一收敛为待核实事实与补强证据，避免旧字段把体验拉回材料清单工具。
- 报告弹窗继续收口旧证明缺口口径：`关键问题` 改为 `当前问题`，`关键证明` / `薄弱证明点` 改为 `待核实点` / `待核实事实`，并把这些文案加入前端 Case Board presentation 合同测试的 forbidden scan。
- 面签复盘 fallback 文案继续收口：默认 improvement plan 不再说“薄弱证明点”，改为围绕“待核实事实”补强回答和证据，并新增单元测试防止旧证明缺口文案回流。
- 后端报告、Gate 支持文案、上传反馈、graph fallback 和 interviewer prompt 同步移除“关键证明/待证明点/上传对应证据”式口径；生产代码只保留“待核实事实/补强证据”语义，避免模型或 fallback 再把体验拉回材料 checklist。
- 前端 Case Board 合同同步更新：`report-modal` 与 Case Board presentation test 纳入 scope，中文 forbidden copy 明确列出 `关键证明`、`缺少关键证明材料`、`薄弱证明点`、`请准备以下材料`、`材料齐套`，并要求 `pnpm test:case-board-presentation` 扫描用户可见源文件。
- 前端状态管理合同同步更新：历史状态 badge 的 `need_more_evidence` 展示从“需补证”改为“待核实”，避免历史侧栏把会话状态重新解释成补材料。
- `RuntimeViewContractService` 冻结 anchored runtime view 的 `requested_documents` / `remaining_required_documents` 语义：一旦 runtime view 锚定到具体 assistant turn，空数组就是明确结果，不再被旧 response fallback 反向污染 Chat Completions、Responses 或 message response。
- `RuntimeLedgerService` 同步冻结 `turn_record` 的空数组语义：旧 assistant turn 明确写入 `requested_documents=[]` 或 `remaining_required_documents=[]` 时，不再从 `focus.document_type` 或 requested fallback 反向补回旧材料缺口，避免 reports/read model/debug view 重新显示 checklist 状态。
- `CapabilityOrchestrator` 的 document review fallback 不再用 `gate_progress.required_documents` 覆盖明确为空的 `evidence_digest.remaining_required_documents`；证据摘要字段存在时就是主事实源，Gate missing list 只能在没有 evidence digest 合同时作为旧 fallback。
- `ReportService.user_report()` 的 `missing_evidence` 同步尊重显式空 `requested_documents` / `remaining_required_documents`，不再从 `current_key_proof` 或 `current_focus.document_type` 反向补成旧材料缺口。
- 非 anchored `RuntimeViewContractService`、material refresh response、Gate turn record 和 debug fill synthetic 入口同步尊重显式空 `requested_documents` / `remaining_required_documents`；只有字段缺失时才允许从 runtime/interviewer stale state 做兼容 fallback。
- interviewer prompt、document review fallback 和 native high-risk fallback 的中文口径继续从“材料核验/待补清单/关键材料”收敛到“证据核验/待核实事实/补强证据”，减少模型回到材料清单问法的概率。
- OpenAI Responses metadata 与 Chat Completions 对齐，直接暴露 public-safe `case_board` 与 `evidence_graph`，外部兼容入口不再只有 runtime view 而缺少 Case Memory 投影。
- Case Board 状态判断补齐 `latest_material` / `next_move`：当报告层已拿到材料理解状态或下一问建议时，不再因为没有 claims/proof/conflict 而回退旧 `requested_documents` / `current_focus.document_type`。
- `case_memory_snapshots` 现在持久化 `latest_material` 与 `conflict_resolutions`；报告、debug、OpenAI-compatible 和 graph case state 可以从 Case Memory read model 读取最新材料状态和已解决冲突，不需要各自回扫 document artifact 或 session state。
- Graph runtime deterministic fallback 兼容 `case_board.open_proof_points`：上传 delta 里的待核实事实可以直接驱动下一问，不再退回泛化的材料状态追问。
- File upload compatibility response 的 `case_board_delta.next_move.reason` 移除“材料齐套”口径，改为“当前可以继续面签对话”，避免上传反馈把用户带回材料清单框架。

关键文件：

- `app/services/case_memory_service.py`
- `app/services/ds160_memory_manager.py`
- `app/services/ds160_context_engine.py`
- `app/services/interview_runtime_service.py`
- `app/services/capability_orchestrator.py`
- `app/services/interview_review_service.py`
- `app/services/graph_case_state_builder.py`
- `app/services/native_interviewer_runtime_service.py`
- `app/services/graph_runtime_adapter.py`
- `app/services/graph_adjudication_node.py`
- `app/services/graph_response_mapper.py`
- `app/services/case_board_projection.py`
- `app/services/file_service.py`
- `app/services/gate_runtime_service.py`
- `app/interviewer_prompts/base.yaml`
- `docs/API.md`
- `app/api/routers/openai_compat.py`
- `app/api/routers/reports.py`
- `app/services/runtime_debug_snapshot_service.py`
- `app/services/runtime_view_contract_service.py`
- `app/services/debug_fill_service.py`
- `web/components/ds160/runtime-debug-panel.tsx`
- `web/components/ds160/analysis-panel.tsx`
- `web/components/ds160/report-modal.tsx`
- `web/lib/case-board-presentation-policy.ts`
- `web/lib/upload-feedback-policy.ts`
- `web/hooks/use-session-workbench.ts`
- `web/lib/api/mappers.ts`
- `web/lib/api/mock-data.ts`
- `web/lib/api/types.ts`
- `app/evals/graph_replay_eval.py`
- `fixtures/graph_replay/visual_i20_updates_case_memory.json`
- `fixtures/graph_replay/complete_interview_success_path.json`
- `tests/unit/test_case_memory_service.py`
- `tests/unit/test_docker_compose_contract.py`
- `tests/unit/test_interview_runtime_service.py`
- `tests/unit/test_capability_orchestrator.py`
- `tests/unit/test_graph_case_state_builder.py`
- `tests/unit/test_graph_replay_eval.py`
- `tests/unit/test_graph_response_mapper.py`
- `tests/unit/test_native_interviewer_runtime_service.py`
- `tests/unit/test_runtime_view_contract_service.py`
- `tests/unit/test_message_service_material_refresh.py`
- `tests/unit/test_debug_fill_service.py`
- `tests/integration/test_openai_compat.py`
- `tests/integration/test_openai_responses.py`
- `tests/integration/test_debug_material_bundles_api.py`
- `tests/integration/test_reports_api.py`
- `web/tests/case-board-presentation-contract.test.mjs`
- `web/tests/upload-feedback-contract.test.mjs`
- `docs/architecture/ai-native-case-understanding-spec.md`

### T8/T9：上传理解链路失败可见性切片

已落地：

- 新上传只 enqueue `case_understanding` job；`gate_parse` 历史 worker fallback 已在确认远程旧队列为空后删除。
- `ParseWorker` 在 parse/pipeline 阶段失败时，不再只把 job 标为 failed。
- parse 失败会写入 failed `MaterialUnderstandingJob`，同步更新 document artifact、`case_board_delta` 和 `case_memory_snapshots`。
- 用户/debug 面板可以通过 `understanding_status="failed"`、`understanding_error.code="parse_failed"` 和 latest material unknown 定位失败节点。
- Runtime debug snapshot 新增 `material_understanding` 摘要，并把材料理解错误并入 `errors`。
- Runtime debug snapshot 新增稳定 `timeline` 数组，把材料生成、材料理解、
  runtime trace、material refresh 和 error 统一投影成
  `phase/step/status/summary/payload` 结构；前端调试台的“时间线”同时展示
  live SSE events 与 snapshot timeline，用户不需要展开 raw JSON 才能定位节点。
- 前端上传 activity、材料库、分析侧栏和 runtime debug panel 都能展示材料理解失败，且不把失败状态写进聊天 transcript。
- 前端会在上传返回 `queued/processing` 后短轮询 runtime debug snapshot；异步 worker 后续把材料理解标记为 failed/completed 时，材料库本地卡片会同步回填状态、错误原因和 `case_board_delta.latest_material`，避免卡片标题仍停在 queued。
- 前端上传反馈策略优先读取 `caseBoardRefresh.understandingStatus` /
  `caseBoardRefresh.failureMessage`，把 queued/failed 状态写入
  `activityEvents` 和材料库，不再依赖聊天主线承载上传状态。
- 本地 production 浏览器 smoke 覆盖两类失败：
  - `.txt` 非支持类型经 UI 触发真实 `/files` 415，错误进入 activity 和失败材料详情，不生成 assistant/system 伪消息。
  - 损坏 PDF 先 202 入队，再由 worker 解析失败；材料库显示“理解失败”，runtime debug panel 显示 `material_understanding.failed`、`parse_failed` 和 errors JSON。
- 回归测试覆盖正常 parse、document pipeline 保留 assessment、parse 失败可见性、debug snapshot 失败摘要和前端 upload feedback 合同。

关键文件：

- `app/workers/parse_worker.py`
- `app/services/runtime_debug_snapshot_service.py`
- `web/lib/upload-feedback-policy.ts`
- `web/hooks/use-session-workbench.ts`
- `web/components/ds160/materials-panel.tsx`
- `web/components/ds160/analysis-panel.tsx`
- `web/components/ds160/runtime-debug-panel.tsx`
- `tests/integration/test_parse_worker.py`
- `tests/integration/test_debug_material_bundles_api.py`
- `web/tests/upload-feedback-contract.test.mjs`
- `docs/architecture/ai-native-case-understanding-spec.md`

### T10/T11/T12/T13：运行可靠性切片

已落地：

- Docker Compose 生产默认数据库为 Postgres，`ds160-api` 与 `ds160-worker` 依赖 `postgres` healthy 后启动。
- 默认 Compose 已拆成 `ds160-api`、`ds160-web`、`ds160-worker`、`postgres`、`nginx`；旧 `ds160-agent2` 只保留在 `combined` profile 里作为兼容模式。
- `ds160-api`、`ds160-web`、`ds160-worker` 分别有健康检查，nginx 等待 API/Web healthy，避免只看容器进程存活。
- 非 SQLite engine 启用 `pool_pre_ping=True`，降低 Postgres 长连接失效后的复用风险。
- SQLite 本地开发继续启用 WAL、`busy_timeout` 和 `synchronous=NORMAL`，并通过 `.gitignore` 忽略 WAL/SHM。
- `.gitignore` 同步忽略 `deploy/certs/`，避免 nginx origin cert/key 被误提交；nginx smoke 需要本地或正式 origin certs 存在后再执行。
- `/livez` 只表示 API 进程存活；`/healthz` 改为分层状态，包含 app、database、LLM configured、worker 状态。
- `/healthz` 在 database error、worker enabled 但未启动、worker stopped 等关键
  readiness 降级时返回 HTTP 503，Docker healthcheck / `curl -fsS` 不会再把
  degraded 状态误判为 healthy。
- message SSE 和 debug material bundle SSE 在进入长流式生命周期前释放入口依赖 session，worker thread 使用独立 DB session。
- 新增标准库 JSON log formatter，保留 `session_id`、`run_id`、`turn_id`、`document_id` 等串联字段，并对常见 secret key 脱敏。
- JSON log formatter 现在会同步覆盖 `uvicorn` / `uvicorn.error` / `uvicorn.access` 既有 handler，并过滤 Uvicorn `color_message` ANSI 字段，避免 Docker 日志里 app access log 仍是纯文本。
- Postgres 迁移 runbook 补充分层健康检查、JSON log 和 compose smoke 检查。

关键文件：

- `app/db/session.py`
- `app/core/health.py`
- `app/core/logging_config.py`
- `app/main.py`
- `app/api/routers/messages.py`
- `app/api/routers/sessions.py`
- `docker-compose.yml`
- `docker/start.sh`
- `deploy/nginx/ds160.conf`
- `.env.example`
- `docs/architecture/postgres-migration-runbook.md`
- `.trellis/spec/backend/database-guidelines.md`
- `.trellis/spec/backend/logging-guidelines.md`
- `tests/unit/test_db_session.py`
- `tests/unit/test_health.py`
- `tests/unit/test_logging_config.py`
- `tests/integration/live/test_infrastructure.py`

### T16：Legacy runtime freeze preflight

已落地：

- 新增 `uv run python -m app.cli.main release-preflight`。
- preflight 将 legacy freeze / deletion 前的必跑项结构化为 JSON：
  - graph replay corpus
  - focused non-live runtime tests
  - live LLM smoke
  - Docker Compose + Postgres + `/healthz` / `/livez` smoke
  - rollback runbook
  - implementation report
- Docker 检查不只看 PATH；会依次探测 `docker` / `docker.exe` 候选 CLI，实际执行 `--version`，再执行 `docker compose config --quiet` 和 `docker info`，避免把“WSL shim 不可用”“compose 配置错误”“daemon 未启动”混成同一个失败。
- preflight 支持用 `--replay-corpus-passed`、`--focused-tests-passed`、`--live-smoke-passed`、`--docker-smoke-passed` 显式标记同一发布窗口已完成的证据。
- preflight 会读取当前进程环境和本地 `.env` 的 key presence，但只输出布尔值，不输出 `OPENAI_API_KEY` / `OPENAI_BASE_URL` 的真实值。
- `live_llm_smoke` 门禁已收敛为 focused smoke：基础模型配置、OpenAI-compatible model factory、model config API、Extractor live、Scoring live。全量 live conversation / OpenAI API 套件仍可作为更重的发布候选回归单独执行。
- 当前环境已完成 Docker/Postgres smoke：WSL `docker` shim 不可用，但 `docker.exe` 可用；启动 Docker Desktop 后，`docker.exe info` 正常返回 server version，`docker.exe compose config --quiet`、`docker.exe compose up -d postgres ds160-api ds160-web ds160-worker`、API 容器内 `/healthz` / `/livez` / `/version` 均通过，`/healthz` 确认数据库 dialect 为 `postgresql`。

本轮 live smoke 修复：

- `ExtractorService` 新增用户消息级确定性护栏：当当前消息明确表达 funding source 未决定时，跳过模型对 `/funding/primary_source` 的误抽取，并保持 `unknown`，同时保留同轮其他字段更新。
- `ScoringService` / `ScoreStateBuilder` 新增 funding gap 稳定化：当输入 findings 指向资金证明缺口，且模型 risk flag 已承认 funding undocumented / missing / unproven 时，确定性补齐 `funding_proof` missing evidence，并把 document readiness / narrative consistency 收到 fallback 上限。
- 修复后 focused live smoke 从 extractor unknown failure 和 scoring missing evidence failure 收敛为全绿。

关键文件：

- `app/cli/main.py`
- `app/services/extractor_service.py`
- `app/services/score_state_builder.py`
- `tests/unit/test_cli_main.py`
- `tests/unit/test_extractor_service.py`
- `tests/unit/test_scoring_service.py`
- `README.md`
- `docs/architecture/agent-runtime-cutover-plan.md`

### 合同与文档同步

已同步：

- `.trellis/spec/backend/interviewer-runtime-contracts.md`
- `docs/architecture/agent-runtime-cutover-plan.md`
- `docs/API.md`
- `docs/architecture/replay-eval-spec.md`
- `docs/architecture/ai-native-case-understanding-spec.md`
- `.trellis/spec/frontend/state-management.md`
- `.trellis/spec/frontend/case-board-contracts.md`
- `.trellis/spec/backend/database-guidelines.md`
- `.trellis/spec/backend/logging-guidelines.md`
- `.trellis/spec/backend/interviewer-runtime-contracts.md`
- `docs/architecture/postgres-migration-runbook.md`

## 验证结果

已通过：

```bash
uv run python -m compileall app
uv run pytest -q tests/integration/test_messages_api.py tests/integration/test_openai_compat.py tests/integration/test_sessions_api.py tests/integration/test_debug_material_bundles_api.py tests/integration/test_parse_worker.py tests/unit/test_graph_replay_eval.py tests/unit/test_health.py -m "not live_llm"
uv run pytest -q -m "not live_llm"
pnpm --dir web test:message-source
pnpm --dir web test:upload-feedback
pnpm --dir web type-check
pnpm --dir web lint
pnpm --dir web build
git diff --check
uv run python - <<'PY'
from pathlib import Path
import yaml
payload = yaml.safe_load(Path('docker-compose.yml').read_text())
api = payload['services']['ds160-api']
web = payload['services']['ds160-web']
worker = payload['services']['ds160-worker']
combined = payload['services']['ds160-agent2']
nginx = payload['services']['nginx']
assert api['depends_on']['postgres']['condition'] == 'service_healthy'
assert worker['depends_on']['postgres']['condition'] == 'service_healthy'
assert worker['depends_on']['ds160-api']['condition'] == 'service_healthy'
assert api['environment']['DS160_PROCESS'] == 'api'
assert web['environment']['DS160_PROCESS'] == 'web'
assert worker['environment']['DS160_PROCESS'] == 'worker'
assert api['environment']['DATABASE_URL'].startswith('${COMPOSE_DATABASE_URL:-postgresql+psycopg://')
assert api['environment']['LOG_FORMAT'] == '${LOG_FORMAT:-json}'
assert api['healthcheck']['test'][0] == 'CMD-SHELL'
assert nginx['depends_on']['ds160-api']['condition'] == 'service_healthy'
assert nginx['depends_on']['ds160-web']['condition'] == 'service_healthy'
assert combined['profiles'] == ['combined']
assert 'ds160-agent2-postgres' in payload['volumes']
print('compose static checks passed')
PY
```

本轮新增/补充回归：

```bash
uv run pytest -q tests/integration/test_messages_api.py::test_message_turn_keeps_family_selection_gate_before_interview_runtime tests/integration/test_messages_api.py::test_native_interviewer_runs_when_gate_is_pending_or_waiting_for_parse tests/unit/test_gate_runtime_service.py -m "not live_llm"
rg -n "当前处于材料门控阶段|系统正在等待解析结果|补齐一套|补齐材料|材料齐了才|等待解析完成" app web tests docs .trellis/spec --glob '!docs/superpowers/**'
uv run pytest -q tests/unit/test_case_memory_service.py tests/unit/test_graph_case_state_builder.py tests/unit/test_native_interviewer_runtime_service.py tests/unit/test_graph_adjudication_node.py tests/integration/test_debug_material_bundles_api.py -m "not live_llm"
uv run pytest -q tests/integration/test_parse_worker.py tests/unit/test_file_service.py tests/unit/test_document_pipeline.py -m "not live_llm"
uv run pytest -q tests/integration/test_debug_material_bundles_api.py::test_runtime_debug_snapshot_includes_material_understanding_failures -m "not live_llm"
pnpm --dir web test:upload-feedback
pnpm --dir web test:case-board-presentation
uv run pytest -q tests/unit/test_graph_replay_eval.py -m "not live_llm"
uv run pytest -q tests/unit/test_cli_main.py -m "not live_llm"
uv run pytest -q tests/unit/test_graph_response_mapper.py -m "not live_llm"
uv run pytest -q tests/unit/test_native_interviewer_runtime_service.py -m "not live_llm"
uv run pytest -q tests/unit/test_report_service.py -m "not live_llm"
uv run pytest -q tests/unit/test_graph_runtime_adapter.py tests/unit/test_graph_replay_eval.py tests/unit/test_report_service.py tests/unit/test_native_interviewer_runtime_service.py tests/unit/test_graph_response_mapper.py -m "not live_llm"
uv run pytest -q tests/integration/test_messages_api.py::test_message_turn_native_interviewer_runtime_reports_native_label tests/integration/test_messages_api.py::test_message_turn_graph_canary_hundred_percent_uses_native_compat_alias tests/integration/test_messages_api.py::test_native_interviewer_runs_when_gate_is_pending_or_waiting_for_parse -m "not live_llm"
uv run pytest -q tests/unit/test_runtime_view_contract_service.py -m "not live_llm"
uv run pytest -q tests/integration/test_openai_compat.py -m "not live_llm"
uv run pytest -q tests/integration/test_openai_responses.py -m "not live_llm"
uv run pytest -q tests/unit/test_db_session.py tests/unit/test_health.py tests/unit/test_logging_config.py tests/unit/test_env_example.py tests/integration/test_simple_auth.py tests/integration/test_messages_api.py::test_messages_stream_allows_default_model_without_user_streaming_switch tests/integration/test_messages_api.py::test_messages_stream_emits_final_payload_contract tests/integration/test_messages_api.py::test_messages_stream_graph_mode_keeps_sse_contract tests/integration/test_debug_material_bundles_api.py::test_debug_material_bundle_stream_emits_progress_and_final tests/integration/test_debug_material_bundles_api.py::test_debug_material_bundle_stream_returns_error_when_seeded_ai_generation_fails -m "not live_llm"
uv run pytest -q tests/integration/test_openai_compat.py tests/integration/test_debug_material_bundles_api.py tests/integration/test_reports_api.py tests/unit/test_report_service.py tests/unit/test_case_memory_service.py -m "not live_llm"
uv run pytest -q tests/unit/test_interview_runtime_service.py tests/unit/test_ds160_memory_manager.py tests/unit/test_capability_orchestrator.py tests/unit/test_case_memory_service.py -m "not live_llm"
uv run pytest -q tests/integration/test_reports_api.py tests/unit/test_report_service.py tests/unit/test_case_memory_service.py -m "not live_llm"
uv run pytest -q tests/unit/test_graph_case_state_builder.py -m "not live_llm"
uv run pytest -q tests/unit/test_runtime_view_contract_service.py tests/unit/test_message_service_material_refresh.py -m "not live_llm"
uv run pytest -q tests/unit/test_debug_fill_service.py tests/unit/test_runtime_view_contract_service.py tests/unit/test_message_service_material_refresh.py -m "not live_llm"
uv run pytest -q tests/integration/test_sessions_api.py -m "not live_llm"
uv run pytest -q tests/integration/test_debug_material_bundles_api.py tests/integration/test_parse_worker.py -m "not live_llm"
uv run pytest -q tests/unit/test_runtime_ledger_service.py tests/unit/test_report_service.py -m "not live_llm"
uv run pytest -q tests/integration/test_openai_compat.py tests/integration/test_openai_responses.py -m "not live_llm"
uv run python -m compileall app/services/debug_fill_service.py app/services/message_service.py app/services/runtime_view_contract_service.py
uv run pytest -q tests/unit/test_cli_main.py -m "not live_llm"
uv run python -m app.cli.main release-preflight
uv run python -m app.cli.main release-preflight --replay-corpus-passed --focused-tests-passed
uv run python -m compileall app/cli/main.py
uv run python -m app.cli.main eval-graph-corpus --fixture-dir fixtures/graph_replay
uv run pytest -q tests/integration/test_messages_api.py tests/integration/test_openai_compat.py tests/integration/test_sessions_api.py tests/integration/test_debug_material_bundles_api.py tests/integration/test_parse_worker.py tests/unit/test_graph_replay_eval.py tests/unit/test_health.py -m "not live_llm"
uv run pytest -q tests/unit/test_extractor_service.py -m "not live_llm"
set -a; source .env; set +a; RUN_LIVE_LLM_TESTS=1 uv run pytest tests/integration/live/test_live_extractor_service.py -q -m live_llm -vv --maxfail=1
uv run pytest -q tests/unit/test_cli_main.py -m "not live_llm"
uv run python -m app.cli.main release-preflight --replay-corpus-passed --focused-tests-passed
uv run pytest -q tests/unit/test_scoring_service.py tests/integration/test_tool_based_scoring.py -m "not live_llm"
set -a; source .env; set +a; RUN_LIVE_LLM_TESTS=1 uv run pytest tests/integration/live/test_live_scoring_service.py -q -m live_llm -vv --maxfail=1
set -a; source .env; set +a; RUN_LIVE_LLM_TESTS=1 uv run pytest tests/integration/live/test_infrastructure.py tests/integration/live/test_live_llm_client.py tests/integration/live/test_live_model_config_api.py tests/integration/live/test_live_extractor_service.py tests/integration/live/test_live_scoring_service.py -q -m live_llm -vv --maxfail=1
uv run pytest -q tests/unit/test_extractor_service.py tests/unit/test_scoring_service.py tests/unit/test_cli_main.py tests/integration/test_tool_based_scoring.py tests/integration/test_messages_api.py tests/integration/test_openai_compat.py tests/integration/test_sessions_api.py tests/integration/test_debug_material_bundles_api.py tests/integration/test_parse_worker.py tests/unit/test_graph_replay_eval.py tests/unit/test_health.py -m "not live_llm"
uv run python -m app.cli.main release-preflight --replay-corpus-passed --focused-tests-passed --live-smoke-passed
uv run pytest -q tests/unit/test_report_service.py tests/unit/test_graph_runtime_adapter.py -m "not live_llm"
uv run pytest -q tests/integration/test_reports_api.py tests/unit/test_report_service.py tests/unit/test_graph_runtime_adapter.py -m "not live_llm"
! rg -n "无需等待材料齐套|材料齐套" app tests web docs --glob '!docs/superpowers/**'
uv run pytest -q tests/unit/test_file_service.py tests/integration/test_files_api.py -m "not live_llm"
git diff --check
'/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' compose config --quiet
'/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' compose up -d postgres ds160-api ds160-web ds160-worker
'/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' compose exec -T ds160-api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=5).read().decode())"
'/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' compose exec -T ds160-api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).read().decode())"
'/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' compose exec -T ds160-api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/version', timeout=5).read().decode())"
uv run python -m app.cli.main release-preflight --replay-corpus-passed --focused-tests-passed --live-smoke-passed --docker-smoke-passed
'/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' cp app.sqlite3 ds160-api:/tmp/app.sqlite3
'/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' compose exec -T ds160-api /app/.venv/bin/python -m app.cli.main migrate-sqlite-to-postgres --source-url sqlite:////tmp/app.sqlite3 --target-url postgresql+psycopg://ds160:ds160@postgres:5432/ds160 --dry-run
'/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' compose exec -T ds160-api rm -f /tmp/app.sqlite3
```

结果：

- T6/T7 追加回归：`37 passed`
- OpenAI-compatible / debug material bundle / report / Case Memory API 回归：`61 passed`
- report export / post-interview review context / Case Memory 回归：`26 passed`
- GraphCaseStateBuilder fallback 脱敏回归：`12 passed`
- 前端 Case Board presentation 合同测试：`7 passed`
- Graph replay evaluator / CLI replay 合同：`10 passed` / `5 passed`
- Graph/native/report Case Board missing-evidence 投影回归：`6 passed` / `4 passed` / `9 passed`，组合回归 `34 passed`
- native/graph-compatible messages 集成抽样：`3 passed`
- RuntimeViewContractService anchored legacy 字段冻结单测：`2 passed`
- RuntimeLedgerService turn_record 空数组冻结单测：`6 passed`
- RuntimeLedgerService / SessionReadModel / CapabilityOrchestrator / Native runtime / prompt registry / reports 组合回归：`40 passed`
- InterviewRuntime / memory manager / CapabilityOrchestrator / report / graph mapper 主问答投影回归：`46 passed`
- OpenAI-compatible / OpenAI Responses / RuntimeViewContractService metadata 回归：`29 passed`
- Runtime/read-model/report/OpenAI 兼容字段冻结组合回归：`79 passed`
- OpenAI-compatible metadata 合同回归：`19 passed`
- OpenAI Responses metadata 合同回归：`8 passed`
- legacy “关键证明/待证明点”后端文案收口回归：`45 passed`，上传/动态上下文夹具同步回归：`38 passed`
- 全量非 live pytest：`592 passed, 11 deselected`
- 前端消息来源合同测试：`3 passed`
- 前端上传反馈合同测试：`7 passed`
- 前端 Case Board presentation 合同测试重跑：`7 passed`
- 前端 `type-check`、`lint`、`build` 重跑通过
- 本地 production 浏览器 smoke：临时 SQLite API + `next start` 可进入 F-1 工作台；聊天主线只显示签证官开场，Case Board/材料库/调试台在独立侧栏视图；`ALLOW_RUNTIME_DEBUG=true` 后调试台可渲染 `runtime_view_state`、Case Board、Evidence Graph 和快照 JSON。截图保存于 Playwright 临时输出：`page-2026-05-29T17-37-46-321Z.png`。
- 本地 production 上传失败浏览器 smoke：临时 SQLite API + `next start` 下，上传 `ds160-upload-failure.txt` 触发真实 415，错误显示为 activity 和失败材料详情；上传损坏 `ds160-broken.pdf` 后 worker 抛 `FileDataError`，材料库自动回填“理解失败”，调试台显示 `material_understanding.failed` / `parse_failed`。截图保存于 Playwright 临时输出：`page-2026-05-29T18-12-53-030Z.png`。
- T3 窄范围回归：`12 passed`
- T6/T7 查询层、OpenAI metadata、debug snapshot 和 report 相关回归：`60 passed`
- T6/T7 显式空 requested/remaining 兼容投影回归：`6 passed`，debug fill synthetic / Gate turn record 入口补充回归后组合 `9 passed`
- material refresh / debug bundle / parse worker / report / ledger 相关回归：`20 passed` / `23 passed` / `16 passed`
- OpenAI-compatible / OpenAI Responses metadata 重跑：`27 passed`
- T16 release preflight / logging / compose contract 回归：`20 passed`
- Extractor unknown funding 单元回归：`15 passed`
- Live extractor smoke：`2 passed`
- Scoring funding gap 单元 / tool-based integration 回归：`10 passed`
- Live scoring smoke：`2 passed`
- Focused live LLM smoke：`6 passed, 1 deselected`
- 本轮 focused non-live runtime + extractor/scoring/CLI 回归：`160 passed`
- Case Board latest material / open proof point 残留回归：`17 passed`
- reports API + report service + graph adapter 组合回归：`27 passed`
- 上传 compatibility response 旧口径扫描：无残留命中
- 文件上传相关回归：`24 passed`
- `release-preflight --replay-corpus-passed --focused-tests-passed` 当前输出 `ok=false`：`.env` 中 live LLM 必需 key 已被识别为 present，live smoke 状态为 `pending`，剩余阻断是 live smoke 证据未用 flag 标记和 Docker WSL integration 未启用。
- 本轮已实际重跑 focused live LLM smoke：`6 passed, 1 deselected, 1 warning`。
- `release-preflight --replay-corpus-passed --focused-tests-passed --live-smoke-passed` 曾输出 `ok=false`，精确定位到 Docker daemon 不响应：`docker` shim 报 WSL integration 不可用；`docker.exe --version` 与 `docker.exe compose config --quiet` 通过；`docker.exe info --format {{.ServerVersion}}` 5 秒超时。
- Graph replay corpus：`fixture_count=13`，`passed=true`，所有 fixture matched expectation。
- Focused non-live runtime tests：`125 passed`
- T8/T9 上传理解相关回归：`24 passed`，debug snapshot 失败摘要单测通过
- T10/T13 运行可靠性相关回归：`35 passed`
- Compose 静态校验通过：Postgres 默认 DATABASE_URL、API/Web/worker healthcheck、nginx depends_on API/Web healthy、JSON log env 均存在。
- Windows Docker CLI 的 `docker compose config` 暴露了一个真实问题：本地 `.env`
  中的 `DATABASE_URL=sqlite...` 会覆盖 compose 的 Postgres 默认值。已将
  `docker-compose.yml` 改为读取 `COMPOSE_DATABASE_URL`，再写入容器内
  `DATABASE_URL`，避免本地 SQLite 配置污染生产 compose。
- 启动 Docker Desktop 后，Windows `docker.exe` 已可完成真实 Compose smoke：
  `compose config --quiet` 通过，`postgres`、`ds160-api`、`ds160-web` 与
  `ds160-worker` 均启动成功；API 容器内 `/livez` 返回 `{"status":"ok"}`，
  `/healthz` 返回 `status=ok` 且 `checks.database.dialect="postgresql"`，
  `/version` 返回 `version=0.1.2`。
- Docker 日志中 Uvicorn startup/access log 已改为 JSON 行，`color_message`
  ANSI 字段不再输出；Next.js standalone 启动行仍由 Next 自身输出纯文本。
- `release-preflight --replay-corpus-passed --focused-tests-passed --live-smoke-passed --docker-smoke-passed`
  当前输出 `ok=true`。
- SQLite->Postgres migration dry-run 已在 Compose 内网完成，源库
  `app.sqlite3` 统计为 `sessions=331`、`session_turns=204`、`documents=51`、
  `document_chunks=40`、`evidence_items=52`、`jobs=45`、`auth_sessions=244`；
  目标 Postgres dry-run 前后均为空，`ok=true`，未写入数据，临时
  `/tmp/app.sqlite3` 已从容器删除。
- 本地 nginx edge smoke 未执行：当前工作树没有 `deploy/certs/origin.crt`
  与 `deploy/certs/origin.key`。已在 runbook 记录证书前置条件，并把
  `deploy/certs/` 加入 `.gitignore`。
- 随后生成了本地自签 `deploy/certs/origin.crt` / `origin.key`（目录已
  gitignored）继续 smoke。`docker.exe compose up -d nginx` 在 WSL bind
  mount 阶段失败，错误为 `stat /run/guest-services/distro-services/ubuntu.sock:
  no such file or directory`，说明问题在 Docker Desktop 访问 WSL 挂载，而不是
  nginx 配置或 app 健康。
- 使用临时 `ds160-nginx-local-smoke` 容器绕过 bind mount：基于
  `nginx:1.27-alpine`，加入 `ds160_pr_default` 网络，`docker cp` 同一份
  `deploy/nginx/ds160.conf` 与本地证书后启动。`https://127.0.0.1:18000/healthz`
  经 `Host: ds160.efastt.store` 返回 `status=ok`，database dialect 为
  `postgresql`；`/api/version` 返回 `version=0.1.2`；根路径返回 Next HTML。
- Gate 阻断语义扫描：产品路径无残留命中，剩余为合同 forbidden marker / replay 负例。
- `compileall`、`type-check`、`lint`、`build`、`diff --check` 均通过
- graph_shadow/native public writer 聚焦回归：`7 passed`
- graph material refresh/fallback 邻近回归：`5 passed`
- 全量非 live 回归：`598 passed, 11 deselected`
- `graph_shadow.*legacy` 旧语义扫描：剩余命中仅为显式 fail-fast 断言、兼容字段说明或历史任务文本，不再存在 graph_shadow 使用 legacy public writer 的合同/测试断言。
- 前端 Case Board presentation 合同测试重跑：`7 passed`
- 前端消息来源合同测试重跑：`4 passed`
- 前端上传反馈合同测试重跑：`7 passed`
- 前端 `type-check` 与 `lint` 重跑通过。
- 前端用户可见旧证明缺口文案扫描：`web/components`、`web/lib` 当前无 `关键证明`、`缺少关键证明材料`、`薄弱证明点`、`请准备以下材料` 残留命中。
- 复盘/报告 Case Board 相关后端回归：`3 passed`
- 后端生产路径旧证明缺口文案扫描：`app` / `web` 当前只剩 replay 负例检测 marker，不存在用户可见 `关键证明`、`待证明点`、`薄弱证明点`、`待补清单`、`材料核验`、`材料齐套`、`当前处于材料门控阶段`、`系统正在等待解析结果` 残留命中。
- T14/T15 文档合同同步后，前端 Case Board presentation 合同测试再次通过：`7 passed`
- T14/T15 文档合同同步后，前端 upload feedback 合同测试再次通过：`7 passed`
- T14/T15 文档合同同步后，`git diff --check` 通过。
- T14/T15 文档合同同步后，Graph replay corpus 实跑通过：`fixture_count=13`，`passed=true`，所有 fixture `matched_expectation=true`。
- 本轮 A-L 审计和前端 role 收口后，前端消息来源合同测试通过：`4 passed`。
- 本轮 A-L 审计和文案合同收口后，前端 Case Board presentation 合同测试通过：`7 passed`。
- 本轮前端 role 收口后，`pnpm --dir web type-check`、`pnpm --dir web lint` 与 `git diff --check` 均通过。
- 本轮用户文档旧证明缺口口径扫描：`README.md` / `docs/API.md` / `app` / `web` 当前无用户路径 `薄弱证明点`、`关键证明`、`缺少关键证明材料`、`请准备以下材料`、`材料齐套` 残留命中；剩余命中仅为合同 forbidden marker 和测试断言。
- T14/T15 文档合同同步后，`release-preflight --replay-corpus-passed --focused-tests-passed --live-smoke-passed --docker-smoke-passed` 当前输出 `ok=true`，`blocking_check_ids=[]`。
- 本轮 Docker 当前状态复查与刷新：WSL 内 `docker` shim 仍不可用，提示未启用 Docker Desktop WSL integration；已通过 Windows `docker.exe` 启动 Docker Desktop daemon，`docker.exe --version` 为 `29.2.1`，`docker.exe compose version` 为 `v5.0.2`，`docker.exe compose config --quiet` 通过，`docker.exe info --format {{.ServerVersion}}` 返回 `29.2.1`。
- 本轮真实 Compose smoke 已刷新：`docker.exe compose up -d --build postgres ds160-api ds160-web ds160-worker` 后默认 split services 均为 `healthy`；Web 容器内 `/` 返回 `200`；API 容器内 `/livez` 返回 `{"status":"ok"}`，`/healthz` 返回 `status=ok` 且 `checks.database.dialect="postgresql"`，`/version` 返回 `version=0.1.2`；worker 日志显示 parse worker 以 loop 模式启动。
- 本轮 SQLite->Postgres migration dry-run 已刷新：将本地 `app.sqlite3` 复制到容器 `/tmp/app.sqlite3`，使用内部 Postgres hostname 执行 `--dry-run`，源库统计为 `sessions=331`、`session_turns=204`、`documents=51`、`document_chunks=40`、`evidence_items=52`、`jobs=45`、`auth_sessions=244`、`case_memory_snapshots=0`；目标 Postgres dry-run 前后均为空，`ok=true`，未写入数据，临时 `/tmp/app.sqlite3` 已删除。
- 本轮本地 Compose Postgres 实写迁移已完成：确认目标 Postgres 为空后，串行复制本地 `app.sqlite3` 到容器 `/tmp/app.sqlite3`，执行 `migrate-sqlite-to-postgres` 非 dry-run，`copied_counts` 与 `source_counts` 完全一致：`sessions=331`、`session_turns=204`、`documents=51`、`document_chunks=40`、`evidence_items=52`、`jobs=45`、`auth_sessions=244`、`case_memory_snapshots=0`；迁移后目标库同样为上述计数，`ok=true`。迁移后容器内 `/livez`、`/healthz`、`/version` 仍正常，临时 `/tmp/app.sqlite3` 已删除。
- 本轮 nginx edge smoke 已刷新：`docker.exe compose up -d nginx` 仍因 WSL bind mount 报 `stat /run/guest-services/distro-services/ubuntu.sock: no such file or directory` 失败；随后使用临时 `ds160-nginx-local-smoke` 容器加 `docker cp` 同一份 `deploy/nginx/ds160.conf` 与 `deploy/certs` 绕过 bind mount，`https://127.0.0.1:18000/healthz` 经 `Host: ds160.efastt.store` 返回 `status=ok` 且 database dialect 为 `postgresql`，`/api/version` 返回 `version=0.1.2`，根路径返回 Next HTML；临时 nginx 容器已删除。
- Legacy deprecation decision / preflight 回归：`tests/unit/test_cli_main.py` 为 `16 passed`。
- 发布/Compose 邻近回归：`tests/unit/test_cli_main.py tests/unit/test_deploy_scripts.py tests/unit/test_docker_compose_contract.py` 为 `20 passed`。
- `release-preflight --replay-corpus-passed --focused-tests-passed --live-smoke-passed --docker-smoke-passed` 当前输出 `ok=true`，并包含 `legacy_deprecation_decision` documented 门禁。
- runtime 边界抽样回归：`graph_shadow` native public writer、shadow failure、native label、显式 legacy fail-open 4 条集成测试通过。
- 本轮 legacy decision 文档同步后，`git diff --check` 通过。
- 远程生产首次 cutover 已安全中断并恢复：脚本在旧 combined 容器停止、SQLite/.env 备份完成后，于镜像构建阶段因 `pymupdf==1.26.7` 下载/解压超时失败；已立刻 `docker start ds160-agent2` 恢复旧 SQLite 服务，本机与公网 `/healthz` 均恢复 200。
- 针对该失败，Docker 构建新增 `UV_HTTP_TIMEOUT` build arg，默认 `180` 秒，并通过 Compose build args 可覆盖，避免默认 30 秒下载超时再次中断维护窗口。
- 远程生产第二次 cutover 改为本地 Windows Docker 构建、传输 image tar、服务器 `docker load`，避免再次在小服务器上构建。服务器成功加载 `ds160-agent2:latest`，image id 为 `bfce27d78f95`，应用镜像内 `APP_GIT_SHA=1b70176`。
- 第二次 cutover 在 `SKIP_DOCKER_BUILD=1` 下进入维护窗口：备份目录为 `.deploy-backups/20260530T160105Z-split-postgres-cutover`，旧 `ds160-agent2` 已停止并复制 SQLite 备份，`postgres` 与 `ds160-api` 曾达到 healthy，随后卡在 `migrate-sqlite-to-postgres --dry-run`。之后公网 `/healthz` 超时，SSH TCP 可连接但 `sshd` 不返回 banner。
- 2026-05-31 复查：本地和 GitHub 均为 `2ec63cb`，服务器仍 SSH banner timeout，公网 `https://ds160.efastt.store/healthz` 仍超时；因此不能宣称生产 cutover 完成，也不能标记总目标完成。
- 已追加低资源保护：`scripts/production-split-postgres-cutover.sh` 现在支持 `MIGRATION_TIMEOUT_SECONDS=600`、`ROLLBACK_ON_FAILURE=1`，迁移先只启动 Postgres，并用一次性 app 容器读取备份文件；失败时尝试停止 split 服务并 `docker start ds160-agent2`。新增 `scripts/production-recover-combined.sh`，用于 SSH 恢复后快速停止 split 服务并启动旧 combined 容器，不执行 build、不打印 `.env`。
- 2026-05-31 SSH 恢复后完成生产迁移：确认 split services 已恢复但 Postgres 为空，停止 unhealthy worker 后从 `.deploy-backups/20260530T160105Z-split-postgres-cutover/app.sqlite3.backup` 重新执行 dry-run 与正式迁移。dry-run 源计数为 sessions=40、session_turns=272、documents=110、document_chunks=109、evidence_items=333、jobs=4、auth_sessions=19、case_memory_snapshots=0，目标为空；正式写入后 `copied_counts`、`source_counts`、`target_after_counts` 完全一致。
- 2026-05-31 生产 split 拓扑验证通过：服务器工作树 HEAD=`69d9a92`；`ds160-api`、`ds160-web`、`ds160-worker`、`ds160-postgres` 均 healthy，`ds160-nginx` running，旧 `ds160-agent2` 未运行。worker healthcheck 已改为轻量 SQLAlchemy `select 1`，避免弱服务器上完整 app import 超时。
- 2026-05-31 公网 smoke 通过：`https://ds160.efastt.store/healthz` 返回 `status=ok` 且 database dialect=`postgresql`；`/api/version` 返回 `version=0.1.2`、`git_sha=1b70176`、`build_time=2026-05-30T15:53:58Z`；根路径返回 HTTP 200。
- 最终报告已生成：`docs/implementation/ds160-runtime-cleanup-final-report-2026-05-31.md`。

## 完成状态与后续项

本轮核心目标已完成：runtime cleanup、Case Memory/Evidence Graph、上传理解、前端状态分层、debug 可观测、split Compose、Postgres 迁移、生产公网 smoke 和最终报告均已落地。

完整 A-L 对账和剩余执行清单见
`docs/implementation/runtime-cleanup-task-audit.md`。

- T6/T7：已完成 runtime、reports、OpenAI-compatible metadata、OpenAI Responses metadata、runtime debug snapshot、post-interview review context、export artifact、`GraphCaseStateBuilder` fallback 脱敏、前端 debug 摘要、Analysis Panel、mock/demo 数据、legacy 兼容文案与材料 fallback 的 Case Memory / Evidence Graph 消费，修复了 replay fixture 的 Case Board next_move 引用漂移，把 graph/native/report 的 missing-evidence advisory fallback 收口到 Case Board proof points，冻结 anchored/non-anchored runtime view、runtime ledger turn_record、material refresh response、Gate turn record、capability document review fallback 与 user report missing_evidence 下 requested/remaining 文档字段的 fallback 污染，补齐 Case Board latest material / open proof point 状态判断，并收口生产代码中的“关键证明/待证明点/待补清单/材料核验/材料齐套”旧口径；兼容字段仍保留给旧 API 消费者，删除需等待外部消费者迁移和发布窗口确认。
- T8/T9：已完成新上传主路径、parse 失败节点可见性、前端 timeline/materials/debug panel 产品化展示，并补齐本地 production 浏览器端到端证据；后续可在 Docker/Postgres 环境补 UI 上传 smoke，但主阻断已不是上传合同。
- T10/T11/T12/T13：已完成代码、静态合同、`docker.exe compose config --quiet` 配置渲染、真实 Postgres 容器启动、app `/healthz` / `/livez` / `/version` 健康检查、本地 `app.sqlite3` 到 Compose Postgres 的 migration dry-run 与本地 Compose Postgres 实写迁移验证，以及基于临时 nginx 容器的 18000 edge smoke；远程生产正式写入迁移和公网 smoke 已完成。
- T14/T15：架构 spec、AI-native case understanding spec、前端 Case Board 合同和状态管理合同已跟随当前 runtime / Case Memory / frontend presentation 语义更新；后续只需在真实 LangGraph public promotion 或 legacy 字段删除时继续追加合同变更。
- T16：已新增可执行 release preflight，并补齐 replay corpus、focused non-live runtime tests、focused live LLM smoke、Docker/Postgres smoke 证据；本轮已完成 legacy runtime freeze 的 `graph_shadow` 收口，并通过 `legacy-runtime-deprecation-decision.md` 接受“生产 cutover 后保留一个发布周期再删除”的决策。当前实现只保留显式 `legacy` 与显式 fail-open fallback。

## 下一批建议

优先继续：

1. Legacy runtime 删除窗口：决策已完成；生产 split Compose + Postgres cutover 后，按 `legacy-runtime-deprecation-decision.md` 保留一个发布周期，再删除旧 runtime 文件、settings enum、fail-open fallback 和相关兼容测试。
2. 重新构建应用镜像到最新 HEAD：当前运行 app image 是 `1b70176`，后续正常发布可构建 `69d9a92+` 镜像，使 `/version` 与工作树 HEAD 完全一致。
3. 可选线上 UI 上传 smoke：用浏览器补一次 `.txt` 415 和损坏 PDF parse failed 的真实 UI 验证。
