# Frontend Aesthetic + Bug Review

> Date: 2026-07-20  
> Scope: `web/` only (desktop workbench, landing, admin, wx)  
> Agents: bug review · aesthetic design · API contract  
> Open Design MCP: **unavailable** this session (daemon process exists via tools-dev IPC; HTTP `127.0.0.1:7456` not listening — cannot list projects / create design artifacts). Re-run visual pass when OD is reachable.

Backend materials list / report contracts are already in place; frontend has **not** wired them yet.

---

## 1. Executive summary

| Track | Verdict |
|-------|---------|
| **Bugs / contracts** | Production material understanding loop is still broken: polls debug-only endpoint; restore clears materials; no list-documents client. |
| **Aesthetics** | Landing/admin dark-cyan brand is strong; workbench is usable but **three systems** (marketing glass / shadcn workbench / light auth) fight each other. |
| **Highest ROI** | Wire public `GET .../documents` poll + restore + report rematerialize **before** visual redesign. |

---

## 2. Bug / contract — priority backlog

### P0 — Must fix (prod-breaking UX)

| ID | Issue | Where | Fix |
|----|--------|--------|-----|
| F0 | No `listSessionDocuments` client | `lib/api/client.ts` | `GET /v1/sessions/{id}/documents` (or `/files`) + types/mapper |
| F1 | Material poll only via `debug/runtime` → 403 when debug off | `use-session-workbench.queueMaterialUnderstandingRefresh` | Poll public documents list; keep debug for debug panel only |
| F2 | Understanding complete does not refresh case board/report | `applyMaterialUnderstandingPatches` | On terminal status → `getUserReport` + merge board |
| F3 | Session restore `setUploadedMaterials([])` forever | desktop + wx restore | After messages, list documents + map materials |
| F4 | History “server stub” can wipe UI on restore | `serverSessionToHistoryEntry` / `handleRestoreSession` | Prefer local rich entry; server-only → `handleLoadBackendSession` |

### P1 — High

| ID | Issue | Fix |
|----|--------|-----|
| F5 | Wx: no understanding poll, no message retry, no send ref | Shared poll + retry CTA + `sendingRef` |
| F6 | Report loading/error blanks last-good analysis | Soft load; error banner; user report independent of internal |
| F7 | All 403 → “调试/流式未开” | Scope special-case to debug/stream only |
| F8 | `content_url` raw `/v1` breaks under `/api` base (esp. wx) | Rewrite at API boundary via `buildApiUrl` / `getFileContentUrl` |
| F9 | `humanizeBackendText` substring corruption | Enum/code map only; leave prose alone |
| F10 | Strict mappers drop proof/evidence rows | Relax required fields (e.g. empty `why_it_matters` OK) |
| F11 | Stream disconnect after `accepted` no transcript reload | `fetchSessionMessages` on post-accept failure |
| F12 | Case board FE only stores `open_proof_points` | Emit dual fields; read either |

### P2 — Medium

| ID | Issue |
|----|--------|
| F13 | Poll ~11s then stops; no session-id guard mid-poll |
| F14 | End-session `Promise.all` with internal report fails whole path |
| F15 | Analysis panel full spinner every message |
| F16 | `isMaterialUnderstandingFailed` too broad |
| F17 | Wx object URL leak; no terminal guard on send |
| F18 | Message mapper drops `agent_runtime` metadata |
| F19 | No delete-document client |

---

## 3. Aesthetic — priority backlog

### Brand / system (P0–P1 design)

| ID | Issue | Recommendation |
|----|--------|----------------|
| D0 | Three auth UIs (landing dark / AuthGuard light / wx) | One auth visual; AuthGuard match landing dark glass |
| D1 | `styles/globals.css` stale second design system | Delete or stop shipping; single `app/globals.css` |
| D2 | `--success` / `--warning` not in `@theme` | Map tokens; case board uses them |
| D3 | Light `--accent` = primary (hover blobs) | Soft tint accent |
| D4 | Noto Sans SC declared, never loaded | `next/font` in layout |
| D5 | Landing logo Sparkles vs workbench `brand-icon.svg` | One mark + one product name |
| D6 | History badges / case board icon wells light-only | Dark variants or token-based |
| D7 | Unsplash VO avatar, dead Bell, Mac traffic lights | Remove / replace with brand monogram |

### Craft waves

1. **Wave 1 (1–3d):** name+logo, remove chrome noise, load font, success/warning tokens, auth dark, history dark badges  
2. **Wave 2 (1–2w):** shared risk/status module, case board hierarchy, Empty/Skeleton, header chrome unify  
3. **Wave 3:** tokenize marketing surfaces, single Auth component variants, landing preview ≈ real chat  

### Design system checklist (minimal)

- Tokens: surface / text / brand / feedback / risk / interview-status  
- Radius: 8 / 12 / 16 / 24 / 32 / pill  
- Type: load Noto Sans SC; no wide tracking on CJK display  
- Case board: risk + result hero; next move callout; claims secondary  
- Effects: max one mesh gradient; glass only on chrome  

---

## 4. Suggested frontend PR sequence

```text
PR-F1  API: list documents + content_url rewrite + types/mappers
PR-F2  Poll understanding on public list → refresh report/board
PR-F3  Restore + history: materials + messages + report (desktop + wx)
PR-F4  Report UX + getErrorMessage + stream transcript recovery
PR-F5  Wx parity (poll, retry, send guard)
PR-F6  humanize + mapper strictness + dual proof fields
PR-F7  Aesthetic Wave 1 (tokens, auth, logo, noise)
PR-F8  Aesthetic Wave 2 (case board hierarchy, status module)
```

Do **not** start visual Wave 2 before F1–F3; users will still see “材料卡住”.

---

## 5. Open Design project（当前前端已导入）

### Primary（真实前端 folder import）

| 项 | 值 |
|----|-----|
| **Project id** | `22e60ae5-30f3-41a4-a675-ae35b1d3d217` |
| **Name** | **DS-160 Web Frontend** |
| **baseDir** | `/home/feng/ds160_pr_/ds160-visa-simulator/web`（与 git 同源，非拷贝） |
| **entryFile** | `od-design/01-design-tokens.html` |
| **Daemon** | `http://127.0.0.1:7456` |

导入方式：`od project import-folder web/ --name "DS-160 Web Frontend"`。  
在 OD 里打开该项目即可直接浏览 `app/`、`components/`、`hooks/` 等**当前源码**。

审美目标放在源码旁（已 gitignore）：

| 路径 | 角色 |
|------|------|
| `web/od-design/README.md` | 项目地图 |
| `web/od-design/00-aesthetic-brief.md` | Brief |
| `web/od-design/01-design-tokens.html` | Token 板 |
| `web/od-design/02-unified-auth.html` | 统一 Auth |
| `web/od-design/03-case-board-hierarchy.html` | Case Board 层级 |
| `web/od-design/04-wave-checklist.md` | F7/F8 清单 |

### Secondary（早期独立 brief 项目，可忽略）

`ds160-frontend-aesthetic` — 仅静态 mock，无源码绑定；以 **DS-160 Web Frontend** 为准。

---

## 6. Tracking

- [x] PR-F1 list documents client  
- [x] PR-F2 public understanding poll  
- [x] PR-F3 restore materials  
- [x] PR-F4 report/error/stream UX  
- [x] PR-F5 wx parity  
- [x] PR-F6 mappers/humanize  
- [x] OD aesthetic project + mocks  
- [ ] PR-F7 aesthetic wave 1 (code)  
- [ ] PR-F8 aesthetic wave 2 (code)  

> Bug fixes on local `main` (2026-07-20). Aesthetic **design** in OD; code not started.
