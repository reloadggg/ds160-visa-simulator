export type VisaFamily = "F-1" | "J-1" | "B-1/B-2" | "H-1B"
export type VisaFamilyCode = "f1" | "j1" | "b1_b2" | "h1b"

export type RiskLevel = "none" | "low" | "medium" | "high"
export type DocumentRelevance = "high" | "medium" | "low" | "unknown" | (string & {})
export type AllowedActionIntent = "upload" | "details" | "continue"
export type AllowedActionCode =
  | "answer_question"
  | "continue_interview"
  | "clarify_key_issue"
  | "upload_key_proof"
  | "explain_missing_proof"
  | "wait_for_review"
  | "review_refusal_result"
  | (string & {})

export interface ChatMessage {
  id: string
  role: "officer" | "user" | "system"
  content: string
  timestamp: string
}

export interface MissingEvidence {
  id: string
  code: string
  name: string
  priority: "high" | "medium" | "low"
}

export interface AllowedAction {
  code: AllowedActionCode
  title: string
  description: string
  cta_text: string
  intent: AllowedActionIntent
}

export interface RequiredDocumentStatus {
  document_type: string
  document_label: string
  status: string
  is_uploaded: boolean
  is_parsed: boolean
  meets_minimum_fields: boolean
}

export interface SessionGateStatus {
  declared_family?: string | null
  declared_family_label?: string | null
  scenario_key?: string | null
  status: string
  required_documents: RequiredDocumentStatus[]
}

export interface GateProgress {
  overall_status: string
  ready_count?: number
  uploaded_count?: number
  missing_count?: number
  documents: RequiredDocumentStatus[]
}

export interface Session {
  session_id: string
  phase_state?: string
  current_governor_decision?: string | null
  gate_status?: SessionGateStatus | null
}

export interface RequiredPackage {
  required_initial_package: string[]
  required_initial_package_labels: string[]
}

export interface MessageResponse {
  assistant_message: string
  governor_decision?: string | null
  requested_documents: string[]
  requested_document_labels: string[]
  gate_progress?: GateProgress | null
  score_summary?: Record<string, number>
  turn_decision?: Record<string, unknown>
  turn_record?: Record<string, unknown>
  prompt_trace?: Record<string, unknown>
  runtime_view_state?: Record<string, unknown>
}

export interface UserReport {
  session_id?: string
  visa_family?: string
  visa_family_label?: string
  governor_decision?: string | null
  interview_status: string
  interview_status_label: string
  outcome_label: string
  summary: string
  strengths: string[]
  risk_points: string[]
  risk_level: RiskLevel
  risk_level_label: string
  current_key_question: string
  current_key_proof?: string | null
  current_key_proof_label?: string | null
  current_risk_code?: string | null
  missing_evidence: MissingEvidence[]
  allowed_next_actions: AllowedAction[]
  recommended_improvements: string[]
  requested_documents: string[]
  requested_document_labels: string[]
  advisory_context?: Record<string, unknown>
  prompt_trace?: Record<string, unknown>
  turn_decision?: Record<string, unknown>
}

export interface FileFeedback {
  status?: string
  supported_document_type?: string | null
  supported_document_label?: string | null
  current_focus_document_type?: string | null
  current_focus_document_label?: string | null
  message?: string | null
}

export interface DocumentAssessment {
  document_type?: string | null
  document_type_label?: string | null
  document_type_hint?: string | null
  document_type_hint_label?: string | null
  document_type_candidates: string[]
  document_type_candidate_labels: string[]
  relevance?: DocumentRelevance | null
  supported_claims: string[]
  confidence?: number | null
  feedback_message?: string | null
  relevant?: boolean | null
  counts_toward_gate?: boolean | null
  main_flow_feedback?: FileFeedback | null
}

export interface FileUploadResponse {
  document_id?: string
  document_status?: string
  job_id?: string
  job_status?: string
  document_type?: string | null
  document_type_label?: string | null
  document_assessment?: DocumentAssessment | null
  document_type_candidates: string[]
  document_type_candidate_labels: string[]
  relevance?: DocumentRelevance | null
  supported_claims: string[]
  confidence?: number | null
  feedback_message?: string | null
  relevant?: boolean | null
  main_flow_feedback?: FileFeedback | null
  requested_documents: string[]
  requested_document_labels: string[]
  gate_progress?: GateProgress | null
  [key: string]: unknown
}

export interface InternalReport {
  session_id?: string
  policy_pack_trace?: Record<string, unknown>
  runtime_trace?: Array<Record<string, unknown>>
  score_history?: Array<Record<string, unknown>>
  governor_history?: Array<Record<string, unknown>>
  runtime_ledger?: Record<string, unknown>
  runtime_view_state?: Record<string, unknown>
  interviewer_state?: Record<string, unknown>
  current_focus?: Record<string, unknown>
  profile_snapshot?: Record<string, unknown>
  turn_decision?: Record<string, unknown>
  advisory_context?: Record<string, unknown>
  [key: string]: unknown
}

export interface BackendSession {
  session_id: string
  phase_state?: string
  current_governor_decision?: string | null
  gate_status?: BackendSessionGateStatus | null
}

export interface BackendSessionGateStatus {
  declared_family?: string | null
  scenario_key?: string | null
  status: string
  required_documents?: BackendRequiredDocumentStatus[]
}

export interface BackendRequiredDocumentStatus {
  document_type: string
  status?: string
  is_uploaded?: boolean
  is_parsed?: boolean
  meets_minimum_fields?: boolean
}

export interface BackendRequiredPackage {
  required_initial_package?: string[]
}

export interface BackendMessageResponse {
  assistant_message: string
  governor_decision?: string | null
  requested_documents?: string[]
  gate_progress?: BackendGateProgress | null
  score_summary?: Record<string, number>
  turn_decision?: Record<string, unknown>
  turn_record?: Record<string, unknown>
  prompt_trace?: Record<string, unknown>
  runtime_view_state?: Record<string, unknown>
}

export interface BackendGateProgress {
  overall_status: string
  ready_count?: number
  uploaded_count?: number
  missing_count?: number
  documents?: BackendRequiredDocumentStatus[]
}

export interface BackendUserReport {
  session_id?: string
  visa_family?: string
  governor_decision?: string | null
  interview_status?: string
  outcome_label?: string
  summary?: string
  strengths?: string[]
  risk_points?: string[]
  risk_level?: RiskLevel | string
  current_key_question?: string | null
  current_key_proof?: string | null
  current_risk_code?: string | null
  missing_evidence?: Array<string | { id?: string; code?: string; name?: string; priority?: string }>
  allowed_next_actions?: string[]
  recommended_improvements?: string[]
  advisory_context?: Record<string, unknown>
  prompt_trace?: Record<string, unknown>
  turn_decision?: Record<string, unknown>
}

export interface BackendFileFeedback {
  status?: string
  supported_document_type?: string | null
  current_focus_document_type?: string | null
  message?: string | null
}

export interface BackendDocumentAssessment {
  document_type?: string | null
  document_type_hint?: string | null
  document_type_candidates?: string[]
  relevance?: DocumentRelevance | null
  supported_claims?: string[]
  confidence?: number | null
  feedback_message?: string | null
  relevant?: boolean | null
  counts_toward_gate?: boolean | null
  main_flow_feedback?: BackendFileFeedback | null
}

export interface BackendFileUploadResponse {
  document_id?: string
  document_status?: string
  job_id?: string
  job_status?: string
  document_type?: string | null
  document_assessment?: BackendDocumentAssessment | null
  document_type_candidates?: string[]
  relevance?: DocumentRelevance | null
  supported_claims?: string[]
  confidence?: number | null
  feedback_message?: string | null
  relevant?: boolean | null
  main_flow_feedback?: BackendFileFeedback | null
  requested_documents?: string[]
  gate_progress?: BackendGateProgress | null
}

export interface BackendInternalReport extends InternalReport {}

export const VISA_FAMILIES: { value: VisaFamily; label: string; description: string }[] = [
  { value: "F-1", label: "F-1 学生签证", description: "赴美留学" },
  { value: "J-1", label: "J-1 交流访问", description: "学者、交流生" },
  { value: "B-1/B-2", label: "B-1/B-2 商务/旅游", description: "短期商务或旅游" },
  { value: "H-1B", label: "H-1B 工作签证", description: "专业技术工作" },
]
