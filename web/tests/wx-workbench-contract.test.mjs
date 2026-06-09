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
