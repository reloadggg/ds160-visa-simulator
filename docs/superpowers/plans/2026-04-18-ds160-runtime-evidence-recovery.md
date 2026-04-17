# DS-160 Runtime & Evidence Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前 DS-160 v1 从“可演示原型”补全为“有正式证据链、可追溯打分、可恢复工作流”的后端，重点补齐 agent runtime、文档证据层、工具调用式打分与受控前端接入。

**Architecture:** 推荐路线是 `FastAPI + PydanticAI + 文档证据检索层 + Chainlit`。PydanticAI 负责结构化输出、tools use、强类型 agent；证据层负责文档解析、chunk、evidence refs 与 retrieval；Governor 继续保留硬护栏裁决。若后续需要更强的人审与暂停恢复，可在 Phase 3 再引入 `pydantic-graph` 或 Temporal durable execution。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, SQLite/Postgres FTS, PydanticAI, pydantic-graph (optional), PyMuPDF, python-docx, Pillow, pytesseract, httpx, Chainlit, pytest

---

## 现状审计

当前仓库已完成：

- 基础 FastAPI API、Session/File/Message/Report 路由
- 基础 `ApplicantProfile / ScoreState / GovernorDecision`
- 真实 OpenAI-compatible LLM 调用
- live LLM 基础测试
- Chainlit 接入设计文档

当前仍未完成的关键能力：

1. **没有正式 agent/runtime 框架**
   - 当前 `MessageService` 是手写顺序编排，不具备正式 tools、pause/resume、持久 workflow。
2. **没有正式文档证据层**
   - `pdf/docx` 解析仍是占位。
   - 没有页面、分段、chunk、doc type、excerpt、evidence item。
3. **没有 retrieval / tools use**
   - extractor/scoring 当前没有通过工具读取文档证据。
4. **没有真正的 parse worker**
   - 上传后虽有 `jobs`，但没有消费者。
5. **没有真实路由与签证模块执行**
   - policy packs 只提供最小 `required_initial_package`。
6. **没有完整 trace/history**
   - `runtime_trace / score_history / governor_history` 基本为空。
7. **没有真正 gate_review 阻塞**
   - 必需材料包、解析完成、正式 interview 之间还没形成强门控。

## 选型结论

### 主推荐

采用：

- `PydanticAI` 作为结构化 agent/tool runtime
- `PydanticAI tools` 作为证据检索与评分调用入口
- `Chainlit` 作为轻 UI
- `pydantic-graph` 或 `Temporal` 延后到 Phase 3

推荐原因：

- PydanticAI 官方支持 **function tools、structured output、toolsets、durable execution**，更适合本仓库强 schema、强约束的事实与评分模型。
- 官方文档明确指出 function tools 适合在指令里放不下上下文时检索额外信息，并且它与 RAG 的关系是“工具是更广义的 Retrieval”。  
  参考：<https://pydantic.dev/docs/ai/tools-toolsets/tools/>
- 官方文档明确支持 `output_type` 强约束结构化输出。  
  参考：<https://pydantic.dev/docs/ai/core-concepts/output/>
- 官方文档提供 `pydantic-graph` 与 durable execution 集成；如果后续需要恢复、人审、长任务，可以逐步升级。  
  参考：<https://pydantic.dev/docs/ai/graph/graph/>  
  参考：<https://pydantic.dev/docs/ai/integrations/durable_execution/overview/>  
  参考：<https://pydantic.dev/docs/ai/integrations/durable_execution/temporal/>

### 备选

`LangGraph` 适合作为更强的状态图和中断恢复 runtime。官方文档强调 durable execution、human-in-the-loop 与 stateful workflow。  
参考：<https://docs.langchain.com/oss/python/langgraph>  
参考：<https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph>  
参考：<https://docs.langchain.com/oss/python/langgraph/durable-execution>

本计划暂不直接选 LangGraph 作为第一步，因为当前首要问题是 **证据结构化与 tool-based judging**，不是复杂多 agent 图编排。

## 目标补全架构

### 核心分层

1. **Document Ingestion Layer**
   - PDF / DOCX / 图片 / OCR
   - 生成 `DocumentArtifact`
   - 生成页级或段级 chunk
2. **Evidence Layer**
   - `EvidenceItem`
   - `EvidenceExcerpt`
   - `EvidenceRef`
   - 文档检索与引用
3. **Agent Runtime Layer**
   - `ExtractorAgent`
   - `ConsistencyAgent`
   - `ScoringAgent`
   - `QuestionAgent`
4. **Governor Layer**
   - 保留硬护栏
   - 禁止低分直接触发 refusal
5. **UI Layer**
   - Chainlit
   - 只做编排，不接管业务事实

### 必备工具

至少补以下 tool 接口：

- `search_evidence(session_id, query, filters) -> list[EvidenceHit]`
- `get_evidence_excerpt(evidence_ref) -> EvidenceExcerpt`
- `extract_document_fields(document_id, schema_name) -> dict`
- `list_required_documents(session_id) -> RequiredDocumentStatus`
- `list_missing_evidence(profile_id) -> list[MissingEvidenceItem]`
- `score_with_evidence(profile_id, evidence_refs, rubric_key) -> ScoreProposal`

## 建议文件结构

**Create**

- `app/domain/evidence.py`
- `app/db/evidence_models.py`
- `app/repositories/evidence_repo.py`
- `app/services/document_pipeline.py`
- `app/services/evidence_service.py`
- `app/services/retrieval_service.py`
- `app/agents/extractor_agent.py`
- `app/agents/scoring_agent.py`
- `app/agents/question_agent.py`
- `app/agents/consistency_agent.py`
- `app/agents/tools.py`
- `app/workflows/interview_runtime.py`
- `app/workflows/gate_runtime.py`
- `app/workers/parse_worker.py`
- `tests/unit/test_evidence_service.py`
- `tests/unit/test_retrieval_service.py`
- `tests/unit/test_agent_tools.py`
- `tests/integration/test_document_pipeline.py`
- `tests/integration/test_gate_review_runtime.py`
- `tests/integration/test_tool_based_scoring.py`

**Modify**

- `app/domain/contracts.py`
- `app/db/models.py`
- `app/repositories/document_repo.py`
- `app/services/file_service.py`
- `app/services/extractor_service.py`
- `app/services/consistency_service.py`
- `app/services/scoring_service.py`
- `app/services/message_service.py`
- `app/services/report_service.py`
- `app/integrations/parsers.py`
- `app/main.py`
- `pyproject.toml`

## Phase 1: 补正式文档证据层

### Task 1: 扩展领域模型为“文档 + 证据”双层

**目标**

- 将当前只有 `raw_text` 的 DocumentRecord 扩展为可回引证据的模型

**必须新增**

- `DocumentArtifact`
- `DocumentChunk`
- `EvidenceItem`
- `EvidenceRef`
- `ExtractionTrace`

**完成标准**

- 每个证据 ref 至少能定位到：
  - `document_id`
  - `page` 或 `chunk_id`
  - `source_type`
  - `excerpt`

### Task 2: 让解析器真正支持 PDF / DOCX / 图片

**目标**

- 去掉当前 `pdf/docx extraction pending` 占位

**需要做**

- PDF：PyMuPDF 提取页文本
- DOCX：python-docx 提取段落文本
- 图片：Pillow + pytesseract OCR
- 统一输出页/段级结构

**验证**

- `tests/integration/test_document_pipeline.py`
- 使用真实 fixture 断言页码、文本、chunk 数量

### Task 3: 建立 chunk + retrieval 基础设施

**目标**

- 让系统能按 query 找到文档片段，而不是整份瞎喂模型

**v1 推荐最小实现**

- 先不强制向量库
- 使用：
  - `session_id`
  - `document_type`
  - `chunk_text`
  - SQLite FTS 或 PostgreSQL FTS
  - metadata filters

**为什么**

- 当前重点是可回引证据，不是花哨向量检索
- 后续可再加 embedding + rerank

## Phase 2: 用正式 tools 替代“直接喂 prompt”

### Task 4: 引入 PydanticAI 并定义强类型 agent 输出

**目标**

- 让 extractor / scoring / question generation 都输出受 Pydantic 约束的结构

**必须定义**

- `ExtractorOutput`
- `ConsistencyFinding`
- `ScoreProposal`
- `InterviewNextAction`

**约束**

- `unknown != false`
- 负面结论必须带 `evidence_refs`
- `simulated_refusal` 不由 agent 直接返回

### Task 5: 把 extractor 改造成 tool-based extraction

**当前缺口**

- 现在 extractor 主要只看 `message_text`

**新设计**

- 聊天消息先抽取 claim
- 若 claim 需要文档支撑，则 agent 调：
  - `search_evidence`
  - `get_evidence_excerpt`
  - `extract_document_fields`

**完成标准**

- 聊天 claim 与上传文档之间能形成显式 provenance

### Task 6: 把 scoring 改造成“基于证据工具”的打分

**当前缺口**

- 现在 scoring 没有真正读证据

**新设计**

- scoring agent 只生成 `ScoreProposal`
- proposal 里必须包含：
  - 各维度分值
  - risk flags
  - `evidence_refs`
  - `missing_evidence`

**Governor 继续负责**

- 禁止低分直接 refusal
- 禁止无证据高风险结论

## Phase 3: 让工作流进入正式 runtime

### Task 7: 建立 gate_review runtime

**目标**

- 真正落实“必需材料包解析完成前，不进入正式 interview”

**需要做**

- `required_initial_package` 状态表
- 每个 required doc 的：
  - 是否已上传
  - 是否已解析
  - 是否已通过最小字段检查

**结果**

- 只有 gate pass 后，才进入 interview

### Task 8: 建立 interview runtime

**目标**

- 让当前 `MessageService` 的顺序编排升级为正式 runtime

**推荐起步**

- 先保持显式 Python runtime
- 但节点职责固定：
  - receive input
  - extract claims
  - resolve evidence
  - consistency
  - scoring
  - governor
  - next question / supplement

**升级点**

- 当需要 pause/resume/human review 时，引入 `pydantic-graph`
- 如果要做跨进程 durable execution，再接 `Temporal`

### Task 9: 让 parse jobs 真正被消费

**目标**

- 结束“只 enqueue 不执行”的状态

**v1 最小方案**

- 先做单进程 worker/command
- 支持：
  - 轮询待处理 job
  - 写入 artifact/chunk/evidence
  - 触发 session recompute

**后续扩展**

- 可迁移到 Temporal activities

## Phase 4: 让签证模块和报告真正可用

### Task 10: 把 policy packs 从“材料清单”扩成“模块策略”

**当前缺口**

- 现有 policy packs 只有最小 required package

**需要补齐**

- family-specific field schemas
- routing rubric
- supplement policies
- terminal pattern policies
- question style / tone

### Task 11: 重建报告追溯链

**目标**

- 用户报告和内部报告都从真实 trace 生成

**内部报告必须包含**

- `runtime_trace`
- `score_history`
- `governor_history`
- `evidence_refs`
- `profile_snapshot`

**用户报告必须包含**

- 当前结论
- 关键缺口
- 已支持证据
- 需要补证项

## Phase 5: 接入 Chainlit，不改业务核心

### Task 12: Chainlit 只做 UI 编排

**目标**

- 基于已有 spec 挂载 Chainlit

**原则**

- Chainlit 不直接访问数据库
- Chainlit 不自己判定 interview 阶段
- Chainlit 只调用领域 API

**必须能力**

- 选签证家族
- 发送消息
- 受控上传
- 查看用户报告
- 查看内部报告（开发模式）

## 测试策略

### 必补测试

1. 文档解析测试
2. retrieval 命中测试
3. tool-based extractor 测试
4. tool-based scoring 测试
5. governor block 测试
6. gate_review 阻塞测试
7. parse worker 重算测试
8. Chainlit 编排 smoke test

### live LLM 测试新增要求

- 不再只测“返回不为空”
- 要测：
  - structured output 可解析
  - tool 调用路径可完成
  - 失败时能正确 fallback

## 实施顺序

推荐顺序：

1. 文档证据模型
2. 真解析器
3. retrieval
4. PydanticAI extractor/scoring agents
5. Governor 接回 proposal
6. gate_review runtime
7. parse worker
8. policy pack 扩展
9. 报告追溯
10. Chainlit

## 当前最关键的三大断点

如果只看“今天最缺什么”，答案是：

1. **正式文档证据层**
   - 当前上传文件还不能算真正被理解
2. **tool-based judging**
   - 当前打分和判断没有通过工具检索证据
3. **正式 runtime**
   - 当前工作流还不足以支撑 gate、pause、补证、重算这些真实状态转移

## 推荐第一批提交切分

建议按以下提交切：

- `feat: add document artifact and evidence models`
- `feat: implement parser pipeline and chunk retrieval`
- `feat: add pydanticai extractor and scoring agents`
- `feat: add gate review runtime and parse worker`
- `feat: expand policy packs and report trace`
- `feat: mount chainlit interview ui`

## 验证命令

```bash
# 现有非 live 回归
uv run pytest -q -m "not live_llm"

# 新增文档与检索回归
uv run pytest tests/integration/test_document_pipeline.py tests/unit/test_retrieval_service.py -q

# 新增 agent/tool 回归
uv run pytest tests/integration/test_tool_based_scoring.py tests/unit/test_agent_tools.py -q

# 全量 live
OPENAI_API_KEY=... OPENAI_BASE_URL=... RUN_LIVE_LLM_TESTS=1 uv run pytest tests/integration/live -q -m live_llm
```

## Self-Review

### Spec coverage

- agent runtime：已覆盖
- evidence retrieval：已覆盖
- parser/OCR：已覆盖
- gate review：已覆盖
- scoring/governor：已覆盖
- Chainlit：已覆盖

### Placeholder scan

- 无 `TODO/TBD`
- 每一阶段有明确目标与完成标准
- 实施顺序与提交切分已明确

### Type consistency

- 统一使用 `EvidenceRef / EvidenceItem / ScoreProposal / GovernorDecision`
- 保持 `ApplicantProfile` 为事实视图，Governor 为最终裁决
