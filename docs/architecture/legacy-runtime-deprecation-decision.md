# Legacy Runtime Deprecation Decision

日期：2026-05-30
状态：已接受，待生产 cutover 后执行删除窗口

## 决策

`InterviewerRuntimeService` 不再是产品主链路，也不再接收新能力。当前保留它只为一个目的：在生产完成 split Compose + Postgres cutover 后的一个发布周期内，提供显式回滚路径。

保留入口只允许两类：

- `AGENT_RUNTIME=legacy`：人工显式回滚到旧 runtime。
- `AGENT_RUNTIME_FAIL_OPEN_TO_LEGACY=true`：native / graph-compatible public runtime 失败后的显式 fail-open fallback。

默认生产入口必须保持：

```text
AGENT_RUNTIME=native_interviewer
AGENT_RUNTIME_FAIL_OPEN_TO_LEGACY=false
```

## 禁止事项

- 不允许把 legacy runtime 作为 `graph`、`graph_canary` 或 `graph_shadow` 的公开 writer。
- 不允许在 legacy runtime 中新增产品能力、提示词策略、材料理解策略或 Case Board 新语义。
- 不允许让 material refresh 默认调用 legacy；只有显式 fail-open 且 native refresh 失败时才可 fallback。
- 不允许用 `agent_runtime` display label 推断真实 writer；必须读取 `runtime_execution`。

## 删除条件

满足以下条件后，下一发布窗口删除 legacy live path：

- 生产已完成 SQLite -> Postgres 迁移，并运行 split Compose 拓扑。
- `/livez`、`/healthz`、`/version`、公网 `https://ds160.efastt.store/healthz` 均通过。
- graph replay corpus 通过。
- focused non-live runtime tests 通过。
- focused live LLM smoke 通过。
- Docker/Postgres smoke 通过。
- `release-preflight` 输出 `ok=true`。
- 生产日志能按 session / run / turn 定位 native runtime、graph shadow 和 fallback 路径。

## 删除范围

删除窗口内优先移除：

- `AGENT_RUNTIME=legacy` 配置值。
- `AGENT_RUNTIME_FAIL_OPEN_TO_LEGACY` 到 legacy 的 fallback 分支。
- `MessageService` 中 legacy public runtime 分支。
- `InterviewerRuntimeService` live path 及只为 legacy 存在的测试夹具。
- 文档中 legacy rollback 指令，替换为 native runtime / previous image rollback。

## 风险与回滚

当前不立即删除，是因为远程生产仍运行旧 combined service + SQLite。生产 cutover 前直接删除 legacy 会扩大回滚风险，并且无法证明新的 Postgres/split 拓扑已经覆盖真实线上数据。

这个决策不是继续维护 legacy，而是把 legacy 从“未决技术债”降级为“有时限的发布回滚开关”。任何新需求默认只能进入 `NativeInterviewerRuntimeService`、Case Memory / Evidence Graph、或未来真实 LangGraph public promotion 分支。
