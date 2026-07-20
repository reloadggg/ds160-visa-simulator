import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"
import ts from "typescript"

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), "..")

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
    throw new Error(`Unexpected runtime require from ${relativePath}: ${specifier}`)
  }
  const evaluate = new Function("exports", "module", "require", compiled.outputText)
  evaluate(cjsModule.exports, cjsModule, requireShim)
  return cjsModule.exports
}

const apiConfig = loadTypeScriptModule("lib/api/config.ts")
const policy = loadTypeScriptModule("lib/upload-feedback-policy.ts")
const mappers = loadTypeScriptModule("lib/api/mappers.ts", {
  "./config": apiConfig,
})

test("file upload mapper exposes case board refresh as frontend contract", () => {
  const mapped = mappers.mapFileUploadResponse({
    document_id: "doc-i20",
    document_status: "uploaded",
    job_id: "job-i20",
    job_status: "queued",
    understanding_status: "queued",
    document_type_candidates: [],
    supported_claims: [],
    evidence_cards: [],
    requested_documents: [],
    remaining_required_documents: [],
    case_board_refresh: {
      event_type: "material_uploaded",
      document_id: "doc-i20",
      status: "queued",
      understanding_status: "queued",
      failure_node: null,
      failure_message: null,
      debug_timeline_scope: {
        session_id: "sess-i20",
        document_id: "doc-i20",
        scope: "material_understanding",
      },
      message_policy: "case_board_timeline_only",
    },
  })

  assert.deepEqual(mapped.caseBoardRefresh, {
    eventType: "material_uploaded",
    documentId: "doc-i20",
    status: "queued",
    understandingStatus: "queued",
    failureNode: null,
    failureMessage: null,
    debugTimelineScope: {
      session_id: "sess-i20",
      document_id: "doc-i20",
      scope: "material_understanding",
    },
    messagePolicy: "case_board_timeline_only",
  })
})

test("message mapper does not promote global missing docs into current upload request", () => {
  const mapped = mappers.mapMessageResponse({
    assistant_message: "Let's continue with your study plan.",
    governor_decision: "continue_interview",
    requested_documents: [],
    remaining_required_documents: ["funding_proof"],
    runtime_view_state: {
      decision: "continue_interview",
      current_focus: {
        kind: "interview_question",
      },
      requested_documents: [],
      remaining_required_documents: ["funding_proof"],
      advisory_context: {
        missing_evidence: ["funding_proof"],
      },
    },
  })

  assert.deepEqual(mapped.requested_documents, [])
  assert.deepEqual(mapped.requested_document_labels, [])
  assert.deepEqual(mapped.remaining_required_documents, ["funding_proof"])
  assert.deepEqual(mapped.remaining_required_document_labels, ["资金证明"])
})

test("message mapper preserves explicit document request when backend asks for evidence", () => {
  const mapped = mappers.mapMessageResponse({
    assistant_message: "Please upload proof of funding.",
    governor_decision: "need_more_evidence",
    requested_documents: ["funding_proof"],
    remaining_required_documents: ["funding_proof"],
  })

  assert.deepEqual(mapped.requested_documents, ["funding_proof"])
  assert.deepEqual(mapped.requested_document_labels, ["资金证明"])
  assert.deepEqual(mapped.remaining_required_documents, ["funding_proof"])
})

test("failed material understanding becomes an error activity", () => {
  const response = {
    understanding_status: "failed",
    case_board_delta: {
      latest_material: {
        understanding_status: "failed",
        unknowns: ["RuntimeError before material understanding."],
      },
    },
  }

  assert.equal(policy.isMaterialUnderstandingFailed(response), true)
  assert.deepEqual(
    policy.buildMaterialUnderstandingActivity("broken.pdf", response),
    {
      content:
        "材料理解失败：broken.pdf。RuntimeError before material understanding.",
      status: "error",
    },
  )
})

test("case board refresh drives upload activity without transcript copy", () => {
  const response = {
    caseBoardRefresh: {
      eventType: "material_uploaded",
      documentId: "doc-broken",
      status: "queued",
      understandingStatus: "failed",
      failureNode: "parse_failed",
      failureMessage: "Failed to open stream",
      messagePolicy: "case_board_timeline_only",
    },
  }

  assert.equal(policy.isMaterialUnderstandingFailed(response), true)
  assert.deepEqual(
    policy.buildMaterialUnderstandingActivity("broken.pdf", response),
    {
      content: "材料理解失败：broken.pdf。Failed to open stream",
      status: "error",
    },
  )
})

test("queued material understanding stays outside transcript as progress", () => {
  const response = {
    understanding_status: "queued",
    case_board_delta: {
      latest_material: {
        understanding_status: "queued",
      },
    },
  }

  assert.equal(policy.isMaterialUnderstandingFailed(response), false)
  assert.deepEqual(
    policy.buildMaterialUnderstandingActivity("i20.pdf", response),
    {
      content: "i20.pdf 已收到，案例理解正在更新，可以继续对话。",
      status: "sending",
    },
  )
})

test("understanding_error alone while queued is not treated as failed (F16)", () => {
  const response = {
    understanding_status: "queued",
    understanding_error: {
      code: "stale_retry",
      message: "previous attempt failed",
    },
    case_board_delta: {
      latest_material: {
        understanding_status: "processing",
        understanding_error: {
          code: "stale_retry",
          message: "previous attempt failed",
        },
      },
    },
    caseBoardRefresh: {
      failureMessage: "previous attempt failed",
      understandingStatus: "processing",
    },
  }

  assert.equal(policy.isMaterialUnderstandingFailed(response), false)
  assert.deepEqual(
    policy.buildMaterialUnderstandingActivity("i20.pdf", response),
    {
      content: "i20.pdf 已收到，案例理解正在更新，可以继续对话。",
      status: "sending",
    },
  )
})

test("upload-only summary counts evidence from case board delta", () => {
  const summary = policy.buildUploadOnlyMaterialActivitySummary([
    {
      document_type_label: "I-20 表格",
      evidence_cards: [],
      case_board_delta: {
        latest_material: {
          understanding_status: "completed",
        },
        evidence_cards: [
          {
            evidence_id: "ev-school",
            excerpt: "School: Example University",
            claim_refs: ["claim-school"],
          },
        ],
        claims: [
          {
            claim_id: "claim-school",
            field_path: "/education/school_name",
            value: "Example University",
            status: "documented",
            supporting_evidence_ids: ["ev-school"],
            conflicting_evidence_ids: [],
          },
        ],
        open_proof_points: [],
        conflicts: [],
      },
      requested_documents: [],
      requested_document_labels: [],
      remaining_required_documents: [],
      remaining_required_document_labels: [],
    },
  ])

  assert.equal(
    summary,
    "材料已加入案例证据：I-20 表格，已形成 1 条证据片段、1 个候选事实。",
  )
})

test("runtime debug material understanding entries can update async upload state", () => {
  const patch = policy.buildMaterialUnderstandingPatchFromRuntimeEntry({
    document_id: "doc-broken",
    filename: "broken.pdf",
    understanding_status: "failed",
    understanding_error: {
      code: "parse_failed",
      message: "FileDataError before material understanding: Failed to open stream",
    },
    latest_material: {
      document_id: "doc-broken",
      filename: "broken.pdf",
      understanding_status: "failed",
    },
  })

  assert.deepEqual(patch, {
    document_id: "doc-broken",
    filename: "broken.pdf",
    understanding_status: "failed",
    understanding_error: {
      code: "parse_failed",
      message:
        "FileDataError before material understanding: Failed to open stream",
    },
  })
  assert.equal(policy.isTerminalMaterialUnderstandingStatus("failed"), true)
  assert.equal(policy.isTerminalMaterialUnderstandingStatus("queued"), false)
})

test("workbench upload branch appends activity instead of system transcript", () => {
  const hookSource = readFileSync(
    resolve(rootDir, "hooks/use-session-workbench.ts"),
    "utf8",
  )
  const uploadActivityStart = hookSource.indexOf(
    "const uploadActivity = buildMaterialUnderstandingActivity",
  )
  assert.notEqual(uploadActivityStart, -1)
  const uploadActivityEnd = hookSource.indexOf(
    "const gateProgressMessage = buildGateProgressMessage",
    uploadActivityStart,
  )
  assert.notEqual(uploadActivityEnd, -1)
  const uploadActivityBranch = hookSource.slice(
    uploadActivityStart,
    uploadActivityEnd,
  )

  assert.match(uploadActivityBranch, /appendActivityEvent/)
  assert.doesNotMatch(uploadActivityBranch, /appendMessage\(/)
  assert.match(hookSource, /caseBoardRefresh/)
  assert.match(hookSource, /queueMaterialUnderstandingRefresh\(sessionId, \[response\]\)/)
  assert.match(
    hookSource,
    /syncUploadedMaterialsFromRuntimeDebugSnapshot\(snapshot\)/,
  )
})

test("workbench current-turn upload feedback is driven only by requested documents", () => {
  const hookSource = readFileSync(
    resolve(rootDir, "hooks/use-session-workbench.ts"),
    "utf8",
  )
  const evidenceMessageStart = hookSource.indexOf(
    "const requestedDocumentsMessage = buildEvidenceSuggestionMessage",
  )
  assert.notEqual(evidenceMessageStart, -1)
  const evidenceMessageEnd = hookSource.indexOf(
    "const gateProgressMessage = buildGateProgressMessage",
    evidenceMessageStart,
  )
  assert.notEqual(evidenceMessageEnd, -1)
  const evidenceMessageBranch = hookSource.slice(
    evidenceMessageStart,
    evidenceMessageEnd,
  )

  assert.match(evidenceMessageBranch, /response\.requested_document_labels/)
  assert.match(evidenceMessageBranch, /response\.governor_decision/)
  assert.doesNotMatch(
    evidenceMessageBranch,
    /remaining_required_document_labels|remaining_required_documents/,
  )
})
