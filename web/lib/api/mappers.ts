import type {
  AllowedAction,
  AllowedActionCode,
  BackendDocumentAssessment,
  BackendFileFeedback,
  BackendFileUploadResponse,
  BackendGateProgress,
  BackendMessageResponse,
  BackendRequiredDocumentStatus,
  BackendRequiredPackage,
  BackendSession,
  BackendSessionGateStatus,
  BackendUserReport,
  DocumentAssessment,
  FileFeedback,
  FileUploadResponse,
  InterviewReviewResponse,
  GateProgress,
  MessageResponse,
  MissingEvidence,
  PublicReasoning,
  RequiredDocumentStatus,
  RequiredPackage,
  RiskLevel,
  Session,
  SessionGateStatus,
  UserReport,
  VisaFamily,
  VisaFamilyCode,
} from "./types"

export const VISA_FAMILY_CODE_BY_UI: Record<VisaFamily, VisaFamilyCode> = {
  "F-1": "f1",
  "J-1": "j1",
  "B-1/B-2": "b1_b2",
  "H-1B": "h1b",
}

const VISA_FAMILY_LABEL_BY_CODE: Record<VisaFamilyCode, VisaFamily> = {
  f1: "F-1",
  j1: "J-1",
  b1_b2: "B-1/B-2",
  h1b: "H-1B",
}

const DOCUMENT_LABELS: Record<string, string> = {
  ds160: "DS-160 确认页",
  passport_bio: "护照首页",
  i20: "I-20 表格",
  admission_letter: "录取信",
  funding_proof: "资金证明",
  ds2019: "DS-2019 表格",
  employer_letter: "雇主证明信",
  i797: "I-797 批准通知",
  itinerary_or_trip_purpose: "行程或出行目的说明",
  school_letter: "学校证明信",
  evidence_of_achievement: "成果证明材料",
  bank_statement: "银行流水",
  sponsor_letter: "资助说明信",
  scholarship_letter: "奖学金证明",
}

const INTERVIEW_STATUS_LABELS: Record<string, string> = {
  continue_interview: "继续面签问答",
  need_more_evidence: "建议补充证明材料",
  route_correction: "签证类型/目的需纠正",
  verify_key_issue: "关键事实待核实",
  waiting_key_proof: "待补齐关键证据",
  high_risk_review: "高风险复核建议",
  simulated_refusal: "模拟结果：建议拒签",
  status_pending: "状态待确认",
}

const BACKEND_TEXT_LABELS: Record<string, string> = {
  continue_interview: "继续面签问答",
  need_more_evidence: "建议补充证明材料",
  route_correction: "签证类型/目的需纠正",
  verify_key_issue: "关键事实待核实",
  waiting_key_proof: "待补齐关键证据",
  high_risk_review: "高风险复核建议",
  simulated_refusal: "模拟结果：建议拒签",
  high_risk: "高风险",
  medium_risk: "中等风险",
  low_risk: "低风险",
  review_status: "审核状态",
  document_review: "材料审核",
  prompt_trace: "提示词追踪",
  runtime_trace: "运行轨迹",
  turn_decision: "本轮判断",
}

const RISK_LEVEL_LABELS: Record<RiskLevel, string> = {
  none: "无明显风险",
  low: "低风险",
  medium: "中风险",
  high: "高风险",
}

const ACTION_LABELS: Record<
  string,
  {
    title: string
    description: string
    cta_text: string
    intent: AllowedAction["intent"]
  }
> = {
  answer_question: {
    title: "继续回答当前问题",
    description: "围绕签证官刚刚的问题继续补充，优先给出直接、具体的回答。",
    cta_text: "继续回答",
    intent: "continue",
  },
  continue_interview: {
    title: "继续正式问答",
    description: "当前可以继续模拟面签，保持回答前后一致并继续推进主线。",
    cta_text: "继续问答",
    intent: "continue",
  },
  clarify_key_issue: {
    title: "补充关键问题说明",
    description: "系统认为关键问题仍需解释，建议直接补充当前主线中的关键细节。",
    cta_text: "补充说明",
    intent: "continue",
  },
  upload_key_proof: {
    title: "上传关键证明",
    description:
      "当前主线仍缺少关键证明材料，上传相关文件后可帮助系统继续判断。",
    cta_text: "上传材料",
    intent: "upload",
  },
  explain_missing_proof: {
    title: "说明暂缺原因",
    description: "如果暂时无法上传材料，可以先解释原因和可替代信息。",
    cta_text: "查看建议",
    intent: "details",
  },
  wait_for_review: {
    title: "等待进一步复核",
    description: "系统已进入复核态，先查看当前报告，再决定下一步动作。",
    cta_text: "查看报告",
    intent: "details",
  },
  review_refusal_result: {
    title: "查看模拟拒签结果",
    description: "当前会话已到达模拟拒签结果，先查看报告中的原因和补强建议。",
    cta_text: "查看结果",
    intent: "details",
  },
}

function knownDocumentCodes(): string[] {
  return Object.keys(DOCUMENT_LABELS)
}

function humanizeUnknownCode(prefix: string): string {
  return `${prefix}待确认`
}

function normalizeStringList(values: unknown): string[] {
  if (!Array.isArray(values)) {
    return []
  }

  return values.filter(
    (value): value is string => typeof value === "string" && value.length > 0,
  )
}

function nullableDocumentLabel(documentType?: string | null): string | null {
  return documentType ? toDocumentLabel(documentType) : null
}

function firstNonEmptyText(
  ...values: Array<string | null | undefined>
): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value
    }
  }

  return null
}

export function toVisaFamilyCode(visaFamily: VisaFamily): VisaFamilyCode {
  return VISA_FAMILY_CODE_BY_UI[visaFamily]
}

export function toVisaFamilyLabel(visaFamilyCode?: string | null): string {
  if (!visaFamilyCode) {
    return "未选择签证类型"
  }

  return (
    VISA_FAMILY_LABEL_BY_CODE[visaFamilyCode as VisaFamilyCode] ??
    "签证类型待确认"
  )
}

export function toDocumentLabel(documentType?: string | null): string {
  if (!documentType) {
    return humanizeUnknownCode("材料")
  }

  return DOCUMENT_LABELS[documentType] ?? humanizeUnknownCode("材料")
}

export function humanizeBackendText(text?: string | null): string {
  if (!text) {
    return ""
  }

  let next = text
  for (const [code, label] of Object.entries(DOCUMENT_LABELS)) {
    next = next.replaceAll(code, label)
  }
  for (const [code, label] of Object.entries(VISA_FAMILY_LABEL_BY_CODE)) {
    next = next.replaceAll(code, label)
  }
  for (const [code, label] of Object.entries(BACKEND_TEXT_LABELS)) {
    next = next.replaceAll(code, label)
  }

  return next
    .replaceAll("formal interview", "正式问答")
    .replaceAll("interview", "面签问答")
    .replaceAll("refusal", "拒签")
    .replaceAll("review", "复核")
}

function normalizeRiskLevel(value?: string | null): RiskLevel {
  if (
    value === "low" ||
    value === "medium" ||
    value === "high" ||
    value === "none"
  ) {
    return value
  }
  return "none"
}

function mapRequiredDocumentStatus(
  document: BackendRequiredDocumentStatus,
): RequiredDocumentStatus {
  return {
    document_type: document.document_type,
    document_label: toDocumentLabel(document.document_type),
    status: document.status ?? "missing",
    is_uploaded: Boolean(document.is_uploaded),
    is_parsed: Boolean(document.is_parsed),
    meets_minimum_fields: Boolean(document.meets_minimum_fields),
  }
}

export function mapSessionGateStatus(
  status?: BackendSessionGateStatus | null,
): SessionGateStatus | null {
  if (!status) {
    return null
  }

  return {
    declared_family: status.declared_family ?? null,
    declared_family_label: toVisaFamilyLabel(status.declared_family),
    scenario_key: status.scenario_key ?? null,
    status: status.status,
    required_documents: (status.required_documents ?? []).map(
      mapRequiredDocumentStatus,
    ),
  }
}

function mapGateProgress(
  progress?: BackendGateProgress | null,
): GateProgress | null {
  if (!progress) {
    return null
  }

  return {
    overall_status: progress.overall_status,
    ready_count: progress.ready_count,
    uploaded_count: progress.uploaded_count,
    missing_count: progress.missing_count,
    documents: (progress.documents ?? []).map(mapRequiredDocumentStatus),
  }
}

function mapPublicReasoning(
  runtimeViewState?: Record<string, unknown>,
): PublicReasoning | null {
  const rawReasoning = runtimeViewState?.public_reasoning
  if (
    !rawReasoning ||
    typeof rawReasoning !== "object" ||
    Array.isArray(rawReasoning)
  ) {
    return null
  }
  const reasoning = rawReasoning as Record<string, unknown>
  const knownFacts = Array.isArray(reasoning.known_fact_summaries)
    ? reasoning.known_fact_summaries.filter(
        (item): item is string =>
          typeof item === "string" && item.trim().length > 0,
      )
    : []

  return {
    basis:
      typeof reasoning.basis === "string"
        ? humanizeBackendText(reasoning.basis)
        : null,
    known_fact_summaries: knownFacts.map(humanizeBackendText),
    latest_assistant_question:
      typeof reasoning.latest_assistant_question === "string"
        ? humanizeBackendText(reasoning.latest_assistant_question)
        : null,
    latest_user_referred_to_materials:
      typeof reasoning.latest_user_referred_to_materials === "boolean"
        ? reasoning.latest_user_referred_to_materials
        : null,
  }
}

function mapMissingEvidence(
  missingEvidence: BackendUserReport["missing_evidence"],
  currentKeyProof?: string | null,
): MissingEvidence[] {
  return (missingEvidence ?? []).map((item, index) => {
    if (typeof item === "string") {
      const priority =
        item === currentKeyProof ? "high" : index === 0 ? "medium" : "low"
      return {
        id: `${item}-${index}`,
        code: item,
        name: toDocumentLabel(item),
        priority,
      }
    }

    const code = item.code ?? item.name ?? `missing-${index}`
    const normalizedPriority =
      item.priority === "high" ||
      item.priority === "medium" ||
      item.priority === "low"
        ? item.priority
        : code === currentKeyProof
          ? "high"
          : index === 0
            ? "medium"
            : "low"

    return {
      id: item.id ?? `${code}-${index}`,
      code,
      name: humanizeBackendText(item.name) || toDocumentLabel(code),
      priority: normalizedPriority,
    }
  })
}

function mapAllowedAction(code: string): AllowedAction {
  const fallback = ACTION_LABELS[code] ?? {
    title: "按当前提示继续",
    description: "后端返回了新的建议动作，建议先查看报告再继续操作。",
    cta_text: "查看详情",
    intent: "details" as const,
  }

  return {
    code: code as AllowedActionCode,
    title: fallback.title,
    description: fallback.description,
    cta_text: fallback.cta_text,
    intent: fallback.intent,
  }
}

function mapFileFeedback(
  feedback?: BackendFileFeedback | null,
): FileFeedback | null {
  if (!feedback) {
    return null
  }

  return {
    status: feedback.status,
    supported_document_type: feedback.supported_document_type,
    supported_document_label: nullableDocumentLabel(
      feedback.supported_document_type,
    ),
    current_focus_document_type: feedback.current_focus_document_type,
    current_focus_document_label: nullableDocumentLabel(
      feedback.current_focus_document_type,
    ),
    message: humanizeBackendText(feedback.message) || null,
  }
}

function mapDocumentAssessment(
  assessment?: BackendDocumentAssessment | null,
): DocumentAssessment | null {
  if (!assessment) {
    return null
  }

  const mainFlowFeedback = mapFileFeedback(assessment.main_flow_feedback)
  const documentTypeCandidates = normalizeStringList(
    assessment.document_type_candidates,
  )
  const supportedClaims = normalizeStringList(assessment.supported_claims)
  return {
    document_type: assessment.document_type,
    document_type_label: nullableDocumentLabel(assessment.document_type),
    document_type_hint: assessment.document_type_hint,
    document_type_hint_label: nullableDocumentLabel(
      assessment.document_type_hint,
    ),
    document_type_candidates: documentTypeCandidates,
    document_type_candidate_labels: documentTypeCandidates.map(toDocumentLabel),
    relevance: assessment.relevance ?? null,
    supported_claims: supportedClaims,
    confidence: assessment.confidence ?? null,
    feedback_message: humanizeBackendText(assessment.feedback_message) || null,
    relevant: assessment.relevant ?? null,
    counts_toward_gate: assessment.counts_toward_gate ?? null,
    main_flow_feedback: mainFlowFeedback,
  }
}

export function mapSession(payload: BackendSession): Session {
  return {
    session_id: payload.session_id,
    phase_state: payload.phase_state,
    current_governor_decision: payload.current_governor_decision ?? null,
    gate_status: mapSessionGateStatus(payload.gate_status),
  }
}

export function mapRequiredPackage(
  payload: BackendRequiredPackage,
): RequiredPackage {
  const required = payload.required_initial_package ?? []
  return {
    required_initial_package: required,
    required_initial_package_labels: required.map(toDocumentLabel),
  }
}

export function mapMessageResponse(
  payload: BackendMessageResponse,
): MessageResponse {
  const requestedDocuments = payload.requested_documents ?? []
  const remainingRequiredDocuments = payload.remaining_required_documents ?? []
  const runtimeViewState = payload.runtime_view_state
  return {
    assistant_message: humanizeBackendText(payload.assistant_message),
    governor_decision: payload.governor_decision ?? null,
    requested_documents: requestedDocuments,
    requested_document_labels: requestedDocuments.map(toDocumentLabel),
    remaining_required_documents: remainingRequiredDocuments,
    remaining_required_document_labels:
      remainingRequiredDocuments.map(toDocumentLabel),
    gate_progress: mapGateProgress(payload.gate_progress),
    score_summary: payload.score_summary,
    turn_decision: payload.turn_decision,
    document_review: payload.document_review,
    turn_record: payload.turn_record,
    prompt_trace: payload.prompt_trace,
    runtime_view_state: runtimeViewState,
    public_reasoning: mapPublicReasoning(runtimeViewState),
  }
}

export function mapUserReport(payload: BackendUserReport): UserReport {
  const currentKeyProof = payload.current_key_proof ?? null
  const requestedDocuments = Array.from(
    new Set(
      mapMissingEvidence(payload.missing_evidence, currentKeyProof).map(
        (item) => item.code,
      ),
    ),
  )
  const riskLevel = normalizeRiskLevel(payload.risk_level)
  const interviewStatus = payload.interview_status ?? "status_pending"

  return {
    session_id: payload.session_id,
    visa_family: payload.visa_family,
    visa_family_label: toVisaFamilyLabel(payload.visa_family),
    governor_decision: payload.governor_decision ?? null,
    interview_status: interviewStatus,
    interview_status_label:
      INTERVIEW_STATUS_LABELS[interviewStatus] ?? "状态待确认",
    outcome_label:
      humanizeBackendText(payload.outcome_label) || "当前状态待确认",
    summary: humanizeBackendText(payload.summary) || "系统尚未生成摘要。",
    strengths: (payload.strengths ?? [])
      .map(humanizeBackendText)
      .filter(Boolean),
    risk_points: (payload.risk_points ?? [])
      .map(humanizeBackendText)
      .filter(Boolean),
    risk_level: riskLevel,
    risk_level_label: RISK_LEVEL_LABELS[riskLevel],
    current_key_question:
      humanizeBackendText(payload.current_key_question) || "暂无",
    current_key_proof: currentKeyProof,
    current_key_proof_label: currentKeyProof
      ? toDocumentLabel(currentKeyProof)
      : null,
    current_risk_code: payload.current_risk_code ?? null,
    missing_evidence: mapMissingEvidence(
      payload.missing_evidence,
      currentKeyProof,
    ),
    allowed_next_actions: (payload.allowed_next_actions ?? []).map(
      mapAllowedAction,
    ),
    recommended_improvements: (payload.recommended_improvements ?? []).map(
      humanizeBackendText,
    ),
    requested_documents: requestedDocuments,
    requested_document_labels: requestedDocuments.map(toDocumentLabel),
    advisory_context: payload.advisory_context,
    prompt_trace: payload.prompt_trace,
    turn_decision: payload.turn_decision,
  }
}

export function mapInterviewReviewResponse(
  payload: InterviewReviewResponse,
): InterviewReviewResponse {
  return {
    ...payload,
    report: {
      ...payload.report,
      outcome: humanizeBackendText(payload.report.outcome),
      outcome_reason: humanizeBackendText(payload.report.outcome_reason),
      executive_summary: humanizeBackendText(payload.report.executive_summary),
      strengths: payload.report.strengths
        .map(humanizeBackendText)
        .filter(Boolean),
      refusal_or_risk_reasons: payload.report.refusal_or_risk_reasons
        .map(humanizeBackendText)
        .filter(Boolean),
      missing_or_weak_evidence: payload.report.missing_or_weak_evidence
        .map(humanizeBackendText)
        .filter(Boolean),
      conversation_issues: payload.report.conversation_issues
        .map(humanizeBackendText)
        .filter(Boolean),
      document_findings: payload.report.document_findings
        .map(humanizeBackendText)
        .filter(Boolean),
      improvement_plan: payload.report.improvement_plan
        .map(humanizeBackendText)
        .filter(Boolean),
      next_practice_focus: payload.report.next_practice_focus
        .map(humanizeBackendText)
        .filter(Boolean),
    },
  }
}

export function mapFileUploadResponse(
  payload: BackendFileUploadResponse,
): FileUploadResponse {
  const requestedDocuments = payload.requested_documents ?? []
  const remainingRequiredDocuments = payload.remaining_required_documents ?? []
  const mainFlowFeedback = mapFileFeedback(payload.main_flow_feedback)
  const documentAssessment = mapDocumentAssessment(payload.document_assessment)
  const documentTypeCandidates = normalizeStringList(
    payload.document_type_candidates,
  )
  const supportedClaims = normalizeStringList(payload.supported_claims)
  const feedbackMessage = firstNonEmptyText(
    payload.feedback_message ?? null,
    mainFlowFeedback?.message ?? null,
    documentAssessment?.feedback_message ?? null,
    documentAssessment?.main_flow_feedback?.message ?? null,
  )

  return {
    document_id: payload.document_id,
    content_url: payload.content_url ?? null,
    document_status: payload.document_status,
    job_id: payload.job_id,
    job_status: payload.job_status,
    document_type: payload.document_type,
    document_type_label: nullableDocumentLabel(payload.document_type),
    document_assessment: documentAssessment,
    document_type_candidates: documentTypeCandidates,
    document_type_candidate_labels: documentTypeCandidates.map(toDocumentLabel),
    relevance: payload.relevance ?? null,
    supported_claims: supportedClaims,
    confidence: payload.confidence ?? null,
    feedback_message: humanizeBackendText(feedbackMessage) || null,
    relevant: payload.relevant,
    main_flow_feedback: mainFlowFeedback,
    requested_documents: requestedDocuments,
    requested_document_labels: requestedDocuments.map(toDocumentLabel),
    remaining_required_documents: remainingRequiredDocuments,
    remaining_required_document_labels:
      remainingRequiredDocuments.map(toDocumentLabel),
    gate_progress: mapGateProgress(payload.gate_progress),
  }
}

export function describeRequestedDocuments(documentCodes: string[]): string {
  if (!documentCodes.length) {
    return ""
  }

  return documentCodes.map(toDocumentLabel).join("、")
}

export function getMockRequiredDocuments(visaFamily: VisaFamily): string[] {
  switch (visaFamily) {
    case "J-1":
      return ["ds160", "passport_bio", "ds2019", "funding_proof"]
    case "B-1/B-2":
      return ["ds160", "passport_bio", "itinerary_or_trip_purpose"]
    case "H-1B":
      return ["ds160", "passport_bio", "i797", "employer_letter"]
    case "F-1":
    default:
      return ["ds160", "passport_bio", "i20"]
  }
}

export function sanitizeVisibleReport(report: UserReport): UserReport {
  return {
    ...report,
    summary: humanizeBackendText(report.summary),
    current_key_question: humanizeBackendText(report.current_key_question),
    recommended_improvements:
      report.recommended_improvements.map(humanizeBackendText),
  }
}

export function getKnownDocumentCodes(): string[] {
  return knownDocumentCodes()
}
