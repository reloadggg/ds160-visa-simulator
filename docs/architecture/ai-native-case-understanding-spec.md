# AI-native Case Understanding Spec

日期：2026-05-25
状态：v1 implementation contract

## Goal

把 DS-160 从 Gate-first 材料清单产品改成 AI-native case understanding runtime。

```text
upload/text turn
  -> Material Understanding
  -> Case Memory / Evidence Graph
  -> Native interviewer public runtime
  -> LangGraph-backed shadow/eval/replay contract
  -> Interview next move
  -> Governor / guardrail
```

## Framework Boundary

- `NativeInterviewerRuntimeService` is the only current public writer for user-visible interview replies and public material refresh.
- LangGraph owns the graph runtime contract for replay, shadow tracing, eval, and a future public-promotion path; it is not the current public writer.
- Case Memory owns product facts and evidence.
- Pydantic AI owns typed LLM calls.
- OpenAI Agents SDK may be tested behind `LLMNodeRunner`, but cannot own global runtime.
- CrewAI / AutoGen do not enter live interview runtime.

## Material Understanding

Applicant images and PDFs are understood by multimodal LLMs directly. OCR is not part of the applicant material path.

The stable result shape is represented in `app/domain/case_memory.py`:

- `MaterialUnderstandingJob`
- `MaterialUnderstandingResult`
- `EvidenceCard`
- `CaseClaim`
- `ProofPoint`
- `CaseConflict`
- `InterviewNextMove`

## Case Memory

Case Memory is persisted product state. It exists because LLM context windows are not enough for:

- long sessions
- stable frontend display
- replay eval
- audit
- deletion semantics
- conflict tracking

The first implementation slice stores source-level `material_understanding_result`,
`material_understanding_job`, and `case_board_delta` in each
`DocumentRecord.artifact_json`, and explicit user-turn claims in turn metadata.
Each Case Memory mutation also writes a session-level
`case_memory_snapshots` projection with schema `case_memory_snapshot.v1`. Runtime
state builders receive that projection as their primary fact input, while source
artifacts remain the compatibility/write-source fallback. The snapshot stores
`latest_material`, claims, evidence cards, proof points, open conflicts,
resolved conflict records, and the next interview move as a stable first-class
read model.

The runtime read path now has an explicit Evidence Graph query layer:

- `CaseMemoryService.get_snapshot(session_id)` reads the persisted first-class
  projection without scanning document artifacts.
- `CaseMemoryService.get_or_build_snapshot(session_id)` falls back to rebuilding
  only when the projection is absent.
- `CaseMemoryService.query_evidence_graph(session_id, field_paths=[...])`
  returns claims, evidence cards, proof points, conflicts, and deterministic
  edges for runtime retrieval.
- Native interviewer receives the Case Memory snapshot and Evidence Graph
  explicitly for the public path; graph runtime case-state builders receive the
  same inputs for shadow/eval/replay, without becoming the public writer.

Explicit user statements are also written into Case Memory through turn
metadata. Uploaded material can contradict those stated claims, producing
`CaseConflict` records that the interview agent can clarify without turning the
session back into a material checklist.

When a document is removed, its Case Memory contribution is tombstoned and no
longer participates in Case Board, replay, or legacy Gate projection.

If parsing fails before material understanding can run, the worker must still
write a failed `MaterialUnderstandingJob` into Case Memory. The document artifact
and Case Board delta expose `understanding_status="failed"`, `error_code`, and a
latest-material unknown so the user/debug surfaces can show the failed node
instead of leaving the upload stuck at queued.

Reports and exports must also treat Case Board as the product state. If Case
Board contains any claims, evidence cards, proof points, or conflicts,
`missing_evidence` is derived only from unresolved Case Board proof points.
Legacy `requested_documents`, `remaining_required_documents`, `current_key_proof`,
and focus document fields are fallback inputs only while Case Board is empty.

## Replay Eval

Replay eval is defined in `docs/architecture/replay-eval-spec.md`. It must record
claims, evidence, conflicts, and next move state instead of only checking final
assistant text or legacy missing-material fields.

## Compatibility Rule

Legacy Gate fields can remain for compatibility, but they are projection fields only. New public runtime decisions must be based on Case Memory and native interviewer state; graph state is the corresponding shadow/eval/replay contract.

Current public runtime contract is stronger than a deployment default: `/messages` and public material refresh always select `native_interviewer`. `graph`, `graph_shadow`, and `graph_canary` remain compatibility labels and replay/shadow/eval paths until replay, live smoke, and provider metrics justify a separately reviewed LangGraph public promotion.
