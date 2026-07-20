# Backend Runtime Defect Fix Plan

> Scope: **backend / worker / API only**. Frontend workbench、wx UI、client mapper 文案等由后续单独改造；本计划只列后端契约与必要的 **public API 补丁**（供前端后续接线，本计划不改 web/）。
>
> Baseline: `main` @ `simplify/agent-runtime-core` tip（`625fea9` 及其后）。  
> Source: multi-agent runtime review（turn / material / auth / dual-runtime）。

---

## 1. Goals & Non-goals

### Goals

1. 公开写路径在失败/并发下 **不卡死、不双写、不静默污染 Case Memory**。
2. 材料生命周期（upload → parse → understand → tombstone）与 Case Board / Gate / Evidence **单一真源、可失效**。
3. Session 访问与配额在所有写入口一致（含 OpenAI 兼容口）。
4. 收口误导性 runtime 配置与响应标签，避免“改 env 以为切换了 writer”。

### Non-goals（本计划不做）

- 任何 `web/` / miniprogram 行为改造（轮询、恢复会话 UI、403 文案、wx 重试等）。
- Graph runtime 重新作为公开 writer（只做隔离/死配置收口）。
- 全面 Postgres 迁移、全量 RAG 治理。
- Access key 加密存储的完整密钥轮换产品（见 P1 可选加密方案；默认可先做加固策略）。

### Out of scope but related

| 前端后续会碰到的后端缺口 | 本计划是否做后端 |
|--------------------------|------------------|
| 材料 understanding 无公开轮询面 | **做**（PR-B3 公共 materials status / documents list） |
| Report 无 top-level `requested_documents` | **做**（PR-B6 契约补齐） |
| 恢复会话需要文件列表 | **做**（PR-B3 list documents） |
| UI 轮询 debug、错误文案、wx 分叉 | **不做** |

---

## 2. Priority map (backend only)

| Priority | Theme | Risk if delayed |
|----------|--------|-----------------|
| **P0** | Turn 卡死 / 材料复活 / Snapshot 粘性 / Compat 越权 | 会话废掉、删不掉材料、跨用户读写 |
| **P1** | 并发锁、失败回滚、Gate readiness、Evidence 残留、配额竞态、phase 分叉 | 双线对话、真值分叉、超额配额 |
| **P2** | State merge、score 空洞、job claim、import 语义、runtime 标签/死配置、上传评估 | 债与误导，偶发脏数据 |
| **P3** | 限流 harden、明文 key 策略、wx ticket 加固、legacy 清理 | 安全与运维面 |

---

## 3. Work packages (recommended PR order)

每个 PR 尽量 **可独立 merge + 有测试**。依赖关系：

```text
PR-B1 Turn failure & concurrency
   │
PR-B2 Case memory invalidation  ──┬──► PR-B3 Material lifecycle (tombstone/jobs/gate)
   │                              │
   └──────────────────────────────┴──► PR-B4 AuthZ + quota on all writers
                                         │
PR-B5 Gate/phase invariants  ◄───────────┤
                                         │
PR-B6 Public contracts (status/list/report fields)  ◄── 前端后续接线
                                         │
PR-B7 Runtime config / dual-label cleanup
                                         │
PR-B8 Security harden (rate limit, tickets, secrets)  ── optional batch
```

---

### PR-B1 — Turn 失败策略与会话并发（P0 + P1）

**问题**

1. `native_quality_guard_failed` 保留 user turn 且无 re-run 路径 → 会话永久 409。
2. 无 session 级锁 → 并发 POST 双 user/assistant。
3. 失败清理只删 `SessionTurnRecord`，claims 已写入 snapshot → 孤儿主张。

**改动面**

| 文件 | 变更 |
|------|------|
| `app/services/message_service.py` | 失败策略统一；清理后 rebuild snapshot；session lock |
| `app/services/native_interviewer_runtime_service.py` | quality guard 错误码/是否可重试语义对齐 |
| `app/repositories/session_repo.py` 或新 `app/services/session_lock.py` | DB 行锁 / advisory lock |
| `tests/integration/test_messages_api.py` | 修正“keep user turn forever”类断言；加并发/回滚测试 |

**设计决策（建议默认）**

| 决策 | 选择 | 理由 |
|------|------|------|
| Quality guard 失败 | **与其它 ModelRuntimeError 一致：删除未完成 user turn + rebuild snapshot** | 无 re-run API 时 keep turn = 死锁 |
| 若产品要“同 turn 重试” | 另开 API `POST .../messages/{turn_id}/retry` 再 keep turn | 本 PR 不半吊子 |
| 并发控制 | 在 `handle_user_turn` 开头对 `SessionRecord` **`SELECT ... FOR UPDATE`**（同事务贯穿到 assistant commit） | 单 DB 部署有效；多 worker 安全 |
| 失败后 claims | `_cleanup_incomplete_committed_user_turn` 后强制 `CaseMemoryService.rebuild_and_persist(session_id)` | 消灭孤儿 claim |

**验收**

- [ ] Quality guard 失败后，新 `client_message_id` 可继续发消息。
- [ ] 同 session 并发 2 请求：一个成功，一个 409/或串行成功，**不会**出现两条未匹配 assistant 的“双线对话”。
- [ ] 用户 turn 提交 claims 后 runtime 失败：snapshot 中无该 turn 的 claims；board 与 transcript 一致。
- [ ] 现有 idempotent 重试（同 `client_message_id` + 已有 assistant）仍返回同一结果。

**不改**

- 前端发送/重试 UX。

---

### PR-B2 — Case Memory 失效与重建契约（P0）

**问题**

`get_or_build_snapshot`：有 snapshot 永远返回，不校验 documents/turns 代数。  
Package import / debug fill / 部分 cleanup **不** rebuild。

**改动面**

| 文件 | 变更 |
|------|------|
| `app/services/case_memory_service.py` | `invalidate` / `rebuild_and_persist`；generation 或 content hash；`get_or_build` 策略 |
| `app/services/material_package_archive_service.py` | import 后 rebuild |
| `app/services/debug_material_bundle_service.py` / `debug_fill_service.py` | 写入后 rebuild |
| `app/services/message_service.py` | 与 B1 清理路径共用 rebuild |
| unit/integration tests | sticky cache 回归用例 |

**设计决策**

| 决策 | 选择 |
|------|------|
| 读取路径 | 默认仍可用 cache，但 **所有写材料/删材料/claims/import/debug** 必须 `invalidate` 或直接 rebuild |
| 可选加强 | snapshot 存 `source_generation`（documents max updated_at + turn count）；不匹配则 rebuild |
| 公开 API | 不强制前端调用 rebuild；全部服务端写路径负责 |

**验收**

- [ ] 先聊天（空 snapshot）→ package import 已理解材料 → 下一 turn case state 含材料 claims。
- [ ] Debug fill 后 `public_case_board` / native case state 立即可见。
- [ ] Tombstone 后 snapshot 不再含该 document 主张（与 B3 联调）。

---

### PR-B3 — 材料生命周期：job 取消、tombstone、gate、evidence（P0 + P1）

**问题**

1. Tombstone 不取消 queued/processing job → worker 把文档“复活”。
2. Gate ready 只看 `status==parsed`，不看 `understanding_status`。
3. Parse 先 commit 再 understand，中间窗口 gate/UI 以为 ready。
4. Tombstone 后 `EvidenceItemRecord` / profile 仍引用已删材料。
5. Understanding 模型失败不回落 legacy evidence 进 case memory（profile 却用 legacy）。

**改动面**

| 文件 | 变更 |
|------|------|
| `app/workers/parse_worker.py` | 处理前检查 tombstone；中途再检；失败/跳过不复活 |
| `app/services/document_pipeline.py` | tombstoned 短路；避免覆盖 tombstone artifact |
| `app/services/material_cleanup_service.py` | cancel jobs；可选 soft-hide evidence；触发 profile recompute + memory rebuild |
| `app/repositories/document_repo.py` | `cancel_jobs_for_document(s)`；claim 时跳过 tombstoned |
| `app/services/gate_runtime_service.py` | readiness 绑定 understanding（见决策） |
| `app/services/evidence_service.py` / `evidence_repo.py` | list/summary 排除 tombstoned document |
| `app/services/profile_recompute_service.py` | 调用点：cleanup 后强制 recompute |
| `app/services/material_understanding_service.py` | 模型失败时：有 legacy evidence 则降级 completed+flag，或明确 failed 且 gate 不 ready |
| tests | 删除竞态、gate 与 understanding 矩阵 |

**设计决策**

| 主题 | 建议 |
|------|------|
| Tombstone vs hard delete | 保持 soft tombstone；**必须** cancel job + 证据查询过滤 + profile recompute |
| Gate ready | 非 funding 类型：`parsed` **且** (`understanding_status in (completed, skipped_legacy)` **或** 显式 offline 模式) |
| Offline / 无模型 | settings 旗标 `material_understanding_required=true`（默认 true）；false 时 parsed+legacy 可 ready |
| 中间 commit | 短期：worker 在 understand 完成前 **不** 让 gate 把 item 标 ready（gate 读 understanding）；中期：单事务或 `status=understanding` 中间态 |
| 模型失败 + legacy | Prefer：**gate 不 ready** + case memory 可带 `understanding_status=failed` 与 legacy 摘要，避免“profile 有、board 无”无说明 |

**顺带公共 API（给前端后续用，本 PR 只做后端）**

| Endpoint | 用途 |
|----------|------|
| `GET /v1/sessions/{id}/documents`（或扩展现有 files list） | 恢复会话 / 轮询 status、understanding_status、case_board_delta |
| 可选 `GET /v1/sessions/{id}/materials/status` | 轻量：仅 id + understanding_status + error |

现有若已有 list 但未暴露 understanding 字段，则 **补字段** 而非新造轮子。

**验收**

- [ ] 上传后立即 delete：worker 完成后 document 仍 tombstoned，不出现在 gate matched docs。
- [ ] Key-scoped cleanup 后 evidence list / profile documented 字段不残留该材料。
- [ ] Understanding failed → gate 对应项不是 ready（默认配置下）。
- [ ] List documents 返回 `understanding_status` 供前端后续轮询。

---

### PR-B4 — 全写入口鉴权与配额（P0 + P1）

**问题**

1. `/v1/chat/completions`、`/v1/responses` 无 `require_session_access`，可跨 session 读写。
2. 经 compat 创建 session **不** `consume_session_quota`。
3. `consume_session_quota` TOCTOU。

**改动面**

| 文件 | 变更 |
|------|------|
| `app/api/routers/openai_compat.py` | 解析 `metadata.session_id` 后 ownership；创建路径走配额 |
| `app/api/routers/openai_responses.py` | 同上 |
| `app/core/dependencies.py` / `simple_auth.py` | 可复用 `require_session_access` 变体（machine key 策略见下） |
| `app/services/access_key_service.py` | 原子配额：`UPDATE ... WHERE usage_count < max` 或行锁 |
| `app/api/routers/sessions.py` | 与 compat 共用同一 create+quota 服务函数 |
| tests | 跨用户 403；并发建会话不超额 |

**设计决策**

| 主体 | 行为 |
|------|------|
| 用户 cookie | 必须 ownership；新建必须配额 |
| `APP_COMPAT_API_KEY` machine | **仍要求** session 存在且可选绑定 service account；**禁止**静默写任意 session，或仅允许 admin 配置的 bypass list |
| 配额 | DB 原子递增；失败则不创建 session / 回滚 |

**验收**

- [ ] User A token + User B `session_id` → 403。
- [ ] Compat 建会话计入 access key 配额；打满后 403。
- [ ] 并发 N 次 create，成功数 ≤ remaining quota。

---

### PR-B5 — Gate / phase 不变量（P1）

**问题**

- `refresh_record` 在 family 已选时总写 `phase_state="interview"`，拒签关闭后材料 parse 会“重开” phase。
- 与 `_is_refusal_closed`（看 governor decision）分叉。

**改动面**

| 文件 | 变更 |
|------|------|
| `app/services/gate_runtime_service.py` | 终端态不覆盖 phase；或 terminal 时跳过 phase 写入 |
| `app/workers/parse_worker.py` | 终端 session 跳过 material refresh / 或只更新材料不改 phase |
| `app/services/message_service.py` | 统一 `_is_terminal_session` |
| tests | refuse → upload/parse → phase 仍 closed |

**验收**

- [ ] `simulated_refusal` / `session_closed` 后 parse 完成：`phase_state` 不变；messages 仍 409。
- [ ] 正常 interview 中 parse 仍可 refresh gate items。

---

### PR-B6 — 后端契约补齐（供前端后续）（P1/P2）

**问题**

- User report 无 top-level `requested_documents`（消息响应有）。
- Case Board `proof_points` vs `open_proof_points` 双名。
- List documents / understanding 字段不完整（部分在 B3）。

**改动面**

| 文件 | 变更 |
|------|------|
| `app/services/report_service.py` | user_report 增加 `requested_documents`（与 turn 对齐或从 current focus/governor 投影） |
| `app/services/case_memory_service.py` / `case_board_projection.py` | **规范字段**：snapshot/public board 同时输出 canonical + alias（过渡期） |
| `docs/runtime-contracts.md` / `docs/API.md` | 更新契约 |
| API tests / contract tests | 字段存在性 |

**Canonical 建议**

- Board 证明点：**`proof_points`** 为 SoT；响应中继续填 `open_proof_points = proof_points` 一个版本周期，避免旧客户端空板。
- Report：`requested_documents: string[]` + 可选 labels。

**验收**

- [ ] `GET user report` JSON 含非空契约字段（有请求时）。
- [ ] `public_case_board` 同时含 `proof_points` 与 `open_proof_points` 且内容一致。

**明确不做**

- `web/lib/api/mappers.ts` 等前端适配。

---

### PR-B7 — Runtime 配置与双标签收口（P2）

**问题**

- `agent_runtime` 响应可显示 `graph`，实际 writer 恒为 native。
- Canary %、typed adjudication、fail-open-to-legacy 等死旋钮误导运维。

**改动面**

| 文件 | 变更 |
|------|------|
| `app/services/message_service.py` | 公开响应 `agent_runtime` 固定 `native_interviewer`；`configured_agent_runtime` 仅 debug |
| `app/core/settings.py` | 废弃注释；canary 标 deprecated 或移除校验枚举中的无效路由含义 |
| `docker-compose.yml` / `.env.example` | 只推荐 native；删误导 rollback 注释 |
| 集成测试 | 删除“env=graph 时 agent_runtime==graph”类断言 |
| docs architecture cutover | 标注 selector 仅历史/标签 |

**验收**

- [ ] 任意 `AGENT_RUNTIME=*`，公开 message 响应 `agent_runtime == native_interviewer` 且 `execution_runtime == native_interviewer`。
- [ ] 设置 graph 不改变 writer（已有行为）且不再伪装 label。

**可选后续（P3）**

- Graph 包挪到 `app/evals/` 或 `experimental/`；删除 `_is_graph_canary_selected` 死代码。

---

### PR-B8 — 安全与运维加固（P3，可拆）

| 项 | 改动 | 验收 |
|----|------|------|
| Admin 登录限流 | 与 user login 同结构或更严 | 连续失败 429 |
| 可信代理 | settings：`trusted_proxy_depth` / 只信 `CF-Connecting-IP` 当 `trust_cloudflare=true` | 伪造 XFF 不绕过 |
| 配额/登录多 worker | 限流改 Redis 或 DB（若多 worker 部署） | 文档说明单机 vs 多机 |
| Access key 明文 | 短期：限制 reveal 审计 + admin only；中期：加密字段或一次性显示 | 无新 key 明文落库（若选加密） |
| Wx ticket | status 脱敏（无完整 content path）；upload 原子 `max_files`；可选绑定 access_key_id | 并发不超额；status 不泄密 |
| Session id 熵 | 加长 session_id | 文档注明 |
| CSRF / X-Forwarded-Host | 仅信任配置的 public origins，忽略客户端 Host 伪造 | 单测 |

---

## 4. Cross-cutting invariants（合并后必须成立）

1. **Single public writer**：仅 `NativeInterviewerRuntimeService` 写用户可见 assistant turn。  
2. **Terminal session**：`phase_state` 与 terminal decision 一致；材料 worker 不重开。  
3. **No unanswered user turn**（除非存在明确 re-run API 且文档化）。  
4. **Snapshot ≤ DB truth**：任何材料/claims/删除/import 后 board 可读最新。  
5. **Tombstone sticky**：tombstoned 文档不被 job 复活；evidence/profile 不引用。  
6. **Ownership on every session write**：含 compat 路由。  
7. **Quota atomic**：成功建会话数 ≤ key 限额。

---

## 5. Test strategy

| 层 | 覆盖 |
|----|------|
| Unit | case_memory invalidate；gate readiness 矩阵；quota SQL；tombstone+pipeline 短路 |
| Integration | messages quality-guard；concurrent turns（同 TestClient 线程或 asyncio）；delete during parse；compat 403；refuse+parse phase |
| Regression | 现有 graph-label 测试改为新标签契约；messages keep-turn 测试按 B1 决策改写 |
| Manual（后端） | 用 curl/httpie：上传→delete→等 worker；建 session 打满配额；compat 跨 session |

不要求本计划内的 Playwright/前端 E2E。

---

## 6. Suggested implementation order & sizing

| Order | PR | 估时（单人熟悉代码） | 阻塞 |
|-------|-----|----------------------|------|
| 1 | **B1** Turn failure + lock + claim rollback | 1–2 d | — |
| 2 | **B2** Snapshot invalidation | 0.5–1 d | B1 可并行收尾 |
| 3 | **B3** Material lifecycle + list API | 2–3 d | 依赖 B2 rebuild API |
| 4 | **B4** AuthZ + quota | 1 d | 可与 B3 并行 |
| 5 | **B5** Phase invariants | 0.5 d | 与 B3 parse 路径相关 |
| 6 | **B6** Report/board contract | 0.5 d | 可与 B5 并行 |
| 7 | **B7** Runtime labels | 0.5 d | 独立 |
| 8 | **B8** Security batch | 1–2 d | 独立，可后置 |

**MVP 后端止血（建议先做）**：B1 + B2 + B3（tombstone/job/gate）+ B4。  
**契约给前端**：B3 list/status + B6。  
**洁癖/运维**：B5 + B7 + B8。

---

## 7. Explicitly deferred（前端负责）

以下审查项 **不在本计划实现**，仅作对接备注：

- `queueMaterialUnderstandingRefresh` 走 debug、理解完成后不 merge board  
- 会话 restore 清空 materials（后端 list 就绪后前端再接）  
- wx 无 poll / 无 retry  
- 403 文案、stream 断连不拉 transcript  
- `humanizeBackendText`、mapper 过滤过严  
- 分享链接 hash/query UX  

后端在 B3/B6 提供稳定字段后，前端改造应不再依赖 `/debug/runtime`。

---

## 8. Open product decisions（动手前拍板）

1. **Quality guard**：删除 turn（推荐）还是做正式 retry API？  
2. **Understanding 是否强制**：无模型/offline 演示是否允许 `parsed` 即 ready？  
3. **Machine compat key**：生产是否仍对用户 cookie 开放 `/v1/chat/completions`？若否，可直接 404/禁用 cookie 路径。  
4. **Package import**：replace 同类型材料 vs 纯追加？  
5. **Access key 明文**：接受 reveal 产品 vs 加密/一次性？  

未拍板前，PR 内采用第 3 节“建议默认”，并在 PR 描述写明。

---

## 9. Tracking checklist

- [x] PR-B1 Turn failure & concurrency  
- [x] PR-B2 Case memory invalidation  
- [x] PR-B3 Material lifecycle + documents API  
- [x] PR-B4 AuthZ + atomic quota  
- [x] PR-B5 Gate/phase invariants  
- [x] PR-B6 Report/board public contract  
- [x] PR-B7 Runtime label/config cleanup  
- [x] PR-B8 Security harden  
- [x] Update `docs/runtime-contracts.md` + `docs/API.md` after B3/B6  
- [x] No frontend commits in these PRs (frontend WIP left unstaged for user)  

> Implemented on local `main` via concurrent worktree agents + merge (2026-07-20). Not pushed.

---

## 10. Reference findings index

| ID | Severity | Backend PR |
|----|----------|------------|
| Quality guard session deadlock | P0 | B1 |
| Concurrent turns no lock | P1 | B1 |
| Failed turn orphans claims | P1 | B1 + B2 |
| Tombstone + job revive | P0 | B3 |
| Sticky case memory snapshot | P0 | B2 |
| Gate ready without understanding | P1 | B3 |
| Mid-parse commit window | P1 | B3 |
| Evidence/profile after tombstone | P1 | B3 |
| Understanding fail vs legacy split | P1 | B3 |
| Phase reopen after refusal | P1 | B5 |
| interviewer_state full replace | P2 | B1 顺手 merge 或 B5 |
| Score/profile not advanced on turns | P2 | 可选 follow-up（非本批必须） |
| Upload hint as truth | P2 | B3 可选收紧 / 独立小 PR |
| Job claim no row lock | P2 | B3 |
| Package import no rebuild / no replace | P1/P2 | B2 + B3 |
| OpenAI compat no ownership/quota | P0 | B4 |
| Quota TOCTOU | P1 | B4 |
| Plaintext access keys | P2/P3 | B8 |
| Admin unlimited login / spoofable XFF | P2/P3 | B8 |
| Wx ticket races / leaky status | P2/P3 | B8 |
| Dual agent_runtime label | P2 | B7 |
| proof_points dual names | P2 | B6 |
| Report missing requested_documents | P2 | B6 |
| Frontend-only defects | — | **excluded** |
