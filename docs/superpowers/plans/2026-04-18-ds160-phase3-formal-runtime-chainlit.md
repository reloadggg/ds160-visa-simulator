# DS-160 Phase 3 Formal Runtime + Chainlit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已完成 Phase 1 `evidence foundation` 与 Phase 2 `PydanticAI runtime cutover` 的基础上，补齐 DS-160 v1 剩余的正式工作流能力：`gate_review runtime`、`runtime trace/history`、更稳定的 interview runtime 编排，以及 `Chainlit` 受控 UI 接入。

**Architecture:** 保持 `FastAPI + SQLAlchemy + SQLite + PydanticAI` 单体。业务事实继续以后端为唯一真源，`Chainlit` 只做 API 编排层。Phase 3 不再重复建设 extractor/scoring/question tools，而是把这些能力纳入正式流程状态机与可追溯报告。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, SQLite, PydanticAI, Chainlit, httpx, pytest

---

## 0. 规划审阅结论

基于当前仓库状态和既有 superpowers 文档，本次 Phase 3 需要先纠正一个语义漂移：

1. **全局项目 Phase 3** 不是 `Chainlit integration` 文档里的 UI Phase 3。
2. 最早路线图把 `PydanticAI tools + Chainlit` 合并称为“后续 Phase 3”，但其中 `PydanticAI tools` 已在已提交的 Phase 2 中完成。
3. 因此现在真正剩余的全局 Phase 3 应聚焦于：
   - 正式 `gate_review runtime`
   - 正式 `interview runtime`
   - `runtime_trace / score_history / governor_history`
   - `Chainlit` UI 接入与受控补证体验

这份计划以“最小正式版”为目标，不把 scope 拉回 durable execution 或复杂前端平台。

## 1. 现状与缺口

当前已完成：

- 文档上传、排队、解析、证据入库、profile 重算
- `search_evidence / get_evidence_excerpt / extract_document_fields`
- `ExtractorService / ScoringService / MessageService` 主路径切到 `PydanticAI`
- `GovernorService` 仍保留为最终硬裁决层

当前主要缺口：

1. `required_initial_package` 只是读取 policy pack，还没有正式状态表或阻塞逻辑。
2. `phase_state` 只有字符串字段，没有真正驱动 gate/interview 切换。
3. `MessageService` 仍是顺序调用，缺少可回放的 runtime 节点 trace。
4. `ReportService` 的 `runtime_trace / score_history / governor_history` 仍是占位或极简输出。
5. 还没有 `Chainlit` UI，演示与人工审阅只能直接打 API。

## 2. 范围与非目标

### 2.1 本阶段范围

本次 Phase 3 只做以下内容：

- 建立正式 `gate_review runtime`
- 让 session 具有可持久化的 required-doc 状态
- 建立最小 `runtime_trace / score_history / governor_history`
- 将 interview 主流程包装成显式 runtime 节点编排
- 挂载 `Chainlit` 到 `/ui`
- 完成“签证家族选择 -> 问答 -> 定向补证 -> 报告查看”闭环

### 2.2 明确不在本阶段内

以下内容不进入本计划：

- 重新设计或替换 Phase 2 agent tool 层
- `pydantic-graph` / `Temporal` / durable execution
- 多 agent 图编排
- 自定义 React 复杂组件
- 大范围扩展 policy packs 为完整规则引擎
- 新建独立前端服务或网关

## 3. 方案选择

### 3.1 方案 A：先做 UI，再回填 runtime

优点：

- 视觉成果最快

缺点：

- UI 会继续建立在“伪阶段机”上
- 之后补 gate/trace 时容易返工

### 3.2 方案 B：先做 runtime/trace，再接 UI

优点：

- 后端状态和报告先稳定
- UI 只是薄壳，边界清晰
- 更符合现有“后端唯一真源”原则

缺点：

- 第一批成果更偏基础设施

### 3.3 选型结论

采用 **方案 B**：

1. 先建立 `gate_review runtime`
2. 再补 `runtime trace/history`
3. 然后把 `MessageService` 包进正式 interview runtime
4. 最后接 `Chainlit`

## 4. 总体设计

### 4.1 Session 状态模型

建议将 session 从“单个 `phase_state` 字符串”提升为两层状态：

- `phase_state`
  - `intake`
  - `gate_review`
  - `interview`
  - `finalized`
- `gate_status_json`
  - 每个 required doc 的状态
  - 是否已上传
  - 是否已解析
  - 是否已通过最小字段检查

这样 `required_initial_package` 才不只是静态清单，而是实际 runtime 输入。

### 4.2 Gate Runtime

`gate_review runtime` 负责：

- 从 policy pack 读取 required docs
- 读取当前 session 的 document/job/evidence 状态
- 判断哪些材料：
  - 尚未上传
  - 已上传未解析
  - 已解析但字段不达标
- 输出 gate 结果：
  - `pass`
  - `need_more_documents`
  - `waiting_for_parse`

只有 gate pass 后，才允许进入正式 `interview`。

### 4.3 Interview Runtime

不直接把 Phase 2 的顺序逻辑拆掉重来，而是先包装成固定节点：

1. `receive_input`
2. `extract_claims`
3. `resolve_evidence`
4. `consistency_check`
5. `score_case`
6. `governor_decide`
7. `build_next_action`

V1 仍然用显式 Python 编排，不引入图引擎。

### 4.4 Runtime Trace 与 History

每轮消息至少写入：

- `runtime_trace`
  - 节点名
  - 输入摘要
  - 输出摘要
  - 关键 evidence refs
- `score_history`
  - 轮次
  - 评分阶段
  - 维度分数
  - risk flags
- `governor_history`
  - 决策
  - 决策依据摘要

用户报告继续保持简洁，内部报告开始真正反映运行轨迹。

### 4.5 Chainlit 边界

`Chainlit` 只做：

- 创建 session
- 选择签证家族
- 调消息 API
- 根据后端返回触发受控上传
- 查看用户/内部报告

`Chainlit` 不做：

- 直接调 service
- 直接查数据库
- 本地维护 `ApplicantProfile`
- 自己决定是否可继续 interview

## 5. 文件规划

**Create**

- `app/domain/runtime.py`
- `app/services/gate_runtime_service.py`
- `app/services/interview_runtime_service.py`
- `app/services/runtime_trace_service.py`
- `app/ui/chainlit_client.py`
- `chainlit_app.py`
- `.chainlit/config.toml`
- `tests/unit/test_gate_runtime_service.py`
- `tests/unit/test_runtime_trace_service.py`
- `tests/integration/test_gate_review_runtime.py`
- `tests/integration/test_interview_runtime_trace.py`
- `tests/integration/test_chainlit_mount.py`

**Modify**

- `app/db/models.py`
- `app/repositories/session_repo.py`
- `app/api/routers/sessions.py`
- `app/api/routers/messages.py`
- `app/api/routers/reports.py`
- `app/services/gate_service.py`
- `app/services/message_service.py`
- `app/services/report_service.py`
- `app/main.py`
- `pyproject.toml`
- `tests/integration/test_sessions_api.py`
- `tests/integration/test_messages_api.py`
- `tests/integration/test_reports_api.py`

## 6. Phase 3 Acceptance Criteria

- session 能持久化 required-doc gate 状态，而不是只返回静态清单
- 未通过 `gate_review` 前，系统不会进入正式 interview
- 上传文件后，gate 状态会随着 parse worker 与字段检查变化
- 每轮 interview 至少写入一条 runtime trace、score history、governor history
- 内部报告能返回真实 trace/history，而不是空数组
- `/ui` 可用，并能完成最小闭环：
  - 选签证家族
  - 发消息
  - 被要求补证
  - 上传材料
  - 继续对话
  - 查看报告

## 7. 任务拆分

### Task 1: 固化 session runtime 状态模型

**目标**

- 为 gate/interview 提供可持久化状态，不再只靠 `phase_state` 字符串和临时推断

**需要做**

- 给 `SessionRecord` 增加：
  - `gate_status_json`
  - `runtime_trace_json`
  - `score_history_json`
  - `governor_history_json`
- 定义对应的 runtime domain model
- 更新 `SessionRepository`

**验证**

- 新 session 自动生成 required-doc 状态骨架
- 旧报告接口仍可读

### Task 2: 建立 gate_review runtime

**目标**

- 真正实现“必需材料包未完成前，不进入 interview”

**需要做**

- 新建 `GateRuntimeService`
- 将 doc status、job status、evidence field coverage 汇总为 gate 结果
- 在 session 创建后初始化 gate 状态
- 在 parse 完成后刷新 gate 状态

**验证**

- `tests/unit/test_gate_runtime_service.py`
- `tests/integration/test_gate_review_runtime.py`

### Task 3: 将 MessageService 包装为正式 interview runtime

**目标**

- 让消息处理从“隐式顺排”升级为“显式节点 runtime”

**需要做**

- 新建 `InterviewRuntimeService`
- 将现有 extractor/consistency/scoring/governor/question 串为固定节点
- 为每个节点产出 trace entry
- 继续保留现有 API 契约

**验证**

- `tests/integration/test_messages_api.py`
- `tests/integration/test_interview_runtime_trace.py`

### Task 4: 建立 runtime trace/history 写入与报告读取

**目标**

- 让报告真正反映系统行为，而不是占位结构

**需要做**

- 新建 `RuntimeTraceService`
- 统一写入 trace/history
- 改造 `ReportService.internal_report`
- 让 `user_report` 能根据真实 gate/trace 生成更准确文案

**验证**

- `tests/integration/test_reports_api.py`
- 内部报告包含真实节点、评分与 governor 记录

### Task 5: 接入 Chainlit UI

**目标**

- 为当前后端提供可操作的轻量演示前端

**需要做**

- 增加 `chainlit` 依赖
- 挂载 `/ui`
- 实现签证家族选择、消息转发、受控上传、报告查看
- 禁用自由上传

**验证**

- `tests/integration/test_chainlit_mount.py`
- 本地启动后 `/ui` 可访问

### Task 6: 补 Phase 3 回归与交付文档

**目标**

- 确保 gate/runtime/UI 没有破坏 Phase 1/2 已有闭环

**需要做**

- 跑 Phase 1/2/3 关键集成测试
- 更新 README 或启动说明
- 记录运行方式：
  - API only
  - API + Chainlit

**验证**

- 回归测试通过
- 文档可让他人单机复现

## 8. 实施顺序建议

建议严格按以下顺序推进：

1. session runtime 状态模型
2. gate_review runtime
3. interview runtime trace/history
4. 报告改造
5. Chainlit 接入
6. 总回归

原因：

- `Chainlit` 依赖后端状态清晰
- 报告准确性依赖 trace/history
- gate runtime 不先做，UI 补证流程会继续漂

## 9. 风险与防线

### 风险 1：把 Phase 3 做成“大重构”

防线：

- 保留现有 API 契约
- 以 service 包装与持久化增强为主

### 风险 2：UI 反过来定义业务状态

防线：

- `Chainlit` 只缓存最小会话字段
- 所有关键状态以后端返回为准

### 风险 3：trace/history 设计过重

防线：

- v1 先用 JSON 挂在 session 上
- 不提前引入独立 trace 表或 durable runtime

### 风险 4：gate field check 做得过细，拖慢交付

防线：

- v1 先只覆盖当前最关键 required docs
- 先把 F1 资金证明路径做完整，再扩 family

## 10. 交付判断

满足以下条件时，可认为全局 Phase 3 最小版完成：

- 后端具备 gate/interview 的正式 runtime 边界
- trace/history 不再是空壳
- `/ui` 能完整演示主流程
- Phase 1/2 的证据链与 Governor 护栏没有被回退

届时可以更准确地说：

- “最小可用后端” 已完成
- “最小可用产品闭环” 也已完成

下一阶段才考虑：

- 更丰富 policy packs
- 自定义报告 UI
- `pydantic-graph` / `Temporal`
- 人审与暂停恢复
