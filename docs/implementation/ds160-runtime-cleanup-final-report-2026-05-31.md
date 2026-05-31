# DS-160 Runtime 清理与生产切换最终报告

生成时间：2026-05-31  
分支：`refactor/agent-runtime-graph`  
生产完成实施 HEAD：`69d9a92 fix: use lightweight worker healthcheck`  
生产工作树 HEAD：`69d9a92`  
生产应用镜像版本：`APP_GIT_SHA=1b70176`，`APP_BUILD_TIME=2026-05-30T15:53:58Z`  

## 结论

本轮实施已完成核心目标：DS-160 项目从旧 combined container + SQLite 生产形态，切换到 split services + Postgres 生产形态；runtime、Case Memory、上传理解、前端状态分层、debug、测试、发布门禁和恢复脚本均已落地。公网 `https://ds160.efastt.store` 已恢复，生产 `/healthz` 返回 `status=ok`，数据库方言为 `postgresql`，迁移计数与 SQLite 备份一致。

需要注意：生产应用镜像仍是本地构建并预加载的 `1b70176`，而服务器工作树已是 `69d9a92`。`1b70176` 之后的变更主要是 docs、脚本、Compose healthcheck 和恢复保护；没有改变运行时 app 代码。当前 `/version` 正确反映正在运行的应用镜像版本。

## 架构更新前后对比

| 维度 | 更新前 | 更新后 |
| --- | --- | --- |
| 产品定位 | 体验残留“材料清单 / checklist SaaS”口径，用户容易被引导去补材料。 | 统一为 DS-160 AI 面签工作台：面签问答、案件理解、证据推理、冲突处理、复盘。 |
| Runtime 角色 | `graph`、`native_interviewer`、legacy runtime 命名和真实执行混杂。 | `native_interviewer` 是公开主链路；Graph 保留为 shadow/debug/eval；legacy 仅显式 fallback/回滚窗口。 |
| 用户可见 writer | 后端 guard/projector、runtime、前端都可能拼接签证官话术。 | 用户可见回答由后端 runtime 统一产生；前端不再自造 officer follow-up。 |
| 聊天主线 | 上传状态、系统状态、debug 信息可能进入聊天窗口。 | 聊天只显示用户与签证官对话；Case Board、timeline、debug panel 承接系统状态。 |
| Gate 边界 | Gate-first 假设导致材料缺失阻断问答。 | Gate 降级为兼容展示层；风险、冲突、未知由 Governor/Case Memory/trace 管理。 |
| Case Memory | 案件事实散落在 metadata/artifact/fallback 文案中。 | Case Memory / Evidence Graph 一等化，可查询 claims、evidence、proof points、conflicts、resolutions。 |
| 上传理解 | 旧 OCR/checklist/gate_parse 语义污染对话和状态。 | material-understanding-first；上传失败/解析失败进入 materials/timeline/debug，不变成签证官消息。 |
| 前端状态 | message/case/debug state 混杂。 | 聊天、Case Board、Debug Panel 分层；角色模型向 `assistant` 收口，禁止新 `officer` role。 |
| 数据库 | 生产使用 SQLite，SSE/worker 并发写入风险高。 | 生产使用 Postgres；迁移数据已写入并验证计数一致。 |
| 运行拓扑 | `ds160-agent2` 单容器承担 API、Web、worker。 | `ds160-api`、`ds160-web`、`ds160-worker`、`postgres`、`nginx` 分离；旧 combined 仅保留 profile 回滚。 |
| 发布可观测性 | `/healthz` 粗粒度，版本信息不稳定。 | `/livez`、分层 `/healthz`、`/version`、build metadata、release-preflight、结构化日志。 |

## 本轮主要提交

| 提交 | 作用 |
| --- | --- |
| `b76fe8d` | 集中完成 runtime cleanup、Case Memory/Evidence Graph、上传理解、前端状态分层、debug timeline、split Compose、Postgres runbook、测试和文档。 |
| `a5b022c` | 增加 Docker `UV_HTTP_TIMEOUT`，避免依赖下载默认 30 秒超时。 |
| `1b70176` | 增加 `SKIP_DOCKER_BUILD=1`，支持预加载镜像后在服务器无构建 cutover。 |
| `fe6f460` | 降低 cutover 资源压力：先 Postgres，迁移用一次性容器，迁移后再启动 API/Web/Worker。 |
| `2ec63cb` | 增加 migration timeout、失败 rollback 尝试、combined 恢复脚本。 |
| `8053ed5` / `5ccc3c7` | 生成并修正中间更新报告。 |
| `9319bec` | 放宽 worker healthcheck timeout。 |
| `69d9a92` | 改用轻量 worker healthcheck，避免导入完整 app 导致弱服务器超时。 |

## 修复内容

### Runtime 与体验

- 统一公开 runtime 语义，避免“显示 graph、实际跑 native/legacy”的认知偏差。
- 收敛唯一用户可见 writer，防止前端和后端多处拼接签证官话术。
- 移除聊天主线里的 system/debug/status 污染。
- 冻结 legacy runtime：保留一个发布周期作为显式回滚开关，后续再删除。

### Case Memory 与证据推理

- 将材料理解、用户陈述、冲突、resolution、proof points 写入 Case Memory / Evidence Graph。
- runtime、report、OpenAI-compatible、OpenAI Responses metadata、debug snapshot 都改为消费统一案件事实层。
- 明确 unknown / conflict / confirmed 语义，避免“没有证据”等于“失败”。

### 上传链路

- 上传主路径转为 material understanding first。
- 415、不支持格式、损坏 PDF、parse failed、material_understanding.failed 都有可见状态。
- 上传结果进入 materials、activity、timeline、debug panel，不直接污染聊天主线。

### 前端与调试

- 新增/强化 message source、Case Board presentation、upload feedback 合同。
- Runtime debug snapshot 包含 timeline、material failure、redaction、runtime metadata、Case Board 和 Evidence Graph。
- 前端旧“关键证明/待证明点/薄弱证明点/材料齐套”口径已从用户路径收口。

### 运维与可靠性

- Compose 拆分为 API/Web/Worker/Postgres/Nginx。
- 新增 SQLite -> Postgres migration CLI，支持 dry-run/write/truncate-target/计数输出。
- 新增 production cutover 脚本、combined recovery 脚本和 release-preflight。
- 避免远程构建：本地 Windows Docker 构建镜像，传输到服务器 `docker load`。
- 修复 worker healthcheck：从完整 app health import 改为轻量 SQLAlchemy `select 1`，生产 worker 已 healthy。

## 生产切换过程

### 第一次尝试

- 旧 combined 停止、SQLite 和 `.env` 备份完成。
- 远程 Docker build 在 `pymupdf==1.26.7` 下载/解压阶段超时。
- 立即恢复旧 combined 服务，公网 `/healthz` 恢复 200。
- 后续修复：增加 `UV_HTTP_TIMEOUT` build arg。

### 第二次尝试

- 改为本地 Windows Docker 构建、传输 image tar、服务器 `docker load`。
- 镜像加载成功：`ds160-agent2:latest`，image id `bfce27d78f95`。
- 初始 cutover 停旧服务、启动 Postgres/API 后卡在 dry-run，服务器进入 SSH banner timeout。
- 服务器恢复后确认 split services 已起来，但 Postgres 为空。
- 重新停止 unhealthy worker，使用 SQLite 备份重新执行 dry-run 和正式写入迁移。

### 恢复后最终处理

- dry-run 成功：源库 `sessions=40`、`session_turns=272`、`documents=110`、`document_chunks=109`、`evidence_items=333`、`jobs=4`、`auth_sessions=19`、`case_memory_snapshots=0`；目标 Postgres 为空。
- 正式迁移成功：`copied_counts` 与 `source_counts` 完全一致。
- 重启 API/Worker/Nginx，补齐远程 `.env` build metadata。
- worker healthcheck 改为轻量 DB probe 后恢复 healthy。
- 公网 `/healthz`、`/api/version`、根路径均返回 200。

## 生产最终验证

### 服务拓扑

服务器工作树：

- `HEAD=69d9a92`

运行服务：

- `ds160-api`: healthy
- `ds160-web`: healthy
- `ds160-worker`: healthy
- `ds160-postgres`: healthy
- `ds160-nginx`: running
- 旧 `ds160-agent2`: 未运行

### 数据库

生产数据库方言：

- `postgresql+psycopg`

迁移后计数：

- `sessions=40`
- `session_turns=272`
- `documents=110`
- `document_chunks=109`
- `evidence_items=333`
- `jobs=4`
- `auth_sessions=19`
- `case_memory_snapshots=0`

迁移证据文件：

- `.deploy-backups/20260530T160105Z-split-postgres-cutover/app.sqlite3.backup`
- `.deploy-backups/20260530T160105Z-split-postgres-cutover/migration-dry-run-retry-20260531T052543Z.json`
- `.deploy-backups/20260530T160105Z-split-postgres-cutover/migration-write-retry-20260531T052638Z.json`

### 健康检查

公网 `https://ds160.efastt.store/healthz` 返回：

- `status=ok`
- `app.version=0.1.2`
- `app.git_sha=1b70176`
- `app.build_time=2026-05-30T15:53:58Z`
- `database.dialect=postgresql`
- `llm.status=configured`

公网 `https://ds160.efastt.store/api/version` 返回：

- `version=0.1.2`
- `git_sha=1b70176`
- `build_time=2026-05-30T15:53:58Z`

公网根路径返回：

- HTTP 200
- Next.js HTML

## 本地验证

最近一轮本地验证：

```bash
uv run pytest -q tests/unit/test_docker_compose_contract.py -m "not live_llm"
docker compose config --quiet
git diff --check
```

结果通过。

历史验证已覆盖：

- 全量非 live 回归：`598 passed, 11 deselected`
- Graph replay corpus：`fixture_count=13`，`passed=true`
- Focused live LLM smoke：`6 passed, 1 deselected`
- 前端 message source contract：`4 passed`
- 前端 Case Board presentation contract：`7 passed`
- 前端 upload feedback contract：`7 passed`
- 前端 `type-check`、`lint`、`build` 通过
- 本地 Docker/Postgres smoke 和本地 SQLite -> Postgres dry-run/write 均通过

## 剩余后续项

这些不阻塞本次目标完成，但应作为后续发布窗口任务：

- Legacy runtime 删除：按 `legacy-runtime-deprecation-decision.md`，生产 cutover 后保留一个发布周期，再删除 legacy live path、settings enum、fail-open fallback 和旧测试。
- 重新构建应用镜像到最新 HEAD：当前运行 app image 是 `1b70176`，后续可在下次正常发布中构建 `69d9a92+` 镜像，使 `/version` 与工作树 HEAD 完全一致。
- 远程 UI 上传 smoke：当前后端和公网 smoke 已完成；后续可补一次真实浏览器上传 `.txt` 415 和损坏 PDF parse failed 的线上 UI 验证。

## 最终结论

本次从架构、代码、测试、运维和生产状态上完成了核心清理：runtime ownership 收敛、Case Memory/Evidence Graph 一等化、上传理解主路径、前端状态分层、debug 可观测、Postgres 迁移、split services 生产拓扑和发布恢复机制均已落地。生产当前可用，公网健康，数据已迁移到 Postgres，worker 已恢复 healthy。
