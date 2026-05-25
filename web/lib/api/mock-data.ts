import { isMockModeEnabled } from "./config"
import { getMockRequiredDocuments, toDocumentLabel } from "./mappers"
import type {
  ChatMessage,
  InternalReport,
  RequiredPackage,
  UserReport,
  VisaFamily,
} from "./types"

export const MOCK_SESSION_ID = "mock-session-001"

export const MOCK_MESSAGES: ChatMessage[] = [
  {
    id: "system-1",
    role: "system",
    content:
      "欢迎开始 F-1 签证面签模拟。请准备以下材料：I-20 表格、护照首页、DS-160 确认页、资金证明。",
    timestamp: "10:20",
  },
  {
    id: "officer-1",
    role: "officer",
    content: "你好，请问你申请的是什么签证？打算去美国做什么？",
    timestamp: "10:21",
  },
  {
    id: "user-1",
    role: "user",
    content:
      "您好，我申请的是 F-1 学生签证。我被加州大学伯克利分校的计算机科学硕士项目录取了，计划今年秋季入学。",
    timestamp: "10:21",
  },
  {
    id: "officer-2",
    role: "officer",
    content: "为什么选择这个学校和专业？你本科学的是什么？",
    timestamp: "10:22",
  },
]

export const MOCK_USER_REPORT: UserReport = {
  session_id: MOCK_SESSION_ID,
  visa_family: "f1",
  visa_family_label: "F-1",
  governor_decision: "need_more_evidence",
  interview_status: "waiting_key_proof",
  interview_status_label: "待核验关键证据",
  outcome_label: "需核验关键事实",
  summary: "当前最关键的待证明点是资金来源，可以继续说明，也可以上传对应证据。",
  strengths: ["已完成签证类型识别"],
  risk_points: [],
  risk_level: "medium",
  risk_level_label: "中风险",
  current_key_question: "请说明你的留学资金来源，以及谁来支付学费和生活费。",
  current_key_proof: "funding_proof",
  current_key_proof_label: "资金证明",
  missing_evidence: [
    {
      id: "mock-funding-proof",
      code: "funding_proof",
      name: "资金证明",
      priority: "high",
    },
    {
      id: "mock-home-ties",
      code: "admission_letter",
      name: "录取信",
      priority: "medium",
    },
  ],
  allowed_next_actions: [
    {
      code: "upload_key_proof",
      title: "上传关键证明",
      description: "可以上传资金证明作为证据，同时继续当前问答主线。",
      cta_text: "上传材料",
      intent: "upload",
    },
    {
      code: "explain_missing_proof",
      title: "说明暂缺原因",
      description: "如果暂时没有材料，可以先解释资金安排和可补充的证据。",
      cta_text: "查看建议",
      intent: "details",
    },
  ],
  recommended_improvements: [
    "先说明资金由谁承担，再用银行流水、资助信或奖学金证明补强证据链。",
  ],
  requested_documents: ["funding_proof"],
  requested_document_labels: ["资金证明"],
  advisory_context: {
    score_summary: {
      category_fit: 78,
      document_readiness: 48,
      narrative_consistency: 72,
      confidence: 65,
    },
  },
  prompt_trace: {
    prompt_pack_id: "ds160.interviewer",
    prompt_version: "v2",
    provider: "openai",
    model: "gpt-5.4",
  },
  turn_decision: {
    decision: "need_more_evidence",
    current_key_proof: "funding_proof",
  },
}

export const MOCK_INTERNAL_REPORT: InternalReport = {
  session_id: MOCK_SESSION_ID,
  visa_family: "f1",
  runtime_view_state: {
    decision: "need_more_evidence",
    governor_decision: "need_more_evidence",
    current_key_question: "请说明你的留学资金来源，以及谁来支付学费和生活费。",
    current_key_proof: "funding_proof",
    requested_documents: ["funding_proof"],
    allowed_next_actions: ["upload_key_proof", "explain_missing_proof"],
  },
  runtime_trace: [
    {
      node_name: "build_next_action",
      summary: "requested_documents=1",
    },
  ],
  score_history: [
    {
      scoring_stage: "interview_turn",
      category_fit: 78,
      document_readiness: 48,
      narrative_consistency: 72,
      confidence: 65,
      missing_evidence: ["funding_proof"],
      risk_flags: [],
      summary: "missing=1 risk_flags=0",
    },
  ],
}

export function isMockMode(): boolean {
  return isMockModeEnabled()
}

export function getMockRequiredPackage(visaFamily: VisaFamily): RequiredPackage {
  const requiredInitialPackage = getMockRequiredDocuments(visaFamily)
  return {
    required_initial_package: requiredInitialPackage,
    required_initial_package_labels: requiredInitialPackage.map(toDocumentLabel),
  }
}
