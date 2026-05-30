# Runtime Cleanup Task Audit

日期：2026-05-30

本文把最初 A-L 清单重新映射到当前代码和验证证据。状态含义：

- `完成`：当前代码、合同和测试已有直接证据。
- `部分完成`：本地产品/代码路径已收口，但仍有外部发布、兼容删除或部署决策。
- `待执行`：需要新增实现或外部操作，不能靠当前代码证明完成。

## A. 产品目标与体验口径

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| A1 明确产品定位文档 | 完成 | `README.md`、`docs/API.md`、Case Board 前端合同均收敛到“面签问答 + 案例理解 + 证据推理”；旧“薄弱证明点”口径已从 README/API 用户文档移除。 | 如果后续新增独立 PRD，要复用同一口径。 |
| A2 移除“材料清单 SaaS”式体验残留 | 完成 | 前端 forbidden copy test、上传反馈 test、Gate 阻断语义扫描和 README/API 口径均证明主线不再围绕 checklist。 | 继续把旧字段限制在兼容投影，不允许新 UI 以 Gate list 为主状态。 |
| A3 定义真实用户成功路径 | 完成 | `fixtures/graph_replay/complete_interview_success_path.json` 覆盖建档、多轮问答、上传、冲突处理、复盘；`eval-graph-corpus` 13 个 fixture 通过。 | 后续可增加更多签证家族成功路径，不阻塞当前目标。 |

## B. Runtime 主链路

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| B1 统一公开 runtime 命名 | 完成 | `runtime_execution` 贯通 messages、SSE、assistant turn、OpenAI-compatible、debug snapshot；默认 `AGENT_RUNTIME=native_interviewer`。 | 继续禁止只看 `agent_runtime` display label 推断真实 writer。 |
| B2 决定 LangGraph 真实角色 | 完成 | 当前决策为：native interviewer 是公开 writer；`graph`/`graph_canary` 是兼容标签；`graph_shadow` 只做 shadow/eval trace。 | 未来若要让 LangGraph public promotion，需要单独 replay + live smoke + 合同更新。 |
| B3 收敛唯一 user-facing writer | 完成 | `MessageService` 只追加一条 assistant turn；前端只从后端 `assistant_message` 生成 transcript assistant message。 | 无。 |
| B4 降级 legacy runtime | 完成 | legacy 已冻结为显式 `AGENT_RUNTIME=legacy` 或显式 fail-open fallback，默认不静默回 legacy；`docs/architecture/legacy-runtime-deprecation-decision.md` 已接受“生产 cutover 后保留一个发布周期再删除”的边界。 | 远程生产 cutover 后执行删除窗口；删除前必须重跑 release-preflight、replay、focused tests、live smoke 和 Docker/Postgres smoke。 |
| B5 统一 turn decision 合同 | 完成 | `turn_decision`、`prompt_trace`、`runtime_view_state`、`turn_record` 在 API、SSE、debug、reports、OpenAI-compatible 中对齐。 | 无。 |

## C. 签证官声音与聊天主线

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| C1 前端停止自造 officer 消息 | 完成 | `web/lib/message-source-policy.ts` 只从后端 final `assistant_message` 构造 transcript；SSE progress/debug 不追加聊天消息。 | 无。 |
| C2 system/status/debug 消息移出聊天主线 | 完成 | `activityEvents` 承载上传、debug、SSE progress、错误；上传合同要求 `case_board_timeline_only`。 | 旧 local history 中 `system` 只做兼容恢复，不允许新动作追加。 |
| C3 统一 assistant/officer role | 完成 | 前端 `ChatMessage.role` 改为 `assistant | user | system`；旧本地 history 的 `officer` 只在 hydrate 时归一化到 `assistant`；UI 仍把 assistant 展示成“签证官”。 | 后续如果清理旧 localStorage 兼容，可删除 `officer` hydrate 分支。 |
| C4 建立签证官口径回归样例 | 完成 | replay fixture、message-source test、Case Board presentation test 和旧文案扫描共同防止回到 checklist/客服语气。 | 可继续增加 live 口径评估，但当前阻断已解除。 |

## D. Gate 与 Governor 边界

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| D1 Gate 降级为兼容展示层 | 完成 | 只有 `family_not_selected` 硬拦；pending/waiting_for_parse 仍进入 interviewer runtime。 | 无。 |
| D2 删除 Gate-first 历史假设 | 完成 | `case_understanding` 是新上传主 job；2026-05-30 远程只读审计确认旧队列无 `gate_parse`，本地 worker 与 Gate projection 已删除 `gate_parse` 兼容路径；旧 Gate copy 已收口。 | 无。 |
| D3 明确 Governor 职责 | 完成 | Governor/risk 进入 trace/advisory，不直接篡改签证官主话术。 | 无。 |
| D4 冲突处理统一到 Case Memory | 完成 | Case Memory 支持 claim/evidence/proof/conflict/resolution；document review fallback 可直接消费 Case Board conflicts/proof points。 | 无。 |

## E. Case Memory 与 Evidence Graph

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| E1 Case Memory 一等化 | 完成 | `case_memory_snapshots` 是一等 read model；claims/evidence/proof/conflicts/resolutions 作为结构化投影持久化。 | 若未来需要强 SQL 查询，可把 snapshot 内结构拆成独立表，但当前合同已满足。 |
| E2 Evidence Graph 查询层 | 完成 | `CaseMemoryService.query_evidence_graph()` 支持按 field path 查询，runtime/report/debug/OpenAI-compatible 消费同一投影。 | 无。 |
| E3 统一 artifact 到 Case Memory 写入路径 | 完成 | 上传、parse worker、debug fill、debug bundle、用户明确陈述都会写入 Case Memory 或对应兼容投影。 | 无。 |
| E4 明确未知不等于否定 | 完成 | funding unknown、parse failed、material unavailable、proof point unresolved 均有独立状态，不自动变成失败/拒签。 | 无。 |

## F. 上传与材料理解

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| F1 material-understanding-first 上传链路 | 完成 | `/files` 新建 `case_understanding` job；图片/PDF/文本经统一 material understanding 输出候选、claims、evidence、proof points。 | 无。 |
| F2 清理 gate_parse 兼容路径 | 完成 | 新上传只创建 `case_understanding`；2026-05-30 远程 jobs 表只有 `case_understanding`，无 `gate_parse`；`ParseWorker` 和 `GateRuntimeService` 已删除 `gate_parse` 兼容消费。 | 无。 |
| F3 文档解析失败状态可见 | 完成 | parse/material understanding 失败写入 artifact、Case Board、debug timeline、前端材料库/activity。 | 无。 |
| F4 上传结果不直接污染对话 | 完成 | 上传反馈只进入 materials/activity/debug timeline，不生成 assistant/system transcript。 | 无。 |

## G. Debug Console 与 Synthetic Bundle

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| G1 Runtime debug timeline 标准化 | 完成 | runtime debug snapshot 暴露 timeline、material_understanding、runtime/material refresh/error 摘要。 | 无。 |
| G2 synthetic bundle 与真实补资料隔离 | 完成 | debug bundle 有 source/generation/expected_findings 隔离，oracle 不进入 prompt/context。 | 无。 |
| G3 fallback 可观测 | 完成 | `runtime_execution`、debug snapshot、preflight、structured log 能显示 native/graph_shadow/legacy fallback 路径。 | 远程生产 smoke 仍需在真实服务器验证日志链路。 |
| G4 debug redaction 固化 | 完成 | public-safe projection 和 snapshot redaction 测试覆盖 debug oracle、bundle id、scenario label 等字段。 | 无。 |

## H. 前端状态与信息架构

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| H1 聊天、Case Board、Debug Panel 状态分层 | 完成 | `messages`、`activityEvents`、`uploadedMaterials`、runtime debug snapshot 分层；ChatPanel 可显示 activity 但不混入 transcript。 | 无。 |
| H2 版本 badge 接入真实 build info | 部分完成 | 前端/后端支持 version、git sha、build time env；`/version` 已在 Compose smoke 返回；`deploy/README.md` 已写入服务器发布时注入 git sha/build time 的命令。2026-05-30 只读远程审计确认生产 `.env` 仍缺少 `APP_GIT_SHA`、`APP_BUILD_TIME`、`NEXT_PUBLIC_GIT_SHA`、`NEXT_PUBLIC_BUILD_TIME`。 | 远程服务器必须实际按该命令重建，并在 UI badge 与 `/version` 验证真实值。 |
| H3 移除误导性按钮和文案 | 完成 | “补齐一套/材料齐套/关键证明/薄弱证明点”等用户路径已收口或只作为 forbidden marker。 | 无。 |
| H4 错误状态产品化 | 完成 | 上传 415、parse failed、runtime/LLM/preflight 错误均进入 activity/debug/health 分层状态。 | 无。 |

## I. 数据库与运行可靠性

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| I1 生产数据库迁移到 Postgres | 部分完成 | Compose 默认 Postgres；本地 Compose Postgres 已完成 SQLite 迁移 dry-run 和实写验证。2026-05-30 只读远程审计确认生产仍是 SQLite，当前计数为 sessions=40、session_turns=276、documents=110、document_chunks=109、evidence_items=333、jobs=4、auth_sessions=19、case_memory_snapshots=0。新增 `scripts/production-split-postgres-cutover.sh`，把备份、dry-run、真实迁移、split compose smoke 串成带确认的维护窗口脚本。 | 远程生产正式迁移仍需维护窗口、目标库确认和生产凭证。 |
| I2 本地 SQLite 运行垃圾清理 | 完成 | `.gitignore` 覆盖 SQLite WAL/SHM。 | 无。 |
| I3 DB session 生命周期整理 | 完成 | SSE/debug bundle 长流式前释放入口 DB session；worker 独立 DB session；Postgres `pool_pre_ping`。 | 无。 |

## J. 测试与评估

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| J3 前端消息来源测试 | 完成 | `pnpm test:message-source` 覆盖 SSE 不自造 transcript、backend response 生成 assistant message、禁止新 `officer` role。 | 无。 |
| J4 Case Memory 写入测试 | 完成 | `tests/unit/test_case_memory_service.py` 覆盖材料理解、用户 claim、冲突、resolution、tombstone、Evidence Graph 查询。 | 无。 |
| J5 debug snapshot 合同测试 | 完成 | runtime debug snapshot fixture/test 覆盖 material understanding failure、timeline、redaction。 | 无。 |

## K. 文档与架构合同

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| K1 runtime architecture spec | 完成 | `interviewer-runtime-contracts.md`、`agent-runtime-cutover-plan.md`、`agent-runtime-spec.md` 与 native public writer / graph shadow 语义对齐。 | LangGraph public promotion 或 legacy 删除时继续更新。 |
| K2 AI-native case understanding spec | 完成 | Case Memory、Evidence Graph、material understanding、unknown/conflict/tombstone 合同已更新。 | 无。 |

## L. 运维与发布

| 编号 | 状态 | 当前证据 | 剩余动作 |
| --- | --- | --- | --- |
| L1 docker-compose 生产形态重整 | 完成 | 默认 Compose 已拆成 `ds160-api`、`ds160-web`、`ds160-worker`、`postgres`、`nginx`；API/worker 关闭 inline worker，nginx 分别指向 API/Web，旧 `ds160-agent2` 仅作为 `combined` profile 兼容模式。本地 split Compose build/smoke 已验证 API/Web/worker/Postgres healthy。2026-05-30 只读远程审计确认生产仍运行旧 `ds160-agent2` + `nginx` 服务。 | 远程生产需要用新拓扑重建并 smoke。 |
| L2 build metadata 注入 | 部分完成 | Docker build args 和 `/version` 支持 git sha/build time；本地 smoke 已验证 `/version`；服务器启动/更新手册已要求注入 `APP_GIT_SHA`、`APP_BUILD_TIME`、`NEXT_PUBLIC_GIT_SHA`、`NEXT_PUBLIC_BUILD_TIME`。 | 远程发布必须实际注入真实 commit/build time，并在 UI badge 与 `/version` 验证。 |
| L3 发布前检查清单 | 完成 | `release-preflight` 输出 replay、focused tests、live smoke、Docker smoke、rollback/report 门禁。 | 远程发布时必须附真实命令输出。 |
| L4 日志结构化 | 完成 | JSON log formatter 覆盖 app/uvicorn，支持 session/run/turn/document 字段和 secret redaction。 | 远程日志采集链路待线上验证。 |
| L5 健康检查分层 | 完成 | `/livez` 与 `/healthz` 分离；database、LLM、worker readiness 降级会返回 503。 | 无。 |

## 仍需执行的可落地清单

### 0. 远程生产当前状态（2026-05-30 只读审计）

- 服务器目录：`/opt/ds160-agent2`。
- 服务器分支：`refactor/agent-runtime-graph`。
- 服务器 HEAD：`ef4dd76`；当前远端分支 HEAD：`c299f7c`。
- 服务器工作树：`git status --short` 为 0 行。
- 当前 Compose 服务：`ds160-agent2`、`nginx`；尚未使用 `ds160-api` / `ds160-web` / `ds160-worker` / `postgres`。
- 当前数据库 dialect：`sqlite`。
- 当前生产表计数：sessions=40、session_turns=276、documents=110、document_chunks=109、evidence_items=333、jobs=4、auth_sessions=19、case_memory_snapshots=0。
- 当前 `.env`：`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`APP_AUTH_PASSWORD` 存在；`COMPOSE_DATABASE_URL`、`APP_GIT_SHA`、`APP_BUILD_TIME`、`NEXT_PUBLIC_GIT_SHA`、`NEXT_PUBLIC_BUILD_TIME` 缺失。
- 服务器本机 `https://127.0.0.1:18000/healthz -H 'Host: ds160.efastt.store'` 返回 `status=ok`。
- 公网 `https://ds160.efastt.store/healthz` 返回 `status=ok`。

### 1. 远程生产迁移与外网验证

- 已确认服务器当前 `HEAD=ef4dd76`，远端 `refactor/agent-runtime-graph` `HEAD=c299f7c`；下一步需在发布窗口快进服务器代码。
- 备份当前生产 SQLite、目标 Postgres 和 `.env`。
- 在维护窗口内确认目标 Postgres 为空或已备份；非空时必须显式确认是否 `--truncate-target`。
- 执行 `migrate-sqlite-to-postgres --dry-run` 并保存 counts。
- 执行正式迁移后核对 sessions、turns、documents、chunks、evidence、jobs、auth_sessions、case_memory_snapshots 计数。
- 可使用 `CONFIRM_PRODUCTION_CUTOVER=I_UNDERSTAND_PRODUCTION_CUTOVER RUN_WRITE_MIGRATION=1 scripts/production-split-postgres-cutover.sh` 执行维护窗口 cutover；脚本会先 dry-run 再真实写入。
- 重建并启动生产 compose，确认 `/livez`、`/healthz`、`/version`。
- 通过服务器本机 `curl -k https://127.0.0.1:18000/healthz -H 'Host: ds160.efastt.store'` 和公网 `https://ds160.efastt.store/healthz` 验证 Cloudflare 18000 链路。
- 保存 rollback 点：旧 SQLite、旧镜像/tag、旧 compose env、迁移前计数。

### 2. Legacy runtime 删除窗口

- 决策已落地：`AGENT_RUNTIME=legacy` 仅作为生产 cutover 后一个发布周期的显式回滚开关。
- 删除窗口内删除 `InterviewerRuntimeService` live path、`AGENT_RUNTIME=legacy` settings enum、fail-open legacy fallback、相关旧测试和文档 fallback。
- 删除前必须重跑 replay corpus、focused non-live runtime tests、focused live smoke、Docker/Postgres smoke 和 `release-preflight`。
- 删除前必须确认生产日志能按 session / run / turn 串起 native runtime、graph shadow 和 fallback 路径。

### 3. Build metadata 发布落地

- 发布脚本注入 `APP_GIT_SHA`、`APP_BUILD_TIME`、`NEXT_PUBLIC_GIT_SHA`、`NEXT_PUBLIC_BUILD_TIME`。
- `/version` 返回 commit/build time 后，前端版本 badge 同步显示同一 git sha。
- release checklist 记录 git sha、branch、migration id、healthz、smoke replay。

### 4. 可选强化 smoke

- 在 Docker/Postgres 环境下跑一次真实 UI 上传 smoke，覆盖 `.txt` 415 和损坏 PDF parse failed。
- 在远程服务器打开 runtime debug 后验证 timeline、Case Board、Evidence Graph 和 JSON log 串联字段。
