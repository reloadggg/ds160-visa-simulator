# DS-160 模拟器 Chainlit 接入设计文档

日期：2026-04-18  
状态：待用户审阅  
目标读者：产品、后端、前端接入、测试

## 1. 背景与目标

当前仓库已经具备完整的后端主流程：

- 创建会话：`POST /v1/sessions`
- 获取必需材料包：`GET /v1/sessions/{session_id}/required-package`
- 发送用户消息：`POST /v1/sessions/{session_id}/messages`
- 上传文件：`POST /v1/sessions/{session_id}/files`
- 获取报告：`GET /v1/sessions/{session_id}/reports/user`
- 获取内部报告：`GET /v1/sessions/{session_id}/reports/internal`

当前缺口不是领域后端，而是一个足够轻量、可快速上线、又不会迫使我们重做整套聊天/RAG/模型平台的交互前端。

本设计的目标是：

- 以最小改动为现有 FastAPI 后端接入 `Chainlit`
- 让用户通过一个真实可用的聊天 UI 完成 `签证家族选择 -> 问答 -> 定向补证 -> 报告查看`
- 不改变现有领域 API 的事实边界、证据链和 Governor 决策方式
- 避免引入 Open WebUI / LibreChat 那种更重的平台后端与文件处理体系

## 2. 选型结论

### 2.1 推荐方案

采用：`FastAPI 主应用 + 同进程挂载 Chainlit 子应用`

Chainlit 只承担以下职责：

- 聊天 UI
- 按钮式选择
- 受控文件上传
- 报告展示
- 会话内轻状态保存

业务事实仍全部由现有 FastAPI 领域接口负责。

### 2.2 不采用的方案

不采用以下方案作为 v1 主路径：

- `Chainlit 直接调用 services/`：会把 UI 与领域逻辑耦合
- `Chainlit 独立服务 + 反向代理`：部署更复杂，对 v1 收益不够
- `Open WebUI / LibreChat 直连`：文件、模型、平台心智过重，不适合作为这套业务后端的薄壳

## 3. 总体架构

### 3.1 挂载方式

在现有 [app/main.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/main.py) 中保留 FastAPI 主入口，并通过 `mount_chainlit(...)` 将 Chainlit 挂到固定路径，建议使用：

- `/ui`

这样：

- `/v1/*` 继续提供领域 API
- `/ui` 提供面向测试和内部使用的聊天前端

### 3.2 责任边界

Chainlit 不直接访问数据库，不直接构造 `ApplicantProfile`，不直接运行 `Extractor/Scoring/Governor`。

Chainlit 只做 API 编排层：

- 调后端创建 session
- 将用户输入转发到消息 API
- 在后端要求补证时主动弹 `AskFileMessage`
- 上传材料到文件 API
- 读取用户报告与内部报告并渲染

后端继续作为唯一事实源。

## 4. 交互流设计

### 4.1 开场

`on_chat_start` 完成以下动作：

1. 展示欢迎语与简要说明
2. 通过 `AskActionMessage` 让用户选择签证家族
3. 调用 `POST /v1/sessions`
4. 记录 `session_id` 到 `cl.user_session`
5. 读取必需材料包并提示用户当前要求
6. 进入正式聊天循环

### 4.2 正式消息流

`on_message` 的行为保持简单：

1. 从 `cl.user_session` 取出 `session_id`
2. 调用 `POST /v1/sessions/{id}/messages`
3. 渲染 `assistant_message`
4. 根据 `governor_decision` 判断是否需要：
   - 继续问答
   - 触发定向补证
   - 展示最终结果

### 4.3 受控补证

v1 明确不允许“用户自由堆材料”。

因此 Chainlit 采用两层约束：

1. 在 `chainlit.toml` 中关闭自由附件上传
2. 仅当后端返回 `requested_documents` 非空时，才通过 `AskFileMessage` 主动要求上传

上传成功后：

- 调用文件 API
- 向用户确认材料已接收
- 允许继续下一轮消息

### 4.4 报告展示

当走到需要展示结果的时点，Chainlit 调用：

- 用户报告接口
- 内部报告接口

v1 展示策略：

- 主聊天区展示用户报告摘要
- 用按钮触发“查看内部 JSON 报告”
- 内部报告可以先以代码块或侧边元素展示，不强制做复杂 React 自定义组件

## 5. 状态模型

`cl.user_session` 只保存前端编排所需最小状态：

- `session_id`
- `declared_family`
- `required_initial_package`
- `last_governor_decision`
- `pending_requested_documents`

不在 Chainlit session 中镜像完整 `ApplicantProfile` 或 `ScoreState`，避免前后端状态漂移。

## 6. 建议文件结构

建议新增以下文件：

- `chainlit_app.py`
  - Chainlit 入口
  - `on_chat_start` / `on_message` / action callback
- `app/ui/chainlit_client.py`
  - 对现有 FastAPI 领域接口的轻量调用封装
- `.chainlit/config.toml`
  - 关闭自由上传、配置 UI 标题和功能开关

建议修改：

- `app/main.py`
  - 挂载 Chainlit 子应用
- `pyproject.toml`
  - 增加 `chainlit` 依赖

## 7. UI 能力边界

v1 不追求复杂前端，而追求“像产品，不像裸 API”。

因此 UI 只做以下增强：

- 签证家族按钮选择
- 清晰的系统消息分区
- 受控上传提示
- 报告查看按钮

v1 不做：

- 自定义 React 大组件
- 多标签复杂工作台
- 高级图表
- 多人协作

如果后续要加强报告展示，再引入 Chainlit `CustomElement`。

## 8. 测试与验收

至少覆盖以下内容：

- Chainlit 启动后能创建 session
- 选择签证家族后能进入消息流
- 后端返回 `requested_documents` 时会触发受控上传
- 上传后能继续对话
- 能查看 `/reports/user`
- 能查看 `/reports/internal`

验收标准：

- 不改现有领域 API 契约
- 不新增前端私有事实源
- 不允许自由上传绕过后端补证时机
- 单机启动即可完成完整演示

## 9. 风险与缓解

### 9.1 风险：UI 编排与后端阶段机不一致

缓解：所有关键状态以后端返回为准，Chainlit 只缓存最小会话字段。

### 9.2 风险：文件上传体验与真实补证流程不一致

缓解：关闭自由上传，只保留系统触发的 `AskFileMessage`。

### 9.3 风险：为了省事把业务逻辑搬进 Chainlit

缓解：明确规定 Chainlit 只能调用 API，不直接触达数据库和领域服务。

## 10. 分阶段落地建议

### Phase 1

完成最小可用链路：

- 挂载 Chainlit
- 选择签证家族
- 发消息
- 请求补证并上传
- 查看用户报告

### Phase 2

增强内部调试能力：

- 查看内部 JSON 报告
- 展示当前阶段与请求材料

### Phase 3

按需增强 UI：

- 更好的报告布局
- 自定义元素
- 更细的错误提示与重试体验

## 11. 外部文档依据

本设计主要依据 Chainlit 官方文档能力做出：

- FastAPI 挂载：<https://docs.chainlit.io/integrations/fastapi>
- 用户会话：<https://docs.chainlit.io/concepts/user-session>
- 受控文件上传：<https://docs.chainlit.io/api-reference/ask/ask-for-file>
- 自由上传开关：<https://docs.chainlit.io/backend/config/features>
- 多模态与禁用自由上传：<https://docs.chainlit.io/advanced-features/multi-modal>
- 动作按钮：<https://docs.chainlit.io/api-reference/ask/ask-for-action>

设计中的“最适合本仓库”这一结论，是基于上述官方能力与当前代码结构做出的工程判断，不是文档原文直接声明。
