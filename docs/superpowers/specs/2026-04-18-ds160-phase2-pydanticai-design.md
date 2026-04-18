# DS-160 Phase 2 PydanticAI Runtime 设计文档

日期：2026-04-18  
状态：待用户审阅  
目标读者：后端、Agent Runtime、测试

## 1. 背景与目标

Phase 1 已经完成并打了 checkpoint：

- git tag：`phase1-evidence-foundation-2026-04-18`
- 能力现状：
  - 文档上传只负责落库与排队
  - `ParseWorker` 能解析文档、写入 `artifact/chunk/evidence`
  - `ProfileRecomputeService` 能把文档证据重算进 `ApplicantProfile`
  - 消息流与报告流已经切换到“上传后必须等 worker”

当前最大缺口不再是证据底座，而是运行时仍然停留在“手写 prompt 编排”：

- [message_service.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/message_service.py) 仍以串行 service 调用为主
- [scoring_service.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/scoring_service.py) 仍通过旧的 `LLMClient` 直接取 JSON
- extractor/scoring/question generation 还没有正式 tool use

本设计的目标是：

- 用 `PydanticAI` 替换当前手写的 extractor/scoring/question 主运行时
- 让模型通过正式工具读取文档证据，而不是继续“直接喂 prompt”
- 保留现有 Governor 作为硬护栏，不让 agent 直接决定 refusal
- 保留现有 OpenAI-compatible 环境变量和供应商接入习惯，但不继续复用旧 `LLMClient` 作为主抽象

## 2. 范围与非目标

### 2.1 本阶段范围

本次 Phase 2 只覆盖“最小后端版”，包含以下能力：

- 引入 `PydanticAI` 作为正式 agent runtime
- 定义强类型输出：
  - `ExtractorOutput`
  - `ConsistencyFinding`
  - `ScoreProposal`
  - `InterviewNextAction`
- 建立最小 evidence tool 层：
  - `search_evidence(session_id, query, filters)`
  - `get_evidence_excerpt(evidence_ref)`
  - `extract_document_fields(document_id, schema_name)`
- 将 extractor、scoring、question generation 改造成 tool-based 流程
- 保持 `GovernorService` 为最终硬裁决层

### 2.2 明确不在本阶段内

以下内容不进入这份 Phase 2 设计：

- `Chainlit` 或其他 UI 接入
- `pydantic-graph` / `Temporal` / durable execution
- gate state / gate_review 状态表
- `runtime_trace / score_history / governor_history` 的持久化写入
- 报告 trace 完整增强
- 多 agent 图编排
- 并行保留旧 runtime 的长期双实现

## 3. 方案对比与结论

### 3.1 方案 A：原生 PydanticAI 直连 provider/model

做法：

- 让 `PydanticAI` 成为 Phase 2 的主 runtime
- 直接通过 `PydanticAI` 的 model/provider 配置接 OpenAI-compatible 端点
- 旧 `LLMClient` 不再作为主调用抽象

优点：

- 架构最干净
- tool calling、structured output、agent 行为不再被两层抽象折叠
- 后续继续接 `pydantic-graph` 或 durable execution 更自然

缺点：

- 一次性替换面较大
- live 测试与 provider 兼容层需要重新接通

### 3.2 方案 B：继续保留旧 LLMClient，在其上套 PydanticAI

做法：

- 把 `PydanticAI` 当成外层语义框架
- provider 调用仍由旧 `LLMClient` 负责

优点：

- 短期改动小
- 现有 provider 兼容逻辑几乎不动

缺点：

- 职责重叠
- 容易出现 “PydanticAI 是壳，真实调用控制仍在旧 client” 的双层抽象
- 后续 tools、output schema、agent 行为调试会变得别扭

### 3.3 方案 C：并行保留旧实现与新实现，用开关切换

做法：

- 保留旧 `ExtractorService / ScoringService / MessageService`
- 新增一条 PydanticAI runtime 路径
- 通过配置切换

优点：

- 风险分散

缺点：

- 用户已明确选择替换式
- 长期维护成本高
- 测试矩阵翻倍

### 3.4 选型结论

采用 **方案 A**：

- 主运行时切到原生 `PydanticAI`
- 保留现有 OpenAI-compatible 配置形状
- 不保留旧 `LLMClient` 作为 Phase 2 主抽象

这意味着：

- 配置兼容保留
- 调用抽象替换

## 4. 总体架构

### 4.1 分层

Phase 2 的最小运行时分为四层：

1. **Evidence Tool Layer**
   - 负责从 Phase 1 的 `chunk/evidence/artifact` 结构中取证
   - 只暴露稳定工具接口，不直接暴露 SQLAlchemy 细节给 agent

2. **PydanticAI Agent Layer**
   - `ExtractorAgent`
   - `ScoringAgent`
   - `QuestionAgent`
   - 都以强类型输出为边界

3. **Service Adapter Layer**
   - 保留现有 service 文件作为业务适配器
   - 例如：`ExtractorService` 改成“调用 `ExtractorAgent` 并把结果 merge 回 `ApplicantProfile`”
   - `ScoringService` 改成“调用 `ScoringAgent` 并把 proposal 映射成 `ScoreState`”

4. **Guardrail / API Layer**
   - `GovernorService` 继续最终裁决
   - `MessageService` 负责单轮 orchestration
   - Router 不直接感知 agent 细节

### 4.2 保留与替换边界

保留：

- `ApplicantProfile`
- `ScoreState`
- `GovernorService`
- 现有 FastAPI API 契约
- Phase 1 的 evidence/worker/profile recompute

替换：

- extractor 的主模型调用路径
- scoring 的主模型调用路径
- next-question 生成路径

暂不替换：

- `ConsistencyService` 的核心确定性逻辑

原因：

- 当前 consistency 规则还比较小，且承担部分硬冲突发现
- Phase 2 最小版不需要把所有东西都 agent 化
- 保持一部分确定性逻辑有助于降低替换风险

## 5. 组件设计

### 5.1 Evidence Tool Layer

建议新增：

- `app/services/retrieval_service.py`
- `app/services/evidence_service.py`
- `app/agents/tools.py`

职责划分：

- `RetrievalService`
  - 基于 `session_id / query / filters` 返回相关 evidence hits
  - v1 先做 SQLite 友好检索，不引入向量库
- `EvidenceService`
  - 将 `EvidenceItem / DocumentChunk / DocumentArtifact` 组织成 agent 可消费的 excerpt
  - 负责 `extract_document_fields(document_id, schema_name)` 这类结构化提取包装
- `agents/tools.py`
  - 暴露给 `PydanticAI` 的 tool 函数
  - 工具内部调 service，不直接写 SQL

### 5.2 Agent Layer

建议新增：

- `app/agents/extractor_agent.py`
- `app/agents/scoring_agent.py`
- `app/agents/question_agent.py`

职责：

- `ExtractorAgent`
  - 输入：当前 message、当前 profile 摘要、必要 evidence 工具
  - 输出：`ExtractorOutput`
  - 任务：把 message 中的 claim 抽成结构化字段更新，并决定哪些 claim 需要文档支撑

- `ScoringAgent`
  - 输入：profile snapshot、typed findings、evidence tools
  - 输出：`ScoreProposal`
  - 任务：基于证据给出可解释的分数、风险和 missing evidence

- `QuestionAgent`
  - 输入：profile、score proposal、governor decision
  - 输出：`InterviewNextAction`
  - 任务：生成下一句 assistant message，并保持 requested documents 与当前状态一致

### 5.3 Service Adapter Layer

建议修改：

- [extractor_service.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/extractor_service.py)
- [scoring_service.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/scoring_service.py)
- [message_service.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/message_service.py)

改造原则：

- 保留现有 service 名称与 API 边界
- 把它们改成调用 agent/runtime 的薄适配器

这样可以：

- 降低 router 和测试改动面
- 把“替换式”控制在 service 内部完成
- 避免 Phase 2 一开始就重写全部 API 调用链

## 6. 类型设计

建议把强类型输出定义放到 [contracts.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/domain/contracts.py) 或一个新的 agent schema 文件中，但 Phase 2 只保留一处真源定义，不允许重复定义。

### 6.1 ExtractorOutput

必须表达：

- 本轮从消息中提取出的 field updates
- 哪些字段只是 `claimed`
- 哪些字段仍然 `unknown`
- 需要文档工具验证的 claim 列表

约束：

- `unknown != false`
- 不允许把“没有证据”直接写成否定事实

### 6.2 ConsistencyFinding

用于替换当前散乱 dict：

- `finding_type`
- `severity`
- `status`
- `summary`
- `evidence_refs`

### 6.3 ScoreProposal

至少表达：

- `category_fit`
- `document_readiness`
- `narrative_consistency`
- `confidence`
- `risk_flags`
- `missing_evidence`
- `evidence_refs_by_issue`

约束：

- 负面结论必须带 `evidence_refs`
- agent 不直接输出 `simulated_refusal`

### 6.4 InterviewNextAction

至少表达：

- `assistant_message`
- `requested_documents`
- `decision_hint`

它不是最终 Governor 决策，只是下一轮交互建议。

## 7. 单轮消息数据流

### 7.1 请求进入

[message_service.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/message_service.py) 继续作为主入口。

### 7.2 运行顺序

建议顺序：

1. 读取 `SessionRecord` 与 `ApplicantProfile`
2. 调 `ExtractorService`（内部已改为 `ExtractorAgent`）
3. 调 `ConsistencyService` 生成 typed findings
4. 调 `ScoringService`（内部已改为 `ScoringAgent`）
5. 调 `GovernorService`
6. 调 `QuestionAgent` 生成 `InterviewNextAction`
7. 保存 profile 与 governor decision
8. 返回现有 API 契约

### 7.3 为什么不让 agent 直接决定最终输出

因为当前系统已经明确要求：

- refusal 不可由低分直接触发
- refusal 必须经过 Governor 硬裁决
- evidence refs 必须可追溯

因此：

- agent 提 proposal
- governor 做最终判定

## 8. Provider 与配置策略

### 8.1 配置兼容

Phase 2 继续兼容现有环境变量：

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`

若当前仓库还有其他 provider 配置项，也继续沿用其命名风格。

### 8.2 抽象策略

不继续复用旧 `LLMClient` 作为主抽象。

取而代之的是一个更薄的 provider factory，仅承担：

- 从环境变量构造 `PydanticAI` 所需 model/provider
- 对 OpenAI-compatible 端点做配置兼容

这个 factory 不能再次演化成“第二个 LLMClient”。

## 9. 错误处理与保守退化

Phase 2 虽然是替换式，但不能接受 agent 一失败就把 API 打成 500。

建议保守策略：

- `ExtractorAgent` 失败：
  - 不写假事实
  - 当前轮仅保留原 profile，不做新 claim 写入

- `ScoringAgent` 失败：
  - 返回保守低置信 proposal
  - 默认转向 `need_more_evidence`

- `QuestionAgent` 失败：
  - 返回确定性兜底文案

原则：

- agent 失败时系统应更保守，而不是更乐观

## 10. 测试策略

### 10.1 单元测试

至少新增：

- `tests/unit/test_retrieval_service.py`
- `tests/unit/test_evidence_service.py`
- `tests/unit/test_agent_tools.py`

验证：

- 检索结果是否包含正确 evidence refs
- excerpt 组织是否稳定
- document field extraction 的 schema 映射是否可控

### 10.2 集成测试

至少新增：

- `tests/integration/test_tool_based_scoring.py`
- `tests/integration/test_gate_review_runtime.py`

验证：

- funding proof 已存在时，tool-based scoring 能消除 `funding_proof` 缺口
- funding proof 缺失时，system 仍是 `need_more_evidence`
- question generation 与 governor 决策不冲突

### 10.3 live 测试

保留现有 `live_llm` 入口，但新增最小覆盖：

- tool 调用路径是否可用
- structured output 是否稳定可解析

## 11. 文件建议

建议新增：

- `app/services/retrieval_service.py`
- `app/services/evidence_service.py`
- `app/agents/extractor_agent.py`
- `app/agents/scoring_agent.py`
- `app/agents/question_agent.py`
- `app/agents/tools.py`

建议修改：

- [extractor_service.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/extractor_service.py)
- [scoring_service.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/scoring_service.py)
- [message_service.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/message_service.py)
- [report_service.py](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/report_service.py)
- [pyproject.toml](/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/pyproject.toml)

`report_service.py` 在本阶段只做最小适配：

- 若 `ScoreProposal` 增加更明确的 `missing_evidence` 与 refs 映射，报告层读取它
- 不在本阶段引入完整 runtime trace/history 持久化

## 12. 风险与缓解

### 12.1 风险：替换式迁移导致现有消息流漂移

缓解：

- 保留现有 service 外部接口
- 只替换内部实现
- 用集成测试锁住 `need_more_evidence / continue_interview / simulated_refusal`

### 12.2 风险：工具层变成第二套业务逻辑

缓解：

- tool 只负责取证，不负责裁决
- 业务决策仍在 service/governor

### 12.3 风险：agent 过度乐观，把 unknown 写成 false

缓解：

- schema 明确禁止
- tests 专门覆盖
- fallback 策略默认保守

## 13. 验收标准

Phase 2 完成后，应满足：

- extractor/scoring/question generation 主路径切换到 `PydanticAI`
- 模型必须通过 tool 读取 evidence，而不是只吃 message text
- `GovernorService` 仍是 refusal/continue 的最终裁决者
- 现有 OpenAI-compatible 配置仍可用
- `not live_llm` 回归持续通过
- live 测试新增最小 tool/structured output 覆盖

## 14. 后续衔接

本设计完成后，下一步应写一份单独的实施计划，文件建议为：

- `docs/superpowers/plans/2026-04-18-ds160-phase2-pydanticai-runtime.md`

这份计划应继续保持最小后端范围，不把 `Chainlit` 或 durable execution 混进来。
