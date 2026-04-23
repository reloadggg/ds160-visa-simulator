# DS-160 Agent 面签官 v1.5 Implementation Plan

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** 把当前“材料流程 + 打分系统”重构成真正的 `Agent 面签官 v1.5`，让系统具备本次会话连续记忆、材料引用、动态追问、高风险与拒签分层、可配置签证官提示词。

**Architecture:** 继续使用 `session_id` 作为本次会话主键，但只服务“本次会话记忆”，不做跨会话人物记忆。系统主线改成：`本次会话记忆 -> 材料理解 -> 面签官主判断 -> 用户可理解结果`。`gate` 退回支持层，不再拦截实质问答；`scoring` 退回辅助层，不再做隐藏主脑。

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest, SQLite bootstrap, PydanticAI, OpenAI-compatible runtime

---

## 0. 实现原则

这份计划按下面 8 条原则执行：

1. 先修主语义，再修边角兼容
2. 所有新能力都先补测试，再写实现
3. `gate` 不能继续做主线拦截器
4. 必须先建立“面签官主判断层”，不能只在旧流水线上打补丁
5. 评分只能做辅助，不能拥有当前焦点的控制权
6. 拒签依据只能来自本次会话的资料和对话
7. 本期不做跨会话人物记忆
8. 每一步都要让产品体验更像真人面签，而不是更像流程系统

---

## Task 1: 建立本次会话记忆底座

**目标：** 让系统真正记住本次会话里发生过什么，而不是只记最后一句。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/db/models.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/main.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/repositories/session_repo.py`
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/repositories/session_turn_repo.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_session_schema_bootstrap.py`
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_session_turn_repo.py`

**要做的事：**

1. 给 `sessions` 增加真正的面签状态字段
   建议字段：
   - `interviewer_state_json`
   - `current_focus_json`
2. 新建 `session_turns` 表
   建议记录：
   - `turn_id`
   - `session_id`
   - `role`
   - `content`
   - `source`
   - `metadata_json`
3. 补 bootstrap 逻辑，保证旧 SQLite 库能自动补齐字段
4. 提供 turn repo，支持：
   - append user turn
   - append assistant turn
   - list session turns

**测试重点：**

1. 旧库升级后能看到新字段
2. 可以写入、读取 session turns
3. turn 顺序稳定

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_session_schema_bootstrap.py -q
uv run pytest tests/unit/test_session_turn_repo.py -q
```

**完成标准：**

系统已经能保存“本次面谈过程”和“当前面签状态”。

---

## Task 2: 拆掉 gate 硬拦截，改成支持层

**目标：** 让“材料未齐也可进入实质问答”成为真正的系统行为，而不是文档口号。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/message_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/gate_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/api/routers/messages.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_gate_runtime_service.py`

**要做的事：**

1. 拆掉“gate 未 ready 就直接不进入 interview runtime”的旧主线
2. 保留 gate 作为支持信息：
   - 当前已有什么材料
   - 当前还缺什么材料
   - 当前哪些材料还在解析
3. 让 message flow 变成：
   - 无论材料是否齐，都可以进入实质问答
   - 但系统要知道当前最缺什么证明
4. `gate_progress` 继续存在，但不再等于“你不能正式面谈”

**测试重点：**

1. 材料未齐时仍能进入实质问答
2. 系统会同步要求关键证明
3. gate 状态仍可查询，但不再硬拦截主线

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/integration/test_messages_api.py -q
uv run pytest tests/unit/test_gate_runtime_service.py -q
```

**完成标准：**

系统已经不再是“先补齐材料才能开始”的流程系统。

---

## Task 3: 修正 `/v1/chat/completions` 的连续会话语义

**目标：** 让 OpenAI-compatible 入口不再每次新建 session。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/api/routers/openai_compat.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_openai_compat.py`

**要做的事：**

1. 支持 `metadata.session_id`
2. 无 `session_id` 时创建新 session
3. 有 `session_id` 时续接既有 session
4. 不存在的 `session_id` 返回 `404`
5. 返回值里明确带上：
   - `session_id`
   - `phase_state`
   - `context_mode`

**测试重点：**

1. 第二轮调用不会新建 session
2. 同一个 `session_id` 能连续对话
3. 续接错误会显式报错，不静默兜底

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/integration/test_openai_compat.py -q
```

**完成标准：**

`/v1/chat/completions` 不再是假连续会话入口。

---

## Task 4: 建立“面签官主判断层”

**目标：** 先把谁是系统主脑这件事定清楚，避免继续沿用旧的三段式流水线思路。

**Files:**
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/message_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interview_runtime_service.py`
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`

**要做的事：**

1. 新建真正的“面签官主判断层”
2. 明确它是唯一 owner，负责：
   - 读取本次会话上下文
   - 读取材料与证据
   - 决定当前最关键问题
   - 决定下一步属于：
     - 继续问
     - 要关键证明
     - 高风险
     - 拒签
3. 旧 `extractor / question / scoring` 全部降成这个主判断层的辅助组件
4. `current_focus_json` 的写入权只给这个主判断层

**测试重点：**

1. 当前最关键问题只由一个地方决定
2. scoring 不能偷偷改写当前焦点
3. 下一问、补证明、高风险、拒签都从同一主判断层发出

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_interviewer_runtime_service.py -q
uv run pytest tests/integration/test_messages_api.py -q
```

**完成标准：**

系统真正拥有了“签证官主脑”，不再是几个小模型拼装起来的假 Agent。

---

## Task 5: 建立 v1.5 面签状态模型

**目标：** 把系统对外状态整理成产品能理解的几类，而不是混在一起。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/domain/contracts.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/domain/runtime.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/report_service.py`
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_interview_state_model.py`

**要做的事：**

1. 新增清楚的面签状态
   建议至少包括：
   - `continue_interview`
   - `verify_key_issue`
   - `waiting_key_proof`
   - `high_risk_review`
   - `simulated_refusal`
2. 在 `interviewer_state_json` 中保存：
   - 当前关键问题
   - 当前关键证明点
   - 当前风险等级
   - 当前允许的下一步动作
3. 对外接口与报告只暴露简单、可懂的状态

**测试重点：**

1. 高风险和拒签是两个不同状态
2. 当前关键问题可被持久化
3. 当前关键证明点可被持久化

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_interview_state_model.py -q
uv run pytest tests/integration/test_reports_api.py -q
```

**完成标准：**

系统状态从“内部工程态”变成“产品可理解态”。

---

## Task 6: 先补强“材料理解层”

**目标：** 让系统后面的材料引用、冲突追问、高风险判断真的有材料可用，而不是口头上说会引用材料。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/document_pipeline.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/profile_recompute_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/evidence_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/retrieval_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_document_pipeline.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_profile_recompute_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_retrieval_service.py`

**要做的事：**

1. 扩大可抽取的关键材料事实范围
2. 让材料不只支持“资金证明”这种单一场景
3. 提升 evidence 检索质量，至少让“带材料追问”有稳定数据来源
4. 让系统能更清楚地知道：
   - 哪些点已被材料支持
   - 哪些点仍未被证明
   - 哪些材料与说法有冲突

**测试重点：**

1. 新增关键字段可从材料里抽出
2. 材料支持与未支持状态可区分
3. retrieval 能稳定返回可用于追问的证据

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_document_pipeline.py -q
uv run pytest tests/unit/test_profile_recompute_service.py -q
uv run pytest tests/unit/test_retrieval_service.py -q
```

**完成标准：**

材料真正变成“可引用证据”，而不是“上传完成的文件”。

---

## Task 7: 把消息入口改成“真正的面签主线”

**目标：** 让系统每一轮都用真实 turn history 和真实证据推进，而不是只处理最后一条输入。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/message_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/extractor_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_interview_runtime_trace.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`

**要做的事：**

1. 每次用户发言都写入 `session_turns`
2. 每次系统回复也写入 `session_turns`
3. 主判断层读取最近多轮对话，而不是只看 `last_user_message`
4. 保留 profile/evidence 机制，但让它建立在真实 turn history 之上
5. 取消“只记最后一句”的隐式逻辑

**测试重点：**

1. 两轮以上对话后，turn history 连续存在
2. 系统第二轮的问法受第一轮影响
3. runtime trace 仍能正常累积

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/integration/test_messages_api.py -q
uv run pytest tests/integration/test_interview_runtime_trace.py -q
```

**完成标准：**

系统已经从“单轮处理器”变成“连续面谈处理器”。

---

## Task 8: 让系统一次只追一个关键点

**目标：** 让会话节奏更像真人面签，不一次扔一堆要求。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/agents/schemas.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/agents/question_agent.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`

**要做的事：**

1. Question agent 输出只允许一个当前重点
2. `requested_documents` 在对外主流程里收敛成一个最关键项
3. 平时不主动总结，只继续问和判断
4. 问题内容要围绕：
   - 当前关键冲突
   - 当前关键证明缺口
   - 当前关键回避点

**测试重点：**

1. 一次只追一个证明点
2. 不主动输出总结型文案
3. 下一问围绕当前关键问题

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/integration/test_messages_api.py -q
```

**完成标准：**

系统的问法从“清单式”变成“聚焦式”。

---

## Task 9: 明确“材料 + 口头解释”的冲突核实逻辑

**目标：** 让系统既不机械相信材料，也不轻易相信改口，而是持续核实。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/consistency_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/profile_recompute_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`

**要做的事：**

1. 识别口头解释与材料冲突
2. 允许改口，但保留旧说法
3. 改口要满足：
   - 逻辑讲得通
   - 必要时能补证明
4. 回避核心问题要累计风险
5. 长期拿不出关键证明要累计风险

**测试重点：**

1. 改口后旧说法仍存在
2. 改口合理时可以继续
3. 改口越来越不合理时进入高风险
4. 回避问题会让风险升高

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/integration/test_messages_api.py -q
```

**完成标准：**

系统已经具备“核实型面签官”的基本行为。

---

## Task 10: 建立高风险与拒签的分层收口

**目标：** 让系统知道什么时候只是高风险，什么时候可以直接拒签。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/governor_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_governor_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`

**要做的事：**

1. 明确区分：
   - 高风险但未拒签
   - 已拒签
2. 红线非常明显时支持直接拒签
3. 拒签后当前会话结束
4. 拒签返回对用户可理解的原因
5. 不把内部打分细节直接暴露给用户

**测试重点：**

1. 高风险不会自动等于拒签
2. 红线明显时可直接拒签
3. 拒签后会话不能继续
4. 拒签原因对用户可读

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_governor_service.py -q
uv run pytest tests/integration/test_messages_api.py -q
```

**完成标准：**

收口逻辑变得像真人面签，而不是像单一规则分支。

---

## Task 11: 让材料上传反馈真正进入主流程

**目标：** 用户随时上传材料后，系统能明确告诉他这份材料有没有帮助，并继续当前主线。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/file_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/api/routers/files.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_files_api.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`

**要做的事：**

1. 强化上传后的反馈语义
2. 让“有帮助 / 没帮助 / 解决了部分问题 / 仍缺关键证明”更明确
3. 明显无关的材料，要直接说没帮助
4. 上传反馈要能被后续问答继续利用

**测试重点：**

1. 有帮助材料会给明确正反馈
2. 无关材料会给明确负反馈
3. 主线不会被无关材料带偏

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/integration/test_files_api.py -q
uv run pytest tests/integration/test_messages_api.py -q
```

**完成标准：**

上传材料不再只是“收文件”，而是“进入面谈判断链”。

---

## Task 12: 把签证官提示词从代码里拿出来

**目标：** 让产品可以方便修改签证官角色、问法和判断口径。

**Files:**
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/interviewer_prompts/base.yaml`
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/interviewer_prompts/f1.yaml`
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/interviewer_prompts/j1.yaml`
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_prompt_registry.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/agents/question_agent.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/agents/extractor_agent.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/agents/scoring_agent.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/agents/model_factory.py`
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_interviewer_prompt_registry.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_model_factory.py`

**要做的事：**

1. 建立签证官提示词配置目录
2. 至少拆成 5 块内容：
   - 角色提示词
   - 面谈风格提示词
   - 判断规则提示词
   - 输出方式提示词
   - 未来案例参考位
3. Agent 不再直接写死长 instructions
4. 产品以后改内容只需要改配置文件

**测试重点：**

1. 可以读取 base prompt
2. 可以读取签证家族覆盖项
3. Agent 真正使用配置内容，而不是硬编码内容

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_interviewer_prompt_registry.py -q
uv run pytest tests/unit/test_model_factory.py -q
```

**完成标准：**

签证官角色提示词终于变成产品可修改的东西。

---

## Task 13: 把打分退回辅助层

**目标：** 防止系统继续围绕打分来组织主流程。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/scoring_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_scoring_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_tool_based_scoring.py`

**要做的事：**

1. 明确评分只给内部使用
2. 评分不直接决定对用户说什么
3. 评分不直接替代高风险或拒签判断
4. 评分不能拥有 `current_focus` 的控制权
5. 评分主要服务于：
   - 风险高低排序
   - 当前焦点的辅助排序
   - 内部状态整理

**测试重点：**

1. 评分存在，但不是对外主文案
2. 评分不会单独触发拒签
3. 当前关键点的 owner 不是 scoring

**验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_scoring_service.py -q
uv run pytest tests/integration/test_tool_based_scoring.py -q
```

**完成标准：**

打分重新回到“后台参考”的位置。

---

## Task 14: 最终回归与文档收口

**目标：** 确保 v1.5 主线闭环，没有留下“看起来做了，其实没接上”的断点。

**Files:**
- Modify: `/home/feng/ds160_pr/docs/superpowers/plans/2026-04-19-ds160-agent-interviewer-v1.5-product-requirements.md`
- Modify: `/home/feng/ds160_pr/docs/superpowers/plans/2026-04-19-ds160-agent-interviewer-v1.5-implementation-plan.md`
- Optionally Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/live/test_live_openai_compat.py`
- Optionally Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/live/test_live_messages_api.py`

**要做的事：**

1. 跑完整单元与集成测试
2. 跑关键 live 测试
3. 对照产品需求逐条验收
4. 更新文档中的最终交付说明

**建议验证命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest -q -m "not live_llm"
RUN_LIVE_LLM_TESTS=1 OPENAI_BASE_URL=... OPENAI_API_KEY=... uv run pytest tests/integration/live -q -m live_llm
```

**完成标准：**

需求、实现、测试三者对齐。

---

## 明确移出 MVP 的内容

下面这些内容先不进入 v1.5 当前开发主线：

1. 跨会话材料复用
2. 跨会话人物记忆
3. 真实案例 RAG
4. 向量库作为主底座

如果以后要做，也要在 v1.5 主线跑稳之后，作为下一阶段能力再接入。

---

## 建议实现顺序

建议严格按下面顺序做：

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6
7. Task 7
8. Task 8
9. Task 9
10. Task 10
11. Task 11
12. Task 12
13. Task 13
14. Task 14

原因很简单：

先把会话主线和面签官主脑立住，再补材料理解，再补提示词，再收打分。

---

## 这份计划的核心判断

这次重构最重要的一句话是：

`先把系统改成真正的面签官，再去谈兼容、评分和案例扩展。`

---

## Task 14 执行结果

2026-04-19 已完成本次 Task 14 收口：

1. 完整非 live 回归已通过
   - 命令：`uv run pytest -q -m "not live_llm"`
   - 结果：`226 passed, 8 deselected, 1 warning`
2. Task 13 已完成双审
   - 规格复审：`PASS`
   - 代码质量复审：`APPROVE`
3. live LLM 测试本轮未执行
   - 原因：当前环境未提供 `OPENAI_BASE_URL` 与 `OPENAI_API_KEY`
4. 文档与实现结论已对齐
   - 系统只保留“本次会话记忆”，未引入跨会话人物记忆
   - gate 已退回支持层
   - interviewer owner 已成为主判断层
   - scoring 已退回后台参考层

## 最终交付说明

本轮 v1.5 已完成 Task 1 到 Task 14 的主线交付，并满足当前 MVP 范围内的需求、实现与测试对齐。

当前剩余工作不再是主线缺口，而是后续环境型或下一阶段工作，例如：

1. 在具备凭据的环境里补跑 live LLM 测试
2. 继续观察 mocked API 边界层对 `score_summary` 的旧断言，防止未来回漏
3. 评估下一阶段是否要接真实案例与案例检索层
