# OpenAI-Compatible 连续会话语义重构设计

日期：2026-04-18

## 1. 事故摘要

这不是一个“接口兼容度略差”的问题，而是一个 P0 级 Agent 能力事故。

当前 `/v1/chat/completions` 的真实行为是：

1. 每次请求都会创建新的 `session`
2. 只消费 `messages` 中最后一条 `user` 消息
3. 已返回给客户端的 `metadata.session_id` 不能用于下一次续接
4. 围绕 `session_id` 聚合的 `gate / files / evidence / profile / governor_history / reports` 都无法在 OpenAI-compatible 入口上连续累积

直接后果是：外部客户端看到的是一个“像 Chat Completions 的单轮 RPC”，而不是一个真正可持续推进材料门控、证据积累、画像更新、Governor 决策和追问链条的对话 Agent。

## 2. 为什么这是 P0

### 2.1 产品层

产品承诺的核心能力不是“会返回一段话”，而是“围绕同一个申请人会话持续推进签证判断”。连续上下文是产品主能力，不是体验增强项。

如果连续上下文缺失，会出现以下失真：

1. 用户上一轮口述事实不会进入下一轮推理累积
2. 上传的材料与后续对话不在同一条 Agent 语义链上
3. `gate_review -> interview -> report` 失去单一会话主线
4. Governor 历史、评分历史、证据引用都碎片化
5. 外部集成方会误以为自己接入了“持续对话 Agent”，实际接入的是“单轮打分问答函数”

这已经触及产品真实性问题，因此是 P0。

### 2.2 协议层

`/v1/chat/completions` 当前暴露的协议外形类似 OpenAI，但真实语义并不成立：

1. 返回了 `metadata.session_id`，却没有续接契约
2. 接收完整 `messages[]`，却只消费最后一条 `user`
3. 返回 `phase_state`，却不保证下一轮仍处于同一 `session`

这属于“看起来兼容，但语义不兼容”的伪兼容，比明确声明“不支持会话续接”更危险。

### 2.3 架构层

系统内部其实已经是 `session-native` 架构：

1. `SessionRecord` 是聚合根
2. `DocumentRecord / JobRecord / EvidenceItem / profile_json / gate_status_json / governor_history_json` 都挂在 `session_id` 上
3. `MessageService.handle_user_turn(session_id, message_text)` 的设计本身就是基于既有会话推进

也就是说，连续会话能力在域模型内部已经存在，事故出在 `openai_compat` 入口把这条链切断了。

## 3. 当前实现复盘

基于当前代码，`/v1/chat/completions` 的真实语义如下：

### 3.1 代码证据

- `app/api/routers/openai_compat.py`
  - 从 `messages` 里倒序查找最后一条 `user`
  - 校验 `metadata.declared_family`
  - 无条件 `session_repo.create(...)`
  - 调用 `MessageService(db).handle_user_turn(new_session_id, last_user_message)`
  - 返回新的 `metadata.session_id`

- `app/api/routers/messages.py`
  - 真正的持续消息入口是 `POST /v1/sessions/{session_id}/messages`
  - 该入口要求已有 `session_id`

- `app/api/routers/files.py`
  - 文件上传是 `POST /v1/sessions/{session_id}/files`
  - 文件与解析任务都绑定到既有 `session_id`

- `app/services/message_service.py`
  - 消息处理依赖 `session_id`
  - Gate 未就绪时返回 gate 响应
  - Gate 就绪后才进入 `InterviewRuntimeService`

- `app/services/interview_runtime_service.py`
  - 从 `record.profile_json` 继续加载画像
  - 持续写入 `runtime_trace / score_history / governor_history`
  - Question Agent 的检索与证据工具也依赖 `session_id`

### 3.2 当前 `/v1/chat/completions` 的真实语义

从产品、协议、架构三个层面，可以把当前语义准确表述为：

#### 产品语义

它不是持续对话，而是“为每次请求临时创建一个全新申请人会话，然后执行一次单轮处理”。

#### 协议语义

它不是“聊天补全”，而是“`messages[]` 外壳下的单条 `last_user_message` RPC”。

#### 架构语义

它不是 `sessions/messages/files/reports` 这套会话架构的兼容入口，而是平行复制了一层会话创建逻辑，并且没有续接能力。

## 4. 设计目标

## 4.1 目标

1. 让 `/v1/chat/completions` 真正具备连续会话语义
2. 让 `/v1/chat/completions` 与 `/v1/sessions/*` 共享同一套会话聚合根
3. 让文件上传、gate、evidence、profile、governor history 在同一 `session_id` 下持续累积
4. 明确首次请求与续接请求的契约，杜绝隐式行为
5. 避免“外形兼容 OpenAI，语义却是单轮函数”的伪兼容

## 4.2 非目标

1. 不在本轮把系统改造成完全无状态、可由整段 transcript 每次重放恢复全部状态的架构
2. 不在本轮强行新增完整 `/v1/files` OpenAI 原生文件协议
3. 不在本轮重写 Gate、Extractor、Scoring、Governor 的域逻辑

## 5. 修复方案

## 5.1 方案 A：最小修补

### 核心思路

在 `/v1/chat/completions` 上增加 `metadata.session_id` 支持：

1. 有 `session_id` 时复用既有 session
2. 没有 `session_id` 时创建新 session
3. 仍然只消费最后一条 `user` 消息

### 优点

1. 改动最小
2. 最快恢复“至少能续接同一个 session”
3. 能立刻让文件、profile、governor 历史重新连上

### 缺点

1. 仍然没有把 `/v1/chat/completions` 明确建模为状态化兼容层
2. 仍然容易让人误以为 `messages[]` 全量 transcript 会被完整解释
3. 没有从协议层解决伪兼容问题

### 结论

可作为紧急热修思路，但不适合作为最终设计。

## 5.2 方案 B：会话权威型兼容层

### 核心思路

把 `/v1/chat/completions` 明确定义为“基于 `session_id` 的状态化兼容入口”，其本质是：

1. 首轮请求：`create_session + handle_user_turn`
2. 后续请求：`handle_user_turn(existing_session_id, last_user_message)`
3. 文件、gate、evidence、profile、governor history、reports 全部继续依赖同一个 `session_id`
4. `/v1/sessions/*` 是规范主入口，`/v1/chat/completions` 是适配层，不再拥有独立业务语义

### 优点

1. 修复了连续上下文的根问题
2. 与现有域架构一致，不需要推翻重做
3. 可以明确声明“服务端上下文权威来源是 session state，而不是每次请求传来的全量 transcript”
4. 能把伪兼容风险收敛为明确契约

### 缺点

1. 需要重新定义兼容层协议文档与测试基线
2. 需要处理 `declared_family`、无效 `session_id`、session 与 transcript 不一致等边界
3. 如果要提升可追溯性，最好补充标准化 turn 历史记录

### 结论

推荐方案。这是当前架构下最正确、风险最低、收益最高的修复路径。

## 5.3 方案 C：全量 transcript 重放型兼容实现

### 核心思路

把 `/v1/chat/completions` 做成接近 OpenAI 的“无状态重放”接口：

1. 每次根据整个 `messages[]` 重建上下文
2. 不强依赖服务端 session
3. 尝试从 transcript 重放 profile、gate、governor 状态

### 优点

1. 形式上最接近 OpenAI 原始语义
2. 理论上更适配完全无状态客户端

### 缺点

1. 与现有 `session-native` 架构冲突
2. 文件上传、解析任务、证据索引、报告生成天然是有状态链路，难以纯 transcript 重放
3. 实现复杂度极高，且很容易出现 state drift
4. 不是修 P0 的正确方式

### 结论

不推荐。本系统当前本质是状态化 Agent，不应伪装成无状态 transcript 引擎。

## 6. 推荐设计

推荐采用方案 B：会话权威型兼容层。

核心原则只有一句话：

`/v1/chat/completions` 必须成为同一套 `InterviewSession` 聚合根的状态化适配层，而不是单轮函数。

## 7. 目标契约

## 7.1 总体原则

1. `session_id` 是连续上下文的唯一权威锚点
2. 服务端上下文以 `session state` 为准，不以客户端重复传入的全量 transcript 为准
3. `/v1/chat/completions` 不能再私自创建平行 session 语义
4. 任何续接失败都必须显式报错，不能静默新建 session

## 7.2 首次请求契约

当 `metadata.session_id` 缺失时，`/v1/chat/completions` 执行首轮会话创建。

建议契约：

```json
{
  "model": "visa-simulator-v1",
  "messages": [
    {"role": "system", "content": "optional"},
    {"role": "user", "content": "I am applying for F1."}
  ],
  "metadata": {
    "declared_family": "f1"
  }
}
```

服务端行为：

1. 提取最后一条 `user` 作为当前 actionable turn
2. 创建新 `session`
3. 若 `metadata.declared_family` 存在，则锁定会话签证家族并初始化 gate
4. 调用统一的 `MessageService.handle_user_turn(session_id, last_user_message)`
5. 返回新 `session_id`

返回元数据至少包含：

```json
{
  "session_id": "sess-xxx",
  "phase_state": "gate_review",
  "context_mode": "session_state"
}
```

## 7.3 后续请求契约

当 `metadata.session_id` 存在时，`/v1/chat/completions` 执行既有会话续接。

建议契约：

```json
{
  "model": "visa-simulator-v1",
  "messages": [
    {"role": "user", "content": "First turn"},
    {"role": "assistant", "content": "Previous reply"},
    {"role": "user", "content": "New turn"}
  ],
  "metadata": {
    "session_id": "sess-xxx"
  }
}
```

服务端行为：

1. 按 `session_id` 读取既有 `SessionRecord`
2. 若 session 不存在，返回 `404`，绝不静默新建
3. 仍以最后一条 `user` 作为当前新增 turn
4. 使用该 session 上已经存在的 `gate_status / profile / evidence / governor_history`
5. 返回同一个 `session_id`

## 7.4 `declared_family` 的续接规则

推荐规则如下：

1. 首轮请求可传 `metadata.declared_family`
2. 若既有 session 的 `declared_family` 为空，则续接请求允许一次性补锁定
3. 若既有 session 已锁定 family，则续接请求中的 `declared_family` 必须缺省或与现值一致
4. 若续接请求试图改写已锁定 family，返回 `409`

这样既保留首轮灵活性，也避免 session 漂移。

## 8. `/v1/sessions/*` 与 `/v1/chat/completions` 的统一关系

## 8.1 统一原则

`/v1/sessions/*` 是域模型主入口，`/v1/chat/completions` 是传输适配层。

二者关系应统一为：

1. `POST /v1/chat/completions` 无 `session_id`
   - 语义等价于：`POST /v1/sessions` + `POST /v1/sessions/{id}/messages`
2. `POST /v1/chat/completions` 有 `session_id`
   - 语义等价于：`POST /v1/sessions/{id}/messages`
3. 文件上传、报告获取继续通过 `/v1/sessions/{id}/files`、`/v1/sessions/{id}/reports/*`

## 8.2 不允许的状态

不允许出现：

1. `chat/completions` 走一套 session 逻辑
2. `sessions/messages/files/reports` 走另一套 session 逻辑

否则问题会再次复发。

## 9. 文件、Gate、Evidence、Profile、Governor History 如何进入连续上下文

## 9.1 当前事实

这些能力今天已经是 `session_id` 驱动的：

1. 文件上传写入 `DocumentRecord(session_id=...)`
2. 解析 worker 基于 `session_id` 回写 evidence 与 profile
3. `GateRuntimeService` 基于 session 关联的 documents/jobs 刷新 gate
4. `InterviewRuntimeService` 基于 `profile_json`、`EvidenceService`、`RetrievalService` 继续运行
5. 报告接口直接从 session 上读取 `profile / runtime_trace / score_history / governor_history`

## 9.2 重构后的进入方式

一旦 `chat/completions` 续接同一个 `session_id`，这些信息会自然进入连续上下文：

1. 用户首轮通过兼容层创建 session
2. 用户通过 `/v1/sessions/{session_id}/files` 上传文件
3. parse worker 回写 evidence、profile、gate 状态
4. 用户下一轮再次调用 `/v1/chat/completions`，带上同一个 `metadata.session_id`
5. `MessageService` 读取到同一 session 的 gate/profile/evidence/history，连续对话成立

## 9.3 关于 `metadata.file_ids`

本轮不建议把 `metadata.file_ids` 作为 P0 主路径。原因是：

1. 当前系统并没有 OpenAI 原生 `/v1/files` 资源模型
2. 真正的上下文锚点是 `session_id`，不是独立 `file_id`
3. 文件进入上下文的正确方式是“文件属于某个 session，并经解析写回该 session 的证据与画像”

后续如果要做更强的 OpenAI 文件兼容，也应让 `file_id -> session_id` 最终回落到同一套会话聚合根，而不是绕开 session。

## 10. 如何避免伪兼容

这是本设计最关键的约束。

### 10.1 明确公开语义

文档必须明确写清楚：

1. 这是一个“状态化 OpenAI-compatible 入口”
2. 连续上下文依赖 `metadata.session_id`
3. 服务端状态权威来源是 session，而不是每次传来的全量 transcript

### 10.2 不允许静默兜底

以下行为必须禁止：

1. 传了不存在的 `session_id` 时静默新建 session
2. 传了冲突的 `declared_family` 时静默覆盖
3. 明明没有连续上下文，却返回一个看起来正常的下一问

### 10.3 返回显式元数据

建议兼容层返回至少以下字段：

1. `metadata.session_id`
2. `metadata.phase_state`
3. `metadata.context_mode = "session_state"`
4. `metadata.gate_status` 或最小可判定的 `gate_progress` 摘要

这样客户端能知道自己拿到的是哪种上下文模式，而不是被动猜测。

### 10.4 可追溯性增强

推荐增加标准化 turn 历史记录，至少记录：

1. 来源入口：`openai_compat` 或 `sessions_api`
2. 当前 user turn 文本
3. assistant 输出
4. 对应 `phase_state`
5. 关联 `session_id`

这不是连续上下文成立的硬前置条件，但它对事故复盘、伪兼容定位和回归测试都非常有价值。

## 11. 建议的数据与接口调整

## 11.1 接口调整

`/v1/chat/completions` 建议支持：

1. `metadata.session_id`
2. `metadata.declared_family`
3. 续接时严格 404 / 409 / 422 错误语义

## 11.2 返回调整

建议统一返回：

```json
{
  "id": "chatcmpl-sess-xxx",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "..."},
      "finish_reason": "stop"
    }
  ],
  "metadata": {
    "session_id": "sess-xxx",
    "phase_state": "interview",
    "context_mode": "session_state"
  }
}
```

## 11.3 可选持久化增强

推荐补充一个标准化 turn ledger。可以有两种做法：

1. `SessionRecord` 上新增 `conversation_history_json`
2. 单独建 `session_turns` 表

推荐顺序：

1. P0 修复先恢复连续会话
2. 随后尽快补齐 turn ledger，提升可观测性与可审计性

## 12. 错误处理策略

建议协议化以下错误：

1. `422`
   - 没有任何 `user` 消息
   - 请求体不满足最小契约
2. `404`
   - `metadata.session_id` 指向不存在的 session
3. `409`
   - 续接请求试图改写既有 `declared_family`
4. `422`
   - `declared_family` 非法

原则是：失败必须显式，不能通过“偷偷新建 session”来掩盖。

## 13. 测试策略

`tests/integration/test_openai_compat.py` 应从“单轮入口测试”升级为“连续会话语义测试”。

至少需要覆盖：

1. 首轮调用会创建 session，并返回 `metadata.session_id`
2. 带相同 `metadata.session_id` 的第二轮调用不会新建 session
3. 第二轮调用会复用同一 session 的 `phase_state`
4. 续接请求使用不存在的 `session_id` 返回 `404`
5. 已锁定 family 的 session 遇到冲突 family 返回 `409`
6. 通过 `/v1/sessions/{id}/files` 上传文件后，再走 `/v1/chat/completions` 能读到同一 gate/profile/evidence 状态
7. `runtime_trace / governor_history / reports` 在连续两轮后属于同一 session，且长度累积

## 14. 推荐落地顺序

1. 先修正 `/v1/chat/completions` 的 session 续接契约
2. 再让其完全复用 `/v1/sessions` 与 `MessageService` 的统一链路
3. 补充连续会话回归测试
4. 追加 turn ledger，提升调试与审计能力
5. 更新对外文档，明确这是状态化兼容入口

## 15. 最终结论

这个问题的本质不是“接口像不像 OpenAI”，而是“系统是否真的提供连续上下文 Agent 能力”。

当前答案是否定的，因此定为 P0 完全成立。

推荐结论如下：

1. 必须引入并正式支持 `metadata.session_id` 作为续接锚点
2. 必须把 `/v1/chat/completions` 重构为 `/v1/sessions/*` 的状态化适配层
3. 必须显式声明服务端上下文来源是 `session state`
4. 必须禁止所有会掩盖上下文断裂的静默兜底行为

只有这样，`/v1/chat/completions` 才不是“会说话的打分系统外壳”，而是同一个 DS-160 Agent 会话的兼容入口。
