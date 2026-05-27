export type VisaFamily = "F-1" | "J-1" | "B-1/B-2" | "H-1B"
export type VisaFamilyCode = "f1" | "j1" | "b1_b2" | "h1b"
export type AttachmentKind = "image" | "pdf" | "file"

export type RiskLevel = "none" | "low" | "medium" | "high"
export type DocumentRelevance =
  | "high"
  | "medium"
  | "low"
  | "unknown"
  | (string & {})
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

export interface UserModelConfig {
  enabled: boolean
  streamingEnabled: boolean
  baseUrl: string
  apiKey: string
  model: string
}

export interface UserModelRuntimeConfig {
  base_url: string
  api_key: string
  model: string
}

export interface ModelListItem {
  id: string
  label: string
}

export interface ModelListResponse {
  models: ModelListItem[]
}

export interface RagCollectionStatus {
  name: string
  source_type: string
  count: number
}

export interface RagStatus {
  enabled: boolean
  ready: boolean
  status: string
  skip_reason?: string | null
  vector_store: string
  index_version: string
  collection_prefix: string
  chroma_mode: string
  embedding_model: string
  rerank_model: string
  upload_max_size_mb: number
  allow_third_party_reference: boolean
  collections: RagCollectionStatus[]
}

export interface RagUploadResponse {
  status: string
  source_id: string
  source_type: string
  title: string
  collection_name: string
  chunk_count: number
  skipped: boolean
  skip_reason?: string | null
}

export interface RagUploadMetadata {
  title?: string
  url?: string
  visa_family?: string
  country?: string
  post?: string
  section_path?: string
}

export type MessageStreamEvent =
  | { event: "accepted"; data: Record<string, unknown> }
  | { event: "analyzing"; data: Record<string, unknown> }
  | { event: "final"; data: BackendMessageResponse }
  | { event: "error"; data: { status?: number; detail?: string } }

export type DebugMaterialBundleScenario =
  | "normal_f1_bundle"
  | "school_mismatch_bundle"
  | "identity_mismatch_bundle"
  | "funding_shortfall_bundle"
  | "sponsor_chain_gap_bundle"
  | "claim_vs_document_bundle"

export interface DebugBundleDocument {
  document_id: string
  filename: string
  document_type: string
  document_type_label?: string | null
  raw_text: string
  fields: Record<string, string>
  content_url?: string | null
}

export interface DebugBundleExpectedFinding {
  kind: string
  description: string
  field_path?: string | null
  document_types?: string[]
  severity?: string
  visible_to_model: boolean
}

export interface DebugBundleSyntheticTurn {
  role: "user"
  content: string
  turn_id?: string
  field_claims?: Record<string, string>
}

export interface DebugMaterialBundleResponse {
  session_id: string
  bundle_id: string
  scenario: DebugMaterialBundleScenario | string
  scenario_label: string
  documents: DebugBundleDocument[]
  synthetic_turns: DebugBundleSyntheticTurn[]
  expected_findings: DebugBundleExpectedFinding[]
  assistant_message?: string | null
  governor_decision?: string | null
  requested_documents?: string[]
  remaining_required_documents?: string[]
  turn_decision?: Record<string, unknown>
  document_review?: Record<string, unknown>
  runtime_view_state?: Record<string, unknown>
  phase_state?: string
  gate_status?: BackendSessionGateStatus | null
  main_flow_refresh_error?: string | null
  generation?: {
    source?: "ai" | "deterministic" | string
    mode?: string
    seed_text_present?: boolean
    seed_source?: "request" | "session_transcript" | string | null
    request_seed_text_present?: boolean
    fallback_used?: boolean
    fallback_reason?: string
    trace?: Record<string, unknown>
  }
}

export type DebugMaterialBundleStreamEvent =
  | {
      event: "accepted"
      data: { session_id?: string } & Record<string, unknown>
    }
  | {
      event: "debug_bundle_started"
      data: {
        session_id?: string
        bundle_id?: string
        scenario?: string
        scenario_label?: string
        document_count?: number
      } & Record<string, unknown>
    }
  | {
      event: "document_created"
      data: {
        bundle_id?: string
        document_id?: string
        filename?: string
        document_type?: string
        document_type_label?: string
      } & Record<string, unknown>
    }
  | {
      event: "evidence_written"
      data: {
        bundle_id?: string
        document_id?: string
        evidence_count?: number
        fields?: Record<string, string>
      } & Record<string, unknown>
    }
  | { event: "profile_recomputed"; data: Record<string, unknown> }
  | { event: "gate_refreshed"; data: Record<string, unknown> }
  | { event: "document_review_started"; data: Record<string, unknown> }
  | {
      event: "governor_decided"
      data: {
        governor_decision?: string | null
        turn_decision?: Record<string, unknown>
      } & Record<string, unknown>
    }
  | {
      event: "progress"
      data: {
        stage?: string
        message?: string
      } & Record<string, unknown>
    }
  | { event: "final"; data: DebugMaterialBundleResponse }
  | { event: "error"; data: { status?: number; detail?: string } }

export interface ChatAttachment {
  id: string
  name: string
  mime_type: string
  kind: AttachmentKind
  size?: number
  preview_url?: string | null
  upload_status?: "pending" | "uploaded" | "error"
  document_id?: string | null
  session_id?: string | null
}

export interface ChatMessage {
  id: string
  role: "officer" | "user" | "system"
  content: string
  timestamp: string
  status?: "sending" | "sent" | "error"
  attachments?: ChatAttachment[]
  public_reasoning?: PublicReasoning | null
}

export interface PublicReasoning {
  basis?: string | null
  known_fact_summaries?: string[]
  latest_assistant_question?: string | null
  latest_user_referred_to_materials?: boolean | null
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
  remaining_required_documents: string[]
  remaining_required_document_labels: string[]
  gate_progress?: GateProgress | null
  score_summary?: Record<string, number>
  turn_decision?: Record<string, unknown>
  document_review?: Record<string, unknown>
  turn_record?: Record<string, unknown>
  prompt_trace?: Record<string, unknown>
  runtime_view_state?: Record<string, unknown>
  public_reasoning?: PublicReasoning | null
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
  case_board?: CaseBoardDelta | null
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

export interface CaseDocumentTypeCandidate {
  document_type: string
  document_type_label?: string | null
  confidence?: number | null
}

export interface CaseEvidenceCard {
  evidence_id: string
  source_type?: string
  document_id?: string | null
  page_number?: number | null
  excerpt: string
  visual_anchor?: string | null
  claim_refs: string[]
  confidence?: number | null
  metadata?: Record<string, unknown>
}

export interface CaseClaim {
  claim_id: string
  field_path: string
  field_label?: string | null
  value?: string | null
  status: string
  supporting_evidence_ids: string[]
  conflicting_evidence_ids: string[]
  confidence?: number | null
  metadata?: Record<string, unknown>
}

export interface CaseProofPoint {
  proof_point_id: string
  visa_family?: string
  question: string
  status: string
  why_it_matters: string
  claim_refs: string[]
  evidence_refs: string[]
  metadata?: Record<string, unknown>
}

export interface CaseConflict {
  conflict_id: string
  claim_ids: string[]
  evidence_ids: string[]
  summary: string
  severity?: string
  suggested_followup?: string | null
}

export interface InterviewNextMove {
  move_type: string
  question: string
  reason: string
  claim_refs: string[]
  evidence_refs: string[]
}

export interface CaseBoardLatestMaterial {
  document_id?: string | null
  filename?: string | null
  understanding_status?: string | null
  document_type?: string | null
  document_type_label?: string | null
  document_type_candidates?: CaseDocumentTypeCandidate[]
  relevance?: DocumentRelevance | null
  supported_claims?: string[]
  confidence?: number | null
  feedback_message?: string | null
  unknowns?: string[]
}

export interface CaseBoardDelta {
  latest_material?: CaseBoardLatestMaterial | null
  evidence_cards: CaseEvidenceCard[]
  claims: CaseClaim[]
  open_proof_points: CaseProofPoint[]
  conflicts: CaseConflict[]
  next_move?: InterviewNextMove | null
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
  content_url?: string | null
  document_status?: string
  job_id?: string
  job_status?: string
  understanding_status?: string | null
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
  evidence_cards: CaseEvidenceCard[]
  case_board_delta?: CaseBoardDelta | null
  requested_documents: string[]
  requested_document_labels: string[]
  remaining_required_documents: string[]
  remaining_required_document_labels: string[]
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

export interface SessionExportPayload {
  schema_version: string
  session: Record<string, unknown>
  reports: {
    user: UserReport
    internal: InternalReport
  }
  profile_snapshot?: Record<string, unknown>
  documents: Array<{
    document_id: string
    filename: string
    status: string
    extracted_text: string
    artifact: Record<string, unknown>
  }>
  [key: string]: unknown
}

export interface InterviewReviewReport {
  outcome: string
  outcome_reason: string
  executive_summary: string
  strengths: string[]
  refusal_or_risk_reasons: string[]
  missing_or_weak_evidence: string[]
  conversation_issues: string[]
  document_findings: string[]
  improvement_plan: string[]
  next_practice_focus: string[]
}

export interface InterviewReviewResponse {
  schema_version: string
  source: "llm" | "fallback" | string
  runtime?: Record<string, unknown>
  report: InterviewReviewReport
  basis?: Record<string, unknown>
}

export interface DebugFillResponse {
  session_id: string
  fill_scenario?: string
  fill_scenario_label?: string
  filled_document_type: string
  filled_summary?: string
  document_id: string
  filename: string
  phase_state?: string
  gate_status?: BackendSessionGateStatus | null
  assistant_message?: string | null
  governor_decision?: string | null
  requested_documents?: string[]
  remaining_required_documents?: string[]
  turn_decision?: Record<string, unknown>
  runtime_view_state?: Record<string, unknown>
  main_flow_refresh_error?: string | null
}

export interface UploadedMaterial {
  id: string
  session_id?: string | null
  name: string
  mime_type: string
  kind: AttachmentKind
  size?: number
  preview_url?: string | null
  content_url?: string | null
  uploaded_at: string
  status_label: string
  document_id?: string
  document_status?: string
  understanding_status?: string | null
  document_type?: string | null
  document_type_label?: string | null
  relevance?: DocumentRelevance | null
  feedback_message?: string | null
  evidence_cards?: CaseEvidenceCard[]
  claims?: CaseClaim[]
  proof_points?: CaseProofPoint[]
  conflicts?: CaseConflict[]
  next_move?: InterviewNextMove | null
  case_board_delta?: CaseBoardDelta | null
  requested_document_labels?: string[]
  current_focus_document_label?: string | null
  counts_toward_gate?: boolean | null
  raw_text?: string | null
  fields?: Record<string, string>
  synthetic_bundle_id?: string | null
  debug_bundle_scenario?: string | null
  expected_findings?: DebugBundleExpectedFinding[]
}

export interface SessionHistoryEntry {
  id: string
  session_id: string
  visa_type: VisaFamily
  status: "active" | "completed" | "abandoned"
  title: string
  summary: string
  last_message?: string | null
  message_count: number
  created_at: string
  updated_at: string
  required_package: RequiredPackage | null
  report: UserReport | null
  materials: UploadedMaterial[]
  messages: ChatMessage[]
}

export interface ComposerCommand {
  type: "focus" | "upload"
  token: number
}

export interface AuthResponse {
  authenticated: boolean
  expires_in: number
}

export interface LoginPayload {
  password: string
}

export interface AuthStatusResponse {
  authenticated: boolean
  expires_at?: string | null
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
  remaining_required_documents?: string[]
  gate_progress?: BackendGateProgress | null
  score_summary?: Record<string, number>
  turn_decision?: Record<string, unknown>
  document_review?: Record<string, unknown>
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
  case_board?: BackendCaseBoardDelta | null
  missing_evidence?: Array<
    string | { id?: string; code?: string; name?: string; priority?: string }
  >
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

export interface BackendCaseDocumentTypeCandidate {
  document_type?: string
  confidence?: number | null
}

export interface BackendCaseEvidenceCard {
  evidence_id?: string
  source_type?: string
  document_id?: string | null
  page_number?: number | null
  excerpt?: string
  visual_anchor?: string | null
  claim_refs?: string[]
  confidence?: number | null
  metadata?: Record<string, unknown>
}

export interface BackendCaseClaim {
  claim_id?: string
  field_path?: string
  value?: string | null
  status?: string
  supporting_evidence_ids?: string[]
  conflicting_evidence_ids?: string[]
  confidence?: number | null
  metadata?: Record<string, unknown>
}

export interface BackendCaseProofPoint {
  proof_point_id?: string
  visa_family?: string
  question?: string
  status?: string
  why_it_matters?: string
  claim_refs?: string[]
  evidence_refs?: string[]
  metadata?: Record<string, unknown>
}

export interface BackendCaseConflict {
  conflict_id?: string
  claim_ids?: string[]
  evidence_ids?: string[]
  summary?: string
  severity?: string
  suggested_followup?: string | null
}

export interface BackendInterviewNextMove {
  move_type?: string
  question?: string
  reason?: string
  claim_refs?: string[]
  evidence_refs?: string[]
}

export interface BackendCaseBoardLatestMaterial {
  document_id?: string | null
  filename?: string | null
  understanding_status?: string | null
  document_type?: string | null
  document_type_candidates?: BackendCaseDocumentTypeCandidate[]
  relevance?: DocumentRelevance | null
  supported_claims?: string[]
  confidence?: number | null
  feedback_message?: string | null
  unknowns?: string[]
}

export interface BackendCaseBoardDelta {
  latest_material?: BackendCaseBoardLatestMaterial | null
  evidence_cards?: BackendCaseEvidenceCard[]
  claims?: BackendCaseClaim[]
  open_proof_points?: BackendCaseProofPoint[]
  proof_points?: BackendCaseProofPoint[]
  conflicts?: BackendCaseConflict[]
  next_move?: BackendInterviewNextMove | null
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
  content_url?: string | null
  document_status?: string
  job_id?: string
  job_status?: string
  understanding_status?: string | null
  document_type?: string | null
  document_assessment?: BackendDocumentAssessment | null
  document_type_candidates?: string[]
  relevance?: DocumentRelevance | null
  supported_claims?: string[]
  confidence?: number | null
  feedback_message?: string | null
  relevant?: boolean | null
  main_flow_feedback?: BackendFileFeedback | null
  evidence_cards?: BackendCaseEvidenceCard[]
  case_board_delta?: BackendCaseBoardDelta | null
  requested_documents?: string[]
  remaining_required_documents?: string[]
  gate_progress?: BackendGateProgress | null
}

export type BackendInternalReport = InternalReport

export const VISA_FAMILIES: {
  value: VisaFamily
  label: string
  description: string
}[] = [
  { value: "F-1", label: "F-1 学生签证", description: "赴美留学" },
  { value: "J-1", label: "J-1 交流访问", description: "学者、交流生" },
  {
    value: "B-1/B-2",
    label: "B-1/B-2 商务/旅游",
    description: "短期商务或旅游",
  },
  { value: "H-1B", label: "H-1B 工作签证", description: "专业技术工作" },
]
