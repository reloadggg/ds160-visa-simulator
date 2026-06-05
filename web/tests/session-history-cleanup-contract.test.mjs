import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import test from "node:test"

const rootDir = resolve(import.meta.dirname, "..")

function readProjectFile(path) {
  return readFileSync(resolve(rootDir, path), "utf8")
}

test("settings copy describes account-level session history cleanup", () => {
  const source = readProjectFile("components/ds160/settings-panel.tsx")

  assert.match(source, /清理本账号的会话历史记录/)
  assert.match(source, /旧版本 Kit 留在本浏览器中的会话历史/)
  assert.doesNotMatch(source, /清空本地历史记录/)
})

test("workbench cleanup clears account sessions and legacy local history keys", () => {
  const source = readProjectFile("hooks/use-session-workbench.ts")

  assert.match(source, /const LEGACY_HISTORY_STORAGE_KEYS = \["ds160-web-history-v1"\]/)
  assert.match(source, /clearAccountSessions\(sessionId\)/)
  assert.match(source, /writeHistoryEntries\(browserHistorySnapshot\)/)
  assert.doesNotMatch(source, /writeHistoryEntries\(sessionHistory\)/)
})

test("api client exposes account session history deletion", () => {
  const source = readProjectFile("lib/api/client.ts")

  assert.match(source, /export async function clearAccountSessions/)
  assert.match(source, /method: "DELETE"/)
  assert.match(source, /exclude_session_id=/)
})
