# brainstorm: DS160 v0前端接入FastAPI

## Goal

将 `Agent2.0` 工作树中的 v0 生成前端接入现有 FastAPI 后端，逐步替换当前挂载在 `/ui` 的 Chainlit 前端，同时保持现有 Apple 风格界面、中文文案、桌面优先布局，以及“模拟面签 / 教练视图”仅切换展示密度而不重置会话状态。

## What I already know

* 现有后端主仓库是 FastAPI 单体应用，核心接口位于 `app/api/routers/`，当前已支持：
  * `POST /v1/sessions`
  * `GET /v1/sessions/{session_id}/required-package`
  * `POST /v1/sessions/{session_id}/messages`
  * `POST /v1/sessions/{session_id}/files`
  * `GET /v1/sessions/{session_id}/reports/user`
  * `GET /v1/sessions/{session_id}/reports/internal`
* v0 新前端不在仓库中展开，而是以压缩包形式存在于 `.worktrees/Agent2.0/b_vfpJBmeR2ox.zip`。
* 该 zip 已解压到仓库内 `web/`，是一个独立 Next.js App Router 项目，包含：
  * `app/page.tsx`
  * `lib/api/client.ts`
  * `lib/api/types.ts`
  * `lib/api/mock-data.ts`
  * `components/ds160/*`
* v0 前端当前特点：
  * `app/page.tsx` 体积较大，集中了 session 生命周期、消息发送、上传、报告拉取、UI 状态
  * `lib/api/client.ts` 将 `API_BASE_URL` 硬编码为 `http://localhost:8000`
  * `lib/api/mock-data.ts` 中 mock 模式默认开启，仅在 `NEXT_PUBLIC_MOCK === "false"` 时关闭
  * UI 签证值直接使用 `F-1 / J-1 / B-1/B-2 / H-1B`
* 当前后端真实契约比 v0 前端假设更丰富：
  * `create_session` 除 `session_id` 外还返回 `phase_state`、`current_governor_decision`、`gate_status`
  * `messages` 返回至少包含 `assistant_message`、`governor_decision`、`requested_documents`，并可能附带 `score_summary`、`turn_decision`、`prompt_trace`、`runtime_view_state`、`gate_progress`
  * `reports/user` 返回 `outcome_label`、`summary`、`current_key_proof`、`recommended_improvements`、`allowed_next_actions` 等 richer 字段
  * `reports/internal` 返回运行时轨迹、score/governor history、runtime ledger、runtime view state 等调试信息
  * `files` 返回 `document_assessment`、`document_type`、`feedback_message`、`main_flow_feedback`、`requested_documents`、`gate_progress`
* 后端签证家族值是后端枚举，不是 UI 标签：
  * `f1`、`j1`、`b1_b2`、`h1b`
* 当前 FastAPI `app/main.py` 尚未配置 `CORSMiddleware`；若 Next.js 以前后端分离形式运行，会被浏览器跨域策略拦截。

## Assumptions (temporary)

* 新前端确定作为仓库内独立目录 `web/` 接入，而不是直接解压到仓库根目录覆盖 Python 后端的 `app/`。
* 第一阶段目标是让 v0 前端完整接通真实后端，不在本轮重做视觉设计或大规模重构组件结构。
* Chainlit 仅在兼容成本低且不会拖慢切换时保留；若兼容成本偏高，本轮可放弃。

## Open Questions

* 已完成 `web/` 解压与初步阅读，下一步进入真实契约对齐与兼容性收敛。

## Requirements (evolving)

* 将 zip 中的 Next.js 项目以独立前端目录方式纳入当前仓库。
* 保留当前 v0 生成的页面结构、视觉风格、中文文案和桌面优先体验。
* 用环境变量管理 API 基础地址：
  * 使用 `NEXT_PUBLIC_API_BASE_URL`
  * 仅在开发环境保留安全的 localhost fallback
* 保留 mock mode，但改成显式、易关闭、可感知的机制。
* 在前端边界层建立 UI 签证值与后端 `declared_family` 枚举值的双向映射。
* 在前端边界层建立文档代码值到中文显示文案的映射，避免在 UI 中暴露 `funding_proof` 等内部代码。
* 扩展并规范化前端 API 类型，使其兼容真实后端 richer payload，同时保持核心字段类型安全。
* 将 `app/page.tsx` 中的 session 生命周期、发送消息、上传文件、刷新报告等副作用下沉到 hook 或 session 模块，保持页面可读性。
* 保持聊天主流程：
  * 选择签证后创建 session
  * 拉取 required package
  * 发送用户消息
  * 展示 officer 回复
  * 每轮成功后刷新右侧分析与报告数据
* 保持文件上传主流程：
  * 上传到当前 session
  * 显示上传 loading
  * 展示后端上传反馈
  * 刷新相关报告或会话状态
* 增强健壮性：
  * 可见的 loading 状态
  * 可见但克制的错误提示
  * 空状态
  * 避免 silent failure
* 保持右侧分析面板和报告弹窗由真实后端数据驱动。
* 为新前端补充最小必要的环境配置说明。
* 为前后端分离运行补上 CORS 支持或等价后端接入能力。

## Acceptance Criteria

* [ ] 仓库中存在一个独立的新前端目录，且不会与后端 `app/` 目录冲突。
* [ ] 新前端可以通过环境变量指向 FastAPI 后端，不再依赖硬编码地址。
* [ ] 关闭 mock 后，可以完成一次真实链路：
  * 创建 session
  * 获取 required package
  * 发送消息并显示 assistant 回复
  * 获取 user/internal report
  * 上传文件并看到反馈
* [ ] UI 中显示的是友好的签证名称和材料中文名，不直接暴露后端枚举或 document type 代码值。
* [ ] “模拟面签 / 教练视图”切换不会重置 `sessionId`、消息历史、报告状态。
* [ ] `app/page.tsx` 的职责明显收敛，核心 API 副作用已抽离。
* [ ] loading / error / empty state 均可见，不存在无提示失败。
* [ ] README 或环境示例文档包含 `NEXT_PUBLIC_API_BASE_URL` 和 `NEXT_PUBLIC_MOCK` 说明。
* [ ] FastAPI 与新前端本地联调时，不会被 CORS 阻断。

## Definition of Done (team quality bar)

* 单元或集成测试按改动范围补齐
* 前端构建可通过，后端测试不被回归破坏
* 关键流程可手工验证
* 必要文档已更新
* 保留平滑回退路径（Chainlit 或原入口不被粗暴删除）

## Research Notes

### Current page/component structure

* 页面入口主要集中在 zip 内 `app/page.tsx`
* 组件结构较清晰，已有可复用 DS160 组件：
  * `components/ds160/analysis-panel.tsx`
  * `components/ds160/chat-panel.tsx`
  * `components/ds160/report-modal.tsx`
  * `components/ds160/top-bar.tsx`
  * `components/ds160/visa-selector.tsx`
* `top-bar` 当前仅切换 tab 值，不会重置 session state，这与目标行为一致

### Current API assumptions in v0 frontend

* 认为 `create_session` 只返回 `session_id`
* 认为 `messages` 只需要 `assistant_message`
* 认为 `reports/user` 只包含较浅字段：
  * `risk_level`
  * `interview_status`
  * `current_key_question`
  * `missing_evidence`
  * `allowed_next_actions`
* 认为 `files` 只需读取 `document_assessment.main_flow_feedback.message`
* 未建立统一 response normalization / adapter 层

### Current mock-mode behavior

* mock 默认开启
* mock 数据集中在 `lib/api/mock-data.ts`
* mock 与真实 API 逻辑混在 `app/page.tsx` 中，导致页面职责过重

### Mismatches with backend contract

* UI 签证值与后端 `declared_family` 不一致
* 用户报告字段形状过浅，无法完整承载 `outcome_label`、`summary`、`current_key_proof`、`recommended_improvements`
* 上传返回体处理过浅，未充分利用 `requested_documents`、`gate_progress`、`feedback_message`
* 消息返回体未显式承接 `governor_decision`、`requested_documents`、`runtime_view_state`
* 缺少材料代码值到中文显示值的适配层
* API base URL 硬编码
* 跨域未处理

### Compatibility assessment after extraction

* `web/` 作为独立前端与 FastAPI API 对接，没有架构级阻塞点。
* `web/` 与 Chainlit 可以并存为两个前端入口，因为两者都只是消费同一组后端 HTTP API。
* 但两者不会天然共享前端会话状态：
  * Chainlit 依赖 `cl.user_session`
  * `web/` 当前依赖本地 React state
* 因此，Chainlit 更适合作为过渡期备用入口或调试入口，而不是与 `web/` 做同一会话的无缝切换。
* 若目标是“同一 FastAPI 进程直接挂载 Next.js 并像 Chainlit 一样内嵌”，实现成本会明显上升，不适合作为首轮目标。

### Constraints from current repo

* 当前仓库根目录主要是 FastAPI 后端，不适合直接混入新的 `app/` 前端目录
* `.trellis/spec/backend`、`.trellis/spec/frontend` 当前大多为模板，不能直接提供很强的项目约束，需要更多依赖现有代码模式
* 现有 README 默认入口仍是 FastAPI + Chainlit

### Feasible approaches here

**Approach A: 独立前端目录 + 前端适配层 + 保留 Chainlit 过渡**（Recommended）

* How it works:
  * 将 zip 解压到独立前端目录
  * 前端内部增加 `api config`、`types`、`mappers`、`session hook`
  * 后端补 `CORS`
  * 继续保留 Chainlit 作为回退入口
* Pros:
  * 风险最低
  * 最符合“保留现有 UI、只做高置信小改”
  * 便于分阶段联调与回滚
* Cons:
  * 短期内存在两个前端入口

**Approach B: 直接用 Next.js 替换 Chainlit 为唯一前端入口**

* How it works:
  * 接入新前端后立刻调整 README / 运行方式 / 默认入口
* Pros:
  * 产品形态更统一
* Cons:
  * 迁移风险更高
  * 首轮若契约仍有偏差，缺少备用路径

**Approach C: 先只做纯 mock 演示页，再第二轮接真实后端**

* How it works:
  * 先把 zip 纳入仓库，只保留 mock 运行
  * 第二轮再接 API
* Pros:
  * 接入速度快
* Cons:
  * 不能尽快暴露真实契约问题
  * 与当前目标不符

## Expansion Sweep

### Future evolution

* 后续很可能会增加更多签证家族；签证映射与文档映射应放在统一 adapter/mapper，而不是散落组件中。
* 右侧分析面板未来可能承载更细的 runtime 指标，因此类型设计要允许 richer fields 渐进接入。

### Related scenarios

* 旧 Chainlit 的一些中文状态映射和上传反馈表达可以复用，避免新前端和旧前端出现术语漂移。
* README、启动方式、环境变量说明需要与新的前端目录保持一致，否则团队使用会混乱。

### Failure & edge cases

* 前端与后端端口分离运行时会遇到 CORS 问题。
* 上传时会遇到大文件、类型不支持、解析等待中的中间态。
* 后端返回 richer payload 时，前端必须在边界层兜底，避免组件到处写 fallback。

## MVP Boundary

### In MVP

* 将 zip 解压到 `web/` 并完成结构评估
* 真实 API 契约对齐
* mock 显式开关
* CORS
* 轻量重构 `page.tsx`
* 基础文档与环境说明

### Out of Scope

* 重做 UI 风格
* 引入复杂前端状态管理框架
* 删除 Chainlit 入口
* 做完整设计系统统一
* 扩展新增签证家族或重写后端 API

## Technical Approach

1. 将 zip 解压到独立目录，例如 `frontend-v0/`
2. 在前端新增：
   * `lib/config/api.ts` 或等价 API config helper
   * `lib/api/types.ts` 扩展真实后端类型
   * `lib/api/mappers.ts` 统一做 visa/document/report/message/upload adapters
   * `hooks/use-session-workbench.ts` 或等价 session lifecycle hook
3. 重构 `app/page.tsx`：
   * 保留页面骨架和组件拼装
   * 移除大部分副作用和 API 调用细节
4. 后端在 `app/main.py` 加入 `CORSMiddleware`
5. 增加 `.env.example` 或前端环境说明
6. 做最小必要联调与验证

## Decision (ADR-lite)

**Context**: 需要将一个独立生成的 v0 前端接入现有 FastAPI 后端，同时尽量避免高风险重构和视觉返工。

**Decision**: 采用 Approach A：独立前端目录 + 前端适配层 + 保留 Chainlit 过渡，并在后端补 CORS 支持。

**Consequences**:

* 短期内存在双前端入口，但能降低切换风险
* 适配逻辑集中后，后续继续对齐 richer payload 成本更低
* 首轮完成后即可基于真实 API 联调，再决定是否将 Next.js 前端提升为默认入口

## Implementation Plan (small PRs)

* PR1: 前端目录落库与运行骨架
  * 解压 zip 到独立目录
  * 校正 package 元数据与基础运行脚本
  * 增加前端 `.env.example`
  * 增加 API config helper
  * 后端补 CORS
* PR2: API 契约对齐与边界适配
  * 补齐前端类型
  * 增加 visa/document/report/message/upload adapters
  * 替换硬编码 base URL
  * 保留 mock，但改为显式开关
* PR3: 页面瘦身与主流程接通
  * 抽出 session lifecycle hook / module
  * 接通 create session、required package、messages、reports、files
  * 保持分析面板与报告弹窗由真实数据驱动
* PR4: 稳定性与产品化收尾
  * loading / error / empty states
  * mock banner
  * README 更新
  * 最小联调验证与必要测试

## Detailed Execution Breakdown

### Phase 1: Repository Integration

* 在仓库中确定新前端目录名
* 将 zip 内容解压到该目录
* 确认不会与后端目录冲突
* 校正前端依赖、脚本、README 入口描述

### Phase 2: Contract Audit

* 以后端 router + service + integration tests 为准，锁定真实请求/响应结构
* 列出前端当前所有错误或过浅假设
* 将这些假设收敛到 `types + mappers + client`

### Phase 3: Runtime Plumbing

* 建立 API base URL 与 mock mode 的统一配置入口
* 建立 visa family 和 document type 的 UI 适配层
* 抽出 session lifecycle hook
* 实现消息发送、上传、报告刷新复用函数

### Phase 4: UI Wiring

* 保留原布局与组件结构
* 让 `VisaSelector` 驱动 session 创建
* 让 `TopBar` 仅切换视图密度
* 让 `ChatPanel`、`AnalysisPanel`、`ReportModal` 接真实数据

### Phase 5: Resilience & Validation

* 完善 loading / error / empty states
* 验证上传反馈与 requested documents 展示
* 验证 report modal 的 internal report
* 验证 mock / real API 切换
* 验证本地前后端联调

## Technical Notes

* 后端关键参考文件：
  * `app/api/routers/sessions.py`
  * `app/api/routers/messages.py`
  * `app/api/routers/reports.py`
  * `app/api/routers/files.py`
  * `app/services/report_service.py`
  * `app/services/message_service.py`
  * `chainlit_app.py`
* 后端真实契约参考测试：
  * `tests/integration/test_sessions_api.py`
  * `tests/integration/test_messages_api.py`
  * `tests/integration/test_files_api.py`
  * `tests/integration/test_reports_api.py`
* v0 前端核心文件来自 zip：
  * `app/page.tsx`
  * `lib/api/client.ts`
  * `lib/api/types.ts`
  * `lib/api/mock-data.ts`
  * `components/ds160/*`
