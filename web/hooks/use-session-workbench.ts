"use client"

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"
import { toPng } from "html-to-image"

import {
  ApiError,
  clearAccountSessions,
  createSession,
  createDebugMaterialBundleStream,
  exportSession,
  fetchSessionMessages,
  generateInterviewReview,
  getFileContentUrl,
  getInternalReport,
  getRequiredPackage,
  getRagStatus,
  getRuntimeDebugSnapshot,
  getUserReport,
  importMaterialPackage,
  listMaterialPackages,
  listSessions,
  listUserModels,
  sendMessage,
  sendMessageStream,
  uploadFile,
  uploadRagFile,
} from "@/lib/api/client"
import { getApiBaseUrl } from "@/lib/api/config"
import { APP_VERSION } from "@/lib/app-version"
import { getDebugMaterialBundleOption } from "@/lib/debug-material-bundles"
import { buildAssistantMessageFromBackendResponse } from "@/lib/message-source-policy"
import {
  buildMaterialUnderstandingPatchFromRuntimeEntry,
  buildMaterialUnderstandingActivity,
  buildUploadOnlyMaterialActivitySummary,
  isTerminalMaterialUnderstandingStatus,
  materialUnderstandingStatus,
  type RuntimeMaterialUnderstandingPatch,
} from "@/lib/upload-feedback-policy"
import {
  getMockRequiredPackage,
  isMockMode,
  MOCK_INTERNAL_REPORT,
  MOCK_MESSAGES,
  MOCK_SESSION_ID,
  MOCK_USER_REPORT,
} from "@/lib/api/mock-data"
import {
  humanizeBackendText,
  mapSessionGateStatus,
  toDocumentLabel,
} from "@/lib/api/mappers"
import type {
  AllowedAction,
  AttachmentKind,
  BackendSessionMessage,
  BackendSessionListItem,
  ChatAttachment,
  ChatMessage,
  ComposerCommand,
  DebugBundleDocument,
  DebugMaterialBundleResponse,
  DebugMaterialBundleScenario,
  DebugMaterialBundleStreamEvent,
  FileUploadResponse,
  InternalReport,
  InterviewReviewResponse,
  MaterialPackageArchiveItem,
  MaterialPackageDocument,
  MaterialPackageImportResponse,
  MessageStreamErrorPayload,
  ModelListItem,
  PublicReasoning,
  RagUploadMetadata,
  RagStatus,
  RequiredPackage,
  RuntimeDebugEvent,
  RuntimeDebugSnapshot,
  Session,
  SessionActivityEvent,
  SessionHistoryEntry,
  UploadedMaterial,
  UserModelConfig,
  UserModelRuntimeConfig,
  UserReport,
  VisaFamily,
} from "@/lib/api/types"

const HISTORY_NAMESPACE_KEY = "auth_history_namespace"
const HISTORY_STORAGE_PREFIX = "ds160-web-history-v2:"
const LEGACY_HISTORY_STORAGE_KEYS = ["ds160-web-history-v1"]
const MODEL_CONFIG_STORAGE_KEY = "ds160-user-model-config-v1"
const MAX_PERSISTED_PREVIEW_BYTES = 2 * 1024 * 1024
const MAX_RUNTIME_DEBUG_EVENTS = 160
const MATERIAL_UNDERSTANDING_REFRESH_DELAYS_MS = [750, 1500, 3000, 6000]

const DEFAULT_USER_MODEL_CONFIG: UserModelConfig = {
  enabled: false,
  streamingEnabled: false,
  baseUrl: "",
  apiKey: "",
  model: "",
}

function getTimestamp(): string {
  return new Date().toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  })
}

function getIsoTimestamp(): string {
  return new Date().toISOString()
}

function isTerminalInterviewState(
  session: Session | null,
  userReport: UserReport | null,
): boolean {
  return (
    session?.phase_state === "completed" ||
    session?.phase_state === "session_closed" ||
    userReport?.interview_result === "passed" ||
    userReport?.interview_result === "not_passed" ||
    userReport?.interview_status === "simulated_refusal"
  )
}

function visaFamilyFromBackendFamily(value?: string | null): VisaFamily {
  switch ((value ?? "").toLowerCase()) {
    case "f1":
    case "f-1":
      return "F-1"
    case "j1":
    case "j-1":
      return "J-1"
    case "b1_b2":
    case "b-1/b-2":
      return "B-1/B-2"
    case "h1b":
    case "h-1b":
      return "H-1B"
    default:
      return "F-1"
  }
}

function serverSessionToHistoryEntry(item: BackendSessionListItem): SessionHistoryEntry {
  const now = getIsoTimestamp()
  const visaType = visaFamilyFromBackendFamily(item.declared_family)
  return {
    id: item.session_id,
    session_id: item.session_id,
    visa_type: visaType,
    status: item.phase_state === "completed" ? "completed" : "active",
    title: `${visaType} 面签会话`,
    summary: "服务器授权会话，打开后读取完整记录。",
    last_message: null,
    message_count: 0,
    created_at: now,
    updated_at: now,
    required_package: null,
    report: null,
    materials: [],
    messages: [],
  }
}

function isPublicReasoning(value: unknown): value is PublicReasoning {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function loadUserModelConfig(): UserModelConfig {
  if (typeof window === "undefined") {
    return DEFAULT_USER_MODEL_CONFIG
  }

  const raw = window.localStorage.getItem(MODEL_CONFIG_STORAGE_KEY)
  if (!raw) {
    return DEFAULT_USER_MODEL_CONFIG
  }

  try {
    const parsed = JSON.parse(raw) as Partial<UserModelConfig>
    return {
      enabled: Boolean(parsed.enabled),
      streamingEnabled: Boolean(parsed.streamingEnabled),
      baseUrl: typeof parsed.baseUrl === "string" ? parsed.baseUrl : "",
      apiKey: typeof parsed.apiKey === "string" ? parsed.apiKey : "",
      model: typeof parsed.model === "string" ? parsed.model : "",
    }
  } catch {
    return DEFAULT_USER_MODEL_CONFIG
  }
}

function persistUserModelConfig(config: UserModelConfig): void {
  if (typeof window === "undefined") {
    return
  }
  window.localStorage.setItem(
    MODEL_CONFIG_STORAGE_KEY,
    JSON.stringify({
      enabled: config.enabled,
      streamingEnabled: config.streamingEnabled,
      baseUrl: config.baseUrl,
      model: config.model,
    }),
  )
}

function toRuntimeModelConfig(
  config: UserModelConfig,
): UserModelRuntimeConfig | null {
  if (!config.enabled) {
    return null
  }
  const baseUrl = config.baseUrl.trim()
  const apiKey = config.apiKey.trim()
  const model = config.model.trim()
  if (!baseUrl || !apiKey || !model) {
    return null
  }
  return {
    base_url: baseUrl,
    api_key: apiKey,
    model,
  }
}

interface SendMessageOptions {
  reuseMessageId?: string
  clientMessageId?: string
}

function formatRequestedDocuments(labels: string[]): string {
  if (!labels.length) {
    return ""
  }

  return labels.join("、")
}

function buildRequiredPackageMessage(
  visaFamily: VisaFamily,
  requiredPackage: RequiredPackage,
): string {
  const materialHint = requiredPackage.required_initial_package_labels.length
    ? "如果你手边有系统建议的基础材料，可以在对话过程中随时上传；我会用它们核对已经陈述的事实。"
    : "如果你手边有能支持学习计划、身份或资金安排的材料，可以在对话过程中随时上传。"

  return `你好，我们开始今天的 ${visaFamily} 签证模拟。我会像真实窗口面谈一样，先了解你的学习计划，再结合材料核对关键细节。\n\n你先简单介绍一下：这次去美国读什么项目？为什么选择这所学校？\n\n${materialHint}`
}

function buildEvidenceSuggestionMessage(
  requestedDocumentLabels: string[],
  governorDecision?: string | null,
): string | null {
  if (
    !requestedDocumentLabels.length ||
    governorDecision !== "need_more_evidence"
  ) {
    return null
  }

  const documentList = formatRequestedDocuments(requestedDocumentLabels)
  return `可补充证据：${documentList}。`
}

function buildGateProgressMessage(overallStatus?: string): string | null {
  if (overallStatus === "waiting_for_parse") {
    return "材料已收到，案例理解正在更新。你可以继续对话。"
  }

  return null
}

function truncateProgressLine(value: string): string {
  return value.length > 220 ? `${value.slice(0, 217)}...` : value
}

function describeDebugBundleEvent(
  event: DebugMaterialBundleStreamEvent,
): string {
  switch (event.event) {
    case "accepted":
      return "已收到调试合成材料生成请求。"
    case "debug_bundle_started":
      return `开始生成${event.data.scenario_label ?? "调试合成材料"}，预计 ${event.data.document_count ?? 0} 份材料。`
    case "document_created":
      return `已生成材料：${event.data.document_type_label ?? event.data.filename ?? "材料"}。`
    case "evidence_written":
      return truncateProgressLine(
        `已整理字段：${Object.keys(event.data.fields ?? {}).join("、") || "字段待确认"}。`,
      )
    case "profile_recomputed":
      return "已根据材料刷新申请人档案。"
    case "gate_refreshed":
      return "已更新案例证据状态。"
    case "document_review_started":
      return "开始核对材料之间的关键细节。"
    case "governor_decided":
      return `已得到下一步状态：${humanizeBackendText(event.data.governor_decision ?? "") || "待确认"}。`
    case "progress":
      return event.data.message ?? "调试合成材料仍在生成或核对中。"
    case "final":
      return "调试合成材料生成完成。"
    case "error":
      return `调试合成材料生成失败：${event.data.detail ?? "未知错误"}`
    default:
      return "调试合成材料生成状态已更新。"
  }
}

function runtimeDebugEventFromMaterialEvent(
  event: DebugMaterialBundleStreamEvent,
): RuntimeDebugEvent {
  const status =
    event.event === "error"
      ? "failed"
      : event.event === "progress"
        ? "still_running"
        : event.event === "debug_bundle_started"
          ? "started"
          : "completed"
  return {
    phase: "material_bundle",
    step: event.event,
    status,
    summary: describeDebugBundleEvent(event),
    payload:
      event.data && typeof event.data === "object" && !Array.isArray(event.data)
        ? (event.data as Record<string, unknown>)
        : {},
  }
}

function buildDebugBundleProgressMessage(lines: string[]): string {
  return lines.join("\n")
}


function getImportStatusWarning(result: MaterialPackageImportResponse): string | null {
  const warnings: string[] = []
  if (result.import_status !== "imported") {
    warnings.push(`导入状态为 ${humanizeBackendText(result.status_label || result.import_status)}`)
  }
  if (result.main_flow_refresh_error) {
    warnings.push(`面签状态刷新失败：${result.main_flow_refresh_error}`)
  }
  return warnings.length ? warnings.join("；") : null
}

function debugBundleDocumentToMaterial(
  sessionId: string,
  document: DebugBundleDocument,
  bundle: DebugMaterialBundleResponse,
): UploadedMaterial {
  return {
    id: document.document_id,
    session_id: sessionId,
    name: document.filename,
    mime_type: "text/plain",
    kind: "file",
    size: new TextEncoder().encode(document.raw_text).length,
    preview_url: getFileContentUrl(sessionId, document.document_id),
    content_url: getFileContentUrl(sessionId, document.document_id),
    uploaded_at: getIsoTimestamp(),
    status_label: "已生成",
    document_id: document.document_id,
    document_status: "parsed",
    document_type: document.document_type,
    document_type_label:
      document.document_type_label ?? toDocumentLabel(document.document_type),
    relevance: "high",
    feedback_message: `${bundle.scenario_label} / ${bundle.bundle_id}`,
    requested_document_labels: [],
    current_focus_document_label: null,
    counts_toward_gate: true,
    raw_text: document.raw_text,
    fields: document.fields,
    synthetic_bundle_id: bundle.bundle_id,
    debug_bundle_scenario: bundle.scenario,
    expected_findings: bundle.expected_findings,
  }
}

function materialPackageDocumentToMaterial(
  sessionId: string,
  document: MaterialPackageDocument,
  result: MaterialPackageImportResponse,
): UploadedMaterial {
  const rawText = document.raw_text ?? ""
  return {
    id: document.document_id,
    session_id: sessionId,
    name: document.filename,
    mime_type: "text/plain",
    kind: "file",
    size: new TextEncoder().encode(rawText).length,
    preview_url: getFileContentUrl(sessionId, document.document_id),
    content_url: getFileContentUrl(sessionId, document.document_id),
    uploaded_at: getIsoTimestamp(),
    status_label: "已导入",
    document_id: document.document_id,
    document_status: document.status ?? "parsed",
    understanding_status: document.understanding_status ?? null,
    document_type: document.document_type ?? null,
    document_type_label:
      document.document_type_label ??
      (document.document_type ? toDocumentLabel(document.document_type) : null),
    relevance: "high",
    feedback_message: `存档材料包 / ${result.package_id}`,
    requested_document_labels: [],
    current_focus_document_label: null,
    counts_toward_gate: true,
    raw_text: rawText,
    fields: document.fields ?? {},
    synthetic_bundle_id: result.imported_bundle_id,
    debug_bundle_scenario: "material_package_import",
  }
}

function buildDebugBundleFinalMessage(
  bundle: DebugMaterialBundleResponse,
): string {
  const documentNames = bundle.documents.map(
    (document) => document.document_type_label ?? document.filename,
  )
  const option = getDebugMaterialBundleOption(bundle.scenario)
  const source =
    bundle.generation?.source === "ai"
      ? "AI 已根据你的提示词生成"
      : "已生成"
  return `${source}${option.label}：${formatRequestedDocuments(documentNames)}。材料已写入材料库，可以直接打开查看正文和提取字段。`
}

function inferAttachmentKind(
  file: Pick<File, "name" | "type">,
): AttachmentKind {
  if (file.type.startsWith("image/")) {
    return "image"
  }

  if (
    file.type === "application/pdf" ||
    file.name.toLowerCase().endsWith(".pdf")
  ) {
    return "pdf"
  }

  return "file"
}

function sanitizeHistoryAttachment(attachment: ChatAttachment): ChatAttachment {
  return {
    ...attachment,
    preview_url: attachment.preview_url?.startsWith("data:")
      ? attachment.preview_url
      : null,
  }
}

function normalizeChatMessageRole(role: unknown): ChatMessage["role"] {
  if (role === "assistant" || role === "officer") {
    return "assistant"
  }
  if (role === "user") {
    return "user"
  }
  return "system"
}

function sanitizeHistoryMessage(
  message: ChatMessage | (Omit<ChatMessage, "role"> & { role?: unknown }),
): ChatMessage {
  return {
    ...message,
    role: normalizeChatMessageRole(message.role),
    attachments: message.attachments?.map(sanitizeHistoryAttachment),
  }
}

function visaFamilyFromReport(report: UserReport | null): VisaFamily {
  const label = report?.visa_family_label
  if (
    label === "F-1" ||
    label === "J-1" ||
    label === "B-1/B-2" ||
    label === "H-1B"
  ) {
    return label
  }
  switch (report?.visa_family) {
    case "j1":
      return "J-1"
    case "b1_b2":
      return "B-1/B-2"
    case "h1b":
      return "H-1B"
    case "f1":
    default:
      return "F-1"
  }
}

function chatMessageFromBackendTurn(turn: BackendSessionMessage): ChatMessage | null {
  const role = normalizeChatMessageRole(turn.role)
  if (role !== "assistant" && role !== "user") {
    return null
  }
  const metadata = turn.metadata ?? {}
  return {
    id: turn.turn_id || `turn-${turn.turn_index}`,
    role,
    content: humanizeBackendText(turn.content),
    timestamp: getTimestamp(),
    status: "sent",
    public_reasoning: isPublicReasoning(metadata.public_reasoning)
      ? metadata.public_reasoning
      : null,
  }
}

function chatMessagesFromBackendTurns(
  turns: BackendSessionMessage[],
): ChatMessage[] {
  return turns
    .map(chatMessageFromBackendTurn)
    .filter((message): message is ChatMessage => Boolean(message))
}

function resolvePersistentMaterialPreview(
  material: UploadedMaterial,
): string | null {
  if (material.preview_url?.startsWith("data:")) {
    return material.preview_url
  }
  if (material.session_id && material.document_id) {
    return getFileContentUrl(material.session_id, material.document_id)
  }
  return null
}

function sanitizeHistoryMaterial(material: UploadedMaterial): UploadedMaterial {
  return {
    ...material,
    preview_url: resolvePersistentMaterialPreview(material),
  }
}

function resolvePersistentAttachmentPreview(
  attachment: ChatAttachment,
): string | null {
  if (attachment.preview_url?.startsWith("data:")) {
    return attachment.preview_url
  }
  if (
    attachment.kind === "image" &&
    attachment.session_id &&
    attachment.document_id
  ) {
    return getFileContentUrl(attachment.session_id, attachment.document_id)
  }
  return null
}

function resolveMaterialMatchKey(name: string, index: number): string {
  return `${name.trim().toLowerCase()}#${index}`
}

function buildMaterialPreviewLookup(
  materials: UploadedMaterial[],
): Map<string, UploadedMaterial> {
  const nameCounts = new Map<string, number>()
  const lookup = new Map<string, UploadedMaterial>()
  for (const material of materials) {
    const normalizedName = material.name.trim().toLowerCase()
    const index = nameCounts.get(normalizedName) ?? 0
    nameCounts.set(normalizedName, index + 1)
    lookup.set(resolveMaterialMatchKey(material.name, index), material)
  }
  return lookup
}

function hydrateHistoryMessages(
  messages: ChatMessage[],
  materials: UploadedMaterial[],
): ChatMessage[] {
  const materialLookup = buildMaterialPreviewLookup(materials)
  const attachmentNameCounts = new Map<string, number>()

  return messages.map((message) => ({
    ...message,
    attachments: message.attachments?.map((attachment) => {
      const directPreview = resolvePersistentAttachmentPreview(attachment)
      if (directPreview) {
        return {
          ...attachment,
          preview_url: directPreview,
        }
      }

      const normalizedName = attachment.name.trim().toLowerCase()
      const index = attachmentNameCounts.get(normalizedName) ?? 0
      attachmentNameCounts.set(normalizedName, index + 1)
      const matchedMaterial = materialLookup.get(
        resolveMaterialMatchKey(attachment.name, index),
      )
      if (!matchedMaterial) {
        return attachment
      }

      return {
        ...attachment,
        document_id:
          matchedMaterial.document_id ?? attachment.document_id ?? null,
        session_id: matchedMaterial.session_id ?? attachment.session_id ?? null,
        preview_url: resolvePersistentMaterialPreview(matchedMaterial),
      }
    }),
  }))
}

function readFileAsDataUrl(file: File): Promise<string | null> {
  if (
    !file.type.startsWith("image/") ||
    file.size > MAX_PERSISTED_PREVIEW_BYTES
  ) {
    return Promise.resolve(null)
  }

  return new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = () => {
      resolve(typeof reader.result === "string" ? reader.result : null)
    }
    reader.onerror = () => resolve(null)
    reader.readAsDataURL(file)
  })
}

const EMPTY_HISTORY_ENTRIES: SessionHistoryEntry[] = []
const historyStoreListeners = new Set<() => void>()
let cachedHistoryRaw: string | null = null
let cachedHistoryKey: string | null = null
let cachedHistoryEntries: SessionHistoryEntry[] = EMPTY_HISTORY_ENTRIES

function currentHistoryStorageKey(): string {
  if (typeof window === "undefined") {
    return `${HISTORY_STORAGE_PREFIX}local-dev`
  }
  const namespace =
    window.localStorage.getItem(HISTORY_NAMESPACE_KEY)?.trim() || "local-dev"
  return `${HISTORY_STORAGE_PREFIX}${namespace}`
}

function loadHistoryEntries(): SessionHistoryEntry[] {
  if (typeof window === "undefined") {
    return EMPTY_HISTORY_ENTRIES
  }

  try {
    const historyKey = currentHistoryStorageKey()
    const raw = window.localStorage.getItem(historyKey)
    if (historyKey === cachedHistoryKey && raw === cachedHistoryRaw) {
      return cachedHistoryEntries
    }
    cachedHistoryKey = historyKey
    cachedHistoryRaw = raw
    if (!raw) {
      cachedHistoryEntries = EMPTY_HISTORY_ENTRIES
      return cachedHistoryEntries
    }

    const parsed = JSON.parse(raw)
    cachedHistoryEntries = Array.isArray(parsed)
      ? parsed
      : EMPTY_HISTORY_ENTRIES
    return cachedHistoryEntries
  } catch {
    cachedHistoryRaw = null
    cachedHistoryEntries = EMPTY_HISTORY_ENTRIES
    return cachedHistoryEntries
  }
}

function getServerHistoryEntries(): SessionHistoryEntry[] {
  return EMPTY_HISTORY_ENTRIES
}

function subscribeHistoryStore(listener: () => void): () => void {
  historyStoreListeners.add(listener)

  if (typeof window === "undefined") {
    return () => historyStoreListeners.delete(listener)
  }

  const handleStorage = (event: StorageEvent) => {
    if (
      event.key === HISTORY_NAMESPACE_KEY ||
      event.key?.startsWith(HISTORY_STORAGE_PREFIX)
    ) {
      cachedHistoryKey = null
      cachedHistoryRaw = null
      listener()
    }
  }
  window.addEventListener("storage", handleStorage)

  return () => {
    historyStoreListeners.delete(listener)
    window.removeEventListener("storage", handleStorage)
  }
}

function writeHistoryEntries(
  entries: SessionHistoryEntry[],
  options?: { notify?: boolean },
): void {
  if (typeof window === "undefined") {
    return
  }

  const raw = JSON.stringify(entries)
  const historyKey = currentHistoryStorageKey()
  cachedHistoryKey = historyKey
  cachedHistoryRaw = raw
  cachedHistoryEntries = entries
  window.localStorage.setItem(historyKey, raw)

  if (options?.notify) {
    historyStoreListeners.forEach((listener) => listener())
  }
}

function removeHistoryEntries(): void {
  if (typeof window === "undefined") {
    return
  }

  cachedHistoryRaw = null
  cachedHistoryKey = null
  cachedHistoryEntries = EMPTY_HISTORY_ENTRIES
  window.localStorage.removeItem(currentHistoryStorageKey())
  for (const legacyKey of LEGACY_HISTORY_STORAGE_KEYS) {
    window.localStorage.removeItem(legacyKey)
  }
  historyStoreListeners.forEach((listener) => listener())
}

function createClientId(prefix: string): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return `${prefix}-${crypto.randomUUID()}`
  }

  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function downloadDataUrl(filename: string, dataUrl: string): void {
  if (typeof window === "undefined") {
    return
  }

  const link = document.createElement("a")
  link.href = dataUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
}

function downloadJsonFile(filename: string, payload: unknown): void {
  if (typeof window === "undefined") {
    return
  }

  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json;charset=utf-8",
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function humanizeUploadStatus(
  status?: string | null,
  fallback = "已上传",
): string {
  switch (status) {
    case "queued":
      return "排队中"
    case "processing":
    case "waiting_for_parse":
    case "parsing":
      return "理解中"
    case "parsed":
    case "uploaded":
    case "completed":
      return fallback
    case "failed":
    case "error":
      return "处理失败"
    default:
      return fallback
  }
}

function describeMessageStreamError(error: MessageStreamErrorPayload): string {
  const detail =
    typeof error.detail === "string" && error.detail.trim()
      ? error.detail.trim()
      : "流式消息处理失败。"
  const categoryLabels: Record<string, string> = {
    model_config: "模型配置缺失",
    upstream_model: "上游模型服务错误",
    upstream_timeout: "上游模型请求超时",
    upstream_connection_error: "上游模型连接失败",
    model_output_invalid: "模型输出格式不符合要求",
    agent_runtime_error: "模型代理运行错误",
    internal_error: "后端内部错误",
    model_runtime: "模型运行错误",
  }
  const category = error.error_category
    ? categoryLabels[error.error_category] ?? error.error_category
    : null
  const retryHint =
    error.retry_exhausted && typeof error.retry_attempts === "number"
      ? `已重试 ${error.retry_attempts} 次，可稍后重试本条。`
      : null
  const modelContext = [error.provider, error.model].filter(Boolean).join("/")
  const suffixParts = [
    category,
    error.upstream_code ? `上游码：${error.upstream_code}` : null,
    modelContext ? `模型：${modelContext}` : null,
  ].filter(Boolean)
  const message = retryHint ? `${detail} ${retryHint}` : detail
  return suffixParts.length ? `${message}（${suffixParts.join("；")}）` : message
}

function isMessageStreamErrorPayload(value: unknown): value is MessageStreamErrorPayload {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const candidate = value as Partial<MessageStreamErrorPayload>
  return (
    typeof candidate.detail === "string" ||
    typeof candidate.error_category === "string" ||
    typeof candidate.status === "number" ||
    typeof candidate.upstream_code === "string" ||
    typeof candidate.retry_exhausted === "boolean"
  )
}

function messageStreamErrorFromUnknown(
  value: unknown,
): MessageStreamErrorPayload | null {
  if (isMessageStreamErrorPayload(value)) {
    return value
  }
  if (typeof value !== "object" || value === null || !("detail" in value)) {
    return null
  }
  const detail = (value as { detail?: unknown }).detail
  return isMessageStreamErrorPayload(detail) ? detail : null
}

function humanizeUnderstandingStatus(status?: string | null): string {
  switch (status) {
    case "queued":
      return "案例理解更新中"
    case "processing":
      return "案例理解中"
    case "failed":
      return "理解失败"
    case "completed":
      return "已理解"
    default:
      return humanizeUploadStatus(status, "已上传")
  }
}

function materialUnderstandingPatchesFromSnapshot(
  snapshot: RuntimeDebugSnapshot | null,
): RuntimeMaterialUnderstandingPatch[] {
  return (snapshot?.material_understanding ?? [])
    .map(buildMaterialUnderstandingPatchFromRuntimeEntry)
    .filter(
      (patch): patch is RuntimeMaterialUnderstandingPatch => patch !== null,
    )
}

function materialMatchesUnderstandingPatch(
  material: UploadedMaterial,
  patch: RuntimeMaterialUnderstandingPatch,
): boolean {
  return (
    Boolean(patch.document_id && material.document_id === patch.document_id) ||
    Boolean(patch.filename && material.name === patch.filename)
  )
}

function applyMaterialUnderstandingPatches(
  materials: UploadedMaterial[],
  patches: RuntimeMaterialUnderstandingPatch[],
): UploadedMaterial[] {
  if (!patches.length) {
    return materials
  }

  let changed = false
  const nextMaterials = materials.map((material) => {
    const patch = patches.find((item) =>
      materialMatchesUnderstandingPatch(material, item),
    )
    if (!patch) {
      return material
    }

    const nextStatus =
      patch.understanding_status ?? material.understanding_status ?? null
    const nextError =
      patch.understanding_error ?? material.understanding_error ?? null
    if (
      nextStatus === material.understanding_status &&
      nextError?.code === material.understanding_error?.code &&
      nextError?.message === material.understanding_error?.message
    ) {
      return material
    }

    changed = true
    const nextLatestMaterial = material.case_board_delta?.latest_material
      ? {
          ...material.case_board_delta.latest_material,
          understanding_status:
            nextStatus ??
            material.case_board_delta.latest_material.understanding_status ??
            null,
          understanding_error:
            nextError ??
            material.case_board_delta.latest_material.understanding_error ??
            null,
          unknowns: nextError?.message
            ? [nextError.message]
            : material.case_board_delta.latest_material.unknowns,
        }
      : null
    return {
      ...material,
      understanding_status: nextStatus,
      understanding_error: nextError,
      status_label: humanizeUnderstandingStatus(
        nextStatus ?? material.document_status ?? null,
      ),
      feedback_message:
        nextError?.message ?? patch.understanding_error?.message ??
        material.feedback_message,
      case_board_delta: material.case_board_delta
        ? {
            ...material.case_board_delta,
            latest_material: nextLatestMaterial,
          }
        : material.case_board_delta,
    }
  })

  return changed ? nextMaterials : materials
}

function responseNeedsMaterialUnderstandingRefresh(
  response: FileUploadResponse,
): boolean {
  const status =
    materialUnderstandingStatus(response) ??
    response.understanding_status ??
    response.job_status ??
    null
  return (
    status === "queued" ||
    status === "processing" ||
    status === "waiting_for_parse" ||
    status === "parsing"
  )
}

function waitForMilliseconds(delayMs: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, delayMs)
  })
}

function firstNonEmptyText(
  ...values: Array<string | null | undefined>
): string | null {
  for (const value of values) {
    const normalized = value?.trim()
    if (normalized) {
      return normalized
    }
  }
  return null
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;")
}

function formatMessageRole(role: ChatMessage["role"]): string {
  switch (role) {
    case "assistant":
      return "签证官"
    case "user":
      return "申请人"
    case "system":
      return "系统记录"
  }
}

function roleClassName(role: ChatMessage["role"]): string {
  switch (role) {
    case "assistant":
      return "assistant"
    case "user":
      return "user"
    case "system":
      return "system"
  }
}

function renderExportAttachment(attachment: ChatAttachment): string {
  const name = escapeHtml(attachment.name)
  if (attachment.kind === "image" && attachment.preview_url) {
    return `
      <figure class="attachment attachment-image">
        <img src="${escapeHtml(attachment.preview_url)}" alt="${name}" />
        <figcaption>${name}</figcaption>
      </figure>
    `
  }

  const kindLabel =
    attachment.kind === "pdf"
      ? "PDF"
      : attachment.kind === "image"
        ? "图片"
        : "文件"
  return `
    <div class="attachment attachment-file">
      <div class="file-icon">${escapeHtml(kindLabel)}</div>
      <div class="file-name">${name}</div>
    </div>
  `
}

function renderExportMessage(message: ChatMessage): string {
  const role = formatMessageRole(message.role)
  const roleClass = roleClassName(message.role)
  const content = escapeHtml(message.content || "（仅包含附件）").replaceAll(
    "\n",
    "<br />",
  )
  const attachments =
    message.attachments?.map(renderExportAttachment).join("") ?? ""
  return `
    <section class="message-row ${roleClass}">
      <div class="avatar">${roleClass === "assistant" ? "VO" : roleClass === "user" ? "我" : "记"}</div>
      <div class="message-stack">
        <div class="message-meta">
          <span>${role}</span>
          <span>${escapeHtml(message.timestamp)}</span>
        </div>
        <div class="bubble">
          ${message.content ? `<div class="message-text">${content}</div>` : ""}
          ${attachments ? `<div class="attachments">${attachments}</div>` : ""}
        </div>
      </div>
    </section>
  `
}

function buildConversationExportHtml(options: {
  sessionId: string
  visaType: VisaFamily | null
  messages: ChatMessage[]
}): string {
  const exportedAt = new Date().toLocaleString("zh-CN")
  const messagesHtml = options.messages.map(renderExportMessage).join("")
  return `
    <div class="share-sheet">
      <header class="hero">
        <div>
          <div class="eyebrow">DS-160 SIMULATION REPORT</div>
          <h1>模拟面签会话长图</h1>
          <p>签证类型：${escapeHtml(options.visaType ?? "未选择")} · 导出时间：${escapeHtml(exportedAt)}</p>
        </div>
        <div class="session-badge">${escapeHtml(options.sessionId)}</div>
      </header>
      <main class="timeline">${messagesHtml}</main>
      <footer class="footer">
        <span>由 面签模拟器生成</span>
        <span>图片材料仅用于本地练习复盘</span>
      </footer>
    </div>
  `
}

function buildConversationExportStyles(): string {
  return `
    .share-sheet {
      box-sizing: border-box;
      width: 1080px;
      min-height: 720px;
      padding: 44px;
      background: linear-gradient(180deg, #eff6ff 0%, #f8fafc 220px, #f8fafc 100%);
      color: #0f172a;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    .hero {
      display: flex;
      justify-content: space-between;
      gap: 28px;
      padding: 38px 42px;
      border-radius: 34px;
      background: linear-gradient(135deg, #0f3b77 0%, #1d4ed8 70%, #38bdf8 100%);
      color: #fff;
      box-shadow: 0 24px 60px rgba(30, 64, 175, 0.24);
    }
    .eyebrow {
      margin-bottom: 12px;
      font-size: 18px;
      font-weight: 800;
      letter-spacing: 0.16em;
      opacity: 0.82;
    }
    .hero h1 {
      margin: 0;
      font-size: 46px;
      line-height: 1.18;
      font-weight: 850;
    }
    .hero p {
      margin: 18px 0 0;
      font-size: 24px;
      line-height: 1.45;
      opacity: 0.88;
    }
    .session-badge {
      align-self: flex-start;
      max-width: 360px;
      border-radius: 999px;
      padding: 14px 20px;
      background: rgba(255, 255, 255, 0.16);
      border: 1px solid rgba(255, 255, 255, 0.26);
      font-size: 20px;
      line-height: 1.35;
      word-break: break-all;
    }
    .timeline {
      margin-top: 38px;
      display: flex;
      flex-direction: column;
      gap: 34px;
    }
    .message-row {
      display: flex;
      gap: 18px;
      align-items: flex-start;
    }
    .message-row.user {
      flex-direction: row-reverse;
    }
    .message-row.system {
      justify-content: center;
    }
    .avatar {
      display: flex;
      width: 58px;
      height: 58px;
      flex: 0 0 auto;
      align-items: center;
      justify-content: center;
      border-radius: 22px;
      background: #dbeafe;
      color: #1d4ed8;
      font-size: 22px;
      font-weight: 800;
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
    }
    .user .avatar {
      background: #1d4ed8;
      color: #fff;
    }
    .system .avatar {
      display: none;
    }
    .message-stack {
      max-width: 780px;
      min-width: 0;
    }
    .user .message-stack {
      align-items: flex-end;
    }
    .system .message-stack {
      max-width: 880px;
      width: 880px;
    }
    .message-meta {
      display: flex;
      gap: 14px;
      margin: 0 0 10px 4px;
      color: #64748b;
      font-size: 20px;
      line-height: 1.3;
      font-weight: 650;
    }
    .user .message-meta {
      justify-content: flex-end;
      margin-right: 4px;
    }
    .system .message-meta {
      justify-content: center;
    }
    .bubble {
      box-sizing: border-box;
      border-radius: 28px;
      padding: 26px 30px;
      background: #fff;
      border: 1px solid #e2e8f0;
      box-shadow: 0 18px 42px rgba(15, 23, 42, 0.08);
    }
    .assistant .bubble {
      border-top-left-radius: 12px;
      background: #ffffff;
    }
    .user .bubble {
      border-top-right-radius: 12px;
      background: #1d4ed8;
      color: #fff;
      border-color: #1d4ed8;
      box-shadow: 0 18px 42px rgba(29, 78, 216, 0.18);
    }
    .system .bubble {
      border-radius: 24px;
      background: #eef2ff;
      color: #475569;
      border-color: #c7d2fe;
      box-shadow: none;
      text-align: center;
    }
    .message-text {
      font-size: 34px;
      line-height: 1.68;
      font-weight: 520;
      letter-spacing: 0.01em;
      word-break: break-word;
    }
    .attachments {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      margin-top: 20px;
    }
    .message-text + .attachments {
      margin-top: 24px;
    }
    .attachment {
      overflow: hidden;
      border-radius: 22px;
      background: rgba(248, 250, 252, 0.95);
      border: 1px solid rgba(226, 232, 240, 0.9);
    }
    .user .attachment {
      background: rgba(255, 255, 255, 0.14);
      border-color: rgba(255, 255, 255, 0.25);
    }
    .attachment-image img {
      display: block;
      width: 100%;
      max-height: 420px;
      object-fit: cover;
      background: #e2e8f0;
    }
    .attachment-image figcaption,
    .file-name {
      padding: 12px 14px;
      color: #475569;
      font-size: 18px;
      line-height: 1.35;
      word-break: break-word;
    }
    .user .attachment-image figcaption,
    .user .file-name {
      color: rgba(255, 255, 255, 0.88);
    }
    .attachment-file {
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 16px;
    }
    .file-icon {
      display: flex;
      width: 58px;
      height: 58px;
      flex: 0 0 auto;
      align-items: center;
      justify-content: center;
      border-radius: 16px;
      background: #dbeafe;
      color: #1d4ed8;
      font-size: 18px;
      font-weight: 800;
    }
    .footer {
      display: flex;
      justify-content: space-between;
      gap: 24px;
      margin-top: 42px;
      padding: 26px 10px 4px;
      color: #64748b;
      font-size: 20px;
      line-height: 1.4;
    }
  `
}

function waitForExportImages(container: HTMLElement): Promise<void> {
  const images = Array.from(container.querySelectorAll("img"))
  return Promise.all(
    images.map(
      (image) =>
        new Promise<void>((resolve) => {
          if (image.complete) {
            resolve()
            return
          }
          image.onload = () => resolve()
          image.onerror = () => resolve()
        }),
    ),
  ).then(() => undefined)
}

async function exportConversationLongImage(
  filename: string,
  options: {
    sessionId: string
    visaType: VisaFamily | null
    messages: ChatMessage[]
  },
): Promise<void> {
  if (typeof window === "undefined") {
    return
  }

  const container = document.createElement("div")
  container.style.position = "fixed"
  container.style.left = "-12000px"
  container.style.top = "0"
  container.style.width = "1080px"
  container.style.zIndex = "-1"
  container.innerHTML = `<style>${buildConversationExportStyles()}</style>${buildConversationExportHtml(options)}`
  document.body.appendChild(container)

  try {
    await waitForExportImages(container)
    const sheet = container.querySelector(".share-sheet")
    if (!(sheet instanceof HTMLElement)) {
      return
    }
    const dataUrl = await toPng(sheet, {
      pixelRatio: 2,
      cacheBust: true,
      backgroundColor: "#f8fafc",
    })
    downloadDataUrl(filename, dataUrl)
  } finally {
    container.remove()
  }
}

function renderReviewList(
  title: string,
  items: string[],
  tone = "default",
): string {
  const normalizedItems = items.map(humanizeBackendText).filter(Boolean)
  const content = normalizedItems.length
    ? `<ul>${normalizedItems.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
    : `<p class="empty">暂无</p>`
  return `
    <section class="review-card ${tone}">
      <h2>${escapeHtml(title)}</h2>
      ${content}
    </section>
  `
}

function buildReviewExportHtml(options: {
  sessionId: string
  visaType: VisaFamily | null
  review: InterviewReviewResponse
}): string {
  const report = options.review.report
  const exportedAt = new Date().toLocaleString("zh-CN")
  return `
    <div class="review-sheet">
      <header class="review-hero">
        <div class="review-eyebrow">INTERVIEW REVIEW REPORT</div>
        <h1>面签复盘报告</h1>
        <p>签证类型：${escapeHtml(options.visaType ?? "未选择")} · 导出时间：${escapeHtml(exportedAt)}</p>
        <div class="review-session">会话 ${escapeHtml(options.sessionId)}</div>
      </header>

      <section class="review-summary">
        <div class="outcome-pill">${escapeHtml(humanizeBackendText(report.outcome))}</div>
        <h2>${escapeHtml(humanizeBackendText(report.executive_summary))}</h2>
        <p>${escapeHtml(humanizeBackendText(report.outcome_reason))}</p>
      </section>

      <main class="review-grid">
        ${renderReviewList("做得好的地方", report.strengths, "success")}
        ${renderReviewList("拒签/风险原因", report.refusal_or_risk_reasons, "danger")}
        ${renderReviewList("缺失或薄弱证据", report.missing_or_weak_evidence, "warning")}
        ${renderReviewList("回答表现问题", report.conversation_issues)}
        ${renderReviewList("材料复盘", report.document_findings)}
        ${renderReviewList("下一步补强计划", report.improvement_plan, "primary")}
        ${renderReviewList("下一轮练习重点", report.next_practice_focus, "primary")}
      </main>

      <footer class="review-footer">
        <span>由面签模拟器生成</span>
        <span>仅供练习复盘参考，不代表真实使馆结论</span>
      </footer>
    </div>
  `
}

function buildReviewExportStyles(): string {
  return `
    .review-sheet {
      box-sizing: border-box;
      width: 1080px;
      min-height: 720px;
      padding: 46px;
      background: #f8fafc;
      color: #0f172a;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    .review-hero {
      border-radius: 36px;
      padding: 42px;
      color: #fff;
      background: radial-gradient(circle at 90% 10%, rgba(56, 189, 248, 0.45), transparent 32%), linear-gradient(135deg, #172554 0%, #1d4ed8 72%, #0f766e 100%);
      box-shadow: 0 28px 70px rgba(30, 64, 175, 0.24);
    }
    .review-eyebrow {
      margin-bottom: 14px;
      font-size: 18px;
      font-weight: 850;
      letter-spacing: 0.18em;
      opacity: 0.82;
    }
    .review-hero h1 {
      margin: 0;
      font-size: 50px;
      line-height: 1.18;
      font-weight: 850;
    }
    .review-hero p,
    .review-session {
      margin: 18px 0 0;
      font-size: 23px;
      line-height: 1.45;
      opacity: 0.9;
      word-break: break-word;
    }
    .review-summary {
      margin-top: 34px;
      border-radius: 30px;
      border: 1px solid #dbeafe;
      background: #ffffff;
      padding: 34px;
      box-shadow: 0 18px 44px rgba(15, 23, 42, 0.08);
    }
    .outcome-pill {
      display: inline-flex;
      margin-bottom: 18px;
      border-radius: 999px;
      padding: 10px 18px;
      background: #fee2e2;
      color: #b91c1c;
      font-size: 22px;
      font-weight: 800;
    }
    .review-summary h2 {
      margin: 0;
      color: #0f172a;
      font-size: 36px;
      line-height: 1.45;
      font-weight: 820;
      word-break: break-word;
    }
    .review-summary p {
      margin: 18px 0 0;
      color: #475569;
      font-size: 28px;
      line-height: 1.7;
      word-break: break-word;
    }
    .review-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 24px;
      margin-top: 28px;
    }
    .review-card {
      border-radius: 28px;
      border: 1px solid #e2e8f0;
      background: #fff;
      padding: 30px 34px;
      box-shadow: 0 14px 34px rgba(15, 23, 42, 0.06);
    }
    .review-card.success { border-color: #bbf7d0; background: #f0fdf4; }
    .review-card.danger { border-color: #fecaca; background: #fff7f7; }
    .review-card.warning { border-color: #fde68a; background: #fffbeb; }
    .review-card.primary { border-color: #bfdbfe; background: #eff6ff; }
    .review-card h2 {
      margin: 0 0 18px;
      color: #0f172a;
      font-size: 30px;
      line-height: 1.35;
      font-weight: 820;
    }
    .review-card ul {
      display: flex;
      flex-direction: column;
      gap: 14px;
      margin: 0;
      padding-left: 28px;
    }
    .review-card li,
    .review-card .empty {
      color: #334155;
      font-size: 28px;
      line-height: 1.68;
      word-break: break-word;
    }
    .review-footer {
      display: flex;
      justify-content: space-between;
      gap: 24px;
      margin-top: 34px;
      color: #64748b;
      font-size: 20px;
      line-height: 1.45;
    }
  `
}

async function exportReviewReportImage(
  filename: string,
  options: {
    sessionId: string
    visaType: VisaFamily | null
    review: InterviewReviewResponse
  },
): Promise<void> {
  if (typeof window === "undefined") {
    return
  }

  const container = document.createElement("div")
  container.style.position = "fixed"
  container.style.left = "-12000px"
  container.style.top = "0"
  container.style.width = "1080px"
  container.style.zIndex = "-1"
  container.innerHTML = `<style>${buildReviewExportStyles()}</style>${buildReviewExportHtml(options)}`
  document.body.appendChild(container)

  try {
    const sheet = container.querySelector(".review-sheet")
    if (!(sheet instanceof HTMLElement)) {
      return
    }
    const dataUrl = await toPng(sheet, {
      pixelRatio: 2,
      cacheBust: true,
      backgroundColor: "#f8fafc",
    })
    downloadDataUrl(filename, dataUrl)
  } finally {
    container.remove()
  }
}

export function useSessionWorkbench() {
  const apiBaseUrl = useMemo(() => getApiBaseUrl(), [])
  const mockMode = useMemo(() => isMockMode(), [])

  const [session, setSession] = useState<Session | null>(null)
  const [visaType, setVisaType] = useState<VisaFamily | null>(null)
  const [requiredPackage, setRequiredPackage] =
    useState<RequiredPackage | null>(null)

  const [isInitializing, setIsInitializing] = useState(false)
  const [initError, setInitError] = useState<string | null>(null)

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [activityEvents, setActivityEvents] = useState<SessionActivityEvent[]>(
    [],
  )
  const [isSending, setIsSending] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const sendingRef = useRef(false)
  const streamProgressEventIdRef = useRef<string | null>(null)
  const loadedSessionIdFromQueryRef = useRef<string | null>(null)
  const [chatError, setChatError] = useState<string | null>(null)

  const [userReport, setUserReport] = useState<UserReport | null>(null)
  const [isLoadingReport, setIsLoadingReport] = useState(false)
  const [reportError, setReportError] = useState<string | null>(null)

  const [internalReport, setInternalReport] = useState<InternalReport | null>(
    null,
  )
  const [interviewReview, setInterviewReview] =
    useState<InterviewReviewResponse | null>(null)
  const [isGeneratingReview, setIsGeneratingReview] = useState(false)
  const [isLoadingInternalReport, setIsLoadingInternalReport] = useState(false)
  const [modalError, setModalError] = useState<string | null>(null)
  const [isReportModalOpen, setIsReportModalOpen] = useState(false)
  const [pendingResetAfterSummary, setPendingResetAfterSummary] =
    useState(false)

  const [uploadedMaterials, setUploadedMaterials] = useState<
    UploadedMaterial[]
  >([])
  const historyStore = useSyncExternalStore(
    subscribeHistoryStore,
    loadHistoryEntries,
    getServerHistoryEntries,
  )
  const [serverHistoryEntries, setServerHistoryEntries] = useState<
    SessionHistoryEntry[]
  >([])
  const updateHistoryStore = useCallback(
    (
      updater:
        | SessionHistoryEntry[]
        | ((prev: SessionHistoryEntry[]) => SessionHistoryEntry[]),
    ) => {
      const previousEntries = loadHistoryEntries()
      const nextEntries =
        typeof updater === "function" ? updater(previousEntries) : updater
      writeHistoryEntries(nextEntries, { notify: true })
    },
    [],
  )

  const refreshServerHistory = useCallback(async () => {
    if (mockMode) {
      setServerHistoryEntries([])
      return
    }
    try {
      const response = await listSessions()
      setServerHistoryEntries(response.sessions.map(serverSessionToHistoryEntry))
    } catch {
      setServerHistoryEntries([])
    }
  }, [mockMode])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshServerHistory()
    }, 0)
    return () => {
      window.clearTimeout(timer)
    }
  }, [refreshServerHistory])
  const [composerCommand, setComposerCommand] =
    useState<ComposerCommand | null>(null)
  const [settingsFeedback, setSettingsFeedback] = useState<string | null>(null)
  const [isDebugBundleGenerating, setIsDebugBundleGenerating] = useState(false)
  const [debugBundleProgress, setDebugBundleProgress] = useState<string[]>([])
  const [runtimeDebugSnapshot, setRuntimeDebugSnapshot] =
    useState<RuntimeDebugSnapshot | null>(null)
  const [runtimeDebugEvents, setRuntimeDebugEvents] = useState<
    RuntimeDebugEvent[]
  >([])
  const [latestDebugMaterialBundle, setLatestDebugMaterialBundle] =
    useState<DebugMaterialBundleResponse | null>(null)
  const [materialPackages, setMaterialPackages] = useState<
    MaterialPackageArchiveItem[]
  >([])
  const [isLoadingMaterialPackages, setIsLoadingMaterialPackages] =
    useState(false)
  const [isImportingMaterialPackage, setIsImportingMaterialPackage] =
    useState(false)
  const [isLoadingRuntimeDebug, setIsLoadingRuntimeDebug] = useState(false)
  const [runtimeDebugError, setRuntimeDebugError] = useState<string | null>(null)
  const [userModelConfig, setUserModelConfig] = useState<UserModelConfig>(() =>
    loadUserModelConfig(),
  )
  const [availableModels, setAvailableModels] = useState<ModelListItem[]>([])
  const [isLoadingModels, setIsLoadingModels] = useState(false)
  const [modelConfigError, setModelConfigError] = useState<string | null>(null)
  const [ragStatus, setRagStatus] = useState<RagStatus | null>(null)
  const [isLoadingRagStatus, setIsLoadingRagStatus] = useState(false)
  const [isUploadingRagFile, setIsUploadingRagFile] = useState(false)
  const [ragError, setRagError] = useState<string | null>(null)

  const [isPaused, setIsPaused] = useState(false)
  const [sessionTime, setSessionTime] = useState(0)

  const sessionId = session?.session_id ?? null

  useEffect(() => {
    if (!settingsFeedback) {
      return
    }

    const timer = window.setTimeout(() => {
      setSettingsFeedback(null)
    }, 2600)

    return () => {
      window.clearTimeout(timer)
    }
  }, [settingsFeedback])

  useEffect(() => {
    persistUserModelConfig(userModelConfig)
  }, [userModelConfig])

  useEffect(() => {
    if (!sessionId || isPaused) {
      return
    }

    const timer = window.setInterval(() => {
      setSessionTime((prev) => prev + 1)
    }, 1000)

    return () => {
      window.clearInterval(timer)
    }
  }, [isPaused, sessionId])

  const appendMessage = useCallback(
    (message: Omit<ChatMessage, "id" | "timestamp">) => {
      const id = createClientId(message.role)
      setMessages((prev) => [
        ...prev,
        {
          id,
          timestamp: getTimestamp(),
          ...message,
        },
      ])
      return id
    },
    [],
  )

  const appendActivityEvent = useCallback(
    (event: Omit<SessionActivityEvent, "id" | "timestamp">) => {
      const id = createClientId(`activity-${event.kind}`)
      setActivityEvents((prev) =>
        [
          ...prev,
          {
            id,
            timestamp: getTimestamp(),
            ...event,
          },
        ].slice(-80),
      )
      return id
    },
    [],
  )

  const updateActivityEventStatus = useCallback(
    (id: string, status: SessionActivityEvent["status"]) => {
      setActivityEvents((prev) =>
        prev.map((event) => (event.id === id ? { ...event, status } : event)),
      )
    },
    [],
  )

  const updateActivityEventContent = useCallback(
    (id: string, content: string) => {
      setActivityEvents((prev) =>
        prev.map((event) => (event.id === id ? { ...event, content } : event)),
      )
    },
    [],
  )

  const updateMessagePatch = useCallback(
    (id: string, patch: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((msg) => (msg.id === id ? { ...msg, ...patch } : msg)),
      )
    },
    [],
  )

  const updateMessageStatus = useCallback(
    (id: string, status: ChatMessage["status"]) => {
      updateMessagePatch(id, { status })
    },
    [updateMessagePatch],
  )

  const updateMessageFailure = useCallback(
    (id: string, errorDetail: string) => {
      updateMessagePatch(id, { status: "error", error_detail: errorDetail })
    },
    [updateMessagePatch],
  )

  const updateMessageAttachment = useCallback(
    (
      messageId: string,
      attachmentId: string,
      patch: Partial<ChatAttachment>,
    ) => {
      setMessages((prev) =>
        prev.map((message) => {
          if (message.id !== messageId || !message.attachments?.length) {
            return message
          }

          return {
            ...message,
            attachments: message.attachments.map((attachment) =>
              attachment.id === attachmentId
                ? { ...attachment, ...patch }
                : attachment,
            ),
          }
        }),
      )
    },
    [],
  )

  const getErrorMessage = useCallback((error: unknown, fallback: string) => {
    if (error instanceof ApiError) {
      if (error.status === 403) {
        return "当前部署未启用这个调试或流式功能，请检查后端开关。"
      }
      if (error.status === 401) {
        return "当前对话模型认证失败，API Key 可能已失效或被禁用。"
      }
      if (error.status === 429) {
        return "当前对话模型额度已耗尽或请求过于频繁，请稍后重试。"
      }
      if (
        error.status === 503 ||
        error.status === 502 ||
        error.status === 504
      ) {
        return error.message || "当前对话模型不可用或运行失败。"
      }
      return `请求失败：${error.message}`
    }
    return fallback
  }, [])

  const refreshMaterialPackages = useCallback(async () => {
    if (mockMode) {
      setMaterialPackages([])
      return
    }

    setIsLoadingMaterialPackages(true)
    try {
      const response = await listMaterialPackages()
      setMaterialPackages(response.packages)
    } catch (error) {
      setSettingsFeedback(getErrorMessage(error, "获取材料包存档失败。"))
    } finally {
      setIsLoadingMaterialPackages(false)
    }
  }, [getErrorMessage, mockMode])

  const refreshRagStatus = useCallback(async () => {
    if (mockMode) {
      setRagStatus({
        enabled: false,
        ready: false,
        status: "unavailable",
        skip_reason: "mock_mode",
        vector_store: "chroma",
        index_version: "v1",
        collection_prefix: "us_visa",
        chroma_mode: "persistent",
        embedding_model: "BAAI/bge-m3",
        rerank_model: "Qwen/Qwen3-Reranker-4B",
        upload_max_size_mb: 32,
        allow_third_party_reference: false,
        collections: [],
      })
      return
    }

    setIsLoadingRagStatus(true)
    setRagError(null)
    try {
      setRagStatus(await getRagStatus())
    } catch (error) {
      setRagError(getErrorMessage(error, "获取 RAG 状态失败。"))
    } finally {
      setIsLoadingRagStatus(false)
    }
  }, [getErrorMessage, mockMode])

  const fetchUserReport = useCallback(
    async (targetSessionId: string): Promise<UserReport | null> => {
      if (mockMode) {
        setUserReport(MOCK_USER_REPORT)
        return MOCK_USER_REPORT
      }

      setIsLoadingReport(true)
      setReportError(null)
      try {
        const report = await getUserReport(targetSessionId)
        setUserReport(report)
        return report
      } catch (error) {
        setReportError(getErrorMessage(error, "获取报告失败，请稍后重试。"))
        return null
      } finally {
        setIsLoadingReport(false)
      }
    },
    [getErrorMessage, mockMode],
  )

  const refreshReports = useCallback(
    async (targetSessionId: string): Promise<UserReport | null> => {
      return fetchUserReport(targetSessionId)
    },
    [fetchUserReport],
  )

  const recordRuntimeDebugEvent = useCallback((event: RuntimeDebugEvent) => {
    setRuntimeDebugEvents((prev) =>
      [
        ...prev,
        {
          ...event,
          received_at: event.received_at ?? new Date().toISOString(),
        },
      ].slice(-MAX_RUNTIME_DEBUG_EVENTS),
    )
  }, [])

  const syncUploadedMaterialsFromRuntimeDebugSnapshot = useCallback(
    (snapshot: RuntimeDebugSnapshot | null) => {
      const patches = materialUnderstandingPatchesFromSnapshot(snapshot)
      if (!patches.length) {
        return
      }
      setUploadedMaterials((prev) =>
        applyMaterialUnderstandingPatches(prev, patches),
      )
    },
    [],
  )

  const refreshRuntimeDebugSnapshot = useCallback(
    async (targetSessionId?: string | null): Promise<RuntimeDebugSnapshot | null> => {
      const nextSessionId = targetSessionId ?? sessionId
      if (!nextSessionId) {
        setRuntimeDebugError("当前没有可调试的会话。")
        return null
      }
      if (mockMode) {
        const mockSnapshot: RuntimeDebugSnapshot = {
          schema_version: "ds160.runtime_debug.v1.mock",
          backend: {
            version: "mock",
            agent_runtime: "mock",
            debug_enabled: true,
          },
          session: {
            session_id: nextSessionId,
            phase_state: session?.phase_state ?? "interview",
            declared_family: visaType,
          },
          runtime_view_state: MOCK_INTERNAL_REPORT.runtime_view_state ?? {},
          runtime_trace: MOCK_INTERNAL_REPORT.runtime_trace ?? [],
          material_generation: {},
          errors: [],
        }
        setRuntimeDebugSnapshot(mockSnapshot)
        setRuntimeDebugError(null)
        return mockSnapshot
      }

      setIsLoadingRuntimeDebug(true)
      setRuntimeDebugError(null)
      try {
        const snapshot = await getRuntimeDebugSnapshot(nextSessionId)
        setRuntimeDebugSnapshot(snapshot)
        syncUploadedMaterialsFromRuntimeDebugSnapshot(snapshot)
        return snapshot
      } catch (error) {
        setRuntimeDebugError(
          getErrorMessage(error, "获取运行时调试快照失败。"),
        )
        return null
      } finally {
        setIsLoadingRuntimeDebug(false)
      }
    },
    [
      getErrorMessage,
      mockMode,
      session?.phase_state,
      sessionId,
      syncUploadedMaterialsFromRuntimeDebugSnapshot,
      visaType,
    ],
  )

  const queueMaterialUnderstandingRefresh = useCallback(
    (targetSessionId: string, responses: FileUploadResponse[]) => {
      const trackedDocumentIds = new Set(
        responses
          .map((response) => response.document_id)
          .filter((documentId): documentId is string => Boolean(documentId)),
      )
      if (
        !trackedDocumentIds.size ||
        !responses.some(responseNeedsMaterialUnderstandingRefresh)
      ) {
        return
      }

      void (async () => {
        for (const delayMs of MATERIAL_UNDERSTANDING_REFRESH_DELAYS_MS) {
          await waitForMilliseconds(delayMs)
          const snapshot = await refreshRuntimeDebugSnapshot(targetSessionId)
          const patches = materialUnderstandingPatchesFromSnapshot(
            snapshot,
          ).filter(
            (patch) =>
              patch.document_id && trackedDocumentIds.has(patch.document_id),
          )
          if (
            patches.some((patch) =>
              isTerminalMaterialUnderstandingStatus(
                patch.understanding_status,
              ),
            )
          ) {
            return
          }
        }
      })()
    },
    [refreshRuntimeDebugSnapshot],
  )

  const handleCopyRuntimeDebugPackage = useCallback(async () => {
    if (!sessionId) {
      setSettingsFeedback("当前没有可复制的调试会话。")
      return
    }
    const snapshot =
      runtimeDebugSnapshot ?? (await refreshRuntimeDebugSnapshot(sessionId))
    const payload = {
      schema_version: "ds160.frontend_debug_package.v1",
      copied_at: new Date().toISOString(),
      frontend: APP_VERSION,
      session_id: sessionId,
      runtime_snapshot: snapshot,
      live_events: runtimeDebugEvents,
      activity_events: activityEvents.slice(-40),
      latest_debug_material_bundle: latestDebugMaterialBundle,
      client_state: {
        visa_type: visaType,
        message_count: messages.length,
        material_count: uploadedMaterials.length,
        last_message: messages.at(-1)
          ? sanitizeHistoryMessage(messages.at(-1) as ChatMessage)
          : null,
      },
    }
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2))
      setSettingsFeedback("调试包已复制到剪贴板。")
    } catch {
      downloadJsonFile(`ds160-debug-${sessionId}.json`, payload)
      setSettingsFeedback("无法写入剪贴板，已下载调试 JSON。")
    }
  }, [
    latestDebugMaterialBundle,
    activityEvents,
    messages,
    refreshRuntimeDebugSnapshot,
    runtimeDebugEvents,
    runtimeDebugSnapshot,
    sessionId,
    uploadedMaterials.length,
    visaType,
  ])

  const queueComposerCommand = useCallback((type: ComposerCommand["type"]) => {
    setComposerCommand({
      type,
      token: Date.now(),
    })
  }, [])

  const handleComposerCommandHandled = useCallback(() => {
    setComposerCommand(null)
  }, [])

  const createMessageAttachments = useCallback(
    async (files: File[]): Promise<ChatAttachment[]> => {
      return Promise.all(
        files.map(async (file) => {
          const kind = inferAttachmentKind(file)
          const previewUrl =
            kind === "image" ? await readFileAsDataUrl(file) : null

          return {
            id: createClientId("attachment"),
            name: file.name,
            mime_type: file.type,
            kind,
            size: file.size,
            preview_url: previewUrl,
            upload_status: "pending",
          }
        }),
      )
    },
    [],
  )

  const buildUploadedMaterial = useCallback(
    (
      file: File,
      attachment: ChatAttachment,
      feedbackMessage: string | null,
      response?: FileUploadResponse,
      isError = false,
    ): UploadedMaterial => ({
      id: attachment.id,
      session_id: sessionId,
      name: file.name,
      mime_type: file.type,
      kind: attachment.kind,
      size: file.size,
      preview_url: attachment.preview_url ?? null,
      uploaded_at: getIsoTimestamp(),
      status_label: isError
        ? "上传失败"
        : humanizeUnderstandingStatus(
            materialUnderstandingStatus(response ?? {}) ??
              response?.job_status ??
              response?.document_status ??
              null,
          ),
      document_id: response?.document_id,
      content_url:
        response?.document_id && sessionId
          ? getFileContentUrl(sessionId, response.document_id)
          : null,
      document_status: response?.document_status,
      understanding_status:
        materialUnderstandingStatus(response ?? {}) ??
        response?.understanding_status ??
        null,
      understanding_error:
        response?.understanding_error ??
        response?.case_board_delta?.latest_material?.understanding_error ??
        (response?.caseBoardRefresh?.failureMessage
          ? {
              code: response.caseBoardRefresh.failureNode ?? null,
              message: response.caseBoardRefresh.failureMessage,
            }
          : null) ??
        null,
      document_type:
        response?.document_type ??
        response?.document_assessment?.document_type ??
        null,
      document_type_label:
        response?.document_type_label ??
        response?.document_assessment?.document_type_label ??
        null,
      relevance:
        response?.relevance ?? response?.document_assessment?.relevance ?? null,
      feedback_message: feedbackMessage,
      evidence_cards:
        response?.case_board_delta?.evidence_cards ??
        response?.evidence_cards ??
        [],
      claims: response?.case_board_delta?.claims ?? [],
      proof_points: response?.case_board_delta?.open_proof_points ?? [],
      conflicts: response?.case_board_delta?.conflicts ?? [],
      next_move: response?.case_board_delta?.next_move ?? null,
      case_board_delta: response?.case_board_delta ?? null,
      caseBoardRefresh: response?.caseBoardRefresh ?? null,
      requested_document_labels: response?.requested_document_labels ?? [],
      current_focus_document_label:
        response?.main_flow_feedback?.current_focus_document_label ??
        response?.document_assessment?.main_flow_feedback
          ?.current_focus_document_label ??
        null,
      counts_toward_gate:
        response?.document_assessment?.counts_toward_gate ?? null,
    }),
    [sessionId],
  )

  const buildHistoryEntry = useCallback(
    (
      status: SessionHistoryEntry["status"],
      overrides?: {
        messages?: ChatMessage[]
        report?: UserReport | null
        materials?: UploadedMaterial[]
        summary?: string
      },
    ) => {
      if (!sessionId || !visaType) {
        return null
      }

      const existing = historyStore.find(
        (entry) => entry.session_id === sessionId,
      )
      const snapshotMessages = overrides?.messages ?? messages
      const snapshotReport = overrides?.report ?? userReport
      const snapshotMaterials = overrides?.materials ?? uploadedMaterials
      const historyMaterials = snapshotMaterials.map(sanitizeHistoryMaterial)

      return {
        id: existing?.id ?? sessionId,
        session_id: sessionId,
        visa_type: visaType,
        status,
        title: `${visaType} 面签会话`,
        summary:
          overrides?.summary ??
          snapshotReport?.summary ??
          snapshotMessages.at(-1)?.content ??
          "暂无摘要",
        last_message: snapshotMessages.at(-1)?.content ?? null,
        message_count: snapshotMessages.length,
        created_at: existing?.created_at ?? getIsoTimestamp(),
        updated_at: getIsoTimestamp(),
        required_package: requiredPackage,
        report: snapshotReport,
        materials: historyMaterials,
        messages: hydrateHistoryMessages(
          snapshotMessages.map(sanitizeHistoryMessage),
          historyMaterials,
        ),
      } satisfies SessionHistoryEntry
    },
    [
      historyStore,
      messages,
      requiredPackage,
      sessionId,
      uploadedMaterials,
      userReport,
      visaType,
    ],
  )

  const persistHistoryEntry = useCallback(
    (
      status: SessionHistoryEntry["status"],
      overrides?: {
        messages?: ChatMessage[]
        report?: UserReport | null
        materials?: UploadedMaterial[]
        summary?: string
      },
    ) => {
      const entry = buildHistoryEntry(status, overrides)
      if (!entry) {
        return
      }
      updateHistoryStore((prev) => [
        entry,
        ...prev.filter((item) => item.id !== entry.id),
      ])
    },
    [buildHistoryEntry, updateHistoryStore],
  )

  const activeHistoryEntry = useMemo(() => {
    if (!sessionId || !visaType) {
      return null
    }
    return buildHistoryEntry("active")
  }, [buildHistoryEntry, sessionId, visaType])

  const localHistoryEntries = useMemo(
    () =>
      historyStore.filter(
        (entry) =>
          !serverHistoryEntries.some(
            (serverEntry) => serverEntry.session_id === entry.session_id,
          ),
      ),
    [historyStore, serverHistoryEntries],
  )

  const sessionHistory = useMemo(() => {
    const mergedHistory = [...serverHistoryEntries, ...localHistoryEntries]
    if (!activeHistoryEntry) {
      return mergedHistory
    }
    return [
      activeHistoryEntry,
      ...mergedHistory.filter((entry) => entry.id !== activeHistoryEntry.id),
    ]
  }, [activeHistoryEntry, localHistoryEntries, serverHistoryEntries])

  const browserHistorySnapshot = useMemo(() => {
    if (!activeHistoryEntry) {
      return localHistoryEntries
    }
    return [
      activeHistoryEntry,
      ...localHistoryEntries.filter((entry) => entry.id !== activeHistoryEntry.id),
    ]
  }, [activeHistoryEntry, localHistoryEntries])

  useEffect(() => {
    writeHistoryEntries(browserHistorySnapshot)
  }, [browserHistorySnapshot])

  const clearCurrentSessionState = useCallback(() => {
    setSession(null)
    setVisaType(null)
    setRequiredPackage(null)
    setMessages([])
    setActivityEvents([])
    setUserReport(null)
    setInternalReport(null)
    setInterviewReview(null)
    setUploadedMaterials([])
    setInitError(null)
    setChatError(null)
    setReportError(null)
    setModalError(null)
    setIsPaused(false)
    setSessionTime(0)
    setIsReportModalOpen(false)
    setComposerCommand(null)
    setIsDebugBundleGenerating(false)
    setDebugBundleProgress([])
    setRuntimeDebugSnapshot(null)
    setRuntimeDebugEvents([])
    setLatestDebugMaterialBundle(null)
    setRuntimeDebugError(null)
    setPendingResetAfterSummary(false)
  }, [])

  const handleLoadBackendSession = useCallback(
    async (targetSessionId: string) => {
      const normalizedSessionId = targetSessionId.trim()
      if (!normalizedSessionId || mockMode) {
        return
      }

      setIsInitializing(true)
      setInitError(null)
      setChatError(null)
      setReportError(null)
      setModalError(null)
      const loadActivityId = createClientId("activity-session-load")
      setActivityEvents([
        {
          id: loadActivityId,
          kind: "session",
          content: `正在从后端恢复会话 ${normalizedSessionId}。`,
          timestamp: getTimestamp(),
          status: "sending",
        },
      ])

      try {
        const [transcript, report, nextRequiredPackage] = await Promise.all([
          fetchSessionMessages(normalizedSessionId),
          fetchUserReport(normalizedSessionId),
          getRequiredPackage(normalizedSessionId).catch(() => null),
        ])
        const restoredVisaType = visaFamilyFromReport(report)
        setSession({
          session_id: transcript.session_id,
          phase_state:
            report?.interview_status === "simulated_refusal"
              ? "completed"
              : "interview",
          current_governor_decision: report?.governor_decision ?? null,
          gate_status: null,
        })
        setVisaType(restoredVisaType)
        setRequiredPackage(
          nextRequiredPackage ?? getMockRequiredPackage(restoredVisaType),
        )
        setMessages(chatMessagesFromBackendTurns(transcript.messages))
        setUploadedMaterials([])
        setInternalReport(null)
        setInterviewReview(null)
        setDebugBundleProgress([])
        setRuntimeDebugEvents([])
        setLatestDebugMaterialBundle(null)
        setRuntimeDebugError(null)
        setIsReportModalOpen(false)
        setPendingResetAfterSummary(false)
        setSessionTime(0)
        setIsPaused(false)
        appendActivityEvent({
          kind: "session",
          content: `已从后端恢复 ${transcript.messages.length} 条会话消息。`,
          status: "sent",
        })
        void refreshRuntimeDebugSnapshot(transcript.session_id)
      } catch (error) {
        setInitError(getErrorMessage(error, "无法恢复这个后端会话。"))
        setActivityEvents((prev) =>
          prev.map((event) =>
            event.id === loadActivityId
              ? { ...event, status: "error" }
              : event,
          ),
        )
      } finally {
        setIsInitializing(false)
      }
    },
    [
      appendActivityEvent,
      fetchUserReport,
      getErrorMessage,
      mockMode,
      refreshRuntimeDebugSnapshot,
    ],
  )

  useEffect(() => {
    if (typeof window === "undefined" || mockMode) {
      return
    }
    const params = new URLSearchParams(window.location.search)
    const querySessionId = params.get("session_id")?.trim()
    if (
      !querySessionId ||
      loadedSessionIdFromQueryRef.current === querySessionId
    ) {
      return
    }
    loadedSessionIdFromQueryRef.current = querySessionId
    void handleLoadBackendSession(querySessionId)
  }, [handleLoadBackendSession, mockMode])

  const handleVisaSelect = useCallback(
    async (visaFamily: VisaFamily) => {
      setIsInitializing(true)
      setInitError(null)
      setChatError(null)
      setReportError(null)
      setModalError(null)
      setMessages([])
      setActivityEvents([])
      setUploadedMaterials([])
      setDebugBundleProgress([])
      setRuntimeDebugSnapshot(null)
      setRuntimeDebugEvents([])
      setLatestDebugMaterialBundle(null)
      setRuntimeDebugError(null)

      try {
        if (mockMode) {
          const mockRequiredPackage = getMockRequiredPackage(visaFamily)
          setSession({
            session_id: MOCK_SESSION_ID,
            phase_state: "interview",
            current_governor_decision:
              MOCK_USER_REPORT.governor_decision ?? null,
            gate_status: null,
          })
          setVisaType(visaFamily)
          setRequiredPackage(mockRequiredPackage)
          setActivityEvents([
            {
              id: createClientId("activity-session"),
              kind: "session",
              content: buildRequiredPackageMessage(
                visaFamily,
                mockRequiredPackage,
              ),
              timestamp: getTimestamp(),
            },
          ])
          setMessages(MOCK_MESSAGES.filter((message) => message.role !== "system"))
          setUserReport(MOCK_USER_REPORT)
          setInternalReport(MOCK_INTERNAL_REPORT)
          setSessionTime(1458)
          return
        }

        const createdSession = await createSession(visaFamily)
        const nextRequiredPackage = await getRequiredPackage(
          createdSession.session_id,
        )

        setSession(createdSession)
        setVisaType(visaFamily)
        setRequiredPackage(nextRequiredPackage)
        setActivityEvents([
          {
            id: createClientId("activity-session"),
            kind: "session",
            content: buildRequiredPackageMessage(
              visaFamily,
              nextRequiredPackage,
            ),
            timestamp: getTimestamp(),
          },
        ])
        setMessages([])
        setSessionTime(0)

        await fetchUserReport(createdSession.session_id)
        void refreshServerHistory()
      } catch (error) {
        setInitError(
          getErrorMessage(error, "无法连接到服务器，请确认后端已启动。"),
        )
      } finally {
        setIsInitializing(false)
      }
    },
    [fetchUserReport, getErrorMessage, mockMode, refreshServerHistory],
  )

  const handleSendMessage = useCallback(
    async (content: string, files?: File[], options?: SendMessageOptions) => {
      if (!sessionId || sendingRef.current || isSending || isUploading) {
        return
      }
      if (isTerminalInterviewState(session, userReport)) {
        setChatError("本轮面签已结束，不能继续发送消息或上传材料。")
        appendActivityEvent({
          kind: "message",
          content: "本轮已结束。你可以查看总结/复盘，或重新开始一轮面签。",
        })
        return
      }

      const trimmedContent = content.trim()
      const nextFiles = files ?? []
      const hasFiles = nextFiles.length > 0
      const hasContent = trimmedContent.length > 0

      if (!hasFiles && !hasContent) {
        return
      }
      sendingRef.current = true
      let userMsgId: string | null = null
      let streamAccepted = false
      streamProgressEventIdRef.current = null
      const upsertStreamProgress = (
        content: string,
        status: SessionActivityEvent["status"] = "sending",
      ) => {
        if (streamProgressEventIdRef.current) {
          updateActivityEventContent(streamProgressEventIdRef.current, content)
          updateActivityEventStatus(streamProgressEventIdRef.current, status)
          return
        }
        streamProgressEventIdRef.current = appendActivityEvent({
          kind: "message",
          content,
          status,
        })
      }
      try {
        const messageAttachments = hasFiles
          ? await createMessageAttachments(nextFiles)
          : []
        const clientMessageId = hasContent
          ? options?.clientMessageId ?? createClientId("client-message")
          : undefined
        if (options?.reuseMessageId) {
          userMsgId = options.reuseMessageId
          updateMessagePatch(userMsgId, {
            status: "sending",
            error_detail: null,
            retry_content: trimmedContent,
            client_message_id: clientMessageId ?? null,
          })
        } else {
          userMsgId = appendMessage({
            role: "user",
            content: trimmedContent,
            attachments: messageAttachments,
            status: "sending",
            client_message_id: clientMessageId ?? null,
            retry_content: trimmedContent,
          })
        }

        setChatError(null)

        let successfulUploads = 0
        const uploadResponses: FileUploadResponse[] = []

        if (hasFiles) {
          setIsUploading(true)

          for (const [index, file] of nextFiles.entries()) {
            const attachment = messageAttachments[index]

            if (mockMode) {
              const mockFeedback = `[Mock] 已上传文件：${file.name}。系统正在分析。`
              setUploadedMaterials((prev) => [
                buildUploadedMaterial(file, attachment, mockFeedback),
                ...prev.filter((item) => item.id !== attachment.id),
              ])
              appendActivityEvent({
                kind: "upload",
                content: mockFeedback,
              })
              successfulUploads += 1
              continue
            }

            try {
              const response = await uploadFile(
                sessionId,
                file,
                hasContent ? trimmedContent : undefined,
              )
              uploadResponses.push(response)
              const uploadFeedback = firstNonEmptyText(
                response.feedback_message ?? null,
                response.main_flow_feedback?.message ?? null,
                response.document_assessment?.main_flow_feedback?.message ??
                  null,
              )

              const uploadedMaterial = buildUploadedMaterial(
                file,
                attachment,
                uploadFeedback,
                response,
              )
              setUploadedMaterials((prev) => [
                uploadedMaterial,
                ...prev.filter((item) => item.id !== attachment.id),
              ])
              updateMessageAttachment(userMsgId, attachment.id, {
                document_id: response.document_id ?? null,
                session_id: sessionId,
                upload_status: "uploaded",
                preview_url:
                  resolvePersistentMaterialPreview(uploadedMaterial) ??
                  attachment.preview_url ??
                  null,
              })

              const uploadActivity = buildMaterialUnderstandingActivity(
                file.name,
                response,
                uploadFeedback,
              )
              appendActivityEvent({
                kind: uploadActivity.status === "error" ? "error" : "upload",
                content: uploadActivity.content,
                status: uploadActivity.status,
              })

              const gateProgressMessage = buildGateProgressMessage(
                response.gate_progress?.overall_status,
              )
              if (
                gateProgressMessage &&
                uploadActivity.status !== "sending" &&
                uploadActivity.status !== "error"
              ) {
                appendActivityEvent({
                  kind: "upload",
                  content: gateProgressMessage,
                })
              }

              queueMaterialUnderstandingRefresh(sessionId, [response])
              successfulUploads += 1
            } catch (error) {
              const fileError = getErrorMessage(
                error,
                `文件 ${file.name} 上传失败。`,
              )
              setUploadedMaterials((prev) => [
                buildUploadedMaterial(
                  file,
                  attachment,
                  fileError,
                  undefined,
                  true,
                ),
                ...prev.filter((item) => item.id !== attachment.id),
              ])
              updateMessageAttachment(userMsgId, attachment.id, {
                upload_status: "error",
              })
              appendActivityEvent({
                kind: "error",
                content: `错误：${fileError}`,
                status: "error",
              })
            }
          }

          setIsUploading(false)
          if (!hasContent) {
            updateMessageStatus(
              userMsgId,
              successfulUploads > 0 ? "sent" : "error",
            )
            if (successfulUploads > 0) {
              await refreshReports(sessionId)
              appendActivityEvent({
                kind: "upload",
                content: buildUploadOnlyMaterialActivitySummary(uploadResponses),
              })
            } else {
              await refreshReports(sessionId)
            }
          }
        }

        if (hasContent) {
          setIsSending(true)

          if (mockMode) {
            updateMessageStatus(userMsgId, "sent")
            appendActivityEvent({
              kind: "message",
              content: "Mock 模式已记录用户消息，未生成签证官回复。",
            })
          } else {
            const runtimeModelConfig = toRuntimeModelConfig(userModelConfig)
            if (userModelConfig.enabled && !runtimeModelConfig) {
              throw new Error(
                "请完整填写 Base URL、API Key 和模型名称，或关闭自带模型。",
              )
            }
            const shouldUseStream =
              !runtimeModelConfig || userModelConfig.streamingEnabled
            const response = shouldUseStream
              ? await sendMessageStream(
                  sessionId,
                  trimmedContent,
                  runtimeModelConfig,
                  clientMessageId,
                  (event) => {
                    if (event.event === "accepted") {
                      streamAccepted = true
                      if (userMsgId) {
                        updateMessageStatus(userMsgId, "sent")
                      }
                      upsertStreamProgress("消息已送达服务器，正在进入本轮分析。")
                      setSettingsFeedback("消息已送达服务器。")
                      return
                    }
                    if (event.event === "analyzing") {
                      const stillRunning = event.data.status === "still_running"
                      upsertStreamProgress(
                        stillRunning
                          ? "后端仍在核对材料、风险和下一步回复。"
                          : "正在核对材料、风险和下一步回复。",
                      )
                      setSettingsFeedback(
                        stillRunning ? "后端仍在处理中。" : "正在生成本轮回复。",
                      )
                      return
                    }
                    if (event.event === "debug_event") {
                      recordRuntimeDebugEvent(event.data)
                      return
                    }
                    if (event.event === "final") {
                      upsertStreamProgress("本轮回复已生成。", "sent")
                      setSettingsFeedback("本轮回复已生成。")
                      return
                    }
                    if (event.event === "error") {
                      upsertStreamProgress(
                        `处理失败：${describeMessageStreamError(event.data)}`,
                        "error",
                      )
                    }
                  },
                )
              : await sendMessage(
                  sessionId,
                  trimmedContent,
                  runtimeModelConfig,
                  clientMessageId,
                )
            updateMessageStatus(userMsgId, "sent")
            const assistantMessage =
              buildAssistantMessageFromBackendResponse(response)
            if (assistantMessage) {
              appendMessage(assistantMessage)
            }

            const requestedDocumentsMessage = buildEvidenceSuggestionMessage(
              response.requested_document_labels,
              response.governor_decision,
            )
            if (requestedDocumentsMessage) {
              appendActivityEvent({
                kind: "message",
                content: requestedDocumentsMessage,
              })
            }

            const gateProgressMessage = buildGateProgressMessage(
              response.gate_progress?.overall_status,
            )
            if (gateProgressMessage) {
              appendActivityEvent({
                kind: "message",
                content: gateProgressMessage,
              })
            }
            await refreshReports(sessionId)
            await refreshRuntimeDebugSnapshot(sessionId)
          }
        }
      } catch (error) {
        const streamError =
          error instanceof ApiError
            ? messageStreamErrorFromUnknown(error.data)
            : null
        const failureDetail = streamError
          ? describeMessageStreamError(streamError)
          : getErrorMessage(error, "发送失败，请重试。")
        if (userMsgId) {
          updateMessageFailure(userMsgId, failureDetail)
        }
        if (streamAccepted && sessionId) {
          upsertStreamProgress(
            streamError
              ? `处理失败：${failureDetail}`
              : "连接中断，但服务器已收到消息；请稍后重试本条或刷新当前分析状态。",
            "error",
          )
          await refreshReports(sessionId).catch(() => undefined)
        }
        setChatError(failureDetail)
      } finally {
        sendingRef.current = false
        setIsSending(false)
        setIsUploading(false)
      }
    },
    [
      appendActivityEvent,
      appendMessage,
      buildUploadedMaterial,
      createMessageAttachments,
      getErrorMessage,
      isSending,
      isUploading,
      mockMode,
      queueMaterialUnderstandingRefresh,
      refreshReports,
      refreshRuntimeDebugSnapshot,
      recordRuntimeDebugEvent,
      sessionId,
      session,
      updateActivityEventContent,
      updateActivityEventStatus,
      updateMessageAttachment,
      updateMessageFailure,
      updateMessagePatch,
      updateMessageStatus,
      userModelConfig,
      userReport,
    ],
  )

  const handleRetryMessage = useCallback(
    (message: ChatMessage) => {
      const retryContent = (message.retry_content ?? message.content).trim()
      if (!retryContent) {
        setChatError("这条失败消息没有可重试的文本内容；附件请重新上传。")
        return
      }
      void handleSendMessage(retryContent, undefined, {
        reuseMessageId: message.id,
        clientMessageId: message.client_message_id ?? undefined,
      })
    },
    [handleSendMessage],
  )

  const handleContinueAnswer = useCallback(() => {
    const currentKeyQuestion = userReport?.current_key_question
    appendActivityEvent({
      kind: "message",
      content:
        currentKeyQuestion && currentKeyQuestion !== "暂无"
          ? `请继续围绕“${currentKeyQuestion}”补充回答，优先说明具体事实。`
          : "请继续补充你的回答，可以提供更多细节或背景信息。",
    })
    queueComposerCommand("focus")
  }, [appendActivityEvent, queueComposerCommand, userReport])

  const handleViewDetails = useCallback(async () => {
    setIsReportModalOpen(true)
    setModalError(null)

    if (!sessionId) {
      return
    }

    if (mockMode) {
      setUserReport(MOCK_USER_REPORT)
      setInternalReport(MOCK_INTERNAL_REPORT)
      setInterviewReview(null)
      return
    }

    setIsLoadingInternalReport(true)
    try {
      const [latestUserReport, latestInternalReport] = await Promise.all([
        getUserReport(sessionId),
        getInternalReport(sessionId),
      ])
      setUserReport(latestUserReport)
      setInternalReport(latestInternalReport)
      setInterviewReview(null)
    } catch (error) {
      setModalError(getErrorMessage(error, "获取报告失败。"))
    } finally {
      setIsLoadingInternalReport(false)
    }
  }, [getErrorMessage, mockMode, sessionId])

  const handleActionClick = useCallback(
    async (action: AllowedAction) => {
      if (action.intent === "upload") {
        queueComposerCommand("upload")
        return
      }

      if (action.intent === "continue") {
        handleContinueAnswer()
        return
      }

      await handleViewDetails()
    },
    [handleContinueAnswer, handleViewDetails, queueComposerCommand],
  )

  const handlePause = useCallback(() => {
    setIsPaused((prev) => !prev)
  }, [])

  const handleEndSession = useCallback(async () => {
    if (!sessionId) {
      return
    }

    let latestUserReport = userReport

    setIsReportModalOpen(true)
    setModalError(null)
    setPendingResetAfterSummary(true)

    if (mockMode) {
      latestUserReport = MOCK_USER_REPORT
      setUserReport(MOCK_USER_REPORT)
      setInternalReport(MOCK_INTERNAL_REPORT)
      setInterviewReview(null)
      persistHistoryEntry("completed", { report: MOCK_USER_REPORT })
    } else {
      setIsLoadingInternalReport(true)
      try {
        const [nextUserReport, nextInternalReport] = await Promise.all([
          getUserReport(sessionId),
          getInternalReport(sessionId),
        ])
        latestUserReport = nextUserReport
        setUserReport(nextUserReport)
        setInternalReport(nextInternalReport)
        setInterviewReview(null)
        persistHistoryEntry("completed", { report: nextUserReport })
      } catch (error) {
        setModalError(getErrorMessage(error, "获取总结失败。"))
        persistHistoryEntry("completed")
      } finally {
        setIsLoadingInternalReport(false)
      }
    }

    if (latestUserReport?.summary) {
      appendActivityEvent({
        kind: "report",
        content: `本轮总结：${latestUserReport.summary}`,
      })
    }
  }, [
    appendActivityEvent,
    getErrorMessage,
    mockMode,
    persistHistoryEntry,
    sessionId,
    userReport,
  ])

  const handleReset = useCallback(() => {
    if (sessionId && visaType && !pendingResetAfterSummary) {
      persistHistoryEntry("abandoned")
    }

    clearCurrentSessionState()
  }, [
    clearCurrentSessionState,
    pendingResetAfterSummary,
    persistHistoryEntry,
    sessionId,
    visaType,
  ])

  const handleReportModalOpenChange = useCallback(
    (open: boolean) => {
      setIsReportModalOpen(open)
      if (!open && pendingResetAfterSummary) {
        clearCurrentSessionState()
      }
    },
    [clearCurrentSessionState, pendingResetAfterSummary],
  )

  const handleGenerateInterviewReview = useCallback(async () => {
    if (!sessionId) {
      setModalError("当前没有可复盘的会话。")
      return
    }

    if (mockMode) {
      setInterviewReview({
        schema_version: "ds160.interview_review.v1.mock",
        source: "fallback",
        report: {
          outcome: "阶段性面签复盘",
          outcome_reason: MOCK_USER_REPORT.summary,
          executive_summary: "这是一份 Mock 复盘，用于验证复盘 UI。",
          strengths: ["已完成基础问答。"],
          refusal_or_risk_reasons: MOCK_USER_REPORT.risk_points,
          missing_or_weak_evidence: MOCK_USER_REPORT.missing_evidence.map(
            (item) => item.name,
          ),
          conversation_issues: ["回答需要更具体、更像真实窗口问答。"],
          document_findings: ["Mock 材料记录可用于调试展示。"],
          improvement_plan: MOCK_USER_REPORT.recommended_improvements,
          next_practice_focus: ["下一轮重点练习资金来源和学习计划。"],
        },
      })
      return
    }

    setIsGeneratingReview(true)
    setModalError(null)
    try {
      const review = await generateInterviewReview(sessionId)
      setInterviewReview(review)
    } catch (error) {
      setModalError(getErrorMessage(error, "生成复盘失败，请稍后重试。"))
    } finally {
      setIsGeneratingReview(false)
    }
  }, [getErrorMessage, mockMode, sessionId])

  const handleCopySessionId = useCallback(async () => {
    if (!sessionId) {
      setSettingsFeedback("当前没有进行中的会话 ID。")
      return
    }

    try {
      await navigator.clipboard.writeText(sessionId)
      setSettingsFeedback("会话 ID 已复制到剪贴板。")
    } catch {
      setSettingsFeedback("复制失败，请手动复制当前会话 ID。")
    }
  }, [sessionId])

  const handleUserModelConfigChange = useCallback(
    (nextConfig: UserModelConfig) => {
      setUserModelConfig(nextConfig)
      setModelConfigError(null)
    },
    [],
  )

  const handleFetchUserModels = useCallback(async () => {
    const baseUrl = userModelConfig.baseUrl.trim()
    const apiKey = userModelConfig.apiKey.trim()
    if (!baseUrl || !apiKey) {
      setModelConfigError("请先填写 Base URL 和 API Key。")
      return
    }

    setIsLoadingModels(true)
    setModelConfigError(null)
    try {
      const response = await listUserModels(baseUrl, apiKey)
      setAvailableModels(response.models)
      setSettingsFeedback(
        response.models.length
          ? `已获取 ${response.models.length} 个模型。`
          : "模型服务未返回可选模型，可手动输入模型名称。",
      )
    } catch (error) {
      setAvailableModels([])
      setModelConfigError(
        getErrorMessage(error, "模型列表获取失败，可手动输入模型名称。"),
      )
    } finally {
      setIsLoadingModels(false)
    }
  }, [getErrorMessage, userModelConfig.apiKey, userModelConfig.baseUrl])

  const handleUploadRagFile = useCallback(
    async (file: File, metadata: RagUploadMetadata = {}) => {
      if (mockMode) {
        setSettingsFeedback("Mock 模式不会写入 RAG 知识库。")
        return
      }

      setIsUploadingRagFile(true)
      setRagError(null)
      try {
        const response = await uploadRagFile(file, metadata)
        if (response.skipped) {
          setRagError(`RAG 上传已跳过：${response.skip_reason ?? "未知原因"}`)
        } else {
          setSettingsFeedback(
            `已写入 RAG 知识库：${response.chunk_count} 个分块。`,
          )
        }
        await refreshRagStatus()
      } catch (error) {
        setRagError(getErrorMessage(error, "RAG 文件上传失败。"))
      } finally {
        setIsUploadingRagFile(false)
      }
    },
    [getErrorMessage, mockMode, refreshRagStatus],
  )

  const handleExportSession = useCallback(async () => {
    if (!sessionId) {
      setSettingsFeedback("当前没有可导出的会话。")
      return
    }

    try {
      const payload = mockMode
        ? {
            schema_version: "ds160.session_export.v1.mock",
            session: {
              session_id: sessionId,
              visa_type: visaType,
              phase_state: session?.phase_state ?? "interview",
            },
            reports: {
              user: userReport ?? MOCK_USER_REPORT,
              internal: internalReport ?? MOCK_INTERNAL_REPORT,
            },
            messages: messages.map(sanitizeHistoryMessage),
            documents: uploadedMaterials.map((material) => ({
              id: material.id,
              filename: material.name,
              status: material.document_status ?? material.status_label,
              extracted_text: "",
              assessment: {
                document_type: material.document_type,
                relevance: material.relevance,
                feedback_message: material.feedback_message,
                counts_toward_gate: material.counts_toward_gate,
              },
            })),
          }
        : await exportSession(sessionId)
      downloadJsonFile(`ds160-session-${sessionId}.json`, payload)
      setSettingsFeedback("会话 JSON 已导出。")
    } catch (error) {
      setSettingsFeedback(getErrorMessage(error, "导出会话失败，请稍后重试。"))
    }
  }, [
    getErrorMessage,
    internalReport,
    messages,
    mockMode,
    session,
    sessionId,
    uploadedMaterials,
    userReport,
    visaType,
  ])

  const handleExportConversationImage = useCallback(async () => {
    if (!sessionId) {
      setSettingsFeedback("当前没有可导出的会话。")
      return
    }
    if (!messages.length) {
      setSettingsFeedback("当前会话还没有消息可导出。")
      return
    }

    try {
      await exportConversationLongImage(
        `ds160-session-${sessionId}-conversation.png`,
        {
          sessionId,
          visaType,
          messages,
        },
      )
      setSettingsFeedback("会话长截图已导出。")
    } catch {
      setSettingsFeedback("导出会话长截图失败，请稍后重试。")
    }
  }, [messages, sessionId, visaType])

  const handleExportReviewImage = useCallback(async () => {
    if (!sessionId) {
      setSettingsFeedback("当前没有可导出的会话。")
      return
    }
    if (!interviewReview) {
      setSettingsFeedback("请先生成复盘，再导出截图。")
      return
    }

    try {
      await exportReviewReportImage(`interview-review-${sessionId}.png`, {
        sessionId,
        visaType,
        review: interviewReview,
      })
      setSettingsFeedback("复盘截图已导出。")
    } catch {
      setSettingsFeedback("导出复盘截图失败，请稍后重试。")
    }
  }, [interviewReview, sessionId, visaType])

  const handleImportMaterialPackage = useCallback(
    async (packageId: string) => {
      if (!sessionId || isImportingMaterialPackage) {
        setSettingsFeedback(
          !sessionId ? "当前没有可导入材料包的会话。" : "材料包正在导入中。",
        )
        return
      }
      if (mockMode) {
        setSettingsFeedback("Mock 模式不导入后端材料包。")
        return
      }

      setIsImportingMaterialPackage(true)
      const progressMessageId = appendActivityEvent({
        kind: "debug",
        content: "正在导入已验证案例包...",
        status: "sending",
      })
      try {
        const result = await importMaterialPackage(sessionId, packageId)
        const importWarning = getImportStatusWarning(result)
        updateActivityEventStatus(progressMessageId, importWarning ? "error" : "sent")
        if (importWarning) {
          updateActivityEventContent(
            progressMessageId,
            `案例包导入未完全成功：${importWarning}`,
          )
        }
        setUploadedMaterials((prev) => {
          const nextMaterials = result.documents.map((document) =>
            materialPackageDocumentToMaterial(sessionId, document, result),
          )
          const importedIds = new Set(
            nextMaterials.map((material) => material.id),
          )
          return [
            ...nextMaterials,
            ...prev.filter((material) => !importedIds.has(material.id)),
          ]
        })
        setSession((prev) =>
          prev
            ? {
                ...prev,
                phase_state: result.phase_state,
                current_governor_decision:
                  result.governor_decision ?? prev.current_governor_decision,
                gate_status:
                  mapSessionGateStatus(result.gate_status) ?? prev.gate_status,
              }
            : prev,
        )
        appendActivityEvent({
          kind: importWarning ? "error" : "debug",
          content: importWarning
            ? `案例包已复制 ${result.documents.length} 份材料，但存在风险：${importWarning}`
            : `已导入已验证案例包：${result.documents.length} 份材料。`,
          status: importWarning ? "error" : "sent",
        })
        if (result.assistant_message) {
          appendMessage({
            role: "assistant",
            content: humanizeBackendText(result.assistant_message),
            public_reasoning: isPublicReasoning(
              result.runtime_view_state?.public_reasoning,
            )
              ? result.runtime_view_state.public_reasoning
              : null,
          })
        }
        await refreshReports(sessionId)
        await refreshRuntimeDebugSnapshot(sessionId)
        await refreshMaterialPackages()
        if (importWarning) {
          setSettingsFeedback(`案例包导入需要处理：${importWarning}`)
        } else {
          setSettingsFeedback("已验证案例包已导入当前会话。")
        }
      } catch (error) {
        updateActivityEventStatus(progressMessageId, "error")
        setSettingsFeedback(getErrorMessage(error, "导入材料包失败。"))
      } finally {
        setIsImportingMaterialPackage(false)
      }
    },
    [
      appendActivityEvent,
      appendMessage,
      getErrorMessage,
      isImportingMaterialPackage,
      mockMode,
      refreshMaterialPackages,
      refreshReports,
      refreshRuntimeDebugSnapshot,
      sessionId,
      updateActivityEventContent,
      updateActivityEventStatus,
    ],
  )

  const runDebugMaterialBundle = useCallback(
    async (scenario: DebugMaterialBundleScenario, seedText?: string) => {
      if (!sessionId || isDebugBundleGenerating) {
        setSettingsFeedback(
          !sessionId ? "当前没有可生成材料包的会话。" : "材料包正在生成中。",
        )
        return
      }
      if (mockMode) {
        appendActivityEvent({
          kind: "debug",
          content: "[Mock] 已生成一组材料包，并写入材料库。",
        })
        setSettingsFeedback("Mock 材料包已生成。")
        return
      }

      const progressLines: string[] = []
      const normalizedSeedText = seedText?.trim() ?? ""
      if (!normalizedSeedText) {
        const message = "请先填写材料生成依据；系统不会写入演示占位材料。"
        setSettingsFeedback(message)
        appendActivityEvent({
          kind: "debug",
          content: message,
          status: "error",
        })
        return
      }
      const progressMessageId = appendActivityEvent({
        kind: "debug",
        content: `正在根据提示词生成${getDebugMaterialBundleOption(scenario).label}...`,
        status: "sending",
      })
      const updateProgress = (line: string) => {
        progressLines.push(line)
        setDebugBundleProgress([...progressLines])
        updateActivityEventContent(
          progressMessageId,
          buildDebugBundleProgressMessage(progressLines),
        )
      }

      setIsDebugBundleGenerating(true)
      setSettingsFeedback("正在生成调试合成材料...")
      setDebugBundleProgress([])
      try {
        const result = await createDebugMaterialBundleStream(
          sessionId,
          scenario,
          true,
          (event) => {
            recordRuntimeDebugEvent(runtimeDebugEventFromMaterialEvent(event))
            updateProgress(describeDebugBundleEvent(event))
          },
          normalizedSeedText,
          "ai_if_available",
        )
        setLatestDebugMaterialBundle(result)
        updateActivityEventStatus(progressMessageId, "sent")
        setSession((prev) =>
          prev
            ? {
                ...prev,
                phase_state: result.phase_state,
                current_governor_decision:
                  result.governor_decision ?? prev.current_governor_decision,
                gate_status:
                  mapSessionGateStatus(result.gate_status) ?? prev.gate_status,
              }
            : prev,
        )
        setUploadedMaterials((prev) => {
          const nextMaterials = result.documents.map((document) =>
            debugBundleDocumentToMaterial(sessionId, document, result),
          )
          const generatedIds = new Set(
            nextMaterials.map((material) => material.id),
          )
          return [
            ...nextMaterials,
            ...prev.filter((material) => !generatedIds.has(material.id)),
          ]
        })
        appendActivityEvent({
          kind: "debug",
          content: buildDebugBundleFinalMessage(result),
        })
        if (result.assistant_message) {
          appendMessage({
            role: "assistant",
            content: humanizeBackendText(result.assistant_message),
            public_reasoning: isPublicReasoning(
              result.runtime_view_state?.public_reasoning,
            )
              ? result.runtime_view_state.public_reasoning
              : null,
          })
        }
        await refreshReports(sessionId)
        await refreshRuntimeDebugSnapshot(sessionId)
        await refreshMaterialPackages()
        if (result.main_flow_refresh_error) {
          setSettingsFeedback(
            `调试合成材料已生成，但下一步刷新失败：${result.main_flow_refresh_error}`,
          )
        } else {
          setSettingsFeedback("AI 调试合成材料已生成，前端已接收完整流式进度。")
        }
      } catch (error) {
        updateActivityEventStatus(progressMessageId, "error")
        const message = getErrorMessage(
          error,
          "调试材料包生成失败，请稍后重试。",
        )
        updateProgress(`错误：${message}`)
        setSettingsFeedback(message)
      } finally {
        setIsDebugBundleGenerating(false)
      }
    },
    [
      appendActivityEvent,
      appendMessage,
      getErrorMessage,
      isDebugBundleGenerating,
      mockMode,
      refreshReports,
      refreshMaterialPackages,
      refreshRuntimeDebugSnapshot,
      recordRuntimeDebugEvent,
      sessionId,
      updateActivityEventContent,
      updateActivityEventStatus,
    ],
  )

  const handleDebugMaterialBundleScenario = useCallback(
    (scenario: DebugMaterialBundleScenario, seedText?: string) =>
      runDebugMaterialBundle(scenario, seedText),
    [runDebugMaterialBundle],
  )

  const handleDebugFillCurrentGap = useCallback(
    () => runDebugMaterialBundle("normal_f1_bundle"),
    [runDebugMaterialBundle],
  )

  const handleClearHistory = useCallback(async () => {
    removeHistoryEntries()

    if (mockMode) {
      setServerHistoryEntries([])
      setSettingsFeedback("本账号的会话历史记录已清理。")
      return
    }

    try {
      const result = await clearAccountSessions(sessionId)
      await refreshServerHistory()
      const preservedCurrentSession = sessionId
        ? "，当前进行中的会话已保留"
        : ""
      const deletedSummary =
        result.deleted_count > 0 ? `，已删除 ${result.deleted_count} 条账号记录` : ""
      setSettingsFeedback(
        `本账号的会话历史记录已清理${deletedSummary}${preservedCurrentSession}。`,
      )
    } catch (error) {
      setSettingsFeedback(
        `旧版本和本浏览器会话记录已清理；账号记录清理失败：${getErrorMessage(
          error,
          "请稍后重试。",
        )}`,
      )
    }
  }, [getErrorMessage, mockMode, refreshServerHistory, sessionId])

  const handleRestoreSession = useCallback((entry: SessionHistoryEntry) => {
    setSession({
      session_id: entry.session_id,
      phase_state: entry.status === "completed" ? "completed" : "interview",
      current_governor_decision: entry.report?.governor_decision ?? null,
      gate_status: null,
    })
    setVisaType(entry.visa_type)
    setRequiredPackage(entry.required_package)
    const restoredMaterials = entry.materials.map(sanitizeHistoryMaterial)
    setMessages(hydrateHistoryMessages(entry.messages, restoredMaterials))
    setActivityEvents([])
    setUserReport(entry.report)
    setInternalReport(null)
    setInterviewReview(null)
    setUploadedMaterials(restoredMaterials)
    setChatError(null)
    setReportError(null)
    setModalError(null)
    setIsDebugBundleGenerating(false)
    setDebugBundleProgress([])
    setRuntimeDebugSnapshot(null)
    setRuntimeDebugEvents([])
    setLatestDebugMaterialBundle(null)
    setRuntimeDebugError(null)
    setIsReportModalOpen(false)
    setPendingResetAfterSummary(false)
    setSessionTime(0)
    setIsPaused(false)
  }, [])

  const sessionTimeLabel = useMemo(() => {
    const hours = Math.floor(sessionTime / 3600)
    const minutes = Math.floor((sessionTime % 3600) / 60)
    const seconds = sessionTime % 60
    return [hours, minutes, seconds]
      .map((value) => value.toString().padStart(2, "0"))
      .join(":")
  }, [sessionTime])

  const currentFocusDocumentLabel = useMemo(() => {
    return (
      userReport?.current_key_proof_label ??
      (userReport?.current_key_proof
        ? toDocumentLabel(userReport.current_key_proof)
        : null)
    )
  }, [userReport])

  const isInterviewTerminal = isTerminalInterviewState(session, userReport)

  return {
    apiBaseUrl,
    mockMode,
    session,
    sessionId,
    isInterviewTerminal,
    visaType,
    requiredPackage,
    isInitializing,
    initError,
    messages,
    activityEvents,
    isSending,
    isUploading,
    chatError,
    userReport,
    isLoadingReport,
    reportError,
    internalReport,
    interviewReview,
    isGeneratingReview,
    isLoadingInternalReport,
    modalError,
    isReportModalOpen,
    handleReportModalOpenChange,
    handleGenerateInterviewReview,
    isPaused,
    sessionTimeLabel,
    uploadedMaterials,
    sessionHistory,
    composerCommand,
    settingsFeedback,
    isDebugBundleGenerating,
    debugBundleProgress,
    materialPackages,
    isLoadingMaterialPackages,
    isImportingMaterialPackage,
    runtimeDebugSnapshot,
    runtimeDebugEvents,
    latestDebugMaterialBundle,
    isLoadingRuntimeDebug,
    runtimeDebugError,
    userModelConfig,
    availableModels,
    isLoadingModels,
    modelConfigError,
    ragStatus,
    isLoadingRagStatus,
    isUploadingRagFile,
    ragError,
    currentFocusDocumentLabel,
    handleComposerCommandHandled,
    handleVisaSelect,
    handleSendMessage,
    handleRetryMessage,
    handleViewDetails,
    handleActionClick,
    handlePause,
    handleEndSession,
    handleReset,
    handleCopySessionId,
    handleLoadBackendSession,
    handleUserModelConfigChange,
    handleFetchUserModels,
    handleUploadRagFile,
    refreshRagStatus,
    handleExportSession,
    handleExportConversationImage,
    handleExportReviewImage,
    refreshRuntimeDebugSnapshot,
    handleCopyRuntimeDebugPackage,
    handleDebugMaterialBundleScenario,
    refreshMaterialPackages,
    handleImportMaterialPackage,
    handleDebugFillCurrentGap,
    handleClearHistory,
    handleRestoreSession,
  }
}
