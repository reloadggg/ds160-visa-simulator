"use client"

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react"
import { toPng } from "html-to-image"

import {
  ApiError,
  createSession,
  createDebugMaterialBundleStream,
  exportSession,
  generateInterviewReview,
  getFileContentUrl,
  getInternalReport,
  getRequiredPackage,
  getRagStatus,
  getUserReport,
  listUserModels,
  sendMessage,
  sendMessageStream,
  uploadFile,
  uploadRagFile,
} from "@/lib/api/client"
import { getApiBaseUrl } from "@/lib/api/config"
import {
  getMockRequiredPackage,
  isMockMode,
  MOCK_INTERNAL_REPORT,
  MOCK_MESSAGES,
  MOCK_SESSION_ID,
  MOCK_USER_REPORT,
} from "@/lib/api/mock-data"
import { humanizeBackendText, mapSessionGateStatus, toDocumentLabel } from "@/lib/api/mappers"
import type {
  AllowedAction,
  AttachmentKind,
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
  ModelListItem,
  RagUploadMetadata,
  RagStatus,
  RequiredPackage,
  Session,
  SessionHistoryEntry,
  UploadedMaterial,
  UserModelConfig,
  UserModelRuntimeConfig,
  UserReport,
  VisaFamily,
} from "@/lib/api/types"

const HISTORY_STORAGE_KEY = "ds160-web-history-v1"
const MODEL_CONFIG_STORAGE_KEY = "ds160-user-model-config-v1"
const MAX_PERSISTED_PREVIEW_BYTES = 2 * 1024 * 1024

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

function toRuntimeModelConfig(config: UserModelConfig): UserModelRuntimeConfig | null {
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

function formatRequestedDocuments(labels: string[]): string {
  if (!labels.length) {
    return ""
  }

  return labels.join("、")
}

function buildRequiredPackageMessage(visaFamily: VisaFamily, requiredPackage: RequiredPackage): string {
  const materialHint = requiredPackage.required_initial_package_labels.length
    ? `如果你手边有 ${formatRequestedDocuments(
    requiredPackage.required_initial_package_labels,
  )}，可以在对话过程中随时上传。`
    : "如果你手边有 I-20、DS-160 确认页、护照首页或资金证明，可以在对话过程中随时上传。"

  return `你好，我们开始今天的 ${visaFamily} 签证模拟。我会像真实窗口面谈一样，先了解你的学习计划，再结合材料核对关键细节。\n\n你先简单介绍一下：这次去美国读什么项目？为什么选择这所学校？\n\n${materialHint}`
}

function buildRequestedDocumentsMessage(
  requestedDocumentLabels: string[],
  governorDecision?: string | null,
): string | null {
  if (!requestedDocumentLabels.length) {
    return null
  }

  const documentList = formatRequestedDocuments(requestedDocumentLabels)
  if (governorDecision === "need_more_evidence") {
    return `请补充：${documentList}。`
  }

  return `还需要核对：${documentList}。`
}

function buildOfficerUploadFollowUp(
  responses: FileUploadResponse[],
  fallbackLabels: string[],
): string {
  const uploadedLabels = Array.from(
    new Set(
      responses
        .map((response) => response.document_type_label ?? response.document_assessment?.document_type_label)
        .filter((label): label is string => Boolean(label)),
    ),
  )
  const remainingLabels = Array.from(
    new Set(
      responses.flatMap((response) => response.remaining_required_document_labels ?? []),
    ),
  )
  const focusLabels = remainingLabels.length ? remainingLabels : fallbackLabels

  if (uploadedLabels.length && !focusLabels.length) {
    return `我收到了你上传的${formatRequestedDocuments(uploadedLabels)}。你这次赴美学习什么项目？`
  }

  if (uploadedLabels.length && focusLabels.length) {
    return `我收到了你上传的${formatRequestedDocuments(uploadedLabels)}。请补充${formatRequestedDocuments(focusLabels)}。`
  }

  if (focusLabels.length) {
    return `材料已收到。请补充${formatRequestedDocuments(focusLabels)}。`
  }

  return "材料已收到。你这次赴美学习什么项目？"
}

function buildGateProgressMessage(overallStatus?: string): string | null {
  if (overallStatus === "waiting_for_parse") {
    return "材料已收到，系统正在解析，请稍后继续查看更新。"
  }

  return null
}

function truncateProgressLine(value: string): string {
  return value.length > 220 ? `${value.slice(0, 217)}...` : value
}

function describeDebugBundleEvent(event: DebugMaterialBundleStreamEvent): string {
  switch (event.event) {
    case "accepted":
      return "已收到材料包生成请求。"
    case "debug_bundle_started":
      return `开始生成${event.data.scenario_label ?? "调试材料包"}，预计 ${event.data.document_count ?? 0} 份材料。`
    case "document_created":
      return `已生成材料：${event.data.document_type_label ?? event.data.filename ?? "材料" }。`
    case "evidence_written":
      return truncateProgressLine(`已写入结构化字段：${Object.keys(event.data.fields ?? {}).join("、") || "字段待确认"}。`)
    case "profile_recomputed":
      return "已根据材料重新计算申请人档案。"
    case "gate_refreshed":
      return "已刷新材料门控状态。"
    case "document_review_started":
      return "开始进行材料交叉核验。"
    case "governor_decided":
      return `已完成本轮裁决：${humanizeBackendText(event.data.governor_decision ?? "") || "状态待确认"}。`
    case "final":
      return "材料包生成完成。"
    case "error":
      return `材料包生成失败：${event.data.detail ?? "未知错误"}`
    default:
      return "材料包生成状态已更新。"
  }
}

function buildDebugBundleProgressMessage(lines: string[]): string {
  return lines.join("\n")
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
    uploaded_at: getIsoTimestamp(),
    status_label: "已生成",
    document_id: document.document_id,
    document_status: "parsed",
    document_type: document.document_type,
    document_type_label: document.document_type_label ?? toDocumentLabel(document.document_type),
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

function buildDebugBundleFinalMessage(bundle: DebugMaterialBundleResponse): string {
  const documentNames = bundle.documents.map((document) => document.document_type_label ?? document.filename)
  const findingCount = bundle.expected_findings.length
  return `${bundle.scenario_label}已生成：${formatRequestedDocuments(documentNames)}。材料正文和字段已写入材料库；预期缺陷 ${findingCount} 条只作为调试 oracle 展示，不进入材料正文。`
}

function inferAttachmentKind(file: Pick<File, "name" | "type">): AttachmentKind {
  if (file.type.startsWith("image/")) {
    return "image"
  }

  if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) {
    return "pdf"
  }

  return "file"
}

function sanitizeHistoryAttachment(attachment: ChatAttachment): ChatAttachment {
  return {
    ...attachment,
    preview_url: attachment.preview_url?.startsWith("data:") ? attachment.preview_url : null,
  }
}

function sanitizeHistoryMessage(message: ChatMessage): ChatMessage {
  return {
    ...message,
    attachments: message.attachments?.map(sanitizeHistoryAttachment),
  }
}

function resolvePersistentMaterialPreview(material: UploadedMaterial): string | null {
  if (material.preview_url?.startsWith("data:")) {
    return material.preview_url
  }
  if (material.kind === "image" && material.session_id && material.document_id) {
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

function resolvePersistentAttachmentPreview(attachment: ChatAttachment): string | null {
  if (attachment.preview_url?.startsWith("data:")) {
    return attachment.preview_url
  }
  if (attachment.kind === "image" && attachment.session_id && attachment.document_id) {
    return getFileContentUrl(attachment.session_id, attachment.document_id)
  }
  return null
}

function resolveMaterialMatchKey(name: string, index: number): string {
  return `${name.trim().toLowerCase()}#${index}`
}

function buildMaterialPreviewLookup(materials: UploadedMaterial[]): Map<string, UploadedMaterial> {
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
      const matchedMaterial = materialLookup.get(resolveMaterialMatchKey(attachment.name, index))
      if (!matchedMaterial) {
        return attachment
      }

      return {
        ...attachment,
        document_id: matchedMaterial.document_id ?? attachment.document_id ?? null,
        session_id: matchedMaterial.session_id ?? attachment.session_id ?? null,
        preview_url: resolvePersistentMaterialPreview(matchedMaterial),
      }
    }),
  }))
}

function readFileAsDataUrl(file: File): Promise<string | null> {
  if (!file.type.startsWith("image/") || file.size > MAX_PERSISTED_PREVIEW_BYTES) {
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
let cachedHistoryEntries: SessionHistoryEntry[] = EMPTY_HISTORY_ENTRIES

function loadHistoryEntries(): SessionHistoryEntry[] {
  if (typeof window === "undefined") {
    return EMPTY_HISTORY_ENTRIES
  }

  try {
    const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY)
    if (raw === cachedHistoryRaw) {
      return cachedHistoryEntries
    }
    cachedHistoryRaw = raw
    if (!raw) {
      cachedHistoryEntries = EMPTY_HISTORY_ENTRIES
      return cachedHistoryEntries
    }

    const parsed = JSON.parse(raw)
    cachedHistoryEntries = Array.isArray(parsed) ? parsed : EMPTY_HISTORY_ENTRIES
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
    if (event.key === HISTORY_STORAGE_KEY) {
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

function writeHistoryEntries(entries: SessionHistoryEntry[], options?: { notify?: boolean }): void {
  if (typeof window === "undefined") {
    return
  }

  const raw = JSON.stringify(entries)
  cachedHistoryRaw = raw
  cachedHistoryEntries = entries
  window.localStorage.setItem(HISTORY_STORAGE_KEY, raw)

  if (options?.notify) {
    historyStoreListeners.forEach((listener) => listener())
  }
}

function removeHistoryEntries(): void {
  if (typeof window === "undefined") {
    return
  }

  cachedHistoryRaw = null
  cachedHistoryEntries = EMPTY_HISTORY_ENTRIES
  window.localStorage.removeItem(HISTORY_STORAGE_KEY)
  historyStoreListeners.forEach((listener) => listener())
}

function createClientId(prefix: string): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
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

function humanizeUploadStatus(status?: string | null, fallback = "已上传"): string {
  switch (status) {
    case "queued":
      return "排队中"
    case "processing":
    case "waiting_for_parse":
    case "parsing":
      return "解析中"
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

function firstNonEmptyText(...values: Array<string | null | undefined>): string | null {
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
    case "officer":
      return "签证官"
    case "user":
      return "申请人"
    case "system":
      return "系统记录"
  }
}

function roleClassName(role: ChatMessage["role"]): string {
  switch (role) {
    case "officer":
      return "officer"
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

  const kindLabel = attachment.kind === "pdf" ? "PDF" : attachment.kind === "image" ? "图片" : "文件"
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
  const content = escapeHtml(message.content || "（仅包含附件）").replaceAll("\n", "<br />")
  const attachments = message.attachments?.map(renderExportAttachment).join("") ?? ""
  return `
    <section class="message-row ${roleClass}">
      <div class="avatar">${roleClass === "officer" ? "VO" : roleClass === "user" ? "我" : "记"}</div>
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
    .officer .bubble {
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


function renderReviewList(title: string, items: string[], tone = "default"): string {
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
  const [requiredPackage, setRequiredPackage] = useState<RequiredPackage | null>(null)

  const [isInitializing, setIsInitializing] = useState(false)
  const [initError, setInitError] = useState<string | null>(null)

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isSending, setIsSending] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)

  const [userReport, setUserReport] = useState<UserReport | null>(null)
  const [isLoadingReport, setIsLoadingReport] = useState(false)
  const [reportError, setReportError] = useState<string | null>(null)

  const [internalReport, setInternalReport] = useState<InternalReport | null>(null)
  const [interviewReview, setInterviewReview] = useState<InterviewReviewResponse | null>(null)
  const [isGeneratingReview, setIsGeneratingReview] = useState(false)
  const [isLoadingInternalReport, setIsLoadingInternalReport] = useState(false)
  const [modalError, setModalError] = useState<string | null>(null)
  const [isReportModalOpen, setIsReportModalOpen] = useState(false)
  const [pendingResetAfterSummary, setPendingResetAfterSummary] = useState(false)

  const [uploadedMaterials, setUploadedMaterials] = useState<UploadedMaterial[]>([])
  const historyStore = useSyncExternalStore(
    subscribeHistoryStore,
    loadHistoryEntries,
    getServerHistoryEntries,
  )
  const updateHistoryStore = useCallback(
    (updater: SessionHistoryEntry[] | ((prev: SessionHistoryEntry[]) => SessionHistoryEntry[])) => {
      const previousEntries = loadHistoryEntries()
      const nextEntries = typeof updater === "function" ? updater(previousEntries) : updater
      writeHistoryEntries(nextEntries, { notify: true })
    },
    [],
  )
  const [composerCommand, setComposerCommand] = useState<ComposerCommand | null>(null)
  const [settingsFeedback, setSettingsFeedback] = useState<string | null>(null)
  const [isDebugBundleGenerating, setIsDebugBundleGenerating] = useState(false)
  const [debugBundleProgress, setDebugBundleProgress] = useState<string[]>([])
  const [userModelConfig, setUserModelConfig] = useState<UserModelConfig>(
    () => loadUserModelConfig(),
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

  const appendMessage = useCallback((message: Omit<ChatMessage, "id" | "timestamp">) => {
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
  }, [])

  const updateMessageStatus = useCallback((id: string, status: ChatMessage["status"]) => {
    setMessages((prev) =>
      prev.map((msg) => (msg.id === id ? { ...msg, status } : msg)),
    )
  }, [])

  const updateMessageContent = useCallback((id: string, content: string) => {
    setMessages((prev) =>
      prev.map((msg) => (msg.id === id ? { ...msg, content } : msg)),
    )
  }, [])

  const updateMessageAttachment = useCallback(
    (messageId: string, attachmentId: string, patch: Partial<ChatAttachment>) => {
      setMessages((prev) =>
        prev.map((message) => {
          if (message.id !== messageId || !message.attachments?.length) {
            return message
          }

          return {
            ...message,
            attachments: message.attachments.map((attachment) =>
              attachment.id === attachmentId ? { ...attachment, ...patch } : attachment,
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
      if (error.status === 503 || error.status === 502 || error.status === 504) {
        return "当前对话模型不可用或运行失败。"
      }
      return `请求失败：${error.message}`
    }
    return fallback
  }, [])

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

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshRagStatus()
    }, 0)

    return () => {
      window.clearTimeout(timer)
    }
  }, [refreshRagStatus])

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

  const queueComposerCommand = useCallback((type: ComposerCommand["type"]) => {
    setComposerCommand({
      type,
      token: Date.now(),
    })
  }, [])

  const handleComposerCommandHandled = useCallback(() => {
    setComposerCommand(null)
  }, [])

  const createMessageAttachments = useCallback(async (files: File[]): Promise<ChatAttachment[]> => {
    return Promise.all(
      files.map(async (file) => {
        const kind = inferAttachmentKind(file)
        const previewUrl = kind === "image" ? await readFileAsDataUrl(file) : null

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
  }, [])

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
        : humanizeUploadStatus(
            response?.job_status ?? response?.document_status ?? null,
            "已上传",
          ),
      document_id: response?.document_id,
      document_status: response?.document_status,
      document_type:
        response?.document_type ?? response?.document_assessment?.document_type ?? null,
      document_type_label:
        response?.document_type_label ??
        response?.document_assessment?.document_type_label ??
        null,
      relevance:
        response?.relevance ?? response?.document_assessment?.relevance ?? null,
      feedback_message: feedbackMessage,
      requested_document_labels: response?.requested_document_labels ?? [],
      current_focus_document_label:
        response?.main_flow_feedback?.current_focus_document_label ??
        response?.document_assessment?.main_flow_feedback?.current_focus_document_label ??
        null,
      counts_toward_gate: response?.document_assessment?.counts_toward_gate ?? null,
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

      const existing = historyStore.find((entry) => entry.session_id === sessionId)
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
      updateHistoryStore((prev) => [entry, ...prev.filter((item) => item.id !== entry.id)])
    },
    [buildHistoryEntry, updateHistoryStore],
  )

  const activeHistoryEntry = useMemo(() => {
    if (!sessionId || !visaType) {
      return null
    }
    return buildHistoryEntry("active")
  }, [buildHistoryEntry, sessionId, visaType])

  const sessionHistory = useMemo(() => {
    if (!activeHistoryEntry) {
      return historyStore
    }
    return [activeHistoryEntry, ...historyStore.filter((entry) => entry.id !== activeHistoryEntry.id)]
  }, [activeHistoryEntry, historyStore])

  useEffect(() => {
    writeHistoryEntries(sessionHistory)
  }, [sessionHistory])

  const clearCurrentSessionState = useCallback(() => {
    setSession(null)
    setVisaType(null)
    setRequiredPackage(null)
    setMessages([])
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
    setPendingResetAfterSummary(false)
  }, [])

  const handleVisaSelect = useCallback(
    async (visaFamily: VisaFamily) => {
      setIsInitializing(true)
      setInitError(null)
      setChatError(null)
      setReportError(null)
      setModalError(null)
      setUploadedMaterials([])
      setDebugBundleProgress([])

      try {
        if (mockMode) {
          const mockRequiredPackage = getMockRequiredPackage(visaFamily)
          setSession({
            session_id: MOCK_SESSION_ID,
            phase_state: "interview",
            current_governor_decision: MOCK_USER_REPORT.governor_decision ?? null,
            gate_status: null,
          })
          setVisaType(visaFamily)
          setRequiredPackage(mockRequiredPackage)
          setMessages([
            {
              id: "system-1",
              role: "system",
              content: buildRequiredPackageMessage(visaFamily, mockRequiredPackage),
              timestamp: getTimestamp(),
            },
            ...MOCK_MESSAGES.slice(1),
          ])
          setUserReport(MOCK_USER_REPORT)
          setInternalReport(MOCK_INTERNAL_REPORT)
          setSessionTime(1458)
          return
        }

        const createdSession = await createSession(visaFamily)
        const nextRequiredPackage = await getRequiredPackage(createdSession.session_id)

        setSession(createdSession)
        setVisaType(visaFamily)
        setRequiredPackage(nextRequiredPackage)
        setMessages([
          {
            id: "system-1",
            role: "system",
            content: buildRequiredPackageMessage(visaFamily, nextRequiredPackage),
            timestamp: getTimestamp(),
          },
        ])
        setSessionTime(0)

        await fetchUserReport(createdSession.session_id)
      } catch (error) {
        setInitError(getErrorMessage(error, "无法连接到服务器，请确认后端已启动。"))
      } finally {
        setIsInitializing(false)
      }
    },
    [fetchUserReport, getErrorMessage, mockMode],
  )

  const handleSendMessage = useCallback(
    async (content: string, files?: File[]) => {
      if (!sessionId || isSending || isUploading) {
        return
      }

      const trimmedContent = content.trim()
      const nextFiles = files ?? []
      const hasFiles = nextFiles.length > 0
      const hasContent = trimmedContent.length > 0

      if (!hasFiles && !hasContent) {
        return
      }

      const messageAttachments = hasFiles ? await createMessageAttachments(nextFiles) : []
      const userMsgId = appendMessage({
        role: "user",
        content: trimmedContent,
        attachments: messageAttachments,
        status: "sending",
      })

      setChatError(null)

      let successfulUploads = 0
      const uploadResponses: FileUploadResponse[] = []

      try {
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
              appendMessage({
                role: "system",
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
                response.document_assessment?.main_flow_feedback?.message ?? null,
              )

              const uploadedMaterial = buildUploadedMaterial(file, attachment, uploadFeedback, response)
              setUploadedMaterials((prev) => [
                uploadedMaterial,
                ...prev.filter((item) => item.id !== attachment.id),
              ])
              updateMessageAttachment(userMsgId, attachment.id, {
                document_id: response.document_id ?? null,
                session_id: sessionId,
                upload_status: "uploaded",
                preview_url: resolvePersistentMaterialPreview(uploadedMaterial) ?? attachment.preview_url ?? null,
              })

              appendMessage({
                role: "system",
                content: uploadFeedback || `已上传文件：${file.name}。`,
              })

              const gateProgressMessage = buildGateProgressMessage(
                response.gate_progress?.overall_status,
              )
              if (gateProgressMessage) {
                appendMessage({
                  role: "system",
                  content: gateProgressMessage,
                })
              }

              const requestedDocumentsMessage = buildRequestedDocumentsMessage(
                response.requested_document_labels,
                null,
              )
              if (
                requestedDocumentsMessage &&
                !uploadFeedback?.includes(requestedDocumentsMessage)
              ) {
                appendMessage({
                  role: "system",
                  content: requestedDocumentsMessage,
                })
              }
              successfulUploads += 1
            } catch (error) {
              const fileError = getErrorMessage(error, `文件 ${file.name} 上传失败。`)
              setUploadedMaterials((prev) => [
                buildUploadedMaterial(file, attachment, fileError, undefined, true),
                ...prev.filter((item) => item.id !== attachment.id),
              ])
              updateMessageAttachment(userMsgId, attachment.id, {
                upload_status: "error",
              })
              appendMessage({
                role: "system",
                content: `错误：${fileError}`,
              })
            }
          }

          setIsUploading(false)
          if (!hasContent) {
            updateMessageStatus(userMsgId, successfulUploads > 0 ? "sent" : "error")
            if (successfulUploads > 0) {
              const latestReport = await refreshReports(sessionId)
              const fallbackLabels = latestReport?.requested_document_labels ?? []
              appendMessage({
                role: "officer",
                content: buildOfficerUploadFollowUp(uploadResponses, fallbackLabels),
              })
            } else {
              await refreshReports(sessionId)
            }
          }
        }

        if (hasContent) {
          setIsSending(true)

          if (mockMode) {
            const mockResponses = [
              "好的，我明白了。请告诉我更多关于你的资金来源。谁来支付你的学费和生活费？",
              "你有没有在美国的亲戚或朋友？他们是什么签证身份？",
              "你之前有没有去过其他国家？可以简单介绍一下你的旅行经历吗？",
              "你的父母是做什么工作的？他们对你出国留学有什么看法？",
            ]
            const randomResponse =
              mockResponses[Math.floor(Math.random() * mockResponses.length)]

            updateMessageStatus(userMsgId, "sent")
            appendMessage({
              role: "officer",
              content: randomResponse,
            })
          } else {
            const runtimeModelConfig = toRuntimeModelConfig(userModelConfig)
            if (userModelConfig.enabled && !runtimeModelConfig) {
              throw new Error("请完整填写 Base URL、API Key 和模型名称，或关闭自带模型。")
            }
            const response =
              runtimeModelConfig && userModelConfig.streamingEnabled
                ? await sendMessageStream(
                    sessionId,
                    trimmedContent,
                    runtimeModelConfig,
                    (event) => {
                      if (event.event === "analyzing") {
                        setSettingsFeedback("正在生成本轮回复。")
                      }
                    },
                  )
                : await sendMessage(sessionId, trimmedContent, runtimeModelConfig)
            updateMessageStatus(userMsgId, "sent")
            appendMessage({
              role: "officer",
              content: response.assistant_message,
            })

            const requestedDocumentsMessage = buildRequestedDocumentsMessage(
              response.requested_document_labels,
              response.governor_decision,
            )
            if (requestedDocumentsMessage) {
              appendMessage({
                role: "system",
                content: requestedDocumentsMessage,
              })
            }

            const gateProgressMessage = buildGateProgressMessage(
              response.gate_progress?.overall_status,
            )
            if (gateProgressMessage) {
              appendMessage({
                role: "system",
                content: gateProgressMessage,
              })
            }
            await refreshReports(sessionId)
          }
        }
      } catch (error) {
        updateMessageStatus(userMsgId, "error")
        setChatError(getErrorMessage(error, "发送失败，请重试。"))
      } finally {
        setIsSending(false)
        setIsUploading(false)
      }
    },
    [
      appendMessage,
      buildUploadedMaterial,
      createMessageAttachments,
      getErrorMessage,
      isSending,
      isUploading,
      mockMode,
      refreshReports,
      sessionId,
      updateMessageAttachment,
      updateMessageStatus,
      userModelConfig,
    ],
  )

  const handleContinueAnswer = useCallback(() => {
    const currentKeyQuestion = userReport?.current_key_question
    appendMessage({
      role: "system",
      content:
        currentKeyQuestion && currentKeyQuestion !== "暂无"
          ? `请继续围绕“${currentKeyQuestion}”补充回答，优先说明具体事实。`
          : "请继续补充你的回答，可以提供更多细节或背景信息。",
    })
    queueComposerCommand("focus")
  }, [appendMessage, queueComposerCommand, userReport])

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
      appendMessage({
        role: "system",
        content: `本轮总结：${latestUserReport.summary}`,
      })
    }
  }, [appendMessage, getErrorMessage, mockMode, persistHistoryEntry, sessionId, userReport])

  const handleReset = useCallback(() => {
    if (sessionId && visaType && !pendingResetAfterSummary) {
      persistHistoryEntry("abandoned")
    }

    clearCurrentSessionState()
  }, [clearCurrentSessionState, pendingResetAfterSummary, persistHistoryEntry, sessionId, visaType])

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
          missing_or_weak_evidence: MOCK_USER_REPORT.missing_evidence.map((item) => item.name),
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

  const handleUserModelConfigChange = useCallback((nextConfig: UserModelConfig) => {
    setUserModelConfig(nextConfig)
    setModelConfigError(null)
  }, [])

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
      setModelConfigError(getErrorMessage(error, "模型列表获取失败，可手动输入模型名称。"))
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
  }, [getErrorMessage, internalReport, messages, mockMode, session, sessionId, uploadedMaterials, userReport, visaType])


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
      await exportConversationLongImage(`ds160-session-${sessionId}-conversation.png`, {
        sessionId,
        visaType,
        messages,
      })
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

  const runDebugMaterialBundle = useCallback(
    async (scenario: DebugMaterialBundleScenario) => {
      if (!sessionId || isDebugBundleGenerating) {
        setSettingsFeedback(
          !sessionId ? "当前没有可生成材料包的会话。" : "材料包正在生成中。",
        )
        return
      }
      if (mockMode) {
        appendMessage({
          role: "system",
          content: "[Mock] 已生成一组调试材料包，并写入材料库。",
        })
        setSettingsFeedback("Mock 调试材料包已生成。")
        return
      }

      const progressLines: string[] = []
      const progressMessageId = appendMessage({
        role: "system",
        content: "正在生成调试材料包...",
        status: "sending",
      })
      const updateProgress = (line: string) => {
        progressLines.push(line)
        setDebugBundleProgress([...progressLines])
        updateMessageContent(progressMessageId, buildDebugBundleProgressMessage(progressLines))
      }

      setIsDebugBundleGenerating(true)
      setSettingsFeedback("正在生成调试材料包...")
      setDebugBundleProgress([])
      try {
        const result = await createDebugMaterialBundleStream(
          sessionId,
          scenario,
          true,
          (event) => {
            updateProgress(describeDebugBundleEvent(event))
          },
        )
        updateMessageStatus(progressMessageId, "sent")
        setSession((prev) => prev ? {
          ...prev,
          phase_state: result.phase_state,
          current_governor_decision: result.governor_decision ?? prev.current_governor_decision,
          gate_status: mapSessionGateStatus(result.gate_status) ?? prev.gate_status,
        } : prev)
        setUploadedMaterials((prev) => {
          const nextMaterials = result.documents.map((document) =>
            debugBundleDocumentToMaterial(sessionId, document, result),
          )
          const generatedIds = new Set(nextMaterials.map((material) => material.id))
          return [
            ...nextMaterials,
            ...prev.filter((material) => !generatedIds.has(material.id)),
          ]
        })
        appendMessage({
          role: "system",
          content: buildDebugBundleFinalMessage(result),
        })
        if (result.assistant_message) {
          appendMessage({
            role: "officer",
            content: humanizeBackendText(result.assistant_message),
          })
        }
        await refreshReports(sessionId)
        if (result.main_flow_refresh_error) {
          setSettingsFeedback(`材料包已生成，但下一步刷新失败：${result.main_flow_refresh_error}`)
        } else {
          setSettingsFeedback("材料包已生成，前端已接收完整流式进度。")
        }
      } catch (error) {
        updateMessageStatus(progressMessageId, "error")
        const message = getErrorMessage(error, "调试材料包生成失败，请稍后重试。")
        updateProgress(`错误：${message}`)
        setSettingsFeedback(message)
      } finally {
        setIsDebugBundleGenerating(false)
      }
    },
    [
      appendMessage,
      getErrorMessage,
      isDebugBundleGenerating,
      mockMode,
      refreshReports,
      sessionId,
      updateMessageContent,
      updateMessageStatus,
    ],
  )

  const handleDebugFillCurrentGap = useCallback(
    () => runDebugMaterialBundle("normal_f1_bundle"),
    [runDebugMaterialBundle],
  )

  const handleDebugFillNormalData = useCallback(
    () => runDebugMaterialBundle("normal_f1_bundle"),
    [runDebugMaterialBundle],
  )

  const handleDebugFillSchoolMismatch = useCallback(
    () => runDebugMaterialBundle("school_mismatch_bundle"),
    [runDebugMaterialBundle],
  )

  const handleDebugFillIdentityMismatch = useCallback(
    () => runDebugMaterialBundle("identity_mismatch_bundle"),
    [runDebugMaterialBundle],
  )

  const handleDebugFillFundingShortfall = useCallback(
    () => runDebugMaterialBundle("funding_shortfall_bundle"),
    [runDebugMaterialBundle],
  )

  const handleDebugFillSponsorEquityGap = useCallback(
    () => runDebugMaterialBundle("sponsor_chain_gap_bundle"),
    [runDebugMaterialBundle],
  )

  const handleDebugFillClaimVsDocument = useCallback(
    () => runDebugMaterialBundle("claim_vs_document_bundle"),
    [runDebugMaterialBundle],
  )

  const handleClearHistory = useCallback(() => {
    removeHistoryEntries()
    setSettingsFeedback("本地历史记录已清空。")
  }, [])

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
    setUserReport(entry.report)
    setInternalReport(null)
    setInterviewReview(null)
    setUploadedMaterials(restoredMaterials)
    setChatError(null)
    setReportError(null)
    setModalError(null)
    setIsDebugBundleGenerating(false)
    setDebugBundleProgress([])
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
      (userReport?.current_key_proof ? toDocumentLabel(userReport.current_key_proof) : null)
    )
  }, [userReport])

  return {
    apiBaseUrl,
    mockMode,
    session,
    sessionId,
    visaType,
    requiredPackage,
    isInitializing,
    initError,
    messages,
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
    handleViewDetails,
    handleActionClick,
    handlePause,
    handleEndSession,
    handleReset,
    handleCopySessionId,
    handleUserModelConfigChange,
    handleFetchUserModels,
    handleUploadRagFile,
    refreshRagStatus,
    handleExportSession,
    handleExportConversationImage,
    handleExportReviewImage,
    handleDebugFillCurrentGap,
    handleDebugFillNormalData,
    handleDebugFillSchoolMismatch,
    handleDebugFillIdentityMismatch,
    handleDebugFillFundingShortfall,
    handleDebugFillSponsorEquityGap,
    handleDebugFillClaimVsDocument,
    handleClearHistory,
    handleRestoreSession,
  }
}
