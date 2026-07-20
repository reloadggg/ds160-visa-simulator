# Post-Review Fix Plan — `48574ab`

**Date:** 2026-07-20  
**Commit reviewed:** `48574aba5c8cf99015683181bb46ee918ddb1bce`  
**Constraint:** **max 3 concurrent workstreams** (file write scopes must be disjoint)  
**Repo root:** `ds160-visa-simulator/` (not Trellis workspace root)

---

## 0. Goals & non-goals

### Goals
1. Close all **P0** review findings before treating the commit as shippable.
2. Close **P1** findings in a second wave without merge conflicts.
3. Keep tests honest (no “source-string contracts” pretending to guard runtime).
4. Align docs/API with actual gate/authz/oracle contracts.

### Non-goals (defer / product decision)
- Encrypting access keys at rest (already deferred in B8).
- Full case-memory generation-token invalidation on every read path (optional B2 strengthen).
- Changing default theme policy for existing users with localStorage (document only unless product asks).
- Replacing daemon `Thread` SSE workers with a full job queue system (only add cancel/lock semantics).

---

## 1. Finding → Work Package map

| ID | Sev | Finding | WP |
|----|-----|---------|----|
| F1 | P0 | Practice gate = practice OR debug | **WP-A** |
| F2 | P0 | No rate/concurrency limit + unlimited seed | **WP-A** |
| F3 | P0 | Product payload leaks `expected_findings` + debug copy | **WP-A** |
| F4 | P0 | FE practice stream falls back to **debug** API | **WP-B** |
| F5a | P0 | `fetchUserReport` guard fails when session is null | **WP-B** |
| F5b | P0 | Practice generate applies after session switch | **WP-B** |
| F5c | P0 | `handleLoadBackendSession` lost-update race | **WP-B** |
| F5d | P0 | Material understanding poll stops on first terminal doc | **WP-B** |
| F6 | P0 | B1 turn concurrency incomplete (short lock / SQLite) | **WP-C** |
| F7 | P1 | Unconditional trust of `CF-Connecting-IP` | **WP-C** |
| F8 | P1 | Wx ticket `max_files` TOCTOU | **WP-C** |
| F9 | P1 | `get_evidence_excerpt` exposes tombstoned docs | **WP-C** |
| F10 | P1 | Report `requested_documents` fallback pollutes types | **WP-C** |
| F11 | P1 | Stream failure / disconnect leaves partial state | **WP-A** (status+tombstone) / **WP-D** (cancel token polish) |
| F12 | P1 | Case memory debug strings on practice path | **WP-A** (with F3) |
| F13 | P1 | OpenAI ownership tightening undocumented breaking | **WP-D** |
| F14 | P1 | Machine key = full session write, soft docs | **WP-D** |
| F15 | P1 | Practice dialog closable while generating | **WP-B** |
| F16 | P1 | Desktop terminal weaker than wx | **WP-B** |
| F17 | P1 | Desktop restored images lack `preview_url` | **WP-B** |
| F18 | P1 | Contract tests are source greps (false green) | **WP-B** + **WP-D** |
| F19 | P2 | Session create + quota not same transaction | **WP-C** (if time) / **WP-D** |
| F20 | P2 | `is_practice_material` always true / FE over-wide | **WP-A** BE + **WP-B** FE |
| F21 | P2 | Dead `_practice_materials_enabled` unused | **WP-A** |
| F22 | P2 | Practice API default `include_synthetic_user_turns=true` | **WP-A** + **WP-D** docs |
| F23 | P2 | Checklist over-claims B1 complete | **WP-D** |
| F24 | P2 | Theme default light | **WP-D** (doc only) |

---

## 2. Concurrency model (3 tracks)

```
Wave 1 (parallel ×3) — P0 + high-ROI P1
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ WP-A Backend        │  │ WP-B Frontend        │  │ WP-C Runtime/Sec     │
│ Practice product    │  │ Session races +      │  │ B1 / evidence /      │
│ gate·limit·oracle   │  │ stream fallback      │  │ report / CF / wx     │
└──────────┬──────────┘  └──────────┬──────────┘  └──────────┬──────────┘
           │                        │                        │
           └────────────┬───────────┴────────────┬───────────┘
                        ▼
Wave 2 (≤3 parallel) — docs, remaining P1/P2, test honesty
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ WP-D Docs + API     │  │ WP-E Extra tests     │  │ WP-F Spec / polish   │
│ contracts, breaking │  │ authz/lifecycle/     │  │ trellis-update-spec  │
│ machine key matrix  │  │ concurrency cases    │  │ optional cleanups    │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘

Wave 3 (serial) — integrate, full verify, commit
```

**Hard rule:** within a wave, tracks must not edit the same file.  
If a track needs a shared type/API change, put the **schema/DTO change in WP-A**, FE adapts in WP-B (read-only against new fields).

---

## 3. Wave 1 — Work Packages (implement now)

### WP-A — Backend practice product hardening  
**Owner agent:** `trellis-implement`  
**Active task prefix:** `Active task: .trellis/tasks/06-07-wechat-webview-mvp`

#### Exclusive write scope
```text
app/api/routers/sessions.py
app/services/debug_material_bundle_service.py
app/services/admin_config_service.py          # only if needed for comments/helpers
app/services/ai_material_bundle_generator_service.py  # seed/token budget only if present
app/core/settings.py                          # rate-limit / seed max settings
app/core/dependencies.py                      # only if sharing rate-limit helper here is natural
# Prefer NEW small module rather than bloating dependencies:
app/services/material_generation_guard.py     # NEW: rate limit + in-flight lock helpers
tests/integration/test_practice_material_bundles_api.py
tests/unit/test_admin_config_service.py       # only gate-related assertions
tests/unit/test_material_generation_guard.py  # NEW
```

**Do NOT touch:** `web/**`, `message_service.py`, `evidence_service.py`, `report_service.py`, `simple_auth.py`, `wx_upload*`.

#### Tasks

**A1. Gate fix (F1, F21)**  
- Practice routes (`POST .../practice/material-bundles` and `/stream`) must call `_practice_materials_enabled(db)` only.  
- Keep `_material_generation_enabled` only if still used by **non-user** internal helpers; otherwise delete or document as debug-union for internal tools.  
- Debug routes continue to use `_debug_material_enabled`.  
- Change test `test_practice_route_still_allowed_when_only_debug_material_enabled` → **expect 403**.  
- Add test: practice ON + debug OFF → 200; both OFF → 403; practice OFF + debug ON → 403 on **practice** URL, debug URL still gated by debug flags.

**A2. Rate limit + concurrency lock (F2)**  
Implement `material_generation_guard.py` (or equivalent) with:
1. **Per access_key / per session** sliding limits (settings-driven defaults, e.g. 5/hour/session, 20/hour/key — pick conservative numbers; document in `.env.example` if env-backed).  
2. **In-flight lock per `session_id`**: acquire before generation; on conflict return **409** with clear detail (`material generation already in progress`).  
   - Prefer DB row lock on session (`get_for_update`) or a small status field in interviewer/session metadata: `material_generation: {status, started_at, bundle_id}`.  
   - TTL auto-expire stale `running` (e.g. 10–15 min) so crashed workers don’t brick the session.  
3. Apply lock to **both** non-stream and stream (`create_bundle` / `create_bundle_events` / `_run_material_bundle_stream`).  
4. `seed_text` hard max (request schema): e.g. **4000 chars** (or 8KB bytes); 422 on overflow.  
5. Optional: global concurrent generation cap (process-level semaphore) if cheap.

**A3. Source split + strip oracle (F3, F12, F20)**  
- Add `source: Literal["practice","debug"]` (or bool `for_practice`) through router → service.  
- Practice `final_payload` / stream `final` **must omit** `expected_findings` (or empty array only if FE requires key — prefer **omit**).  
- Debug path retains `expected_findings`.  
- Set `is_practice_material = (source == "practice")` only (stop hardcoding `True` on debug).  
- Case memory / proof point / next_move strings:  
  - practice → 「练习材料…」 / metadata `practice_material_bundle`  
  - debug → keep debug wording / `debug_material_bundle`  
- Practice path must **not** inject oracle-driven conflicts into case board from `expected_findings`.  
- Integration test: practice final event **must not** contain non-empty oracle findings; debug may.

**A4. Partial failure / disconnect baseline (F11 subset)**  
- On generation start: mark session `material_generation.status=running` + `bundle_id`.  
- On error after materials committed: tombstone documents for that `bundle_id` when possible, `rebuild_and_persist`, clear running flag; surface error in SSE.  
- On success: `status=completed`, clear running.  
- Stream: if client disconnect is detectable in ASGI, best-effort cancel flag; if not reliable, TTL + lock still prevent double run. Full cancel token can land in WP-D if incomplete.

**A5. API default (F22)**  
- For **practice** request schema defaults: `include_synthetic_user_turns=False` (match FE product intent).  
- Debug schema may keep `True` if useful for tests.

#### Acceptance (WP-A)
```bash
cd ds160-visa-simulator
pytest -q \
  tests/integration/test_practice_material_bundles_api.py \
  tests/unit/test_admin_config_service.py \
  tests/unit/test_material_generation_guard.py \
  -m 'not live_llm'
```
Manual/API checks:
- `practice_materials_enabled=false`, `debug_material_enabled=true` → practice 403, debug still debug-gated.  
- Two concurrent practice stream on same session → second 409.  
- Practice response has no usable oracle findings.

#### Rollback
Revert WP-A files; flags remain admin-toggleable. No schema migration preferred; if adding columns, use JSON metadata on existing session fields to avoid migration pain.

---

### WP-B — Frontend races + product path correctness  
**Owner agent:** `trellis-implement`  
**Exclusive write scope**
```text
web/lib/api/client.ts
web/lib/api/types.ts                    # only if practice response types need optional findings
web/lib/api/mappers.ts
web/hooks/use-session-workbench.ts
web/hooks/use-wx-workbench.ts
web/components/ds160/practice-materials-dialog.tsx
web/components/ds160/materials-panel.tsx  # findings UI only if debug-gated
web/components/ds160/login or page wiring only if dialog onOpenChange lives there
# Prefer fixing at dialog props from login/page if needed:
web/app/login/page.tsx                  # only practice dialog onOpenChange glue
web/tests/practice-materials-contract.test.mjs
web/tests/api-layer-contract.test.mjs   # stream fallback + preview_url
web/tests/wx-workbench-contract.test.mjs
# optional new:
web/tests/session-race-guards-contract.test.mjs  # NEW preferred
```

**Do NOT touch:** `app/**` (consume WP-A API once available; until then FE can be coded against planned contracts).

#### Tasks

**B1. Practice stream fallback (F4)**  
- Add `createPracticeMaterialBundle(...)` → `POST /v1/sessions/{id}/practice/material-bundles`.  
- In `createMaterialBundleStream`, when `!response.body`, fall back using **same product family**:  
  - path contains `/practice/` → practice non-stream  
  - path contains `/debug/` → debug non-stream  
- Never call `createDebugMaterialBundle` from practice entry.

**B2. Session guards (F5a–c)**  
Shared pattern:
```ts
const targetSessionId = sessionId /* or arg */
// after await:
if (sessionIdRef.current !== targetSessionId) return
```
Apply to:
1. `fetchUserReport` — remove truthy short-circuit; use strict inequality only.  
2. `runDebugMaterialBundle` / practice generate completion — capture `targetSessionId` at start; after stream, guard all `set*` / refresh.  
3. `handleLoadBackendSession` — monotonic `loadSeq` ref; ignore stale responses.  
4. (Recommended) `handleSendMessage` assistant apply — same guard.

**B3. Multi-doc understanding poll (F5d)**  
- Desktop: stop only when **all** tracked document ids are terminal (completed/failed/tombstoned) or timeout.  
- Align with wx `pending` list approach if cleaner.  
- Partial terminal: merge patches + optional soft refresh, continue backoff.

**B4. Dialog lock while generating (F15)**  
- `onOpenChange`: if `!open && isGenerating` → ignore.  
- Disable escape / outside dismiss while generating (`onEscapeKeyDown` / `onPointerDownOutside` preventDefault).  
- Hide or disable close button while generating.

**B5. Terminal parity (F16)**  
- Desktop `isTerminalInterviewState` include `current_governor_decision` in {passed, not_passed, refused, simulated_refusal} (match wx).  
- After send response, update session phase/governor from response before/without waiting solely on report.

**B6. preview_url (F17)**  
- `mapSessionDocumentToUploadedMaterial`: for image kind, set `preview_url` from content_url (parity with wx).  
- Or materials-panel fallback to content_url — pick one; prefer mapper so all consumers benefit.

**B7. Badge / findings UI (F20, F3 FE)**  
- `is_practice_material` mapping: **only** `Boolean(bundle.is_practice_material)` (drop scenario/bundle_id OR).  
- Render `expected_findings` / 「核验线索」 **only** when debug console path / debug flag; never on practice brief.

**B8. Dead-end debug fill (F11 FE side of F11 list)**  
- `handleDebugFillCurrentGap` without seed: remove CTA or route through practice dialog with required seed.

**B9. Wx ticket cross-session (F10 FE list)**  
- In `use-wx-workbench` ticket refresh: if `sessionIdRef.current !== effectiveSessionId` → **return** (do not merge).

**B10. Tests (F18 subset)**  
Replace pure string greps with at least:
1. Stream helper: practice path + no body → calls practice non-stream URL (mock fetch).  
2. `sessionIdRef` null/other → report/generate must not apply (unit-ish or pure function extract).  
3. Poll exit condition: 1/2 docs terminal → keep polling.  
4. Dialog generating blocks close (if component testable; else document manual).

#### Acceptance (WP-B)
```bash
cd ds160-visa-simulator/web
pnpm test -- --run tests/practice-materials-contract.test.mjs tests/api-layer-contract.test.mjs tests/wx-workbench-contract.test.mjs
pnpm type-check
pnpm lint
```

#### Rollback
Revert `web/**` only; backend remains usable via API clients.

---

### WP-C — Runtime / security hardening (P0-B1 + P1)  
**Owner agent:** `trellis-implement`  
**Exclusive write scope**
```text
app/services/message_service.py
app/repositories/session_repo.py
app/services/evidence_service.py
app/services/report_service.py
app/core/simple_auth.py
app/core/settings.py                 # ONLY if WP-A not claiming it — prefer coordinate:
# If WP-A already owns settings.py, put CF trust flag in simple_auth using existing pattern
# or add settings keys in WP-A first. Default: WP-C may edit settings.py ONLY for:
#   trust_cf_connecting_ip
# WP-A may edit settings.py ONLY for:
#   material_generation_* limits / seed_max
# → Split settings sections carefully; or WP-C hardcodes default false + env via settings in one PR after merge.
# SAFER: WP-C adds trust_cf_connecting_ip in settings.py; WP-A adds material_* in settings.py
#   → CONFLICT. Resolve by: WP-A owns settings.py entirely; WP-C reads existing IP helpers only
#   and implements CF gate as: trust only when trust_x_forwarded_for is true OR new helper
#   in simple_auth without new setting: "trust CF header only if request.client.host is loopback/private
#   OR settings.trust_x_forwarded_for". Document. Prefer one new setting added by WP-C AFTER Wave1 merge.
app/services/wx_upload_ticket_service.py
app/api/routers/wx_upload.py
app/core/dependencies.py             # create_session_with_quota atomicity (F19) if low risk
tests/integration/test_messages_api.py
tests/integration/test_material_lifecycle_b3_b5.py  # excerpt / tombstone if needed
tests/unit/test_report_service.py
tests/unit/test_request_metadata_ip.py
tests/unit/test_wx_upload_ticket_service.py
tests/integration/test_wx_upload_ticket_api.py      # concurrent max_files if exists
docs/implementation/backend-runtime-defect-fix-plan.md  # mark B1 partial only if WP-D not taking docs
```

**Conflict avoidance with WP-A:**  
- **WP-C does not edit `settings.py` in Wave 1.** CF-IP fix uses: trust `CF-Connecting-IP` only when `settings.trust_x_forwarded_for` is true (same “behind trusted proxy” switch). Document that operators behind CF must set `TRUST_X_FORWARDED_FOR=true` **and** lock origin to CF IPs. Optional dedicated `trust_cf_connecting_ip` moves to WP-D.  
- WP-C does not edit `sessions.py` or material bundle service.

#### Tasks

**C1. B1 concurrency honesty + best-effort fix (F6)**  
Minimum (must ship):
1. Document in code comment + defect plan: SQLite `FOR UPDATE` is no-op; lock does not span LLM.  
2. Add integration test that **documents current behavior** or fails under Postgres if double-append possible — prefer a **serializable guard**:
   - After appending user turn, set `session.processing_user_turn_id` / flag in same commit.  
   - Concurrent second request sees unanswered **or** processing flag → 409.  
   - Clear flag when assistant commits or cleanup runs.  
3. If full lock through assistant is too heavy for this wave: implement **processing flag** (recommended) instead of long FOR UPDATE.  
4. Update `backend-runtime-defect-fix-plan.md` B1 checklist to **partial** if flag-only (or complete if flag meets “no double user turn”).

**C2. Evidence excerpt tombstone (F9)**  
- `get_evidence_excerpt`: if parent document tombstoned → return `None`.  
- Unit/integration assertion.

**C3. Report requested_documents (F10)**  
- `_resolve_requested_documents` fallback: only document_type-like values (whitelist / remaining_required_documents + focus docs); **do not** dump raw `missing_evidence` proof_point ids.  
- Unit test for missing key projection.

**C4. CF-Connecting-IP (F7)**  
- Wave-1 approach (no settings conflict): only honor CF header when `trust_x_forwarded_for` is true; else use `request.client.host`.  
- Update unit tests in `test_request_metadata_ip.py`.  
- Comment: production behind CF must enable trust flag + network allowlist.

**C5. Wx ticket max_files (F8)**  
- Reserve slot under lock **before** `FileService.upload`, or:  
  - validate+increment in same critical section; on upload failure rollback count + optional tombstone doc.  
- Integration: concurrent two uploads with `max_files=1` → only one document sticks / second fails cleanly.

**C6. Optional F19**  
- If small: make `create_session_with_quota` single transaction (no intermediate commit). Else leave for WP-D.

#### Acceptance (WP-C)
```bash
cd ds160-visa-simulator
pytest -q \
  tests/integration/test_messages_api.py \
  tests/unit/test_report_service.py \
  tests/unit/test_request_metadata_ip.py \
  tests/unit/test_wx_upload_ticket_service.py \
  tests/integration/test_wx_upload_ticket_api.py \
  tests/integration/test_material_lifecycle_b3_b5.py \
  -m 'not live_llm'
```

---

## 4. Wave 2 — after Wave 1 merge (≤3 parallel)

### WP-D — Docs & contracts  
**Write scope**
```text
docs/API.md
docs/runtime-contracts.md
README.md
.env.example
docs/implementation/backend-runtime-defect-fix-plan.md
docs/implementation/post-review-fix-plan-2026-07-20.md  # check off items
```

#### Tasks
1. [x] Practice gate = **only** `practice_materials_enabled`; document 403 cases.  
2. [x] Practice request/response schema (seed max, defaults, no oracle fields).  
3. [x] Rate limit / 409 in-flight semantics.  
4. [x] OpenAI-compat **breaking** note: ownership + quota.  
5. [x] Machine key permission matrix: full session write, trusted backends only.  
6. [x] §8 index: `GET .../files`, `GET .../documents`.  
7. [x] `MATERIAL_UNDERSTANDING_REQUIRED`, IP trust matrix (XFF / CF).  
8. [x] B1 checklist honesty (already in backend-runtime-defect-fix-plan).  
9. [ ] Theme default light note (F24) — product/FE doc only if needed; not API contract.

### WP-E — Test gap fill  
**Write scope**
```text
tests/integration/test_openai_compat_authz.py   # unauth 401, bad bearer, responses quota
tests/integration/test_practice_material_bundles_api.py  # if WP-A left TODOs
tests/integration/test_simple_auth.py
tests/unit/test_case_memory_service.py          # only if practice source assertions needed
# no web/ if WP-B still open; after WP-B:
web/tests/* additional behavioral tests
```

#### Tasks
- Practice: unauthenticated / cross-session 403 when `app_auth` on.  
- List documents includes tombstoned (per product contract).  
- Concurrent practice generation 409.  
- Oracle absent on practice final.  
- Compat: unauthenticated, wrong machine key, responses path quota if missing.

### WP-F — Spec capture & small polish  
**Write scope**
```text
.trellis/spec/**   via trellis-update-spec skill
# tiny code leftovers only if no conflict:
app/services/case_memory_service.py   # only if WP-A didn't fully fix copy injection site
```

#### Tasks
- Capture contracts: practice gate, generation lock, session race guards, IP trust, tombstone excerpt.  
- Dead code cleanup only if safe.  
- Stream cancel polish (F11 remainder) if not done in A4.

---

## 5. Wave 3 — Integrate & ship

### Serial steps (main session)
1. Merge order: **WP-A → WP-C → WP-B** (FE last so it can match final API), or A∥C then B.  
2. Conflict hotspots to re-check: `settings.py` (if both touched), any accidental dual edits.  
3. Full verification:
```bash
cd ds160-visa-simulator
pytest -q -m 'not live_llm'   # or targeted suite if time-boxed
cd web && pnpm type-check && pnpm lint && pnpm test
```
4. `trellis-check` agent once.  
5. `trellis-update-spec` if WP-F not done.  
6. Commit (Phase 3.4) with message like:
   ```
   fix: close post-48574ab review P0/P1 (practice gate, races, hardening)
   ```
7. `/trellis:finish-work` only when task acceptance met.

---

## 6. Dispatch prompts (copy-paste)

### Wave 1 Agent 1 — WP-A
```text
Active task: .trellis/tasks/06-07-wechat-webview-mvp

Implement WP-A from docs/implementation/post-review-fix-plan-2026-07-20.md.
Repo: ds160-visa-simulator/
Exclusive files only (see plan §3 WP-A). Do not edit web/** or message/evidence/report/simple_auth/wx_upload.
Deliver: practice-only gate, generation rate/concurrency guard, seed max, source=practice|debug oracle strip + copy split, practice defaults, tests green.
No git commit.
```

### Wave 1 Agent 2 — WP-B
```text
Active task: .trellis/tasks/06-07-wechat-webview-mvp

Implement WP-B from docs/implementation/post-review-fix-plan-2026-07-20.md.
Exclusive web/** files only. Do not edit app/**.
Deliver: practice stream fallback not to debug; session race guards; multi-doc poll; dialog lock; terminal parity; preview_url; findings/badge; wx ticket session guard; behavioral tests.
No git commit.
```

### Wave 1 Agent 3 — WP-C
```text
Active task: .trellis/tasks/06-07-wechat-webview-mvp

Implement WP-C from docs/implementation/post-review-fix-plan-2026-07-20.md.
Exclusive runtime/security files only. Do NOT edit settings.py (coordinate: CF header only when trust_x_forwarded_for). Do not edit sessions.py or debug_material_bundle_service.py or web/**.
Deliver: B1 processing flag or equivalent no double user-turn; evidence excerpt tombstone; report requested_documents pure types; CF-IP trust coupling; wx ticket reserve slot; tests green.
No git commit.
```

---

## 7. Definition of Done (all waves)

| Check | Pass criteria |
|-------|----------------|
| F1 | practice OFF + debug ON → practice API 403 |
| F2 | concurrent generate → 409; seed oversize → 422; rate limit returns 429 |
| F3 | practice final has no oracle findings; UI no 核验线索 on practice |
| F4 | practice stream without body hits practice non-stream |
| F5 | switch session during report/generate/load never paints foreign state |
| F5d | multi-upload poll continues until all docs terminal |
| F6 | no double user turn under concurrent posts (or documented SQLite limit + flag guard) |
| F7 | with trust_x_forwarded_for=false, CF header ignored |
| F8 | max_files hard under concurrency |
| F9 | excerpt None when document tombstoned |
| F10 | requested_documents only document types |
| Docs | [x] API.md matches gates, breaking notes present (WP-D 2026-07-20) |
| Tests | targeted suites green; no new false-green greps as sole coverage |

---

## 8. Risk register

| Risk | Mitigation |
|------|------------|
| WP-A vs WP-C both want `settings.py` | Wave 1: only WP-A writes settings; WP-C reuses `trust_x_forwarded_for` |
| FE ships before BE oracle strip | FE already hides findings; BE still must strip for API clients |
| Generation lock on SQLite weak | Use committed status flag + TTL, not only FOR UPDATE |
| Rate limit storage | Start in-DB or in-process; document multi-worker needs Redis later |
| Large `use-session-workbench.ts` merge pain | Single owner WP-B; no other track edits it |
| Live LLM tests flaky | Keep `-m 'not live_llm'`; stub generator in practice tests |

---

## 9. Estimated effort (rough)

| WP | Effort | Parallel |
|----|--------|----------|
| A | M–L (1–2 sessions) | Wave 1 slot 1 |
| B | M–L (1–2 sessions) | Wave 1 slot 2 |
| C | M (1 session) | Wave 1 slot 3 |
| D | S–M | Wave 2 |
| E | S–M | Wave 2 |
| F | S | Wave 2 |
| Integrate | S | Wave 3 serial |

---

## 10. Execution checklist (main session)

Wave 1:
- [x] Dispatch WP-A, WP-B, WP-C (3 agents)
- [x] Await all three; resolve any accidental file overlap
- [x] Smoke: practice 403 gate + FE type-check + messages tests  
  *(code evidence: practice-only `_practice_materials_enabled`, `MaterialGenerationGuard` 409/429, oracle strip on practice `final_payload`, CF-IP gated by `trust_x_forwarded_for`, processing_user_turn / B1 honesty in defect plan)*

Wave 2:
- [x] Dispatch WP-D (docs/API contracts — this pass)
- [x] Extra tests: admin model channels, material generation guard, chunked AI material generator unit tests
- [x] Re-read API.md vs routers for drift (WP-D aligned gates, seed max, 409/429, ownership breaking, machine matrix, IP trust, §8 GET files/documents)
- [x] Follow-ups landed post-plan: multi-channel admin model config + UI; chunked material generation for unstable gateways

Wave 3:
- [x] Full verify (targeted unit/integration + live channel smoke: materials + pass/fail interview paths)
- [ ] trellis-check / finish-work (optional session wrap)
- [x] Commit

---

*Generated from multi-agent review of 48574ab (security / practice BE / runtime / FE / QA).*  
*Updated 2026-07-20: multi-channel model config + chunked practice material generation.*
