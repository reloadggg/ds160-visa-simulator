# RAG Knowledge Spec

日期：2026-05-24
状态：v1 草案，可执行合同先行

## 目标

RAG 是知识平面，不是裁判。

它只回答：

- 来源是什么。
- 当前材料证明了什么。
- 当前材料没有证明什么。
- 引用是否过期或不可用。

它不直接决定签证结果。

## 知识分层

```text
official_policy   官方政策、DS-160、SEVIS、使领馆说明
case_evidence     用户上传材料、OCR 字段、历史口头声明
product_rubric    模拟面签评分、训练建议、复盘模板
```

边界：

- `official_policy` 可支撑政策断言。
- `case_evidence` 可支撑用户材料和口头声明断言。
- `product_rubric` 只能支撑产品建议，不得伪装成官方要求。

## Citation 粒度

代码合同位于：

- `app/domain/agent_runtime.py::CitationRef`

每条 citation 必须包含：

- `citation_id`
- `source_type`
- `source_authority`
- `source_id`
- `document_id`
- `chunk_id`
- `span_start`
- `span_end`
- `content_hash`
- `quote_or_summary`
- `retrieved_at`
- `published_or_effective_date`
- `staleness_policy`
- `claim_ids`

URL 级 citation 不合格。

## Public Claim 规则

代码合同：

- `PublicClaim`

规则：

- `claim_type=official_policy` 必须有 citation。
- `claim_type=case_evidence` 必须有 citation。
- `claim_type=product_guidance` 可以无 citation，但必须标明是产品建议。
- `claim_type=conversation_state` 可以无 citation，但只能描述本轮运行状态，不得变成政策或材料断言。

## 数据治理

代码合同位于：

- `app/domain/knowledge_plane.py`

用户材料进入向量库前必须先解决：

- tenant/user/session 隔离。
- case evidence 默认 `session_scoped`。
- 删除会话后，chunk 和 embedding 不可再检索。
- 支持 tombstone / compaction。
- 支持 embedding version replacement。
- 检索和引用写审计日志。

Postgres / pgvector 目标 baseline 表：

- `knowledge_sources`
- `knowledge_documents`
- `knowledge_chunks`
- `knowledge_embeddings`
- `citation_claims`
- `ingest_runs`
- `graph_checkpoints`
- `graph_run_events`
- `knowledge_audit_events`

当前仓库没有 Alembic 迁移底座，因此本阶段只冻结 schema / scope / lifecycle 合同，不迁生产数据。

## 删除语义

用户材料默认是 `case_evidence + session_scoped`。删除会话或材料时必须：

- 使用 `tenant_id + user_id + session_id` 作为最小删除过滤条件。
- 先 tombstone chunk / embedding，再 compaction。
- tombstoned / deleted chunk 不允许生成新的 public citation。
- embedding tombstone / delete 必须记录 `deletion_request_id`。
- 审计事件必须记录 delete / tombstone / compact。

## Ingestion Pipeline

MVP 必须定义：

- source manifest
- fetcher
- parser
- chunker
- embedding model/version
- index version
- refresh schedule
- blue/green index switch
- citation invalidation

## Retrieval Planning

retrieval planner 必须先判断 claim 类型：

- policy claim 查 `official_policy`
- material / oral-claim claim 查 `case_evidence`
- training / review suggestion 查 `product_rubric`

检索失败不能让 agent 编造结论。

代码合同：

- `official_policy` claim 只能检索 `official_policy`。
- `case_evidence` claim 只能检索当前 session scope 的 `case_evidence`。
- `product_guidance` claim 只能检索 `product_rubric`。
- `third_party_reference` 不能默认进入 public citation。

## Staleness

官方政策来源可能过期。

`staleness_policy`：

- `stable`
- `expires`
- `refresh_required`
- `invalidated`

当 citation stale / invalidated：

- 不得支撑新的政策断言。
- 可以用于历史 replay，但必须标记状态。

## 验收

- citation 可回放到 chunk/span/hash。
- policy index refresh 后，旧 citation 可标记 stale 或 invalidated。
- 删除 session 后，case evidence 无法检索。
- product guidance 不会作为 official policy 输出。
- 无 citation 时输出“无法确认 + 下一步需要什么来源/材料”。
