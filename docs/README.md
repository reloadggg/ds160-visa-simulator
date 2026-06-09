# DS-160 文档导航

这组文档分成两类：一类帮你快速理解项目，另一类帮你在需要时查合同、跑验证、做部署。读的时候不必从头到尾顺序刷完，按问题进入即可。

## 先读什么

- **第一次了解项目**：先看 [`../README.md`](../README.md)。那里讲的是项目为什么存在、用户实际会怎么用，以及当前系统的大块结构。
- **准备接 API 或排查接口**：看 [`API.md`](./API.md)。它按登录、会话、消息、材料、报告、后台和 OpenAI-compatible adapter 的真实工作流组织。
- **确认前端入口**：公开产品首页是 `/`，普通用户工作台是 `/login`，微信 web-view 轻量工作台是 `/wx`，项目状态页是 `/health`，后台入口是 `/admin`；这些入口的使用说明在 [`../README.md`](../README.md) 和 [`API.md`](./API.md) 里维护。
- **接微信小程序壳或原生上传页**：看 [`wechat-miniprogram-mvp.md`](./wechat-miniprogram-mvp.md)。它记录 `/wx` web-view、`miniprogram/` 源码壳、原生聊天文件上传页和 upload ticket API 合同。
- **确认当前 runtime 边界**：看 [`runtime-contracts.md`](./runtime-contracts.md)、[`architecture/agent-runtime-spec.md`](./architecture/agent-runtime-spec.md) 和 [`architecture/ai-native-case-understanding-spec.md`](./architecture/ai-native-case-understanding-spec.md)。当前公开用户回复只由 native interviewer 写入。
- **跑可复用 F-1 demo**：看 [`f1-demo-live-validation-protocol.md`](./f1-demo-live-validation-protocol.md)。它记录稳定 F-1 material package 从 PDF 渲染、上传、材料理解、面谈到报告采集的验收流程。
- **部署或迁移数据库**：看 [`../deploy/README.md`](../deploy/README.md) 和 [`architecture/postgres-migration-runbook.md`](./architecture/postgres-migration-runbook.md)。

## 当前 source of truth

| 主题 | 当前文档 | 说明 |
| --- | --- | --- |
| 项目定位与快速启动 | [`../README.md`](../README.md) | 面向读者的入口，不承担全部 API 细节。 |
| HTTP API / SSE / auth / frontend routes | [`API.md`](./API.md) | 按真实请求顺序查接口，并说明 `/`、`/login`、`/wx`、`/health`、`/admin` 入口边界。 |
| WeChat Mini Program lightweight entry | [`wechat-miniprogram-mvp.md`](./wechat-miniprogram-mvp.md) | 小程序 web-view 轻量入口、原生上传页、upload ticket、手动 smoke test 和上线检查清单。 |
| Runtime 合同 | [`runtime-contracts.md`](./runtime-contracts.md) | Gate、native interviewer、材料、报告之间的主线边界。 |
| Native runtime / graph 角色 | [`architecture/agent-runtime-spec.md`](./architecture/agent-runtime-spec.md) | `graph` 保留为 replay/shadow/eval/兼容语境，不是当前公开 writer。 |
| Case Memory / Evidence Graph | [`architecture/ai-native-case-understanding-spec.md`](./architecture/ai-native-case-understanding-spec.md) | 材料理解、长期事实、证据图谱和 runtime 上下文边界。 |
| F-1 validated demo | [`f1-demo-live-validation-protocol.md`](./f1-demo-live-validation-protocol.md) | 可复用演示材料包的验收协议和证据路径。 |
| RAG 知识库 | [`architecture/rag-knowledge-spec.md`](./architecture/rag-knowledge-spec.md)、[`rag/`](./rag/) | 政策资料、source pack 与检索配置。 |
| Docker / server | [`../deploy/README.md`](../deploy/README.md) | Compose、Nginx、健康检查、回滚和环境变量。 |

## 历史报告怎么看

[`implementation/`](./implementation/) 里的文件保留了 runtime cleanup 过程中的阶段性判断、测试证据和任务审计。它们对追溯“为什么这样改”很有价值，但不应直接当成今天的部署手册。遇到冲突时，优先级按下面来：

1. 当前代码和测试；
2. `runtime-contracts.md` 与 `architecture/*spec.md`；
3. `API.md`、`deploy/README.md`、F-1 validation protocol；
4. `implementation/` 下的历史报告。

## 术语速记

- **Native interviewer**：当前唯一公开面谈 writer，负责用户可见回复和材料刷新主流程。
- **Case Memory / Evidence Graph**：事实、材料证据、冲突和证明点的长期状态源。
- **Case Board**：前端给用户和调试者看的事实/证据/冲突视图。
- **Graph / LangGraph**：当前用于 replay、shadow/eval 或兼容标签语境；未来如果要公开接管，需要重新完成验证。
- **Legacy runtime**：历史运行时，不再作为普通公开路径描述。
- **Material package**：通过验证后可导入的材料包/模板资产；debug material generation 只用于本地或受控测试。
