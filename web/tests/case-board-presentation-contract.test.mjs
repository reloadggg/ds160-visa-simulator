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

const policy = loadTypeScriptModule("lib/case-board-presentation-policy.ts")
const mockData = loadTypeScriptModule("lib/api/mock-data.ts", {
  "./config": {
    isMockModeEnabled: () => true,
  },
  "./mappers": {
    getMockRequiredDocuments: () => [],
    toDocumentLabel: (value) => value,
  },
})

test("report case board is the primary analysis source after refresh", () => {
  const caseBoard = {
    latest_material: {
      filename: "i20.pdf",
      understanding_status: "completed",
    },
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
    evidence_cards: [
      {
        evidence_id: "ev-school",
        excerpt: "School Name: Example University",
        claim_refs: ["claim-school"],
      },
    ],
    open_proof_points: [],
    conflicts: [],
    next_move: {
      move_type: "ask",
      question: "Why did you choose Example University?",
      reason: "The school is documented.",
      claim_refs: ["claim-school"],
      evidence_refs: ["ev-school"],
    },
  }
  const staleMaterials = [
    {
      name: "old-bank.pdf",
      claims: [
        {
          claim_id: "claim-stale",
          field_path: "/funding/primary_source",
          value: "parents",
          status: "documented",
          supporting_evidence_ids: [],
          conflicting_evidence_ids: [],
        },
      ],
      evidence_cards: [],
      proof_points: [],
      conflicts: [],
    },
  ]

  const presentation = policy.selectCaseUnderstandingPresentation(
    caseBoard,
    staleMaterials,
  )

  assert.equal(presentation.source, "case_board")
  assert.deepEqual(
    presentation.claims.map((claim) => claim.claim_id),
    ["claim-school"],
  )
  assert.equal(presentation.latestMaterialName, "i20.pdf")
  assert.equal(
    presentation.latestMaterialStatusSource.case_board_delta.latest_material
      .understanding_status,
    "completed",
  )
  assert.equal(
    presentation.latestNextMove.question,
    "Why did you choose Example University?",
  )
})

test("materials are only the fallback when report case board is empty", () => {
  const materials = [
    {
      name: "funding.pdf",
      understanding_status: "completed",
      claims: [
        {
          claim_id: "claim-funding",
          field_path: "/funding/primary_source",
          value: "parents",
          status: "documented",
          supporting_evidence_ids: [],
          conflicting_evidence_ids: [],
        },
      ],
      evidence_cards: [],
      proof_points: [],
      conflicts: [],
    },
  ]

  const presentation = policy.selectCaseUnderstandingPresentation(null, materials)

  assert.equal(presentation.source, "materials")
  assert.deepEqual(
    presentation.claims.map((claim) => claim.claim_id),
    ["claim-funding"],
  )
  assert.equal(presentation.latestMaterialName, "funding.pdf")
})

test("material fallback treats the first material as the latest upload", () => {
  const materials = [
    {
      name: "latest-i20.pdf",
      understanding_status: "completed",
      claims: [
        {
          claim_id: "claim-latest",
          field_path: "/education/school_name",
          value: "Latest University",
          status: "documented",
          supporting_evidence_ids: [],
          conflicting_evidence_ids: [],
        },
      ],
      evidence_cards: [],
      proof_points: [],
      conflicts: [],
    },
    {
      name: "older-bank.pdf",
      understanding_status: "completed",
      claims: [
        {
          claim_id: "claim-older",
          field_path: "/funding/primary_source",
          value: "parents",
          status: "documented",
          supporting_evidence_ids: [],
          conflicting_evidence_ids: [],
        },
      ],
      evidence_cards: [],
      proof_points: [],
      conflicts: [],
    },
  ]

  const presentation = policy.selectCaseUnderstandingPresentation(null, materials)

  assert.equal(presentation.source, "materials")
  assert.equal(presentation.latestMaterialName, "latest-i20.pdf")
  assert.deepEqual(
    presentation.claims.map((claim) => claim.claim_id),
    ["claim-latest", "claim-older"],
  )
})

test("mock data keeps the demo transcript interview-only", () => {
  assert.ok(mockData.MOCK_MESSAGES.length > 0)
  assert.equal(
    mockData.MOCK_MESSAGES.some((message) => message.role === "system"),
    false,
  )

  const transcriptText = mockData.MOCK_MESSAGES.map((message) => message.content).join("\n")
  assert.doesNotMatch(transcriptText, /请准备以下材料/)
  assert.doesNotMatch(transcriptText, /材料清单/)
})

test("mock report is driven by a resolvable case board", () => {
  const report = mockData.MOCK_USER_REPORT
  const caseBoard = report.case_board
  assert.ok(caseBoard)
  assert.notEqual(report.interview_status, "waiting_key_proof")
  assert.deepEqual(report.requested_documents, [])
  assert.ok(caseBoard.claims.length >= 3)
  assert.ok(caseBoard.evidence_cards.length >= 2)

  const claimIds = new Set(caseBoard.claims.map((claim) => claim.claim_id))
  const evidenceIds = new Set(
    caseBoard.evidence_cards.map((evidence) => evidence.evidence_id),
  )
  for (const claimId of caseBoard.next_move?.claim_refs ?? []) {
    assert.equal(claimIds.has(claimId), true)
  }
  for (const evidenceId of caseBoard.next_move?.evidence_refs ?? []) {
    assert.equal(evidenceIds.has(evidenceId), true)
  }

  const openProofPointIds = new Set(
    caseBoard.open_proof_points
      .filter((proofPoint) => proofPoint.status !== "resolved")
      .map((proofPoint) => proofPoint.proof_point_id),
  )
  assert.deepEqual(
    report.missing_evidence.map((item) => item.id),
    Array.from(openProofPointIds),
  )
})

test("mock internal report does not describe a document request as the main path", () => {
  const traceText = JSON.stringify(mockData.MOCK_INTERNAL_REPORT.runtime_trace)
  const viewState = mockData.MOCK_INTERNAL_REPORT.runtime_view_state

  assert.doesNotMatch(traceText, /requested_documents=1/)
  assert.deepEqual(viewState.requested_documents, [])
  assert.equal(viewState.case_board, mockData.MOCK_CASE_BOARD)
})

test("user-facing compatibility copy avoids key-proof checklist framing", () => {
  const source = [
    "lib/api/mappers.ts",
    "hooks/use-session-workbench.ts",
    "components/ds160/analysis-panel.tsx",
    "components/ds160/report-modal.tsx",
  ]
    .map((relativePath) => readFileSync(resolve(rootDir, relativePath), "utf8"))
    .join("\n")

  assert.doesNotMatch(source, /关键证明/)
  assert.doesNotMatch(source, /缺少关键证明材料/)
  assert.doesNotMatch(source, /薄弱证明点/)
  assert.doesNotMatch(source, /请准备以下材料/)
})
