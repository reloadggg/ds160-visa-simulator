import type {
  CaseBoardDelta,
  CaseClaim,
  CaseConflict,
  CaseEvidenceCard,
  CaseProofPoint,
  InterviewNextMove,
  UploadedMaterial,
} from "./api/types"

type MaterialUnderstandingStatusSource = Pick<
  Partial<UploadedMaterial>,
  "understanding_status" | "understanding_error" | "case_board_delta" | "feedback_message"
>

export interface CaseUnderstandingPresentation {
  source: "case_board" | "materials"
  claims: CaseClaim[]
  evidenceCards: CaseEvidenceCard[]
  proofPoints: CaseProofPoint[]
  conflicts: CaseConflict[]
  latestMaterialName: string | null
  latestMaterialStatusSource: MaterialUnderstandingStatusSource | null
  latestNextMove: InterviewNextMove | null
}

function hasCaseBoardState(caseBoard?: CaseBoardDelta | null): caseBoard is CaseBoardDelta {
  return Boolean(
    caseBoard &&
      ((caseBoard.claims?.length ?? 0) > 0 ||
        (caseBoard.evidence_cards?.length ?? 0) > 0 ||
        (caseBoard.open_proof_points?.length ?? 0) > 0 ||
        (caseBoard.conflicts?.length ?? 0) > 0 ||
        caseBoard.latest_material ||
        caseBoard.next_move),
  )
}

function latestMaterialFromMaterials(
  materials: UploadedMaterial[],
): UploadedMaterial | null {
  return (
    materials.find(
      (material) =>
        material.understanding_status ||
        material.claims?.length ||
        material.evidence_cards?.length,
    ) ?? null
  )
}

export function selectCaseUnderstandingPresentation(
  caseBoard: CaseBoardDelta | null | undefined,
  materials: UploadedMaterial[],
): CaseUnderstandingPresentation {
  if (hasCaseBoardState(caseBoard)) {
    const latestMaterial = caseBoard.latest_material ?? null
    return {
      source: "case_board",
      claims: caseBoard.claims ?? [],
      evidenceCards: caseBoard.evidence_cards ?? [],
      proofPoints: caseBoard.open_proof_points ?? [],
      conflicts: caseBoard.conflicts ?? [],
      latestMaterialName:
        latestMaterial?.document_type_label ??
        latestMaterial?.filename ??
        null,
      latestMaterialStatusSource: latestMaterial
        ? {
            case_board_delta: {
              latest_material: latestMaterial,
              evidence_cards: [],
              claims: [],
              open_proof_points: [],
              conflicts: [],
            },
          }
        : null,
      latestNextMove: caseBoard.next_move ?? null,
    }
  }

  const latestMaterial = latestMaterialFromMaterials(materials)
  return {
    source: "materials",
    claims: materials.flatMap((material) => material.claims ?? []),
    evidenceCards: materials.flatMap((material) => material.evidence_cards ?? []),
    proofPoints: materials.flatMap((material) => material.proof_points ?? []),
    conflicts: materials.flatMap((material) => material.conflicts ?? []),
    latestMaterialName: latestMaterial
      ? latestMaterial.document_type_label ?? latestMaterial.name
      : null,
    latestMaterialStatusSource: latestMaterial,
    latestNextMove:
      latestMaterial?.next_move ??
      latestMaterial?.case_board_delta?.next_move ??
      null,
  }
}
