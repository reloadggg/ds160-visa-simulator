# Replay Eval Spec

日期：2026-05-25
状态：AI-native case understanding regression contract

## Goal

Replay must prove the product center has moved from Gate readiness to Case Memory.
It should record what the agent knew, which evidence supported it, and why the
next move was chosen.

## Required Replay Payload

`ReplayRunner.replay_session()` returns:

- `case_memory`: claims, evidence cards, proof points, conflicts, and next move
- `case_board`: frontend-facing case understanding projection
- `turns`: user and assistant turns with runtime events
- `score_evals`: legacy score summaries for compatibility only

`score_evals.missing_evidence` may still exist, but it cannot be the only replay
signal for interview progress.

## Required Fixtures

Fixtures live in `fixtures/graph_replay/`:

- `no_material_chat_starts.json`
- `purpose_answer_advances.json`
- `visual_i20_updates_case_memory.json`
- `funding_claim_conflict.json`
- `complete_interview_success_path.json`
- `high_risk_simulation_without_full_package.json`
- `ocr_not_used_for_applicant_image.json`
- `refuse_fabrication_request.json`

These fixtures assert:

- no-material F-1 chat can proceed without Gate blocking
- after the user answers the purpose/program-choice question, the next turn advances
  to a new topic instead of repeating the same generic question
- visual I-20 understanding creates claim and evidence records
- upload follow-up uses Case Board `next_move` instead of waiting for a material gate
- funding self-vs-parents mismatch becomes a concrete conflict clarification
- complete success path covers session start, natural interview answers, upload
  understanding, conflict resolution, Case Board next move, and explainable review
- high-risk simulation can proceed with unknown caveats before a full material package
- applicant image replay contains no OCR parser or text markers
- fabrication requests are refused instead of producing fake sponsor/document text

## Eval Boundary

`GraphReplayEvaluator` checks graph output and product-state shape. It does not
call live LLMs. Live LLM replay may be added later, but should only assert stable
contract fields and not exact prose.
