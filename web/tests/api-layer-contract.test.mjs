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

const apiConfig = loadTypeScriptModule("lib/api/config.ts")
const mappers = loadTypeScriptModule("lib/api/mappers.ts", {
  "./config": apiConfig,
})
const presentation = loadTypeScriptModule(
  "lib/case-board-presentation-policy.ts",
)
const clientSource = readFileSync(resolve(rootDir, "lib/api/client.ts"), "utf8")
const typesSource = readFileSync(resolve(rootDir, "lib/api/types.ts"), "utf8")
const indexSource = readFileSync(resolve(rootDir, "lib/api/index.ts"), "utf8")

test("listSessionDocuments client targets public documents path (F0)", () => {
  assert.match(clientSource, /export async function listSessionDocuments/)
  assert.match(
    clientSource,
    /buildApiUrl\(`\/v1\/sessions\/\$\{sessionId\}\/documents`\)/,
  )
  assert.match(clientSource, /mapSessionDocumentListResponse/)
  assert.match(clientSource, /export async function deleteSessionDocument/)
  assert.match(
    clientSource,
    /method:\s*"DELETE"/,
  )
  assert.match(typesSource, /export interface SessionDocumentListItem/)
  assert.match(typesSource, /export interface SessionDocumentListResponse/)
  assert.match(indexSource, /export \* from "\.\/client"/)
  assert.match(indexSource, /export \* from "\.\/mappers"/)
})

test("rewriteBackendContentUrl prefixes /v1 under API base (F8)", () => {
  const previous = process.env.NEXT_PUBLIC_API_BASE_URL
  try {
    process.env.NEXT_PUBLIC_API_BASE_URL = "/api"
    const rewritten = apiConfig.rewriteBackendContentUrl(
      "/v1/sessions/sess-1/files/doc-1/content",
    )
    assert.equal(rewritten, "/api/v1/sessions/sess-1/files/doc-1/content")

    const absolute = apiConfig.rewriteBackendContentUrl(
      "https://cdn.example.com/doc-1",
    )
    assert.equal(absolute, "https://cdn.example.com/doc-1")

    const alreadyPrefixed = apiConfig.rewriteBackendContentUrl(
      "/api/v1/sessions/sess-1/files/doc-1/content",
    )
    assert.equal(
      alreadyPrefixed,
      "/api/v1/sessions/sess-1/files/doc-1/content",
    )

    const fallback = apiConfig.rewriteBackendContentUrl(null, {
      sessionId: "sess-1",
      documentId: "doc-1",
    })
    assert.equal(fallback, "/api/v1/sessions/sess-1/files/doc-1/content")
  } finally {
    if (previous === undefined) {
      delete process.env.NEXT_PUBLIC_API_BASE_URL
    } else {
      process.env.NEXT_PUBLIC_API_BASE_URL = previous
    }
  }
})

test("mapFileUploadResponse rewrites content_url (F8)", () => {
  const previous = process.env.NEXT_PUBLIC_API_BASE_URL
  try {
    process.env.NEXT_PUBLIC_API_BASE_URL = "/api"
    const mapped = mappers.mapFileUploadResponse(
      {
        document_id: "doc-i20",
        content_url: "/v1/sessions/sess-1/files/doc-i20/content",
        document_status: "uploaded",
        document_type_candidates: [],
        supported_claims: [],
        evidence_cards: [],
        requested_documents: [],
        remaining_required_documents: [],
      },
      "sess-1",
    )
    assert.equal(
      mapped.content_url,
      "/api/v1/sessions/sess-1/files/doc-i20/content",
    )
  } finally {
    if (previous === undefined) {
      delete process.env.NEXT_PUBLIC_API_BASE_URL
    } else {
      process.env.NEXT_PUBLIC_API_BASE_URL = previous
    }
  }
})

test("mapSessionDocumentList rewrites content_url and maps materials (F0/F8)", () => {
  const previous = process.env.NEXT_PUBLIC_API_BASE_URL
  try {
    process.env.NEXT_PUBLIC_API_BASE_URL = "/api"
    const list = mappers.mapSessionDocumentListResponse({
      session_id: "sess-1",
      count: 2,
      documents: [
        {
          document_id: "doc-i20",
          filename: "i20.pdf",
          status: "parsed",
          understanding_status: "completed",
          document_type: "i20",
          uploaded_at: "2026-07-01T00:00:00Z",
          content_url: "/v1/sessions/sess-1/files/doc-i20/content",
          case_board_delta: {
            latest_material: { document_id: "doc-i20" },
            claim_count: 1,
            evidence_card_count: 2,
          },
          tombstoned: false,
        },
        {
          document_id: "doc-gone",
          filename: "old.pdf",
          status: "tombstoned",
          understanding_status: null,
          document_type: null,
          content_url: "/v1/sessions/sess-1/files/doc-gone/content",
          tombstoned: true,
        },
      ],
    })

    assert.equal(list.session_id, "sess-1")
    assert.equal(list.count, 2)
    assert.equal(
      list.documents[0].content_url,
      "/api/v1/sessions/sess-1/files/doc-i20/content",
    )
    assert.equal(list.documents[0].document_type_label, "I-20 表格")
    assert.equal(list.documents[1].tombstoned, true)

    const materials = mappers.mapSessionDocumentsToUploadedMaterials(list)
    assert.equal(materials.length, 1)
    assert.equal(materials[0].document_id, "doc-i20")
    assert.equal(materials[0].name, "i20.pdf")
    assert.equal(materials[0].kind, "pdf")
    assert.equal(
      materials[0].content_url,
      "/api/v1/sessions/sess-1/files/doc-i20/content",
    )
  } finally {
    if (previous === undefined) {
      delete process.env.NEXT_PUBLIC_API_BASE_URL
    } else {
      process.env.NEXT_PUBLIC_API_BASE_URL = previous
    }
  }
})

test("humanizeBackendText does not corrupt free prose (F9)", () => {
  const prose = "Please review your I-20"
  assert.equal(mappers.humanizeBackendText(prose), prose)

  const interviewProse =
    "The interviewer may continue the interview after document review."
  assert.equal(mappers.humanizeBackendText(interviewProse), interviewProse)

  assert.equal(mappers.humanizeBackendText("funding_proof"), "资金证明")
  assert.equal(
    mappers.humanizeBackendText("continue_interview"),
    "继续面签问答",
  )
  assert.equal(mappers.humanizeBackendText("i20"), "I-20 表格")
  // Idempotent: already-mapped Chinese label stays put
  assert.equal(mappers.humanizeBackendText("资金证明"), "资金证明")
  assert.equal(
    mappers.humanizeBackendText("继续面签问答"),
    "继续面签问答",
  )

  // Whole-token snake_case inside a sentence may map; plain English does not.
  const mixed = "Need funding_proof before formal interview"
  const humanizedMixed = mappers.humanizeBackendText(mixed)
  assert.match(humanizedMixed, /资金证明/)
  assert.doesNotMatch(humanizedMixed, /面签问答/) // "formal interview" is multi-word phrase only exact
  assert.ok(humanizedMixed.includes("formal interview") || humanizedMixed.includes("正式问答"))
})

test("relaxed proof/evidence mappers keep partial rows (F10)", () => {
  const mapped = mappers.mapFileUploadResponse({
    document_id: "doc-1",
    document_type_candidates: [],
    supported_claims: [],
    evidence_cards: [
      {
        evidence_id: "ev-empty-excerpt",
        excerpt: "",
        claim_refs: ["claim-1"],
      },
    ],
    case_board_delta: {
      evidence_cards: [
        {
          evidence_id: "ev-empty-excerpt",
          excerpt: "",
          claim_refs: ["claim-1"],
        },
      ],
      claims: [],
      open_proof_points: [
        {
          proof_point_id: "pp-1",
          question: "Who pays tuition?",
          why_it_matters: "",
          status: "open",
        },
      ],
      conflicts: [],
    },
    requested_documents: [],
    remaining_required_documents: [],
  })

  assert.equal(mapped.evidence_cards.length, 1)
  assert.equal(mapped.evidence_cards[0].excerpt, "")
  assert.equal(mapped.case_board_delta.open_proof_points.length, 1)
  assert.equal(
    mapped.case_board_delta.open_proof_points[0].why_it_matters,
    "",
  )
  assert.equal(
    mapped.case_board_delta.open_proof_points[0].question,
    "Who pays tuition?",
  )
})

test("case board mapper emits dual proof fields (F12)", () => {
  const fromOpen = mappers.mapFileUploadResponse({
    document_id: "doc-1",
    document_type_candidates: [],
    supported_claims: [],
    evidence_cards: [],
    case_board_delta: {
      evidence_cards: [],
      claims: [],
      open_proof_points: [
        {
          proof_point_id: "pp-open",
          question: "Why this school?",
          why_it_matters: "Intent",
        },
      ],
      conflicts: [],
    },
    requested_documents: [],
    remaining_required_documents: [],
  })

  assert.deepEqual(
    fromOpen.case_board_delta.open_proof_points.map((p) => p.proof_point_id),
    ["pp-open"],
  )
  assert.deepEqual(
    fromOpen.case_board_delta.proof_points.map((p) => p.proof_point_id),
    ["pp-open"],
  )

  const fromProof = mappers.mapUserReport({
    session_id: "sess-1",
    interview_status: "continue_interview",
    case_board: {
      evidence_cards: [],
      claims: [],
      // backend may send proof_points only
      proof_points: [
        {
          proof_point_id: "pp-backend",
          question: "Funding source?",
          why_it_matters: "Ties",
        },
      ],
      conflicts: [],
    },
  })

  assert.deepEqual(
    fromProof.case_board.open_proof_points.map((p) => p.proof_point_id),
    ["pp-backend"],
  )
  assert.deepEqual(
    fromProof.case_board.proof_points.map((p) => p.proof_point_id),
    ["pp-backend"],
  )

  // Presentation reads open_proof_points ?? proof_points
  const presentationFromProofOnly = presentation.selectCaseUnderstandingPresentation(
    {
      latest_material: null,
      evidence_cards: [],
      claims: [],
      open_proof_points: undefined,
      proof_points: [
        {
          proof_point_id: "pp-only",
          question: "Intent?",
          why_it_matters: "",
          status: "open",
          claim_refs: [],
          evidence_refs: [],
        },
      ],
      conflicts: [],
    },
    [],
  )
  assert.equal(presentationFromProofOnly.source, "case_board")
  assert.deepEqual(
    presentationFromProofOnly.proofPoints.map((p) => p.proof_point_id),
    ["pp-only"],
  )
})

test("material package document content_url is rewritten (F8)", () => {
  const previous = process.env.NEXT_PUBLIC_API_BASE_URL
  try {
    process.env.NEXT_PUBLIC_API_BASE_URL = "/api"
    const mapped = mappers.mapMaterialPackageDocument(
      {
        document_id: "doc-pkg",
        filename: "i20.pdf",
        content_url: "/v1/sessions/sess-x/files/doc-pkg/content",
      },
      "sess-x",
    )
    assert.equal(
      mapped.content_url,
      "/api/v1/sessions/sess-x/files/doc-pkg/content",
    )
  } finally {
    if (previous === undefined) {
      delete process.env.NEXT_PUBLIC_API_BASE_URL
    } else {
      process.env.NEXT_PUBLIC_API_BASE_URL = previous
    }
  }
})
