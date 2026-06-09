# DS-160 API Guide

这份文档面向要接入或调试 DS-160 AI 面签模拟器的技术读者。它按真实工作流组织 API，而不是按源码文件顺序罗列。所有路径均已按当前 FastAPI 路由、前端 API client 和 integration tests 做过人工核对。

当前公开工作台是 **native-only public runtime**：会话消息、材料上传后的主流程刷新，以及 OpenAI-compatible adapter 最终都执行 `native_interviewer` / `NativeInterviewerRuntimeService`。`legacy` 不是公开 fallback；`graph` / `graph_shadow` / `graph_canary` 只保留为 shadow、eval、兼容标签或未来 promotion 语境。

## 1. Base URL 与路径约定

| 场景 | Base URL | 示例 |
| --- | --- | --- |
| 前端产品首页 | Web 根路径 | `GET /` |
| 前端用户工作台 | Web 路由 | `GET /login` |
| 前端微信 web-view 工作台 | Web 路由，小程序壳内打开同一套轻量工作台 | `GET /wx` |
| 前端项目状态页 | Web 路由，读取后端 `/healthz` | `GET /health` |
| 前端后台入口 | Web 路由 | `GET /admin` |
| 直接访问后端 FastAPI | `http://localhost:8000/v1` | `GET http://localhost:8000/v1/app-config` |
| 浏览器经 Next / Nginx 反代 | `/api/v1` | `GET /api/v1/app-config` |
| 健康检查 / 版本 | 后端根路径 | `GET /healthz`、`GET /version` |

本文示例默认使用直接后端路径 `http://localhost:8000/v1`。如果你从生产前端或 Nginx 入口调用，把 `/v1/...` 替换成 `/api/v1/...` 即可。公开首页 `/` 只负责产品展示和入口组织；真正的授权会话、材料、复盘和调试能力仍在 `/login` 工作台中完成，首页授权弹窗成功后也会进入该工作台。`/wx` 是微信小程序 web-view MVP 的 H5 入口，使用同一个 access key 登录体系，不依赖 `wx.login` / OpenID。

FastAPI 自动文档在 `/docs`、`/redoc`、`/openapi.json`；生产默认受 `APP_AUTH_PROTECT_DOCS=true` 保护。

## 2. Endpoint 类型

| 类型 | 说明 | 认证 |
| --- | --- | --- |
| User / workbench | 普通工作台：登录、会话、消息、材料、报告 | Web Cookie；本地未设置 `APP_AUTH_PASSWORD` 时可关闭 |
| Admin | 后台登录、access key、运行时模型配置、后台设置、后台 RAG 状态 | Admin Cookie |
| WeChat / wx-upload | `/wx` H5 工作台创建短期 upload ticket；小程序原生页用 ticket 上传微信聊天文件 | 创建 ticket 需要 Session access；ticket status/upload 是短期 public scoped 凭证 |
| Debug / controlled demo | runtime snapshot、debug material generation、runtime trace；material package archive/list/import 仅用于受控 demo/模板资产 | 普通或 admin session + debug 开关 |
| OpenAI-compatible | `/v1/chat/completions`、`/v1/responses` 的状态化 DS-160 adapter | Cookie 或 `Authorization: Bearer <APP_COMPAT_API_KEY>` |

## 3. 认证方式

### 3.1 Web Cookie 登录

普通用户登录使用：

```http
POST /v1/auth/login
Content-Type: application/json
```

```json
{"password":"<user-password-or-access-key>"}
```

成功后后端设置 `HttpOnly` Cookie，并返回：

```json
{"authenticated":true,"expires_in":86400,"history_namespace":"local-dev"}
```

如果 password 是管理员发放的 access key，`history_namespace` 会变成 `key_<key_id>`。前端用它隔离本地历史记录。

Access key 登录只建立普通 user cookie，不消耗使用次数。使用次数、禁用和过期检查发生在 `POST /v1/sessions` 创建新会话时；这样已耗尽、禁用或过期的 key 仍可回到已绑定的服务器历史，但不能继续开新 session。

登录请求不再接收或要求用户显示名。前端会保留已有本地 profile，或生成临时显示名；用户可以进入工作台后在设置里修改显示名。该显示名是前端工作台资料，不是后端账号身份字段。

后台 access key 卡片提供三类显式动作：

- `显示明文`：只在当前后台界面 reveal 该 Key；
- `复制 Key`：读取该 Key 的 secret 并直接写入剪贴板；
- `一键分享链接`：生成 `/#ds160_access_key=<access-key-secret>`，让用户打开首页、`/login` 或 `/wx` 后点击启用进入工作台。

分享链接优先使用 hash 参数，因为普通页面请求不会把 hash 发给后端。兼容解析仍支持 `?ds160_access_key=`、`?access_key=`、`?key=` 等 query 形式，但不推荐新链接使用 query，避免 access key 进入服务端访问日志、代理日志或 Referer。分享链接登录成功后，前端会用 `history.replaceState` 清理地址栏中的 Key。

### 3.2 Admin Cookie 登录

后台登录使用：

```http
POST /v1/admin/login
Content-Type: application/json
```

```json
{"password":"<admin-password>"}
```

`ADMIN_AUTH_PASSWORD` 优先；未设置时使用 `APP_AUTH_PASSWORD` 作为后台 fallback。后台登录设置独立的 admin cookie。

### 3.3 Machine Bearer token

外部机器客户端调用 OpenAI-compatible endpoint 时，配置：

```env
APP_COMPAT_API_KEY=<machine-api-key>
```

请求携带：

```http
Authorization: Bearer <machine-api-key>
```

当前 middleware 只对以下路径接受 machine bearer token。文档里的 token 均使用占位符；不要把真实 bearer、access key 或模型 key 写进 Markdown、issue、截图或日志：

- `POST /v1/chat/completions`
- `POST /v1/responses`

### 3.4 CSRF / Origin

开启 `APP_AUTH_CSRF_PROTECTION=true` 时，受 Cookie 保护的非安全方法需要合法 `Origin` 或 `Referer`。允许来源来自请求 host、`CORS_ALLOW_ORIGINS`，以及反代传入的 `X-Forwarded-Host` / `X-Forwarded-Proto`。

常见失败：

```json
{"detail":"csrf validation failed"}
```

## 4. 快速工作流

### 4.1 登录、创建会话、发送一轮消息

```bash
curl -i -c cookie.txt \
  -H 'Content-Type: application/json' \
  -d '{"password":"<user-password-or-access-key>"}' \
  http://localhost:8000/v1/auth/login

curl -b cookie.txt -c cookie.txt \
  -H 'Content-Type: application/json' \
  -d '{"declared_family":"f1"}' \
  http://localhost:8000/v1/sessions

curl -b cookie.txt -c cookie.txt \
  -H 'Content-Type: application/json' \
  -d '{"role":"user","content":"I will study computer science in the US.","client_message_id":"client-msg-001"}' \
  http://localhost:8000/v1/sessions/sess_123/messages
```

### 4.2 上传材料并继续对话

```bash
curl -b cookie.txt -c cookie.txt \
  -F 'file=@fixtures/sample-i20.pdf' \
  -F 'context_text=This is my I-20.' \
  http://localhost:8000/v1/sessions/sess_123/files
```

上传返回 `202` 表示文件已保存并进入材料理解队列；聊天可以继续，不必等所有材料理解完成。

### 4.3 后台发放 access key

```bash
curl -i -c admin.txt \
  -H 'Content-Type: application/json' \
  -d '{"password":"<admin-password>"}' \
  http://localhost:8000/v1/admin/login

curl -b admin.txt \
  -H 'Content-Type: application/json' \
  -d '{"label":"demo visitor","usage_limit":2,"expires_at":null,"enabled":true}' \
  http://localhost:8000/v1/admin/access-keys
```

`key` 只应在创建响应或可 reveal 的受控后台界面中短暂出现，不要写入文档、日志或截图。

后台 UI 可以直接复制 Key 或生成一键分享链接。分享链接示例只使用占位符：

```text
https://YOUR_DOMAIN/#ds160_access_key=<access-key-secret>
```

收到链接的用户打开后，在首页授权弹窗、`/login` guard 或 `/wx` 授权卡片里点击启用即可进入工作台；无需再次输入 Key，也无需在登录时填写名字。

### 4.4 微信小程序上传材料

`/wx` H5 工作台在用户已登录并选择 session 后，先创建一个短期 upload ticket：

```bash
curl -b cookie.txt -c cookie.txt \
  -X POST \
  http://localhost:8000/v1/sessions/sess_123/upload-ticket
```

返回：

```json
{
  "ticket":"wxup_<short-lived-token>",
  "session_id":"sess_123",
  "expires_at":"2026-06-09T08:05:00Z",
  "max_files":5,
  "uploaded_count":0,
  "remaining_files":5,
  "status":"active",
  "upload_results":[]
}
```

小程序原生页随后用 `wx.uploadFile` 上传：

```bash
curl -F 'file=@fixtures/sample-i20.pdf' \
  -F 'session_id=sess_123' \
  -F 'context_text=I-20 from WeChat chat' \
  http://localhost:8000/v1/wx/upload-tickets/wxup_xxx/files
```

ticket 默认 300 秒有效、最多 5 个文件；后端只保存 ticket hash。ticket 出现在 URL path 中，反代 access log 可能记录它，因此只能作为短期上传凭证使用。

## 5. 常用概念与合同

### 5.1 Session

`session_id` 是一场面签模拟的状态主键。普通 access key 用户只能访问该 key 创建的 session；admin 可以查看全部 session。

### 5.2 Message turn 与失败重试

普通消息请求支持 `client_message_id`：

```json
{"role":"user","content":"My parents will sponsor me.","client_message_id":"client-msg-002"}
```

合同：

- 同一个 `client_message_id` 已完成时，重复请求返回同一 assistant turn，并带 `idempotent_replay=true`。
- 同一个 `client_message_id` 仍在处理中时，返回 `409`。
- 前端失败消息会保留 `retry_content` 和 `client_message_id`，用户点“重试本条”时复用同一 id。
- 模型供应商的临时连接/超时/部分 5xx/可重试 429 会在后端自动重试；流式接口通过 `debug_event.step="provider_runtime_retry"` 暴露尝试状态。
- 对确定性配置错误、认证错误、额度耗尽或 quality guard 失败，不会盲目重试。

### 5.3 SSE 事件

消息流和 debug material generation 都是事件式 SSE，不是 token 级逐字流。

通用格式：

```text
event: final
data: {"assistant_message":"..."}
```

合同：

- HTTP 请求成功并进入流后，业务失败可能以 `event: error` 返回，HTTP status 仍可能是 `200`。
- 客户端必须消费 SSE event，不能只看 HTTP status。
- `event: final` 是成功终止事件；`event: error` 是失败终止事件。
- `Cache-Control: no-cache` 与 `X-Accel-Buffering: no` 会由后端返回；反向代理仍需关闭响应缓冲。

### 5.4 材料理解与 Case Board

上传材料会创建 `case_understanding` 任务，并立即返回一个 Case Board refresh payload。材料理解失败时会在 `case_board_refresh.failure_message`、runtime debug snapshot 和材料库 UI 中体现；上传结果不应被前端拼成 assistant 对话气泡。

### 5.5 Runtime config snapshot 语义

运行时模型配置有三层：

1. 请求级用户 BYOK：`user_model_config` / `model_config`，只在本次消息请求中生效，不写入数据库。
2. Admin saved config：后台保存的 `model_base_url`、`model_api_key`、`model_name` 和 `model_streaming_enabled`。
3. Env fallback：`OPENAI_BASE_URL`、`OPENAI_API_KEY`。

后台模型配置测试接口会构造一次 `AdminRuntimeModelSnapshot`：

- 如果请求体传了 `base_url` / `api_key` / `model`，`source="draft"`；
- 否则如果后台保存过模型配置，`source="admin"`；
- 否则来自环境变量，`source="env"`；
- 响应不会回显 API key，只会返回 `base_url`、`model`、`source` 和测试结果。

`GET /v1/sessions/{session_id}/debug/runtime` 返回的是一次只读、redacted 的调试快照，用来观察当前 session 的 runtime、latest turn、timeline、errors、Case Board 和材料理解状态；它不是写入接口，也不是实时订阅。

### 5.6 Public runtime 语义

- 公开请求的 canonical writer 是 `native_interviewer`。即使环境变量仍接受 `graph`、`graph_shadow`、`graph_canary` 或历史值，`/messages` 与材料变更刷新也不应把它们解释成公开 writer 切换。
- `legacy` 只表示历史/冻结实现或迁移兼容值，不是 native runtime 出错后的普通公开 fallback。native 执行失败时按错误合同返回，不能静默改由 legacy 生成回复。
- `graph` 相关标签仅用于 replay/eval、shadow 对比、兼容 metadata 或未来单独验证的 promotion 分支；当前生产公开链路不要把它写成与 native 并列的可选 runtime。

## 6. 错误 payload 与状态码

FastAPI 常规错误：

```json
{"detail":"authentication required"}
```

模型运行错误常见形态：

```json
{
  "detail": {
    "status": 502,
    "detail": "Unable to connect to upstream model service.",
    "error_category": "upstream_connection_error",
    "upstream_code": "upstream_connection_error",
    "provider": "openai_compatible",
    "model": "gpt-compatible-model",
    "retry_attempts": 1,
    "retry_exhausted": true
  }
}
```

SSE `error` event 常见形态：

```json
{
  "status": 409,
  "detail": "这条消息正在处理中，请等待上一轮结果返回。"
}
```

| Status | 常见原因 |
| --- | --- |
| `400` | 后台模型配置不完整，或测试请求缺少必要配置 |
| `401` | 未登录、Cookie 失效、machine bearer token 缺失/错误 |
| `403` | CSRF 失败、access key 无权访问该 session、feature flag 未开启 |
| `404` | session、document、runtime trace、access key 或 upload ticket 不存在 |
| `409` | 会话已关闭、重复消息仍在处理中、签证类别未锁定、upload ticket 已完成/停用/超限 |
| `410` | upload ticket 已过期 |
| `413` | 上传文件超过限制 |
| `415` | 不支持的文件类型 |
| `422` | 请求字段不合法、签证类别不支持、debug scenario / generation mode 不合法 |
| `429` | 登录限流或上游模型限流 |
| `502` / `503` / `504` | 上游模型、RAG、材料生成或连接/超时问题 |

## 7. Endpoint Reference

### 7.1 App / version / health

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/app-config` | Public | 返回前端可见功能开关 |
| `GET` | `/livez` | Public | 轻量存活检查 |
| `GET` | `/healthz` | Public | 健康检查 |
| `GET` | `/version` | Public | 后端版本、git sha、build time |

#### `GET /v1/app-config`

Success example:

```json
{
  "show_github_link": false,
  "debug_console_enabled": false,
  "debug_material_enabled": false,
  "user_model_config_enabled": false,
  "rag_status_user_visible": false
}
```

注意：当前 public app config 不向普通用户开放 BYOK 和 RAG 状态；后台 DB flag 只用于受控内部 endpoint guard，不代表普通公开能力已开放。

前端使用说明：

- `show_github_link=false` 时，公开首页和 `/health` 状态页都隐藏 GitHub 链接。
- `show_github_link=true` 时，前端使用 `PROJECT_INFO.githubUrl` 渲染 GitHub 链接，不应在页面里硬编码第二份仓库 URL。
- 如果 `/v1/app-config` 暂不可用，公开页面按安全默认值处理：隐藏 GitHub 链接，其他入口继续可见。

### 7.2 Auth

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/auth/login` | Public | 普通用户密码或 access key 登录 |
| `GET` | `/v1/auth/me` | Public | 查看普通登录状态 |
| `POST` | `/v1/auth/logout` | Cookie | 撤销普通 session 并清 Cookie |

#### `POST /v1/auth/login`

Request:

```json
{"password":"<user-password-or-access-key>"}
```

Response:

```json
{"authenticated":true,"expires_in":86400,"history_namespace":"key_ak_123"}
```

Important notes:

- access key 登录走同一个普通登录入口；
- `APP_AUTH_PASSWORD_USER_FALLBACK_ENABLED=false` 时，普通共享密码不会作为用户 fallback，access key 是主要用户入口；
- 登录失败会按 IP hash 做短窗口限流，可能返回 `429`。

### 7.3 Sessions

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/sessions` | User/Admin Cookie | 创建 session |
| `GET` | `/v1/sessions` | User/Admin Cookie | 列出当前可见 session |
| `GET` | `/v1/sessions/{session_id}/required-package` | Session access | 读取兼容最低材料包 |
| `GET` | `/v1/sessions/{session_id}/runtime-traces/{run_id}` | Session access + debug | 读取单次 runtime trace |

#### `POST /v1/sessions`

Request:

```json
{"declared_family":"f1"}
```

`declared_family` 支持 `f1`、`j1`、`b1_b2`、`h1b` 或 `null`。

Response:

```json
{
  "session_id":"sess_abc123",
  "phase_state":"created",
  "current_governor_decision":null,
  "gate_status":{"status":"missing_required_documents"}
}
```

Access key 语义：创建 session 时会消耗该 key 的 quota；超过 `usage_limit`、禁用或过期会返回 `403`。

#### `GET /v1/sessions`

普通 access key 用户只看到该 key 创建的 session；admin 看到全部 session。

Response:

```json
{
  "sessions":[
    {"session_id":"sess_abc123","phase_state":"interview","declared_family":"f1","current_governor_decision":"continue_interview"}
  ]
}
```

#### `GET /v1/sessions/{session_id}/required-package`

返回旧前端兼容的最低材料包信息。材料缺失不会阻断聊天；主线追问仍由 interviewer runtime 的 `turn_decision` 决定。

Response:

```json
{
  "required_initial_package":["passport_bio","i20","funding_proof"],
  "required_initial_package_labels":["Passport bio page","I-20","Funding proof"]
}
```

### 7.4 Messages and streaming

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/sessions/{session_id}/messages` | Session access | 读取 public transcript |
| `POST` | `/v1/sessions/{session_id}/messages` | Session access | 提交用户消息并同步返回 assistant 回复 |
| `POST` | `/v1/sessions/{session_id}/messages/stream` | Session access | 事件式 SSE 消息入口 |

#### `POST /v1/sessions/{session_id}/messages`

Request:

```json
{
  "role":"user",
  "content":"My parents will pay for my first year tuition.",
  "client_message_id":"client-msg-002"
}
```

可选 BYOK 字段，只有后台允许用户模型配置时才接受：

```json
{
  "role":"user",
  "content":"Please continue the interview.",
  "model_config": {
    "base_url":"https://models.example.test/v1",
    "api_key":"user-key",
    "model":"gpt-compatible-model"
  }
}
```

Response excerpt:

```json
{
  "assistant_message":"Can you explain how your parents will fund the tuition?",
  "governor_decision":"continue_interview",
  "requested_documents":["funding_proof"],
  "remaining_required_documents":[],
  "gate_progress":{"overall_status":"in_progress"},
  "turn_decision":{"decision":"need_more_evidence"},
  "document_review":{},
  "runtime_view_state":{},
  "phase_state":"interview"
}
```

Important notes:

- `role` 只接受 `user`；
- `model_config` 是 `user_model_config` 的兼容 alias；
- 用户 API key 只在本次请求中使用，不写入后端数据库；
- `native_interviewer` 失败不会静默生成 legacy 回复；错误会按模型运行错误合同返回。

#### `POST /v1/sessions/{session_id}/messages/stream`

Request 与同步消息接口相同。

事件类型：

| Event | Data |
| --- | --- |
| `accepted` | `{"session_id":"..."}` |
| `analyzing` | `{"stage":"interview_runtime"}` 或 `{"status":"still_running"}` |
| `debug_event` | runtime progress / provider retry event |
| `final` | 完整 `MessageResponse` |
| `error` | `{"status":...,"detail":"..."}` 或模型运行错误 payload |

示例：

```text
event: accepted
data: {"session_id":"sess_abc123"}

event: final
data: {"assistant_message":"...","requested_documents":[]}
```

### 7.5 Files / materials

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/sessions/{session_id}/files` | Session access | 上传材料 |
| `GET` | `/v1/sessions/{session_id}/files/{document_id}/content` | Session access | 预览/下载原始材料内容 |
| `DELETE` | `/v1/sessions/{session_id}/files/{document_id}` | Session access | tombstone 一份材料 |
| `POST` | `/v1/sessions/{session_id}/upload-ticket` | Session access | 为微信小程序原生上传页创建短期 upload ticket |
| `GET` | `/v1/wx/upload-tickets/{ticket}` | Short-lived ticket | 查询 ticket 状态和已上传结果 |
| `POST` | `/v1/wx/upload-tickets/{ticket}/files` | Short-lived ticket | 用 ticket 上传微信聊天文件，返回 `202` |
| `GET` | `/v1/material-packages` | Session access + debug material switch | 列出已验证 material package archive |
| `POST` | `/v1/sessions/{session_id}/material-packages/{package_id}/import` | Session access + debug material switch | 导入已验证 material package |

#### `POST /v1/sessions/{session_id}/files`

`multipart/form-data` fields:

| Field | Required | Description |
| --- | --- | --- |
| `file` | yes | PDF、PNG、JPG、JPEG 或后端支持的文件 |
| `document_type` | no | 用户或调试工具显式纠偏的材料类型 |
| `context_text` | no | 同一条聊天消息里的原始用户文本 |

Response excerpt:

```json
{
  "document_id":"doc_abc123",
  "content_url":"/v1/sessions/sess_abc123/files/doc_abc123/content",
  "document_status":"uploaded",
  "job_id":"job_123",
  "job_status":"queued",
  "understanding_status":"queued",
  "document_type":null,
  "case_board_refresh":{
    "event_type":"material_uploaded",
    "status":"queued",
    "understanding_status":"queued",
    "message_policy":"case_board_timeline_only"
  },
  "evidence_cards":[],
  "requested_documents":[],
  "remaining_required_documents":[]
}
```

Notes:

- 返回 `202` 表示已入队，不表示材料理解已完成；
- 已关闭/已拒签/已完成 session 不能继续上传，返回 `409`；
- 图片不走 OCR 文件名猜测，视觉理解由多模态材料理解服务处理；
- `case_board_refresh.message_policy="case_board_timeline_only"` 表示只刷新 Case Board / timeline。

#### `POST /v1/sessions/{session_id}/upload-ticket`

为微信小程序原生上传页创建一个短期上传凭证。创建该 ticket 需要当前用户有访问该 session 的权限；ticket 本身只用于后续 status/upload，不需要 cookie。

Response:

```json
{
  "ticket":"wxup_<short-lived-token>",
  "session_id":"sess_abc123",
  "expires_at":"2026-06-09T08:05:00Z",
  "max_files":5,
  "uploaded_count":0,
  "remaining_files":5,
  "status":"active",
  "upload_results":[]
}
```

默认合同：

- `ticket` 原文只在响应和小程序路由里短期流转，数据库保存 `sha256(ticket)`；
- 默认 TTL 是 300 秒；
- 默认最多 5 个文件，服务端当前把上限限制在 10 以内；
- ticket 绑定创建时的 `session_id` 和 access key 语境，不能跨 session 使用。

#### `GET /v1/wx/upload-tickets/{ticket}`

查询 ticket 状态，供 `/wx` 从原生上传页返回后刷新材料状态。

```json
{
  "ticket":"wxup_<short-lived-token>",
  "session_id":"sess_abc123",
  "expires_at":"2026-06-09T08:05:00Z",
  "max_files":5,
  "uploaded_count":1,
  "remaining_files":4,
  "status":"active",
  "upload_results":[
    {
      "document_id":"doc_abc123",
      "file_name":"i20.pdf",
      "mime_type":"application/pdf",
      "size":12345,
      "uploaded_at":"2026-06-09T08:01:00Z"
    }
  ]
}
```

#### `POST /v1/wx/upload-tickets/{ticket}/files`

`multipart/form-data` fields:

| Field | Required | Description |
| --- | --- | --- |
| `file` | yes | `wx.uploadFile` 选择的微信聊天文件 |
| `session_id` | no | 小程序回传的 session id；若传入且与 ticket 不匹配，返回 `403` |
| `document_type` | no | 用户显式纠偏的材料类型 |
| `context_text` | no | 用户在小程序上传页输入的补充说明 |
| `original_name` | no | 微信文件原名；优先用于后端材料 filename |

成功返回 `202`，body 包含 ticket 状态和标准材料上传 payload：

```json
{
  "ticket":"wxup_<short-lived-token>",
  "session_id":"sess_abc123",
  "status":"active",
  "uploaded_count":1,
  "remaining_files":4,
  "upload_results":[{"document_id":"doc_abc123","file_name":"i20.pdf"}],
  "upload":{
    "document_id":"doc_abc123",
    "document_status":"uploaded",
    "job_status":"queued",
    "understanding_status":"queued",
    "case_board_refresh":{"message_policy":"case_board_timeline_only"}
  }
}
```

Ticket-specific errors:

| Status | Detail |
| --- | --- |
| `403` | `session_id` 与 ticket 绑定的 session 不匹配 |
| `404` | ticket 或绑定 session 不存在 |
| `409` | ticket 已完成、停用、文件数超限，或 session 已结束 |
| `410` | ticket 过期 |
| `413` | 文件超过后端上传大小限制 |
| `415` | 文件类型不支持 |

#### `DELETE /v1/sessions/{session_id}/files/{document_id}`

删除语义是 tombstone，不是物理删除审计记录。

```json
{
  "document_id":"doc_abc123",
  "document_status":"tombstoned",
  "case_board":{"schema_version":"case_board.v1","claims":[],"evidence_cards":[]}
}
```

#### `GET /v1/material-packages`

列出已发布到 archive 的可复用 material package。当前主要用途是受控 demo/模板资产，例如经过 `scripts/f1_demo_material_package.py validate` 与 `publish` 的 F-1 自洽材料包；它不是让普通公开用户在线生成材料的入口。

边界要分清：

- **material package archive/list/import**：读取和导入已经验证过的模板资产，可用于受控演示、回归验证和客户 demo 初始化。
- **debug material generation**：`/debug/material-bundles` 和 `/debug/fill-current-gap` 这类本地/受控测试能力，用来生成 synthetic/debug materials；不要作为公开生产用户功能开放。
- 当前 archive/list/import 仍受 `debug_material_enabled` / `ALLOW_DEBUG_FILL` 保护开关约束；如果关闭，会返回 `403`，这是预期的安全边界。

```json
{
  "packages":[
    {"package_id":"f1-demo-validated-package","label":"F-1 validated demo package","status":"ready","document_count":6,"document_types":["i20","funding_proof"]}
  ]
}
```

#### `POST /v1/sessions/{session_id}/material-packages/{package_id}/import`

把已验证 material package 复制到目标 session，并触发材料变更刷新。导入后的材料属于目标 session 的材料库；archive 源包本身不会被消费或删除。

```json
{
  "session_id":"sess_target",
  "package_id":"f1-demo-validated-package",
  "imported_bundle_id":"pkg-import-abc123",
  "import_status":"imported",
  "documents":[],
  "main_flow_refresh_error":null
}
```

如果复制成功但刷新失败，`import_status` 可能是 `partial`，错误在 `main_flow_refresh_error` 中说明。

### 7.6 Reports

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/sessions/{session_id}/reports/user` | Session access | 用户可读准备报告 |
| `GET` | `/v1/sessions/{session_id}/reports/internal` | Session access | 内部调试报告 |
| `POST` | `/v1/sessions/{session_id}/reports/review` | Session access | 生成面试复盘 |
| `GET` | `/v1/sessions/{session_id}/reports/export` | Session access | 导出 session 快照 |

#### `GET /v1/sessions/{session_id}/reports/user`

```json
{
  "session_id":"sess_abc123",
  "summary":"You should clarify funding source and school choice.",
  "risk_level":"medium",
  "missing_evidence":[],
  "allowed_next_actions":[],
  "case_board":{"schema_version":"case_board.v1"}
}
```

#### `GET /v1/sessions/{session_id}/reports/export`

导出包含 session、用户报告、内部报告、profile snapshot 和材料摘要。被 tombstone 的材料不会继续贡献 Case Board 事实或证据。

```json
{
  "schema_version":"ds160.session_export.v1",
  "session":{"session_id":"sess_abc123","phase_state":"interview"},
  "reports":{"user":{},"internal":{}},
  "profile_snapshot":{},
  "documents":[]
}
```

### 7.7 RAG

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/rag/status` | Admin Cookie | 后台查看 RAG 状态 |
| `POST` | `/v1/rag/files` | Admin Cookie | 上传第三方知识库资料 |
| `GET` | `/v1/admin/rag/status` | Admin Cookie | 后台 RAG 状态别名/后台入口 |

当前 `app_config.rag_status_user_visible` 对普通工作台固定隐藏；RAG 状态和上传入口是后台/运维面。

#### `GET /v1/rag/status`

```json
{
  "enabled":true,
  "ready":false,
  "status":"available",
  "skip_reason":null,
  "vector_store":"chroma",
  "index_version":"v1",
  "collection_prefix":"us_visa",
  "embedding_model":"BAAI/bge-m3",
  "rerank_model":"Qwen/Qwen3-Reranker-4B",
  "upload_max_size_mb":32,
  "allow_third_party_reference":false,
  "collections":[]
}
```

#### `POST /v1/rag/files`

`multipart/form-data` fields:

| Field | Required | Description |
| --- | --- | --- |
| `file` | yes | 知识库文件 |
| `title` | no | 来源标题 |
| `url` | no | 来源 URL |
| `visa_family` | no | 签证类型 |
| `country` | no | 国家 |
| `post` | no | 领馆/地区 |
| `section_path` | no | 章节路径 |

公共上传入口固定写入 `source_type="third_party_reference"`，不会把用户上传资料提升为官方来源。

```json
{
  "status":"indexed",
  "source_id":"source_123",
  "source_type":"third_party_reference",
  "title":"Consulate FAQ",
  "collection_name":"us_visa_third_party_reference_v1",
  "chunk_count":10,
  "skipped":false,
  "skip_reason":null
}
```

### 7.8 User model config

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/model-config/models` | Cookie + feature flag | 用用户提供的 OpenAI-compatible 配置读取 `/models` |

#### `POST /v1/model-config/models`

需要后台/配置允许用户模型配置；否则返回 `403`。

Request:

```json
{"base_url":"https://models.example.test/v1","api_key":"<user-model-api-key>"}
```

Response:

```json
{"models":[{"id":"gpt-compatible-model","label":"gpt-compatible-model"}]}
```

用户 `api_key` 不会被持久化。前端也不应把它长期写入 `localStorage`。

### 7.9 Admin access keys

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/admin/login` | Public | 后台登录 |
| `GET` | `/v1/admin/me` | Public | 后台登录状态 |
| `POST` | `/v1/admin/logout` | Admin Cookie | 后台登出 |
| `POST` | `/v1/admin/access-keys` | Admin Cookie | 创建 access key |
| `GET` | `/v1/admin/access-keys` | Admin Cookie | 查询 access keys |
| `PATCH` | `/v1/admin/access-keys/{key_id}` | Admin Cookie | 更新 label、limit、enabled、expires_at |
| `GET` | `/v1/admin/access-keys/{key_id}/secret` | Admin Cookie | reveal 可用 secret |
| `GET` | `/v1/admin/access-keys/{key_id}/sessions` | Admin Cookie | 查询 key 创建的 sessions |
| `GET` | `/v1/admin/sessions/{session_id}/messages` | Admin Cookie | 后台查看 session transcript |

#### `POST /v1/admin/access-keys`

Request:

```json
{"label":"demo visitor","usage_limit":2,"expires_at":null,"enabled":true}
```

Response:

```json
{
  "key":"<access-key-secret>",
  "record":{
    "key_id":"ak_123",
    "label":"demo visitor",
    "usage_limit":2,
    "usage_count":0,
    "remaining_uses":2,
    "enabled":true,
    "can_create_session":true,
    "created_at":"2026-06-05T08:00:00Z",
    "expires_at":null
  }
}
```

`key` 是敏感 secret；不要提交到代码、文档或公开日志。后续列表通常只返回 masked preview / record。

后台界面行为：

- `显示明文` 会 reveal 当前选中的 Key，并在页面内显示；
- `复制 Key` 会 reveal 当前选中 Key 后直接写入剪贴板，不需要先点选列表再到右侧详情复制；
- `一键分享链接` 会生成 `/#ds160_access_key=...` 链接并写入剪贴板，供用户打开后点击启用进入工作台；
- 如果 secret 不可 reveal 或剪贴板失败，界面应明确提示，并保留可手动复制的受控文本。

分享链接等同于持有 access key。运营上应给这类 Key 设置合理 `usage_limit`、`expires_at` 和标签，避免长期公开转发。

#### `GET /v1/admin/access-keys`

Query params:

| Param | Values | Description |
| --- | --- | --- |
| `q` | string | 按 key id / label 等搜索 |
| `status` | `enabled` / `disabled` / `all` | 状态过滤，默认 `all` |
| `expired` | `true` / `false` | 过期过滤 |

Response:

```json
{"keys":[{"key_id":"ak_123","label":"demo visitor","remaining_uses":1,"enabled":true,"secret_available":true}]}
```

#### `PATCH /v1/admin/access-keys/{key_id}`

```json
{"enabled":false,"usage_limit":3,"expires_at":"2026-07-01T00:00:00Z"}
```

Response:

```json
{"record":{"key_id":"ak_123","enabled":false,"remaining_uses":0}}
```

#### `GET /v1/admin/access-keys/{key_id}/secret`

```json
{"key_id":"ak_123","key":"<access-key-secret>","available":true}
```

如果 secret 已不可 reveal：

```json
{"key_id":"ak_123","key":null,"available":false,"detail":"secret is no longer available"}
```

### 7.10 Admin runtime model config and settings

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/admin/settings` | Admin Cookie | 读取后台设置，API key 只返回 configured bool |
| `PATCH` | `/v1/admin/settings` | Admin Cookie | 更新后台设置 |
| `POST` | `/v1/admin/model-config/models` | Admin Cookie | 用 draft/saved/env 配置读取模型列表 |
| `POST` | `/v1/admin/model-config/test` | Admin Cookie | 做低成本 chat completion 连通性测试 |

#### `GET /v1/admin/settings`

```json
{
  "model_base_url":"https://models.example.test/v1",
  "model_name":"gpt-compatible-model",
  "model_streaming_enabled":true,
  "model_api_key_configured":true,
  "user_model_config_enabled":false,
  "show_github_link":false,
  "debug_console_enabled":false,
  "debug_material_enabled":false,
  "rag_status_user_visible":false
}
```

#### `PATCH /v1/admin/settings`

Request:

```json
{
  "model_base_url":"https://models.example.test",
  "model_api_key":"<model-api-key>",
  "model_name":"gpt-compatible-model",
  "model_streaming_enabled":true,
  "debug_console_enabled":true,
  "debug_material_enabled":false
}
```

Response 会规范化 `model_base_url` 到 `/v1`，并隐藏 `model_api_key` 原文。

#### `POST /v1/admin/model-config/models`

Request 可为空，表示使用 saved/admin/env snapshot：

```json
{}
```

也可传 draft 覆盖：

```json
{"base_url":"https://models.example.test/v1","api_key":"<draft-model-api-key>"}
```

Response:

```json
{
  "models":[{"id":"gpt-compatible-model","label":"gpt-compatible-model"}],
  "source":"draft",
  "base_url":"https://models.example.test/v1"
}
```

#### `POST /v1/admin/model-config/test`

Request:

```json
{"base_url":"https://models.example.test/v1","api_key":"<draft-model-api-key>","model":"gpt-compatible-model"}
```

Success:

```json
{
  "ok":true,
  "latency_ms":320,
  "model":"gpt-compatible-model",
  "provider":"openai_compatible",
  "base_url":"https://models.example.test/v1",
  "source":"draft",
  "detail":"OK",
  "upstream":{"status_code":200}
}
```

Failure still returns a structured body instead of raising FastAPI error for most upstream failures:

```json
{
  "ok":false,
  "latency_ms":1100,
  "model":"gpt-compatible-model",
  "provider":"openai_compatible",
  "base_url":"https://models.example.test/v1",
  "source":"admin",
  "detail":"Unable to connect to upstream model service.",
  "upstream":{"status":502,"error_category":"upstream_connection_error"}
}
```

### 7.11 Debug endpoints

Debug endpoints 只面向本地或受控测试环境。`debug/runtime` 是只读观测；`debug/material-bundles` 与 `debug/fill-current-gap` 会生成或写入 synthetic/debug materials，不能当成普通公开 demo 用户能力。公开演示如果需要稳定材料，应优先使用已经验证并发布的 material package archive，而不是现场生成 debug material。

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/sessions/{session_id}/debug/fill-current-gap` | Session access + debug material switch | 用调试场景填当前材料缺口，本地/受控测试专用 |
| `POST` | `/v1/sessions/{session_id}/debug/material-bundles` | Session access + debug material switch | 非流式生成 synthetic/debug material bundle |
| `POST` | `/v1/sessions/{session_id}/debug/material-bundles/stream` | Session access + debug material switch | SSE 生成 synthetic/debug material bundle |
| `GET` | `/v1/sessions/{session_id}/debug/runtime` | Session access + runtime debug | 获取 runtime debug snapshot |
| `GET` | `/v1/sessions/{session_id}/runtime-traces/{run_id}` | Session access + runtime debug | 获取单个 runtime trace |

Debug 开关来自后台 settings；初始默认值可由 `ALLOW_RUNTIME_DEBUG` / `ALLOW_DEBUG_FILL` 注入。生产公开环境建议保持 debug material 关闭；如为了受控 demo 临时开启，应同时限制访问入口、记录发布窗口，并在演示后关闭。

#### `GET /v1/sessions/{session_id}/debug/runtime`

Response excerpt:

```json
{
  "schema_version":"ds160.runtime_debug.v1",
  "backend":{"agent_runtime":"native_interviewer","debug_enabled":true},
  "session":{"session_id":"sess_abc123","phase_state":"interview"},
  "current_runtime":{},
  "latest_turn":{},
  "runtime_trace":[],
  "runtime_view_state":{},
  "case_board":{},
  "material_understanding":[],
  "timeline":[],
  "errors":[]
}
```

#### `POST /v1/sessions/{session_id}/debug/material-bundles`

这个接口用于生成 synthetic/debug material bundle。若要沉淀成可复用 demo 模板，需要走离线验证与 publish 流程；不要把一次在线 debug generation 的输出直接称为已验证 material package。

Request:

```json
{
  "scenario":"school_mismatch_bundle",
  "include_synthetic_user_turns":true,
  "seed_text":"Applicant will study MS Computer Science and parents will sponsor tuition.",
  "generation_mode":"ai_if_available"
}
```

Current scenario examples:

- `normal_f1_bundle`
- `normal_j1_bundle`
- `normal_b1_b2_bundle`
- `normal_h1b_bundle`
- `school_mismatch_bundle`
- `identity_mismatch_bundle`
- `funding_shortfall_bundle`
- `sponsor_chain_gap_bundle`
- `claim_vs_document_bundle`

Response excerpt:

```json
{
  "session_id":"sess_abc123",
  "bundle_id":"dbg-bundle-abc123",
  "scenario":"school_mismatch_bundle",
  "scenario_label":"学校材料冲突包",
  "documents":[{"document_id":"doc_1","filename":"synthetic_i20.txt","document_type":"i20"}],
  "expected_findings":[],
  "assistant_message":"Please clarify the school mismatch.",
  "main_flow_refresh_error":null
}
```

`expected_findings` 只给 API 测试和前端调试展示，不应写入材料正文、evidence excerpt、profile 或 document review prompt/context。

#### `POST /v1/sessions/{session_id}/debug/material-bundles/stream`

事件类型：

- `accepted`
- `debug_bundle_started`
- `document_created`
- `evidence_written`
- `profile_recomputed`
- `gate_refreshed`
- `document_review_started`
- `governor_decided`
- `progress`
- `final`
- `error`

`final` 包含完整 `DebugMaterialBundleResponse`；`error` 可能是 `{status, detail}` 或模型运行错误 payload。

### 7.12 OpenAI-compatible adapters

这些 endpoint 是 DS-160 产品层 adapter，不是模型供应商透传代理。它们会创建/复用本地 session、导入 transcript、运行 `MessageService`，并把 Case Board / runtime metadata 放到响应里。

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/chat/completions` | Cookie or machine bearer | OpenAI Chat Completions 风格入口 |
| `POST` | `/v1/responses` | Cookie or machine bearer | OpenAI Responses 风格入口 |

#### `POST /v1/chat/completions`

Request:

```json
{
  "model":"ds160-runtime",
  "messages":[
    {"role":"user","content":"I want to apply for an F-1 visa."},
    {"role":"assistant","content":"Which school will you attend?"},
    {"role":"user","content":"I will attend Example University."}
  ],
  "metadata":{
    "declared_family":"f1",
    "session_id":"sess_existing",
    "client_message_id":"client-chat-003"
  }
}
```

Rules:

- `messages` 至少包含一条 `user`；最后一条 `user` 是本轮输入；
- 推荐传 `metadata.session_id` 复用会话；新建会话时需要 `metadata.declared_family`；
- `metadata.client_message_id` / `metadata.idempotency_key` / HTTP `Idempotency-Key` 都可参与幂等；
- `system` message 作为请求上下文，不进入 public transcript。

Response excerpt:

```json
{
  "id":"chatcmpl-sess_abc123",
  "object":"chat.completion",
  "choices":[{"index":0,"message":{"role":"assistant","content":"..."},"finish_reason":"stop"}],
  "metadata":{
    "session_id":"sess_abc123",
    "phase_state":"interview",
    "context_mode":"existing_session",
    "governor_decision":"continue_interview",
    "case_board":{"schema_version":"case_board.v1"},
    "evidence_graph":{"schema_version":"evidence_graph.v1"},
    "runtime_view_state":{}
  }
}
```

#### `POST /v1/responses`

Request:

```json
{
  "model":"ds160-runtime",
  "input":"I want to apply for an F-1 visa.",
  "metadata":{"declared_family":"f1","client_message_id":"client-response-001"}
}
```

Follow-up with previous response:

```json
{
  "model":"ds160-runtime",
  "previous_response_id":"resp_sess_abc123_2",
  "input":"I will attend Example University.",
  "metadata":{"client_message_id":"client-response-002"}
}
```

Rules:

- `input` 支持 string 或简化消息数组；
- `instructions` 会作为请求级 system message；
- `previous_response_id` 必须映射到本地 assistant turn；
- 同时传 `metadata.session_id` 和 `previous_response_id` 时必须指向同一 session；
- 当前不承诺完整官方 Responses API 的工具调用、远端会话保存或全量字段。

Response excerpt:

```json
{
  "id":"resp_sess_abc123_2",
  "object":"response",
  "status":"completed",
  "output_text":"...",
  "metadata":{"session_id":"sess_abc123","phase_state":"interview","context_mode":"previous_response"}
}
```

## 8. Route map verified in this task

当前 FastAPI app 注册了以下根路径和 API 路径：

```text
GET    /healthz
GET    /livez
GET    /version
GET    /v1/app-config
POST   /v1/auth/login
GET    /v1/auth/me
POST   /v1/auth/logout
POST   /v1/sessions
GET    /v1/sessions
GET    /v1/sessions/{session_id}/required-package
GET    /v1/sessions/{session_id}/messages
POST   /v1/sessions/{session_id}/messages
POST   /v1/sessions/{session_id}/messages/stream
POST   /v1/sessions/{session_id}/files
GET    /v1/sessions/{session_id}/files/{document_id}/content
DELETE /v1/sessions/{session_id}/files/{document_id}
POST   /v1/sessions/{session_id}/upload-ticket
GET    /v1/wx/upload-tickets/{ticket}
POST   /v1/wx/upload-tickets/{ticket}/files
GET    /v1/sessions/{session_id}/reports/user
GET    /v1/sessions/{session_id}/reports/internal
POST   /v1/sessions/{session_id}/reports/review
GET    /v1/sessions/{session_id}/reports/export
GET    /v1/rag/status
POST   /v1/rag/files
POST   /v1/model-config/models
POST   /v1/admin/login
GET    /v1/admin/me
POST   /v1/admin/logout
POST   /v1/admin/access-keys
GET    /v1/admin/access-keys
PATCH  /v1/admin/access-keys/{key_id}
GET    /v1/admin/access-keys/{key_id}/secret
GET    /v1/admin/access-keys/{key_id}/sessions
GET    /v1/admin/sessions/{session_id}/messages
GET    /v1/admin/settings
PATCH  /v1/admin/settings
POST   /v1/admin/model-config/models
POST   /v1/admin/model-config/test
GET    /v1/admin/rag/status
GET    /v1/sessions/{session_id}/debug/runtime
POST   /v1/sessions/{session_id}/debug/fill-current-gap
POST   /v1/sessions/{session_id}/debug/material-bundles
POST   /v1/sessions/{session_id}/debug/material-bundles/stream
GET    /v1/sessions/{session_id}/runtime-traces/{run_id}
GET    /v1/material-packages
POST   /v1/sessions/{session_id}/material-packages/{package_id}/import
POST   /v1/chat/completions
POST   /v1/responses
```

主要验证来源：

- `app/main.py`
- `app/api/routers/*.py`
- `web/lib/api/client.ts`
- `web/lib/api/types.ts`
- `tests/integration/test_simple_auth.py`
- `tests/integration/test_sessions_api.py`
- `tests/integration/test_messages_api.py`
- `tests/integration/test_files_api.py`
- `tests/integration/test_wx_upload_ticket_api.py`
- `tests/integration/test_reports_api.py`
- `tests/integration/test_rag_api.py`
- `tests/integration/test_model_config_api.py`
- `tests/integration/test_admin_demo_api.py`
- `tests/integration/test_openai_compat.py`
- `tests/integration/test_openai_responses.py`
- `tests/integration/test_debug_material_bundles_api.py`
- `tests/integration/test_interview_runtime_trace.py`
