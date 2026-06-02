import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"
import ts from "typescript"

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), "..")

function loadTypeScriptModule(relativePath) {
  const filename = resolve(rootDir, relativePath)
  const source = readFileSync(filename, "utf8")
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
    fileName: filename,
  })
  const cjsModule = { exports: {} }
  const evaluate = new Function("exports", "module", compiled.outputText)
  evaluate(cjsModule.exports, cjsModule)
  return cjsModule.exports
}

const policy = loadTypeScriptModule("lib/message-source-policy.ts")

test("message stream events never append transcript messages directly", () => {
  const backendFinal = {
    assistant_message: "请说明你的学习计划如何支持毕业后的职业安排。",
    public_reasoning: { basis: "case_memory" },
  }
  const events = [
    { event: "accepted", data: { session_id: "sess-test" } },
    { event: "analyzing", data: { stage: "interview_runtime" } },
    {
      event: "debug_event",
      data: {
        phase: "message_turn",
        step: "runtime",
        status: "completed",
      },
    },
    { event: "final", data: backendFinal },
  ]

  const transcriptDrafts = events.flatMap((event) =>
    policy.transcriptMessagesFromMessageStreamEvent(event),
  )
  assert.deepEqual(transcriptDrafts, [])

  const assistantMessage =
    policy.buildAssistantMessageFromBackendResponse(backendFinal)
  assert.deepEqual(assistantMessage, {
    role: "assistant",
    content: backendFinal.assistant_message,
    public_reasoning: backendFinal.public_reasoning,
  })
})

test("empty backend assistant text does not create a blank assistant bubble", () => {
  assert.equal(
    policy.buildAssistantMessageFromBackendResponse({
      assistant_message: "   ",
      public_reasoning: null,
    }),
    null,
  )
})

test("workbench final SSE branch is activity-only", () => {
  const hookSource = readFileSync(
    resolve(rootDir, "hooks/use-session-workbench.ts"),
    "utf8",
  )
  const finalBranchStart = hookSource.indexOf('if (event.event === "final")')
  assert.notEqual(finalBranchStart, -1)
  const finalBranchEnd = hookSource.indexOf(
    'if (event.event === "error")',
    finalBranchStart,
  )
  assert.notEqual(finalBranchEnd, -1)
  const finalBranch = hookSource.slice(finalBranchStart, finalBranchEnd)

  assert.match(finalBranch, /upsertStreamProgress/)
  assert.doesNotMatch(finalBranch, /appendMessage\(/)
  assert.match(
    hookSource,
    /buildAssistantMessageFromBackendResponse\(response\)/,
  )
})

test("message stream errors surface backend cause fields", () => {
  const typesSource = readFileSync(resolve(rootDir, "lib/api/types.ts"), "utf8")
  assert.match(typesSource, /interface MessageStreamErrorPayload/)
  assert.match(typesSource, /error_category\?: string/)
  assert.match(typesSource, /upstream_code\?: string \| null/)

  const hookSource = readFileSync(
    resolve(rootDir, "hooks/use-session-workbench.ts"),
    "utf8",
  )
  assert.match(hookSource, /function describeMessageStreamError/)
  assert.match(hookSource, /describeMessageStreamError\(event\.data\)/)
  assert.match(hookSource, /messageStreamErrorFromUnknown\(error\.data\)/)
  assert.match(hookSource, /上游模型请求超时/)
  assert.match(hookSource, /模型输出格式不符合要求/)
})

test("workbench can hydrate a backend session transcript by session id", () => {
  const clientSource = readFileSync(resolve(rootDir, "lib/api/client.ts"), "utf8")
  assert.match(clientSource, /function fetchSessionMessages/)
  assert.ok(
    clientSource.includes("`/v1/sessions/${sessionId}/messages`"),
  )

  const hookSource = readFileSync(
    resolve(rootDir, "hooks/use-session-workbench.ts"),
    "utf8",
  )
  assert.match(hookSource, /function chatMessageFromBackendTurn/)
  assert.match(hookSource, /handleLoadBackendSession/)
  assert.match(hookSource, /URLSearchParams\(window\.location\.search\)/)
  assert.match(hookSource, /params\.get\("session_id"\)/)
})

test("frontend transcript role uses assistant instead of officer", () => {
  const files = [
    "lib/api/types.ts",
    "lib/message-source-policy.ts",
    "lib/api/mock-data.ts",
    "hooks/use-session-workbench.ts",
    "components/ds160/chat-panel.tsx",
    "components/ds160/history-panel.tsx",
  ]

  for (const file of files) {
    const source = readFileSync(resolve(rootDir, file), "utf8")
    assert.doesNotMatch(source, /role:\s*["']officer["']/)
    if (file !== "hooks/use-session-workbench.ts") {
      assert.doesNotMatch(source, /role\s*===\s*["']officer["']/)
    }
  }

  const hookSource = readFileSync(
    resolve(rootDir, "hooks/use-session-workbench.ts"),
    "utf8",
  )
  const legacyRoleChecks = hookSource.match(/role\s*===\s*["']officer["']/g)
  assert.equal(
    legacyRoleChecks?.length ?? 0,
    1,
    "only legacy history hydration may read the old officer role",
  )
})
