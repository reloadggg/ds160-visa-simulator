import assert from "node:assert/strict"
import { readFileSync, existsSync } from "node:fs"
import { join } from "node:path"
import { test } from "node:test"

const root = process.cwd()
const read = (path) => readFileSync(join(root, path), "utf8")

test("/wx route and lightweight workbench files exist", () => {
  assert.ok(existsSync(join(root, "app/wx/page.tsx")))
  assert.ok(existsSync(join(root, "hooks/use-wx-workbench.ts")))
  assert.ok(existsSync(join(root, "components/wx/wx-shell.tsx")))
})

test("/wx reads app config and stays closed when wx entry is disabled", () => {
  const shell = read("components/wx/wx-shell.tsx")
  const types = read("lib/api/types.ts")
  const client = read("lib/api/client.ts")
  assert.match(types, /wx_entry_enabled: boolean/)
  assert.match(client, /export async function getAppConfig/)
  assert.match(shell, /getAppConfig\(\)/)
  assert.match(shell, /wx_entry_enabled/)
  assert.match(shell, /微信端内测中/)
  assert.match(shell, /返回首页/)
  assert.match(shell, /setWxEntryEnabled\(config\.wx_entry_enabled\)/)
  assert.match(shell, /setWxEntryEnabled\(false\)/)
  assert.match(shell, /<WxWorkbenchShell \/>/)
})

test("wx workbench reuses existing non-streaming API contracts only", () => {
  const source = read("hooks/use-wx-workbench.ts")
  assert.match(source, /sendMessage\(/)
  assert.match(source, /createSession\(/)
  assert.match(source, /listSessions\(/)
  assert.match(source, /fetchSessionMessages\(/)
  assert.match(source, /uploadFile\(/)
  assert.match(source, /getUserReport\(/)
  assert.doesNotMatch(source, /sendMessageStream/)
  assert.doesNotMatch(source, /useSessionWorkbench/)
})

test("wx restores materials via listSessionDocuments and rewrites content_url", () => {
  const source = read("hooks/use-wx-workbench.ts")
  const client = read("lib/api/client.ts")
  assert.match(source, /listSessionDocuments/)
  assert.match(source, /mapSessionDocumentsToUploadedMaterials/)
  assert.match(source, /loadSessionDocuments/)
  assert.match(source, /getFileContentUrl\(/)
  assert.match(client, /export async function listSessionDocuments/)
  assert.match(client, /\/v1\/sessions\/\$\{sessionId\}\/documents/)
  assert.match(source, /tombstoned/)
  assert.doesNotMatch(
    source,
    /content_url:\s*upload\.content_url\s*\?\?\s*\(upload\.document_id/,
  )
})

test("wx polls documents list after upload and refreshes report on terminal", () => {
  const source = read("hooks/use-wx-workbench.ts")
  assert.match(source, /queueMaterialUnderstandingRefresh/)
  assert.match(source, /MATERIAL_UNDERSTANDING_POLL_DELAYS_MS/)
  assert.match(source, /sessionIdRef\.current !== targetSessionId/)
  assert.match(source, /isTerminalMaterialUnderstandingStatus/)
  assert.match(source, /refreshReport\(targetSessionId\)/)
  assert.match(source, /queueMaterialUnderstandingRefresh\(sessionId, uploadedIds\)/)
})

test("wx send guard uses sendingRef and blocks terminal sessions", () => {
  const source = read("hooks/use-wx-workbench.ts")
  assert.match(source, /sendingRef/)
  assert.match(source, /isTerminalInterviewState/)
  assert.match(source, /isSessionTerminal/)
  assert.match(source, /retryMessage/)
  assert.match(source, /reuseMessageId/)
  assert.match(source, /client_message_id/)
  assert.match(source, /retry_content/)
  assert.match(source, /not_passed/)
})

test("wx ticket refresh hard-returns on cross-session mismatch", () => {
  const source = read("hooks/use-wx-workbench.ts")
  const start = source.indexOf("const refreshNativeUploadTicket")
  assert.notEqual(start, -1)
  const block = source.slice(start, start + 900)
  assert.match(block, /sessionIdRef\.current !== effectiveSessionId/)
  assert.match(block, /Cross-session guard/)
  // Must not soft-continue past the mismatch.
  assert.doesNotMatch(
    block,
    /still apply if no active session switch mid-flight/,
  )
})

test("wx revokes object URLs for image previews", () => {
  const source = read("hooks/use-wx-workbench.ts")
  assert.match(source, /URL\.createObjectURL/)
  assert.match(source, /URL\.revokeObjectURL/)
  assert.match(source, /revokeIfObjectUrl|revokeMaterialObjectUrls/)
})

test("wx message list exposes retry for failed user messages", () => {
  const list = read("components/wx/wx-message-list.tsx")
  const panel = read("components/wx/wx-chat-panel.tsx")
  const shell = read("components/wx/wx-shell.tsx")
  assert.match(list, /onRetryMessage/)
  assert.match(list, /重试本条/)
  assert.match(panel, /onRetryMessage/)
  assert.match(panel, /isSessionTerminal/)
  assert.match(shell, /retryMessage/)
  assert.match(shell, /isSessionTerminal/)
})

test("wx route does not import desktop workbench panels", () => {
  const page = read("app/wx/page.tsx")
  const shell = read("components/wx/wx-shell.tsx")
  const combined = `${page}\n${shell}`
  assert.doesNotMatch(combined, /components\/ds160\/chat-panel/)
  assert.doesNotMatch(combined, /components\/ds160\/materials-panel/)
  assert.doesNotMatch(combined, /admin|runtime debug|RAG|BYOK/i)
})

test("api client exposes wx upload ticket functions", () => {
  const client = read("lib/api/client.ts")
  const types = read("lib/api/types.ts")
  assert.match(client, /export async function createWxUploadTicket/)
  assert.match(client, /export async function getWxUploadTicketStatus/)
  assert.match(client, /\/v1\/sessions\/\$\{sessionId\}\/upload-ticket/)
  assert.match(client, /\/v1\/wx\/upload-tickets\/\$\{encodeURIComponent\(ticket\)\}/)
  assert.match(types, /interface WxUploadTicketResponse/)
  assert.match(types, /interface WxUploadTicketStatusResponse/)
})

test("admin settings expose wx entry feature switch", () => {
  const admin = read("app/admin/page.tsx")
  const types = read("lib/api/types.ts")
  assert.match(types, /wx_entry_enabled\?: boolean/)
  assert.match(admin, /wx_entry_enabled/)
  assert.match(admin, /微信端入口（默认关闭）/)
  assert.match(admin, /updateFeatureFlag/)
  assert.match(admin, /updateAdminSettings\(patch\)/)
})
