import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { test } from "node:test"

const root = process.cwd()
const read = (path) => readFileSync(join(root, path), "utf8")

test("mini program bridge builds upload page url with encoded contract params", () => {
  const source = read("lib/wx/miniprogram-bridge.ts")
  assert.match(source, /new URLSearchParams/)
  assert.match(source, /session_id: params\.sessionId/)
  assert.match(source, /ticket: params\.ticket/)
  assert.match(source, /api_base_url:/)
  assert.match(source, /\/pages\/upload\/index\?\$\{search\.toString\(\)\}/)
})

test("mini program bridge has safe non-mini-program fallback", () => {
  const source = read("lib/wx/miniprogram-bridge.ts")
  assert.match(source, /reason: "not_browser"/)
  assert.match(source, /reason: "not_miniprogram"/)
  assert.match(source, /reason: "bridge_unavailable"/)
  assert.match(source, /isInWeChatMiniProgram/)
  assert.match(source, /wxWindow\.__wxjs_environment === "miniprogram"/)
})

test("pending upload ticket return helper persists and clears the handoff", () => {
  const source = read("lib/wx/upload-return.ts")
  assert.match(source, /storePendingWxUploadTicket/)
  assert.match(source, /readPendingWxUploadTicket/)
  assert.match(source, /clearPendingWxUploadTicket/)
  assert.match(source, /upload_done/)
  assert.match(source, /upload_ticket/)
})
