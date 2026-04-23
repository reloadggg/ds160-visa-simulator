# DS-160 UX Flow P0/P1 Implementation Plan

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** 修复 DS-160 模拟器当前最影响完成率的 P0/P1 体验问题，让 F-1 主链路从“可演示”提升到“可自动推进、可理解、可继续”。

**Architecture:** 本计划不重做整体架构，继续沿用现有 FastAPI + SQLAlchemy + Chainlit 的单体结构，在现有 `gate_status_json`、`ParseWorker`、`report_service` 基础上做最小增量。P0 先解决流程推进和材料识别契约断裂，P1 再把已有状态能力透出到接口和 UI，补齐阶段提示与报告展示。

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy, Chainlit, pytest, uv

---

## 范围与原则

- 只覆盖 `docs/superpowers/2026-04-18-ux-flow-evaluation.md` 中已确认的 `P0/P1`
- 不在本轮处理 `P2 OpenAI-compatible` 会话语义重定义
- 所有改动必须先补测试，再做最小实现
- 优先复用现有 `gate_status_json.required_documents[*]` 状态字段，不重复发明新状态模型

## 任务概览

1. `P0` 自动消费 `gate_parse` 任务，消除永久卡在 `waiting_for_parse` 的风险
2. `P0` gate 材料匹配改为优先使用上传时 `document_type`
3. `P1` 把 gate 进度明细透出到消息接口和前端
4. `P1` 统一 gate/interview 阶段提示文案
5. `P1` 优化 Chainlit 报告展示与开发态入口

---

### Task 1: 增加正式 parse worker 运行机制

**Files:**
- Modify: `.worktrees/ds160-simulator-v1/app/main.py`
- Modify: `.worktrees/ds160-simulator-v1/app/workers/parse_worker.py`
- Modify: `.worktrees/ds160-simulator-v1/tests/integration/test_parse_worker.py`
- Create: `.worktrees/ds160-simulator-v1/tests/integration/test_parse_worker_runtime.py`

**目标：**
- 应用启动后具备正式的 parse 消费机制
- 不再要求测试或人工手动显式循环 `ParseWorker(db).run_once()` 才能推进状态
- 运行机制需要可关闭，避免测试和本地调试出现不可控并发

**Step 1: Write the failing test**
- 新增集成测试，构造一个上传完材料的 session
- 在启用 `PARSE_WORKER_INLINE=1` 或等价配置时，只等待短时间轮询数据库，不手工调用 `ParseWorker`
- 断言 session 状态最终从 `gate_review/waiting_for_parse` 自动进入 `interview/ready_for_interview`

**Step 2: Run test — confirm it fails**
- Command: `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && uv run pytest tests/integration/test_parse_worker_runtime.py -q`
- Expected: FAIL，原因应为应用当前没有自动后台消费器

**Step 3: Write minimal implementation**
- 在 `app/main.py` 中增加受配置控制的启动钩子
- 启动时创建后台轮询任务或生命周期任务，定期 claim `gate_parse`
- 轮询逻辑放在 `app/workers/parse_worker.py` 或附近辅助函数，避免把 worker 细节散落到 `main.py`
- 增加关闭钩子，避免测试结束后残留线程

**Step 4: Run test — confirm it passes**
- Command: `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && uv run pytest tests/integration/test_parse_worker_runtime.py tests/integration/test_parse_worker.py -q`
- Expected: PASS

**Step 5: Commit**
- `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && git add app/main.py app/workers/parse_worker.py tests/integration/test_parse_worker.py tests/integration/test_parse_worker_runtime.py && git commit -m "feat: add managed parse worker runtime"`

---

### Task 2: gate 材料匹配优先读取上传时的 document_type

**Files:**
- Modify: `.worktrees/ds160-simulator-v1/app/services/gate_runtime_service.py`
- Modify: `.worktrees/ds160-simulator-v1/tests/unit/test_gate_runtime_service.py`
- Modify: `.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`

**目标：**
- 用户在上传时已选择的 `document_type` 应成为 gate 匹配主依据
- 文件名子串匹配仅保留为兼容兜底
- 修复前后端契约脱节问题

**Step 1: Write the failing test**
- 在 `tests/unit/test_gate_runtime_service.py` 新增用例：
  - `filename="bank-statement-final.pdf"`
  - `artifact_json.document_type="funding_proof"`
  - 断言 `_matches_document_type()` 对 `funding_proof` 返回真
- 在 `tests/integration/test_messages_api.py` 新增用例：
  - 上传不含 `funding_proof` 文件名的 PDF，但表单带 `document_type=funding_proof`
  - worker 完成后该材料能推动 gate 状态前进

**Step 2: Run test — confirm it fails**
- Command: `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && uv run pytest tests/unit/test_gate_runtime_service.py tests/integration/test_messages_api.py -q`
- Expected: FAIL，当前实现只按文件名匹配

**Step 3: Write minimal implementation**
- 调整 `GateRuntimeService._matches_document_type()`
- 匹配顺序：
  1. `document.artifact_json.document_type == document_type`
  2. 若缺失，再退回文件名子串匹配
- 不修改 `FileService.upload()` 写入结构，只消费已有 `artifact_json.document_type`

**Step 4: Run test — confirm it passes**
- Command: `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && uv run pytest tests/unit/test_gate_runtime_service.py tests/integration/test_messages_api.py -q`
- Expected: PASS

**Step 5: Commit**
- `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && git add app/services/gate_runtime_service.py tests/unit/test_gate_runtime_service.py tests/integration/test_messages_api.py && git commit -m "fix: honor uploaded document type in gate matching"`

---

### Task 3: 透出 gate 进度明细到消息接口

**Files:**
- Modify: `.worktrees/ds160-simulator-v1/app/services/gate_runtime_service.py`
- Modify: `.worktrees/ds160-simulator-v1/app/services/message_service.py`
- Modify: `.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`

**目标：**
- 保留现有 `requested_documents`
- 额外返回用户可渲染的 `gate_progress` 结构
- 让前端可直接显示“已上传/待解析/已就绪/缺失”而不再猜测状态

**建议响应结构：**

```json
{
  "governor_decision": "need_more_evidence",
  "assistant_message": "Your uploaded documents are waiting to be parsed.",
  "requested_documents": ["passport_bio", "i20"],
  "gate_progress": {
    "overall_status": "waiting_for_parse",
    "ready_count": 3,
    "uploaded_count": 1,
    "missing_count": 1,
    "documents": [
      {
        "document_type": "ds160",
        "status": "ready",
        "is_uploaded": true,
        "is_parsed": true,
        "meets_minimum_fields": true
      }
    ]
  }
}
```

**Step 1: Write the failing test**
- 在 `tests/integration/test_messages_api.py` 新增断言：
  - gate 未通过时，响应里包含 `gate_progress`
  - `waiting_for_parse` 场景下能拿到 `ready_count/uploaded_count/missing_count`
  - `documents[*]` 顺序与 `required_documents` 保持一致

**Step 2: Run test — confirm it fails**
- Command: `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && uv run pytest tests/integration/test_messages_api.py -q`
- Expected: FAIL，当前响应没有 `gate_progress`

**Step 3: Write minimal implementation**
- 在 `GateRuntimeService` 新增组装 `gate_progress` 的辅助方法
- `build_gate_response()` 返回 `gate_progress`
- 不改 interview ready 路径，只有 gate 阶段补充进度结构

**Step 4: Run test — confirm it passes**
- Command: `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && uv run pytest tests/integration/test_messages_api.py -q`
- Expected: PASS

**Step 5: Commit**
- `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && git add app/services/gate_runtime_service.py app/services/message_service.py tests/integration/test_messages_api.py && git commit -m "feat: expose gate progress in message responses"`

---

### Task 4: 统一 gate/interview 阶段提示文案

**Files:**
- Modify: `.worktrees/ds160-simulator-v1/app/services/gate_runtime_service.py`
- Modify: `.worktrees/ds160-simulator-v1/app/services/report_service.py`
- Modify: `.worktrees/ds160-simulator-v1/chainlit_app.py`
- Modify: `.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`
- Modify: `.worktrees/ds160-simulator-v1/tests/integration/test_reports_api.py`

**目标：**
- 用户每轮都能知道自己当前在哪个阶段
- gate 阶段文案不再只有泛化英文句子
- report 和 chat 入口的阶段名称保持一致

**目标文案策略：**
- `gate_review + pending_documents`：明确“当前处于材料门控阶段，还缺哪些材料”
- `gate_review + waiting_for_parse`：明确“当前处于材料门控阶段，材料已提交，正在解析”
- `interview`：明确“已进入正式 interview，可继续回答问题”

**Step 1: Write the failing test**
- `tests/integration/test_messages_api.py` 断言 gate 两种状态返回的文案都包含阶段提示
- `tests/integration/test_reports_api.py` 断言 `user_report.summary/outcome_label` 与消息阶段语义一致

**Step 2: Run test — confirm it fails**
- Command: `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && uv run pytest tests/integration/test_messages_api.py tests/integration/test_reports_api.py -q`
- Expected: FAIL，当前主消息提示过于粗糙

**Step 3: Write minimal implementation**
- 在 `GateRuntimeService.build_gate_response()` 中统一输出阶段化文案
- `ReportService.user_report()` 只做同一语义下的摘要表达，不再各写各的
- Chainlit 收到 gate 响应后直接展示后端文案，避免前端再拼另一套阶段说明

**Step 4: Run test — confirm it passes**
- Command: `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && uv run pytest tests/integration/test_messages_api.py tests/integration/test_reports_api.py -q`
- Expected: PASS

**Step 5: Commit**
- `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && git add app/services/gate_runtime_service.py app/services/report_service.py chainlit_app.py tests/integration/test_messages_api.py tests/integration/test_reports_api.py && git commit -m "feat: unify gate and interview stage messaging"`

---

### Task 5: 优化 Chainlit 报告展示与开发态入口

**Files:**
- Modify: `.worktrees/ds160-simulator-v1/chainlit_app.py`
- Modify: `.worktrees/ds160-simulator-v1/tests/unit/test_chainlit_app.py`
- Modify: `.worktrees/ds160-simulator-v1/tests/integration/test_reports_api.py`

**目标：**
- `show_user_report` 不再直接把字典对象原样发给用户
- 用户报告以摘要文本或卡片化格式呈现
- `show_internal_report` 继续可用，但显式标明“内部调试信息”

**建议最小展示格式：**

```text
当前结论：补件审核中
摘要：材料已提交，仍在解析中，暂不能进入正式 interview。
缺失材料：funding_proof
建议：
- 等待解析完成后再继续
```

**Step 1: Write the failing test**
- 在 `tests/unit/test_chainlit_app.py` 中新增测试：
  - `show_user_report` 获取后端 payload 后，发送的是格式化字符串而不是原始 dict
  - `show_internal_report` 发送内容包含“内部报告”或“调试信息”标记

**Step 2: Run test — confirm it fails**
- Command: `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && uv run pytest tests/unit/test_chainlit_app.py -q`
- Expected: FAIL，当前直接 `cl.Message(content=report).send()`

**Step 3: Write minimal implementation**
- 在 `chainlit_app.py` 内新增格式化辅助函数，如 `_format_user_report()`、`_format_internal_report()`
- `show_user_report` 渲染摘要文本
- `show_internal_report` 继续展示结构化内容，但增加标题和用途说明

**Step 4: Run test — confirm it passes**
- Command: `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && uv run pytest tests/unit/test_chainlit_app.py tests/integration/test_reports_api.py -q`
- Expected: PASS

**Step 5: Commit**
- `cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1 && git add chainlit_app.py tests/unit/test_chainlit_app.py tests/integration/test_reports_api.py && git commit -m "feat: format chainlit reports for user readability"`

---

## 最终回归

完成全部任务后统一执行：

```bash
cd /home/feng/ds160_pr/.worktrees/ds160-simulator-v1
uv run pytest tests/unit/test_gate_runtime_service.py tests/unit/test_chainlit_app.py tests/integration/test_parse_worker.py tests/integration/test_parse_worker_runtime.py tests/integration/test_messages_api.py tests/integration/test_reports_api.py -q
```

期望：
- 全部通过
- 不需要手动调用 worker 才能完成 happy path
- 非规范文件名上传 + 正确 `document_type` 时 gate 能识别
- gate 阶段返回可渲染进度
- Chainlit 用户报告不再输出原始字典

## 风险与决策点

- 若选择后台轮询而不是独立进程，需要谨慎处理测试中的生命周期清理
- `document_type` 优先后，历史无 `artifact_json.document_type` 的数据仍需文件名兜底
- `internal_report` 是否继续对普通用户暴露，只在本轮做提示，不做权限系统

## 执行顺序建议

1. 先做 Task 1，解决流程卡死
2. 再做 Task 2，修正材料识别契约
3. 接着做 Task 3，补进度反馈
4. 然后做 Task 4，统一阶段文案
5. 最后做 Task 5，优化报告展示
