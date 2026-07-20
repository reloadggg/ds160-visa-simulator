import type { FileUploadResponse, UploadedMaterial } from "./api/types"

export type MaterialUnderstandingActivity = {
  content: string
  status: "sending" | "sent" | "error"
}

export type RuntimeMaterialUnderstandingPatch = {
  document_id?: string | null
  filename?: string | null
  understanding_status?: string | null
  understanding_error?: { code?: string | null; message?: string | null } | null
}

type MaterialUnderstandingLike = Partial<
  Pick<
    FileUploadResponse | UploadedMaterial,
    | "understanding_status"
    | "case_board_delta"
    | "caseBoardRefresh"
    | "feedback_message"
  >
> & {
  understanding_error?: { code?: string | null; message?: string | null } | null
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null
  }
  return value as Record<string, unknown>
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : []
}

function materialUnderstandingErrorFromRuntime(
  value: unknown,
): RuntimeMaterialUnderstandingPatch["understanding_error"] {
  const error = asRecord(value)
  if (!error) {
    return null
  }
  const code = stringOrNull(error.code)
  const message = stringOrNull(error.message)
  if (!code && !message) {
    return null
  }
  return { code, message }
}

export function buildMaterialUnderstandingPatchFromRuntimeEntry(
  entry: Record<string, unknown>,
): RuntimeMaterialUnderstandingPatch | null {
  const latestMaterial = asRecord(entry.latest_material)
  const documentId =
    stringOrNull(entry.document_id) ?? stringOrNull(latestMaterial?.document_id)
  const filename =
    stringOrNull(entry.filename) ?? stringOrNull(latestMaterial?.filename)
  const status =
    stringOrNull(entry.understanding_status) ??
    stringOrNull(latestMaterial?.understanding_status)
  const runtimeError =
    materialUnderstandingErrorFromRuntime(entry.understanding_error) ??
    materialUnderstandingErrorFromRuntime(latestMaterial?.understanding_error)
  const unknownMessage = stringList(latestMaterial?.unknowns)[0] ?? null
  const error =
    runtimeError ??
    (unknownMessage ? { code: null, message: unknownMessage } : null)

  if (!documentId && !filename) {
    return null
  }
  if (!status && !error) {
    return null
  }

  return {
    document_id: documentId,
    filename,
    understanding_status: status,
    understanding_error: error,
  }
}

export function isTerminalMaterialUnderstandingStatus(
  status?: string | null,
): boolean {
  return (
    status === "failed" ||
    status === "error" ||
    status === "completed" ||
    status === "parsed" ||
    status === "skipped_legacy"
  )
}

export function materialUnderstandingStatus(
  item: MaterialUnderstandingLike,
): string | null {
  return (
    item.caseBoardRefresh?.understandingStatus ??
    item.case_board_delta?.latest_material?.understanding_status ??
    item.understanding_status ??
    null
  )
}

export function materialUnderstandingErrorMessage(
  item: MaterialUnderstandingLike,
): string | null {
  return (
    item.caseBoardRefresh?.failureMessage ??
    item.understanding_error?.message ??
    item.case_board_delta?.latest_material?.understanding_error?.message ??
    item.case_board_delta?.latest_material?.unknowns?.[0] ??
    item.feedback_message ??
    null
  )
}

/**
 * True only for terminal failure statuses.
 * Presence of understanding_error while still queued/processing is NOT failed.
 */
export function isMaterialUnderstandingFailed(
  item: MaterialUnderstandingLike,
): boolean {
  const status = materialUnderstandingStatus(item)
  return status === "failed" || status === "error"
}

export function buildMaterialUnderstandingActivity(
  filename: string,
  response: FileUploadResponse,
  fallbackMessage?: string | null,
): MaterialUnderstandingActivity {
  const status = materialUnderstandingStatus(response)
  if (isMaterialUnderstandingFailed(response)) {
    const reason =
      materialUnderstandingErrorMessage(response) ??
      "请重新上传一份更清晰的文件，或稍后重试。"
    return {
      content: `材料理解失败：${filename}。${reason}`,
      status: "error",
    }
  }

  if (status === "queued" || status === "processing") {
    return {
      content: `${filename} 已收到，案例理解正在更新，可以继续对话。`,
      status: "sending",
    }
  }

  return {
    content: fallbackMessage || `已上传文件：${filename}。`,
    status: "sent",
  }
}

function formatLabelList(labels: string[]): string {
  return labels.join("、")
}

export function buildUploadOnlyMaterialActivitySummary(
  responses: FileUploadResponse[],
): string {
  const evidenceCount = responses.reduce(
    (total, response) =>
      total +
      (response.case_board_delta?.evidence_cards.length ??
        response.evidence_cards.length),
    0,
  )
  const claimCount = responses.reduce(
    (total, response) =>
      total + (response.case_board_delta?.claims.length ?? 0),
    0,
  )
  const conflictCount = responses.reduce(
    (total, response) =>
      total + (response.case_board_delta?.conflicts.length ?? 0),
    0,
  )
  const failedCount = responses.filter(isMaterialUnderstandingFailed).length
  const materialLabels = Array.from(
    new Set(
      responses
        .map(
          (response) =>
            response.document_type_label ??
            response.document_assessment?.document_type_label,
        )
        .filter((label): label is string => Boolean(label)),
    ),
  )

  const materialPart = materialLabels.length
    ? `：${formatLabelList(materialLabels)}`
    : ""
  const evidencePart = evidenceCount
    ? `，已形成 ${evidenceCount} 条证据片段`
    : ""
  const claimPart = claimCount ? `、${claimCount} 个候选事实` : ""
  const conflictPart = conflictCount
    ? `，其中 ${conflictCount} 个冲突待核验`
    : ""

  if (evidenceCount || claimCount || conflictCount) {
    return `材料已加入案例证据${materialPart}${evidencePart}${claimPart}${conflictPart}。`
  }

  if (failedCount) {
    return `${failedCount} 份材料理解失败，请打开材料库查看失败原因。`
  }

  return `材料已收到${materialPart}，案例理解正在更新。`
}
