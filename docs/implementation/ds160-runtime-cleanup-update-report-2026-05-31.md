# DS-160 Runtime 清理与生产切换更新报告

> **历史快照说明（2026-06-06 文档刷新）**：本文是 2026-05-31 中途更新报告，保留当时生产 cutover 尚未完成、SSH/health 超时等现场证据。它不代表当前运行状态，也不应作为恢复或部署步骤执行。当前公开用户链路以 `native_interviewer` 为唯一 public runtime；文中 legacy/Graph/cutover 相关内容仅用于理解历史迁移过程。当前操作说明请优先看 `README.md`、`docs/API.md`、`docs/runtime-contracts.md`、`deploy/README.md` 和相关 architecture runtime 文档。


生成时间：2026-05-31
分支：`refactor/agent-runtime-graph`
实施基线 HEAD：`2ec63cb fix: add production recovery guardrails`
覆盖范围：从 `c299f7c` 之后到 `2ec63cb` 的本轮实施、验证、生产切换尝试与恢复保护；本报告文件自身的提交不改变实施结论。

## 一句话结论

本轮已完成 runtime、Case Memory、上传理解、前端状态分层、测试评估、发布门禁和 split/Postgres 运维路径的大部分代码与文档实施；但远程生产 cutover 尚未完成。生产服务器在第二次 cutover 的 migration dry-run 阶段后进入 `sshd` banner timeout / 公网 health timeout 状态，当前必须先通过服务商控制台或服务器自恢复拿回 SSH，再继续恢复旧 combined 服务或重跑低资源 cutover。

## 当前权威状态

- 本地工作树：干净。
- 本地分支：`refactor/agent-runtime-graph`。
- GitHub 远端实施基线：`2ec63cbc74b360b64203c3c52d69537024fdc1d5`。
- 实施基线提交：`2ec63cb fix: add production recovery guardrails`。
- 服务器：`root@conectv6.302dog.icu`，目录 `/opt/ds160-agent2`。
- 生产公网：`https://ds160.efastt.store/healthz` 当前超时。
- SSH：TCP 可连接，但 `sshd` 不返回 banner，报 `Connection timed out during banner exchange`。
- 因为无法登录服务器，生产 DB 方言、服务拓扑、迁移计数和 `/version` 当前不能重新验证。

## 架构更新前后对比

| 维度 | 更新前 | 更新后 |
| --- | --- | --- |
| 产品主线 | 项目体验残留“材料清单 / checklist SaaS”口径，聊天容易围绕补材料打转。 | 统一为 DS-160 AI 面签工作台：面签问答、案件理解、证据推理、冲突处理、复盘。 |
| Runtime 所有权 | `graph`、`native_interviewer`、legacy runtime 命名和真实执行路径混杂。 | 明确 `native_interviewer` 是公开主链路，Graph 作为 shadow/debug/eval 路径，legacy 仅保留显式 fallback/回滚窗口。 |
| 用户可见 writer | 后端 guard/projector、runtime、前端状态都可能拼接签证官话术。 | 用户可见回答收敛到后端 runtime writer；前端不再自造 officer follow-up/system pseudo message。 |
| 聊天信息架构 | 上传状态、系统状态、debug 信息可能进入聊天主线。 | 聊天主线只保留用户与签证官对话；Case Board、timeline、debug panel 承接系统状态。 |
| Gate 边界 | Gate-first 历史假设导致“材料不足/字段不全”阻断问答。 | Gate 降级为兼容展示层；风险、冲突、未知边界进入 Governor/Case Memory trace，不直接篡改话术。 |
| Case Memory | 案件事实多散落在 metadata/artifact/fallback 文案里。 | Case Memory / Evidence Graph 一等化，claims、evidence、conflicts、resolutions、proof points 可被 runtime/report/debug 消费。 |
| 上传理解 | 旧 OCR/checklist/gate_parse 语义容易污染对话与状态。 | 上传主路径转向 material understanding first；失败节点进入 timeline/materials/debug，不变成签证官消息。 |
| 前端状态 | message state、case state、debug state 边界不清。 | 聊天、Case Board、Debug Panel 状态分层；角色模型向 `assistant` 收口，禁止新 `officer` role。 |
| 数据库 | 生产仍是 combined container + SQLite，SSE/worker 并发写入风险高。 | Compose 默认拆为 Postgres + API + Web + Worker + Nginx；本地 Postgres dry-run/实写迁移已验证，远程迁移未完成。 |
| 运维拓扑 | 单容器 `ds160-agent2` 承担 API、Web、worker；nginx 代理旧容器。 | 默认 split services：`ds160-api`、`ds160-web`、`ds160-worker`、`postgres`、`nginx`；旧 combined 仅在 `profiles: ["combined"]` 下作为回滚兼容。 |
| 健康与发布 | `/healthz` 只能粗略证明进程可用；构建版本不稳定可追踪。 | 增加 `/livez`、分层 `/healthz`、`/version`、build metadata、release-preflight、结构化日志。 |
| 调试与评估 | Debug bundle、runtime trace、fallback 观测不统一。 | Runtime debug timeline、snapshot redaction、graph replay corpus、preflight gates 和合同测试补齐。 |

## 本轮提交与更新内容

| 提交 | 主题 | 主要内容 |
| --- | --- | --- |
| `b76fe8d` | `feat: consolidate runtime cleanup and cutover path` | 集中完成 A-L 清理主干：runtime ownership、Case Memory/Evidence Graph、上传理解、前端状态分层、debug timeline、release-preflight、split Compose、Postgres migration runbook、测试与文档。 |
| `a5b022c` | `fix: increase docker dependency download timeout` | 为 Docker build 增加 `UV_HTTP_TIMEOUT` build arg，避免远程构建在 `pymupdf` 下载/解压时使用默认 30 秒超时。 |
| `1b70176` | `fix: allow production cutover without remote docker build` | 给生产 cutover 加 `SKIP_DOCKER_BUILD=1`，支持预加载镜像后直接启动服务，避免在小服务器上构建。 |
| `fe6f460` | `fix: reduce production cutover resource pressure` | 降低 cutover 资源压力：先启动 Postgres，迁移用一次性容器读取 SQLite 备份，迁移后再启动 API/Web/Worker。 |
| `2ec63cb` | `fix: add production recovery guardrails` | 增加 migration 超时、失败 rollback 尝试、`production-recover-combined.sh` 恢复脚本，并更新 runbook/progress/task audit。 |

代码规模：`c299f7c..2ec63cb` 共 125 个文件变更，约 `11519` 行新增、`779` 行删除。

## 已修复和已完成的重点

### Runtime 与聊天主线

- 明确 runtime 角色：公开路径由 `native_interviewer` 承担，Graph 不再伪装成已完全公开主链路。
- 冻结 legacy runtime：仅允许显式 `AGENT_RUNTIME=legacy` 或显式 fail-open fallback；删除安排推迟到生产 cutover 后一个发布周期。
- 收敛用户可见 writer：签证官话术由后端 runtime 产生，前端不再拼接 follow-up。
- 收口 role 模型：前端消息合同禁止新 `officer` role，统一向后端 `assistant` 输出对齐。
- 补齐 graph replay corpus，覆盖完整成功路径、目的回答推进、拒绝编造、I-20 可视材料更新 Case Memory 等场景。

### Case Memory / Evidence Graph

- 将案件事实从 metadata/artifact fallback 提升为可查询的 Case Memory / Evidence Graph。
- 补齐 claims、evidence、proof points、conflicts、resolutions、tombstone、open proof point 的服务和测试覆盖。
- 统一 runtime、report、OpenAI-compatible、OpenAI Responses metadata、debug snapshot 对 Case Board / Evidence Graph 的消费。
- 固化“未知不等于否定”：unknown、conflict、confirmed 在报告、问答、风险判断中分开处理。

### 上传与材料理解

- 上传主路径转向 material understanding first，减少旧 OCR/checklist/gate_parse 对产品语义的污染。
- 上传成功/失败不再直接变成签证官消息；状态进入 materials、activity、timeline、debug panel。
- 文件解析失败可见化：415、不支持格式、损坏 PDF、parse failed、material_understanding.failed 都有明确状态。
- 加入前端 upload feedback 合同测试，避免失败再次变成内部术语或聊天污染。

### 前端状态与体验

- 聊天、Case Board、Debug Panel 分层，避免系统/debug/status 信息混进聊天主线。
- Case Board presentation policy、message-source policy、upload-feedback policy 成为前端合同层。
- 移除或冻结用户路径里的“关键证明/待证明点/薄弱证明点/材料齐套/补齐一套”等旧材料清单口径。
- 前端测试覆盖消息来源、Case Board 展示、上传反馈，防止 UI 再次回到 checklist 体验。

### Debug、日志、健康检查

- 标准化 runtime debug snapshot：timeline、material failure、redaction、runtime metadata、Case Board、Evidence Graph。
- 增加 JSON 结构化日志，覆盖 app/uvicorn，并保留 session/run/turn/document 关联字段入口。
- 增加 `/livez` 与分层 `/healthz`，区分进程存活、DB ready、LLM configured、worker readiness。
- `/version` 支持 git sha/build time，Docker build args 支持注入前后端版本信息。

### 数据库与运维

- `docker-compose.yml` 默认拆为 `postgres`、`ds160-api`、`ds160-web`、`ds160-worker`、`nginx`。
- `ds160-agent2` 旧 combined 容器保留在 `combined` profile 作为回滚路径。
- Compose 内环境改为 `COMPOSE_DATABASE_URL -> DATABASE_URL`，避免本地 `.env` 中 SQLite 覆盖生产 Postgres 默认值。
- 新增 `migrate-sqlite-to-postgres` CLI，支持 dry-run、write、truncate-target、计数输出和 URL 脱敏。
- 新增 `release-preflight`，聚合 replay、focused non-live tests、live smoke、Docker/Postgres smoke、rollback docs、implementation report 门禁。
- 新增 `scripts/production-split-postgres-cutover.sh` 和 `scripts/production-recover-combined.sh`，分别用于受保护的生产迁移和恢复旧 combined 服务。

## 验证证据

已完成并记录的主要验证包括：

- 全量非 live 回归曾刷新到 `598 passed, 11 deselected`。
- Graph replay corpus：`fixture_count=13`，`passed=true`。
- Focused live LLM smoke：`6 passed, 1 deselected`。
- Frontend message source contract：`4 passed`。
- Frontend Case Board presentation contract：`7 passed`。
- Frontend upload feedback contract：`7 passed`。
- Frontend `type-check`、`lint`、`build` 通过。
- 本地 Windows Docker Compose smoke：`postgres`、`ds160-api`、`ds160-web`、`ds160-worker` 均 healthy；API `/livez`、`/healthz`、`/version` 正常。
- 本地 SQLite -> Compose Postgres dry-run 和实写迁移成功，`copied_counts` 与 `source_counts` 一致。
- 本地 nginx edge smoke 使用临时 nginx 容器绕过 WSL bind mount，`/healthz`、`/api/version`、根路径均验证通过。
- 最近一次 guardrail 回归：`bash -n`、`tests/unit/test_deploy_scripts.py`、`tests/unit/test_docker_compose_contract.py`、`docker compose config --quiet`、`git diff --check` 均通过。

## 生产 cutover 现场记录

### 第一次远程 cutover

- 旧 combined 容器停止，SQLite 和 `.env` 已备份。
- 远程 Docker build 在 `pymupdf==1.26.7` 下载/解压阶段超时失败。
- 已手动 `docker start ds160-agent2` 恢复旧 SQLite 服务。
- 之后补充 `UV_HTTP_TIMEOUT` build arg，避免默认 30 秒超时。

### 第二次远程 cutover

- 改为本地 Windows Docker 构建，传输 image tar 到服务器并 `docker load`。
- 服务器成功加载 `ds160-agent2:latest`，image id：`bfce27d78f95`。
- 加载镜像内应用版本：`APP_GIT_SHA=1b70176`。
- cutover 使用 `SKIP_DOCKER_BUILD=1`，不在服务器构建。
- 备份目录：`.deploy-backups/20260530T160105Z-split-postgres-cutover`。
- 旧 `ds160-agent2` 已停止，SQLite 备份已复制。
- `postgres` 与 `ds160-api` 曾启动到 healthy。
- 卡在 `migrate-sqlite-to-postgres --dry-run` 后，服务器进入 `sshd` banner timeout，公网 `/healthz` 超时。

## 当前未完成项

当前不能把总目标标记为完成，原因是以下生产证据缺失：

- 生产旧 combined 是否已恢复健康：未验证。
- 生产 Postgres 是否完成正式写入迁移：未完成/未验证。
- 生产 split services 是否最终运行：未验证。
- 生产 DB 方言是否为 `postgresql`：未验证。
- 生产迁移计数是否匹配旧 SQLite：未验证。
- 生产 `/version` 是否显示最新 git sha/build time：未验证。
- 公网 `https://ds160.efastt.store/healthz` 是否恢复：当前超时。

## 后续恢复顺序

如果 SSH 恢复，优先不要直接重跑 cutover。先恢复一个可用基线：

```bash
cd /opt/ds160-agent2
git fetch origin refactor/agent-runtime-graph
git pull --ff-only origin refactor/agent-runtime-graph
scripts/production-recover-combined.sh
```

如果远端尚未拉到 `2ec63cb` 或恢复脚本不可用，使用等价手工恢复：

```bash
cd /opt/ds160-agent2
docker compose stop ds160-worker ds160-api ds160-web postgres || true
docker start ds160-agent2
curl -k -fsS https://127.0.0.1:18000/healthz -H 'Host: ds160.efastt.store'
curl --noproxy '*' -fsS https://ds160.efastt.store/healthz
```

恢复健康后，再重跑低资源 cutover：

```bash
CONFIRM_PRODUCTION_CUTOVER=I_UNDERSTAND_PRODUCTION_CUTOVER \
RUN_WRITE_MIGRATION=1 \
SKIP_GIT_PULL=1 \
SKIP_DOCKER_BUILD=1 \
MIGRATION_TIMEOUT_SECONDS=600 \
ROLLBACK_ON_FAILURE=1 \
scripts/production-split-postgres-cutover.sh
```

## 最终完成标准

后续继续处理时，需要拿到这些证据后才能标记完成：

- 服务器可 SSH 登录。
- 旧 combined 已恢复，或 split services 已完整健康。
- `docker compose ps` 显示 `postgres`、`ds160-api`、`ds160-web`、`ds160-worker`、`nginx` 处于目标状态。
- `/livez`、`/healthz`、`/version` 在 API 内部和 nginx edge 均可用。
- `/healthz` 的 database dialect 为 `postgresql`。
- SQLite 迁移前计数与 Postgres 迁移后计数一致。
- 公网 `https://ds160.efastt.store/healthz` 返回 `200`。
- `/version` 和前端 badge 显示发布 commit/build time。
- 更新最终报告，明确生产 cutover 完成证据和 rollback 点。

## 本次结论

代码层面和本地验证已经把长期架构问题向目标状态推进了一大步：runtime ownership、单 writer、Case Memory、一等 evidence、上传理解、前端状态分层、debug 可观测、Postgres 拓扑、发布门禁和恢复脚本都已落地。当前剩余问题不再是本地代码缺口，而是生产服务器在 cutover 中进入不可登录状态。等服务器恢复后，应先恢复旧 combined 服务到可用基线，再使用已经推送的低资源脚本继续完成生产迁移与最终报告。
