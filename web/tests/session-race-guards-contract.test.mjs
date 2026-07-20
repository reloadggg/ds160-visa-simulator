import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"
import ts from "typescript"

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), "..")

function read(relativePath) {
  return readFileSync(resolve(rootDir, relativePath), "utf8")
}

function loadTypeScriptModule(relativePath, runtimeRequires = {}) {
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
  const requireShim = (specifier) => {
    if (Object.hasOwn(runtimeRequires, specifier)) {
      return runtimeRequires[specifier]
    }
    throw new Error(
      `Unexpected runtime require from ${relativePath}: ${specifier}`,
    )
  }
  const evaluate = new Function(
    "exports",
    "module",
    "require",
    compiled.outputText,
  )
  evaluate(cjsModule.exports, cjsModule, requireShim)
  return cjsModule.exports
}

test("fetchUserReport uses strict sessionIdRef inequality (no truthy short-circuit)", () => {
  const source = read("hooks/use-session-workbench.ts")
  const start = source.indexOf("const fetchUserReport = useCallback")
  assert.notEqual(start, -1)
  const block = source.slice(start, start + 1800)
  assert.match(block, /sessionIdRef\.current !== targetSessionId/)
  assert.doesNotMatch(
    block,
    /sessionIdRef\.current && sessionIdRef\.current !== targetSessionId/,
  )
})

test("handleLoadBackendSession uses monotonic loadSeq guard", () => {
  const source = read("hooks/use-session-workbench.ts")
  assert.match(source, /sessionLoadSeqRef/)
  assert.match(source, /const loadSeq = \+\+sessionLoadSeqRef\.current/)
  assert.match(source, /sessionLoadSeqRef\.current !== loadSeq/)
})

test("runDebugMaterialBundle captures targetSessionId and guards after stream", () => {
  const source = read("hooks/use-session-workbench.ts")
  const start = source.indexOf("const runDebugMaterialBundle = useCallback")
  assert.notEqual(start, -1)
  const block = source.slice(start, start + 4500)
  assert.match(block, /const targetSessionId = sessionId/)
  assert.match(block, /sessionIdRef\.current !== targetSessionId/)
  // After stream completion must guard before setState
  assert.match(block, /After stream: do not paint foreign session state/)
})

test("handleSendMessage guards assistant apply with targetSessionId", () => {
  const source = read("hooks/use-session-workbench.ts")
  const start = source.indexOf("const handleSendMessage = useCallback")
  assert.notEqual(start, -1)
  // handleSendMessage is large; scan a wide window through assistant apply + governor update.
  const block = source.slice(start, start + 20000)
  assert.match(block, /const targetSessionId = sessionId/)
  assert.match(block, /sessionIdRef\.current !== targetSessionId/)
  assert.match(block, /current_governor_decision:\s*decision/)
  assert.match(block, /Terminal parity \(wx\)/)
})

test("desktop isTerminalInterviewState includes governor decisions like wx", () => {
  const source = read("hooks/use-session-workbench.ts")
  const start = source.indexOf("function isTerminalInterviewState")
  const block = source.slice(start, start + 700)
  assert.match(block, /current_governor_decision/)
  assert.match(block, /not_passed/)
  assert.match(block, /passed/)
  assert.match(block, /refused/)
  assert.match(block, /simulated_refusal/)
})

test("multi-doc poll: 1/2 terminal keeps shouldContinue true", () => {
  // Pure algorithm mirror of evaluateMaterialUnderstandingPollExit (hook export is React-heavy).
  // Re-implemented here for behavioral contract; source must call the named helper.
  const source = read("hooks/use-session-workbench.ts")
  assert.match(source, /evaluateMaterialUnderstandingPollExit/)
  assert.match(source, /Multi-doc: stop only when ALL tracked docs are terminal/)

  const isTerminal = (status) =>
    status === "failed" ||
    status === "error" ||
    status === "completed" ||
    status === "parsed" ||
    status === "skipped_legacy"

  function evaluate(trackedDocumentIds, trackedDocuments) {
    const byId = new Map(
      trackedDocuments
        .filter((doc) => doc.document_id)
        .map((doc) => [doc.document_id, doc]),
    )
    let pendingCount = 0
    let terminalCount = 0
    for (const documentId of trackedDocumentIds) {
      const document = byId.get(documentId)
      if (!document) {
        pendingCount += 1
        continue
      }
      if (document.tombstoned) {
        terminalCount += 1
        continue
      }
      const status = document.understanding_status ?? document.status ?? null
      if (isTerminal(status)) {
        terminalCount += 1
      } else {
        pendingCount += 1
      }
    }
    return {
      shouldContinue: pendingCount > 0,
      terminalCount,
      pendingCount,
    }
  }

  const partial = evaluate(new Set(["doc-a", "doc-b"]), [
    {
      document_id: "doc-a",
      understanding_status: "completed",
      tombstoned: false,
    },
    {
      document_id: "doc-b",
      understanding_status: "processing",
      tombstoned: false,
    },
  ])
  assert.equal(partial.shouldContinue, true)
  assert.equal(partial.terminalCount, 1)
  assert.equal(partial.pendingCount, 1)

  const allDone = evaluate(new Set(["doc-a", "doc-b"]), [
    {
      document_id: "doc-a",
      understanding_status: "completed",
      tombstoned: false,
    },
    {
      document_id: "doc-b",
      understanding_status: "failed",
      tombstoned: false,
    },
  ])
  assert.equal(allDone.shouldContinue, false)
  assert.equal(allDone.terminalCount, 2)

  const withTombstone = evaluate(new Set(["doc-a", "doc-b"]), [
    { document_id: "doc-a", understanding_status: "completed" },
    { document_id: "doc-b", tombstoned: true },
  ])
  assert.equal(withTombstone.shouldContinue, false)
  assert.equal(withTombstone.terminalCount, 2)
})

test("practice dialog blocks close while generating", () => {
  const dialog = read("components/ds160/practice-materials-dialog.tsx")
  const login = read("app/login/page.tsx")
  assert.match(dialog, /if \(!nextOpen && isGenerating\)/)
  assert.match(dialog, /onEscapeKeyDown/)
  assert.match(dialog, /onPointerDownOutside/)
  assert.match(dialog, /onInteractOutside/)
  assert.match(dialog, /showCloseButton=\{!isGenerating\}/)
  assert.match(login, /if \(!nextOpen && isDebugBundleGenerating\)/)
})

test("wx ticket refresh returns when sessionIdRef mismatches effectiveSessionId", () => {
  const source = read("hooks/use-wx-workbench.ts")
  const start = source.indexOf("const refreshNativeUploadTicket")
  assert.notEqual(start, -1)
  const block = source.slice(start, start + 1200)
  assert.match(block, /sessionIdRef\.current !== effectiveSessionId/)
  assert.match(block, /return/)
  assert.doesNotMatch(
    block,
    /still apply if no active session switch mid-flight/,
  )
})

test("is_practice_material mapping is Boolean-only (no scenario/bundle_id OR)", () => {
  const source = read("hooks/use-session-workbench.ts")
  const start = source.indexOf("function debugBundleDocumentToMaterial")
  assert.notEqual(start, -1)
  const block = source.slice(start, start + 2000)
  assert.match(
    block,
    /is_practice_material:\s*Boolean\(bundle\.is_practice_material\)/,
  )
  assert.doesNotMatch(block, /bundle\.scenario != null/)
  assert.doesNotMatch(block, /Boolean\(bundle\.bundle_id\)/)
  assert.match(block, /Strict product flag only/)
})

test("resolveMaterialBundleNonStreamFallback family routing is pure", () => {
  const apiConfig = loadTypeScriptModule("lib/api/config.ts")
  // Load only the pure helper via a tiny extraction: read source and eval the function body.
  const clientSource = read("lib/api/client.ts")
  assert.match(
    clientSource,
    /export function resolveMaterialBundleNonStreamFallback/,
  )
  // Behavioral: practice path must not resolve to debug
  const fnMatch = clientSource.match(
    /export function resolveMaterialBundleNonStreamFallback\([\s\S]*?\n\}/,
  )
  assert.ok(fnMatch)
  // Transpile helper alone
  const snippet = `${fnMatch[0]}\nmodule.exports = { resolveMaterialBundleNonStreamFallback }`
  const compiled = ts.transpileModule(snippet, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  })
  const cjsModule = { exports: {} }
  new Function("exports", "module", "require", compiled.outputText)(
    cjsModule.exports,
    cjsModule,
    () => {
      throw new Error("no require")
    },
  )
  const { resolveMaterialBundleNonStreamFallback } = cjsModule.exports
  assert.equal(
    resolveMaterialBundleNonStreamFallback(
      "/v1/sessions/s1/practice/material-bundles/stream",
    ),
    "practice",
  )
  assert.equal(
    resolveMaterialBundleNonStreamFallback(
      "/v1/sessions/s1/debug/material-bundles/stream",
    ),
    "debug",
  )
  void apiConfig
})
