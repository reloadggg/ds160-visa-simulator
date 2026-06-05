import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import test from "node:test"

const rootDir = resolve(import.meta.dirname, "..")

function readProjectFile(path) {
  return readFileSync(resolve(rootDir, path), "utf8")
}

test("auth responses expose safe access-key quota metadata", () => {
  const typeSource = readProjectFile("lib/api/types.ts")
  const hookSource = readProjectFile("hooks/use-auth.ts")

  assert.match(typeSource, /export interface AccessKeyQuota/)
  assert.match(typeSource, /remaining_uses: number/)
  assert.match(typeSource, /can_create_session: boolean/)
  assert.match(typeSource, /access_key_quota\?: AccessKeyQuota \| null/)
  assert.match(hookSource, /setAccessKeyQuota\(response\.access_key_quota \?\? null\)/)
  assert.match(hookSource, /setAccessKeyQuota\(status\.access_key_quota \?\? null\)/)
})

test("visa selector confirms before creating an account-consuming session", () => {
  const source = readProjectFile("components/ds160/visa-selector.tsx")

  assert.match(source, /确认创建新的面签会话/)
  assert.match(source, /会消耗 1 次访问 key 创建额度/)
  assert.match(source, /创建额度：\{quotaLabel\}/)
  assert.match(source, /quotaBlocksSession \? "创建额度已用尽" : "开始模拟面签"/)
  assert.match(source, /setPendingVisa\(selectedVisa\)/)
  assert.match(source, /onSelect\(pendingVisa\)/)
})

test("workbench passes quota state to session creation UI", () => {
  const source = readProjectFile("app/page.tsx")

  assert.match(source, /const \{ userProfile, accessKeyQuota(?:, logout)? \} = useAuth\(\)/)
  assert.match(source, /accessKeyQuota=\{accessKeyQuota\}/)
})
