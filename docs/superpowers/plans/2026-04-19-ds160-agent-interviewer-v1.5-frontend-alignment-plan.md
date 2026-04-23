# DS-160 Agent 面签官 v1.5 Frontend Alignment Plan

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** 在不引入重前端工作台的前提下，把当前 `/ui` 的 Chainlit 前端收口到 v1.5 主线，让用户真正感受到“本次会话连续面谈 + 材料随时上传 + 上传结果有主线反馈 + 对外状态可理解”。

**Architecture:** 继续使用挂载在 FastAPI 下的 `Chainlit` 作为薄前端，不新增独立 React 工作台。前端只消费产品态接口：`/v1/sessions`、`/v1/sessions/{session_id}/messages`、`/v1/sessions/{session_id}/files`、`/v1/sessions/{session_id}/reports/*`。前端文案和交互必须围绕 `assistant_message`、`requested_documents`、`gate_progress`、`main_flow_feedback`、`interview_status`、`current_key_question`、`current_key_proof`、`risk_level` 展开，不把 `score_summary` 拉回用户主体验。

**Tech Stack:** Chainlit, httpx AsyncClient, FastAPI mounted UI, pytest

---

## 0. 前端收口原则

这份计划按下面 7 条原则执行：

1. 不做复杂前端工作台，只收口现有 Chainlit 薄 UI
2. 不重写后端主线，前端只对齐已经存在的 v1.5 契约
3. 不把 `gate` 再做成硬拦截体验
4. 不把 `score_summary` 再暴露成用户主体验
5. 用户必须可以随时主动上传材料，而不是只能在被点名时上传
6. 上传反馈必须告诉用户“有没有帮助、帮到了哪条主线、还缺什么”
7. 每一步都先补前端单测，再写实现

---

## Task 1: 把用户报告面板改成真正的产品态摘要

**目标：** 让“查看用户报告”真正反映 v1.5 状态模型，而不是只显示一段摘要文本。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/chainlit_app.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_chainlit_app.py`

**要做的事：**

1. 扩展 `_format_user_report()`，除了 `outcome_label` 和 `summary`，还展示：
   - `interview_status`
   - `risk_level`
   - `current_key_question`
   - `current_key_proof`
   - `allowed_next_actions`
2. 给 `interview_status`、`risk_level`、`allowed_next_actions` 加一层前端中文映射，不把内部枚举原样丢给用户
3. 保留“缺失材料 / 建议”部分，但顺序改成：
   - 当前状态
   - 当前关键问题 / 关键证明
   - 缺失材料
   - 建议动作
4. 内部报告仍保持调试态输出，不在这一轮做复杂可视化

**测试重点：**

1. 报告格式能展示 `interview_status`、风险等级和关键问题
2. 没有 `current_key_question` 或 `current_key_proof` 时，格式输出仍稳定
3. `allowed_next_actions` 会被转成用户能理解的文案

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_chainlit_app.py -q
```

**完成标准：**

用户点击“查看用户报告”后，能直接理解当前是在继续问答、等待关键证明、高风险复核还是模拟拒签，而不是只看到一段泛化摘要。

---

## Task 2: 把上传反馈真正接到前端主线里

**目标：** 前端必须优先显示 Task 11 已经实现的 `main_flow_feedback`，并同步更新当前主线缺口。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/chainlit_app.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_chainlit_app.py`

**要做的事：**

1. 新增统一的上传响应处理逻辑，优先展示：
   - `main_flow_feedback.message`
   - 没有时再退回 `feedback_message`
2. 上传后同步刷新前端会话态：
   - `pending_requested_documents`
   - 最近一次 `gate_progress`
3. 不再在上传后把 `pending_requested_documents` 机械写回旧请求列表；必须以接口真实返回为准
4. 对 `helpful / partial_helpful / not_helpful` 三种情况给出清晰文案分层
5. 上传只有附件、没有文字输入时，提示语改成“材料已接收，可继续回答或继续上传”，不再让用户误以为必须停下来等

**测试重点：**

1. 当前关键证明材料上传后，会显示主线帮助文案
2. 无关材料上传后，会显示“对当前主线没有直接帮助”
3. 上传后 `pending_requested_documents` 会随接口响应变化，不会停留在旧值

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_chainlit_app.py -q
```

**完成标准：**

前端已经能让用户明确感知“这份材料有没有帮上当前面谈主线”，而不是只看到泛化的“已接收文件”。

---

## Task 3: 放开“随时主动上传”，不要把上传入口绑死在待补件列表上

**目标：** 让用户即使没有被明确点名，也能主动上传材料；前端不能比后端更保守。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/chainlit_app.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_chainlit_app.py`

**要做的事：**

1. 调整 `_choose_document_type()`：
   - 保留当前 `requested_documents` / `required_initial_package` 优先项
   - 新增“其他材料 / 暂不指定类型”选项
2. 当当前没有 `pending_requested_documents` 时，上传按钮仍可用，不能直接提示“当前没有可上传的材料类型”
3. 前端允许以 `document_type=None` 发起上传，让后端自行判断是否可归类
4. 如果用户从附件按钮直接上传材料，不强制要求必须先选到待补件列表中的一种类型
5. 保持上传入口始终可见，但把“当前优先材料”作为推荐项，而不是唯一项

**测试重点：**

1. 没有 pending 文档时，上传动作仍可走通
2. 用户可以选择“不指定类型”并成功触发上传
3. 上传类型选项里既有推荐项，也有“其他材料”兜底项

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_chainlit_app.py -q
```

**完成标准：**

前端体验已经符合“用户可以随时主动上传材料”的产品约束，而不是继续沿用旧 gate 习惯。

---

## Task 4: 软化 gate 交互，避免把“缺材料”做成前台硬拦截

**目标：** 让前端体验符合 Task 2 和 Task 7 的语义：即使缺材料，也可以继续问答；上传只是支持当前主线，不是强制阻塞步骤。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/chainlit_app.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_chainlit_app.py`

**要做的事：**

1. 修改开场文案：
   - 不再把“必需材料包”写成必须先补齐才能开始
   - 明确告诉用户“可以先开始回答，也可以随时上传材料”
2. 修改 `_send_report_actions()` 的固定提示文案：
   - 告诉用户当前仍可继续回答
   - 如果缺材料，只把上传作为建议动作，不作为唯一下一步
3. 调整 `on_message()` 中的自动上传触发逻辑：
   - 不再在 `need_more_evidence` 时自动弹 `AskFileMessage`
   - 改为发送一条轻量 CTA，提示“当前最缺 X，可现在上传，也可继续解释”
4. “上传材料”按钮继续保留，并作为用户主动动作入口

**测试重点：**

1. 首轮或中途缺材料时，前端不会自动强弹上传框
2. 助手回复会先展示，再给出轻量上传建议
3. 即使 `governor_decision=need_more_evidence`，用户仍被允许继续走消息主线

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_chainlit_app.py -q
```

**完成标准：**

前端不再制造“先补件再面谈”的错觉，而是把缺材料表达成当前主线下的一个支持动作。

---

## Task 5: 收口前端契约边界，防止 scoring 重新回到前台

**目标：** 保证前端测试和前端代码都不再暗示 `score_summary` 是用户体验的一部分。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_chainlit_client.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_chainlit_app.py`

**要做的事：**

1. 清理 `tests/unit/test_chainlit_client.py` 里不必要的 `score_summary` 模拟依赖
2. 明确前端真正依赖的消息字段只有：
   - `assistant_message`
   - `governor_decision`
   - `requested_documents`
   - `gate_progress`
3. 明确前端真正依赖的上传字段只有：
   - `main_flow_feedback`
   - `feedback_message`
   - `requested_documents`
   - `gate_progress`
4. 新增一个回归断言：前端展示逻辑不渲染任何评分明细

**测试重点：**

1. UI 相关测试不再因为 `score_summary` 缺失而失败
2. Chainlit 客户端契约测试只覆盖 UI 实际依赖字段
3. 前端显示文案中不会出现评分字段或评分明细

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_chainlit_client.py -q
uv run pytest tests/unit/test_chainlit_app.py -q
```

**完成标准：**

前端已经完全站到 v1.5 的“interviewer owner + 产品态状态模型”一侧，不再带着旧打分体验的残影。

---

## Task 6: 最终前端回归与文案收口

**目标：** 确保这一轮前端收口没有留下“文案变了，但交互状态还是旧的”断点。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_chainlit_app.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_chainlit_client.py`
- Verify only: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_chainlit_mount.py`

**要做的事：**

1. 跑前端相关单测，覆盖：
   - 报告展示
   - 上传反馈
   - 主动上传
   - 非硬拦截交互
2. 跑 Chainlit mount 集成测试，确认 `/ui` 仍正常挂载
3. 人工快速核对 4 个关键场景的前端文案：
   - 首次进入会话
   - 上传有帮助材料
   - 上传无关材料
   - 缺关键证明但仍可继续问答
4. 如果前端文案里仍出现“必须先补齐材料才能开始”或任何评分措辞，必须在本 task 收掉

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_chainlit_app.py -q
uv run pytest tests/unit/test_chainlit_client.py -q
uv run pytest tests/integration/test_chainlit_mount.py -q
```

**完成标准：**

当前 `/ui` 前端已经与 v1.5 主线语义对齐：

1. 用户可以先聊，也可以随时上传
2. 上传材料后会收到明确主线反馈
3. 用户能看到产品态状态，而不是工程态或评分态
4. 前端不会再把 gate 演成硬拦截

---

## 不在本计划内

这份前端计划明确不做下面这些事：

1. 不新建独立 React / Next.js 前端
2. 不做复杂运营后台或分析大盘
3. 不改动后端 scoring / governor / interviewer runtime 主逻辑
4. 不新增跨会话人物记忆
5. 不把 OpenAI-compatible 入口做成前端主路径

---

## 推荐执行顺序

建议按下面顺序执行：

1. Task 1：先把报告面板变成产品态
2. Task 2：把上传反馈真正接上主线
3. Task 3：放开主动上传
4. Task 4：软化 gate 交互
5. Task 5：收口 scoring 边界
6. Task 6：统一回归

这样做的原因是：

1. 先把“看得见的状态”做对
2. 再把“上传反馈”做对
3. 最后再调交互节奏和边界防回漏
