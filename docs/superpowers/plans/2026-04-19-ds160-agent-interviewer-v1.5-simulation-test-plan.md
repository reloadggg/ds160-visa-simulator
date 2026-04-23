# DS-160 Agent 面签官 v1.5 全流程仿真测试计划

> **For implementer:** 先做可重复、可定位的仿真，再做真实 LLM smoke。不要一开始就把 UI、真实模型、异步解析、多轮上传绑成一条不可拆的黑盒链路。

**Goal:** 在当前 `v1.5 MVP` 已完成主线实现的前提下，补一轮“全流程仿真测试”，验证系统在真实会话节奏下不会卡死、不会状态乱跳、不会出现前后端语义脱节。

**Architecture:** 测试按 3 层执行：

1. `脚本化后端仿真`
   直接走 `TestClient` 调用 `/v1/sessions`、`/v1/sessions/{session_id}/messages`、`/v1/sessions/{session_id}/files`、`/v1/sessions/{session_id}/reports/*`
2. `真实 LLM smoke`
   仍走后端 API，但启用真实 OpenAI-compatible 模型
3. `人工 UI 冒烟`
   只验证 `/ui` 关键路径，不把 UI 自动化作为第一轮阻塞项

**Runtime for this round:**

- `OPENAI_BASE_URL=https://sub2.flashbuynow.com`
- `OPENAI_API_KEY=<仅本地注入，不写入仓库>`
- `RUNTIME_DEFAULT_MODEL=gpt-5.4-mini`

**Tech Stack:** pytest, FastAPI TestClient, SQLite 临时库, live LLM integration tests, Chainlit mounted UI

---

## 0. 测试原则

1. 先验证“会不会卡住”，再验证“答得像不像真人”
2. 先跑后端仿真，再跑真实模型，再跑 UI 冒烟
3. 每个“卡住”都必须落成明确断言，而不是人工感觉
4. 本轮不把 UI 自动化当成主战场，UI 只做 smoke
5. 不把 `OPENAI_API_KEY` 写进 `.env.example`、测试代码或计划文档
6. 如果 live 测试失败，必须先判断是“流程失败”还是“模型配置断言失败”

---

## 1. 关键风险先说明

当前 live 测试不能直接无改动切到 `gpt-5.4-mini`，原因是现有断言里写死了 `gpt-5.4`。

至少包括：

- `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/live/test_live_messages_api.py`
- `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/live/test_live_openai_compat.py`

这些测试当前会断言：

- `("question_agent", "interview_turn", "gpt-5.4")`

所以如果直接按你提供的模型 `gpt-5.4-mini` 跑，测试可能因为“模型名不匹配”失败，而不是因为流程真的卡住。

**因此本轮测试前置动作必须是：**

1. 把 live 测试里的期望模型改成“读取环境变量后的期望值”
2. 或者把 live smoke 单独拎出来，不复用现有硬编码模型断言

---

## 2. 这轮要回答的核心问题

这轮仿真测试不是泛泛“跑通一下”，而是明确回答下面 6 个问题：

1. 会话创建后，系统是否能稳定进入消息主线
2. 缺关键材料时，系统是否会继续问答而不是硬拦截
3. 上传有帮助材料后，系统是否能从 `need_more_evidence` 推进到 `continue_interview`
4. 上传无关材料后，系统是否只给主线反馈而不乱跳状态
5. `/v1/chat/completions` 续接同一 `session_id` 时，是否真的是连续会话
6. Chainlit `/ui` 是否还能覆盖“开场 -> 问答 -> 上传 -> 报告查看”的主路径

---

## 3. 卡住的判定标准

下面任一情况都视为“卡住”或“需要排查”的失败：

1. 同一 `session_id` 下，连续 2 到 3 轮消息都停在同一 `current_key_proof`，且没有新解释、新状态推进
2. 文件上传后 `pending_requested_documents` 不变化，且 `main_flow_feedback` 为空或与当前主线无关
3. `gate_progress.overall_status == "waiting_for_parse"` 长时间不恢复，或必须人工重复触发才能继续
4. `assistant_message` 有回复，但 `user report` / `internal report` 的状态不推进
5. `/v1/chat/completions` 第二轮没有续接前一轮 `session_id`
6. 附件-only 流程出现空 turn、无回执或无可执行下一步

---

## 4. 执行分层

### Phase A: 脚本化后端仿真

**目标：** 在不依赖真实模型波动的前提下，把主链路、状态推进和卡点观测先跑顺。

**推荐复用与扩展：**

- `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/e2e/test_f1_happy_path.py`
- `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`
- `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_files_api.py`
- `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_reports_api.py`
- `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_openai_compat.py`

**本轮至少补 2 条脚本化场景：**

1. `golden_path_f1_parent_sponsored`
   - 创建 `f1` session
   - 用户先口头说明“父母资助”
   - 系统进入 `need_more_evidence`
   - 上传关键资金证明
   - 解析完成后再发下一轮消息
   - 断言进入 `continue_interview`
   - 拉取 `user/internal report`，验证状态推进一致

2. `stuck_guard_irrelevant_upload`
   - 创建 `f1` session
   - 用户先口头说明“父母资助”
   - 系统进入 `need_more_evidence`
   - 上传无关材料
   - 再发下一轮消息
   - 断言系统继续主线问答，但不会错误清空 `requested_documents`
   - 断言提示仍围绕当前关键证明，而不是跳到别的焦点

**建议新增文件：**

- `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/e2e/test_simulation_flow.py`

**这层的核心断言：**

1. 每一步 `status_code` 正确
2. `session_id` 连续
3. `governor_decision` 推进符合预期
4. `requested_documents` 与 `current_key_proof` 一致
5. `main_flow_feedback` 能反映“有帮助 / 无直接帮助”
6. `user report` 与 `internal report` 不互相打架

---

### Phase B: 真实 LLM smoke

**目标：** 用你提供的 OpenAI-compatible 配置验证“真实模型参与时，流程仍能走通”，但不做全量回归。

**环境变量：**

```bash
export RUN_LIVE_LLM_TESTS=1
export OPENAI_BASE_URL="https://sub2.flashbuynow.com"
export OPENAI_API_KEY="<你提供的密钥，仅当前 shell 会话使用>"
export RUNTIME_DEFAULT_MODEL="gpt-5.4-mini"
export RUNTIME_QUESTION_AGENT_INTERVIEW_TURN_MODEL="gpt-5.4-mini"
export RUNTIME_EXTRACTOR_AGENT_INTERVIEW_TURN_MODEL="gpt-5.4-mini"
export RUNTIME_SCORING_AGENT_INTERVIEW_TURN_MODEL="gpt-5.4-mini"
```

**注意：**

如果 live 用例仍保留硬编码 `gpt-5.4` 断言，本阶段应先改测试，再执行。

**优先跑的 smoke 用例：**

1. `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/live/test_live_messages_api.py`
   - 首轮要求关键证明
   - 上传关键材料后继续问答
2. `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/live/test_live_openai_compat.py`
   - OpenAI-compatible 入口映射到同一条 domain flow

**建议命令：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/integration/live/test_live_messages_api.py -q -m live_llm
uv run pytest tests/integration/live/test_live_openai_compat.py -q -m live_llm
```

**这层的通过标准：**

1. 真实模型参与时不会因为消息主线或文件主线卡死
2. `need_more_evidence -> continue_interview` 至少能稳定走通 1 条
3. `/v1/chat/completions` 至少能稳定跑通 1 条连续会话 smoke

---

### Phase C: 人工 UI 冒烟

**目标：** 不做复杂自动化，只验证 Chainlit 薄前端和主 API 契约没有脱节。

**先跑自动回归：**

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_chainlit_app.py -q
uv run pytest tests/unit/test_chainlit_client.py -q
uv run pytest tests/integration/test_chainlit_mount.py -q
```

**再人工核对 4 个场景：**

1. 首次进入
   - 开场文案不是“必须先补件才能开始”
2. 缺关键证明
   - 助手先回复，再给轻量 CTA
   - 不会自动强弹上传框
3. 上传有帮助材料
   - 有 `main_flow_feedback`
   - `pending_requested_documents` 推进
4. 上传无关材料
   - 明确提示“对当前主线没有直接帮助”
   - 仍可继续问答或继续上传

---

## 5. 推荐执行顺序

1. 先修 live 测试的硬编码模型断言
2. 新增脚本化后端仿真测试
3. 跑脚本化仿真测试，先定位纯流程问题
4. 再跑真实 LLM smoke
5. 最后跑 Chainlit 前端 smoke

原因：

先把“流程会不会卡住”在可重复环境里定位出来，再把真实模型和 UI 叠上去，排错成本最低。

---

## 6. 预期产出

这轮仿真测试结束后，应该拿到 3 类结果：

1. 一份稳定可复现的后端全流程仿真用例
2. 一组真实 LLM smoke 结果，明确 `gpt-5.4-mini` 在当前 base URL 下是否可用
3. 一份“卡点清单”
   - 是流程卡住
   - 是解析异步卡住
   - 是前端状态没跟上
   - 还是只是 live 测试断言写死导致的假失败

### 6.1 补充：generic consistency 校准集

从 `2026-04-20` 起，后续和“泛化判断能力”相关的升级，不再只看 happy path 或单条仿真链路。

至少还要维护一套最小校准集：

1. `happy_path`
2. `category_purpose_mismatch`
3. `category_duration_mismatch`
4. `category_evidence_mismatch`
5. `record_conflict`
6. `evasive_answer`
7. `upload_helpfulness`

建议位置：

- `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/fixtures/generic_consistency/`
- `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/e2e/test_generic_consistency_eval.py`

这组校准集的用途不是替代主流程仿真，而是补一层：

1. prompt 升级回归
2. 风险骨架回归
3. 模型版本对比
4. 常见错配问题的最小评测基线

---

## 7. 本轮不做的事

1. 不做 Playwright 级别的 Chainlit 全自动 UI 测试
2. 不引入新的外部案例库或 RAG 检索
3. 不把真实用户密钥写入仓库任何文件
4. 不把“模型回答质量评审”混成“流程是否卡住”的判断标准

---

## 8. 建议下一步

建议下一步按下面顺序推进：

1. 先做一个很小的修整 task
   - 把 live 测试里写死的 `gpt-5.4` 断言改成读取环境期望模型
2. 再新增 `/tests/e2e/test_simulation_flow.py`
   - 先落 `golden path`
   - 再落 `stuck guard`
3. 这两步完成后，再正式执行 live smoke

如果要继续，我建议下一轮直接开始实现第 1 步和第 2 步，而不是先手工跑一遍。
