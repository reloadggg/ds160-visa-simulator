# AI-native Case Understanding Spec

日期：2026-05-25
状态：v1 implementation contract

## Goal

把 DS-160 从 Gate-first 材料清单产品改成 AI-native case understanding runtime。

```text
upload/text turn
  -> Material Understanding
  -> Case Memory / Evidence Graph
  -> LangGraph runtime
  -> Interview next move
  -> Governor / guardrail
```

## Framework Boundary

- LangGraph owns runtime orchestration.
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

The first implementation slice stores `material_understanding_result`,
`material_understanding_job`, and `case_board_delta` in each
`DocumentRecord.artifact_json`. `GraphCaseStateBuilder` then projects those
document artifacts into `case_memory` and `case_board` for LangGraph nodes. This
keeps the domain shape aligned with the future Evidence Graph while avoiding a
large DB migration in the same cutover.

Explicit user statements are also written into Case Memory through turn
metadata. Uploaded material can contradict those stated claims, producing
`CaseConflict` records that the interview agent can clarify without turning the
session back into a material checklist.

When a document is removed, its Case Memory contribution is tombstoned and no
longer participates in Case Board, replay, or legacy Gate projection.

## Replay Eval

Replay eval is defined in `docs/architecture/replay-eval-spec.md`. It must record
claims, evidence, conflicts, and next move state instead of only checking final
assistant text or legacy missing-material fields.

## Compatibility Rule

Legacy Gate fields can remain during migration, but they are projection fields only. New runtime decisions must be based on Case Memory and graph state.
