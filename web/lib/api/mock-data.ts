import { isMockModeEnabled } from "./config"
import { getMockRequiredDocuments, toDocumentLabel } from "./mappers"
import type {
  CaseBoardDelta,
  ChatMessage,
  InternalReport,
  RequiredPackage,
  UserReport,
  VisaFamily,
} from "./types"

export const MOCK_SESSION_ID = "mock-session-001"

export const MOCK_CASE_BOARD: CaseBoardDelta = {
  latest_material: {
    document_id: "mock-i20",
    filename: "berkeley-i20.pdf",
    understanding_status: "completed",
    document_type: "i20",
    document_type_label: "I-20 表格",
    relevance: "high",
    supported_claims: ["学校和项目信息", "入学时间"],
    confidence: 0.91,
    feedback_message: "已识别学校、项目和入学时间。",
    unknowns: ["资金承担人、可用金额和资金存放位置仍需要在问答中说明。"],
  },
  evidence_cards: [
    {
      evidence_id: "ev-i20-school",
      source_type: "document",
      document_id: "mock-i20",
      excerpt: "University of California, Berkeley",
      claim_refs: ["claim-school"],
      confidence: 0.94,
    },
    {
      evidence_id: "ev-i20-program",
      source_type: "document",
      document_id: "mock-i20",
      excerpt: "Master of Science in Computer Science, start date Fall 2026",
      claim_refs: ["claim-program", "claim-start-term"],
      confidence: 0.9,
    },
    {
      evidence_id: "ev-user-funding",
      source_type: "user_statement",
      document_id: null,
      excerpt: "用户口述：父母和家庭储蓄会承担学费和生活费。",
      claim_refs: ["claim-funding-source"],
      confidence: 0.68,
    },
  ],
  claims: [
    {
      claim_id: "claim-school",
      field_path: "/education/school_name",
      field_label: "就读学校",
      value: "University of California, Berkeley",
      status: "documented",
      supporting_evidence_ids: ["ev-i20-school"],
      conflicting_evidence_ids: [],
      confidence: 0.94,
    },
    {
      claim_id: "claim-program",
      field_path: "/education/program",
      field_label: "就读项目",
      value: "Master of Science in Computer Science",
      status: "documented",
      supporting_evidence_ids: ["ev-i20-program"],
      conflicting_evidence_ids: [],
      confidence: 0.9,
    },
    {
      claim_id: "claim-start-term",
      field_path: "/education/start_term",
      field_label: "入学时间",
      value: "Fall 2026",
      status: "documented",
      supporting_evidence_ids: ["ev-i20-program"],
      conflicting_evidence_ids: [],
      confidence: 0.9,
    },
    {
      claim_id: "claim-funding-source",
      field_path: "/funding/primary_source",
      field_label: "资金来源",
      value: "父母和家庭储蓄",
      status: "stated",
      supporting_evidence_ids: ["ev-user-funding"],
      conflicting_evidence_ids: [],
      confidence: 0.68,
    },
  ],
  open_proof_points: [
    {
      proof_point_id: "proof-funding-source",
      visa_family: "f1",
      question: "谁承担学费和生活费，资金金额是多少，资金目前在哪里？",
      status: "open",
      why_it_matters: "F-1 面签需要确认学习计划有真实、稳定、可解释的资金支持。",
      claim_refs: ["claim-funding-source"],
      evidence_refs: ["ev-user-funding"],
    },
  ],
  conflicts: [],
  next_move: {
    move_type: "ask_followup",
    question:
      "你刚才说父母和家庭储蓄会支持学业。请具体说明资金金额、存放在哪里，以及毕业后的计划。",
    reason: "学校和项目已经有材料支持，资金安排仍主要来自口述，需要自然追问。",
    claim_refs: ["claim-funding-source", "claim-school", "claim-program"],
    evidence_refs: ["ev-user-funding", "ev-i20-school", "ev-i20-program"],
  },
}

export const MOCK_MESSAGES: ChatMessage[] = [
  {
    id: "assistant-1",
    role: "assistant",
    content: "你好。你今天申请什么签证？去美国的主要目的是什么？",
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
    id: "assistant-2",
    role: "assistant",
    content: "为什么选择伯克利和这个计算机科学项目？",
    timestamp: "10:22",
  },
  {
    id: "user-2",
    role: "user",
    content:
      "这个项目的系统方向和我本科做过的分布式系统项目很匹配。我毕业后计划回国做工程平台相关工作。",
    timestamp: "10:23",
  },
  {
    id: "assistant-3",
    role: "assistant",
    content:
      "谁会支付你的学费和生活费？请具体说明资金来源和目前准备情况。",
    timestamp: "10:24",
  },
]

export const MOCK_USER_REPORT: UserReport = {
  session_id: MOCK_SESSION_ID,
  visa_family: "f1",
  visa_family_label: "F-1",
  governor_decision: "continue_interview",
  interview_status: "continue_interview",
  interview_status_label: "继续面签问答",
  outcome_label: "可继续追问资金安排",
  summary:
    "案例已理解到申请人、学校、项目和入学时间；当前不是材料阻断，下一轮应自然追问资金承担人与毕业计划。",
  strengths: [
    "I-20 已支持学校、项目和入学时间。",
    "用户已说明选择项目的学习理由和职业方向。",
  ],
  risk_points: ["资金来源目前主要来自口述，仍需问清金额、账户和承担人。"],
  risk_level: "medium",
  risk_level_label: "中风险",
  current_key_question:
    "你刚才说父母和家庭储蓄会支持学业。请具体说明资金金额、存放在哪里，以及毕业后的计划。",
  current_key_proof: null,
  current_key_proof_label: null,
  missing_evidence: [
    {
      id: "proof-funding-source",
      code: "funding_source_explanation",
      name: "资金承担人、金额和资金来源说明",
      priority: "medium",
    },
  ],
  allowed_next_actions: [
    {
      code: "answer_question",
      title: "继续回答当前追问",
      description: "围绕资金承担人、金额、账户来源和毕业计划直接回答。",
      cta_text: "继续回答",
      intent: "continue",
    },
    {
      code: "upload_key_proof",
      title: "上传可补强证据",
      description: "如果手边有银行流水、资助说明或奖学金文件，可以作为证据补强。",
      cta_text: "上传证据",
      intent: "upload",
    },
  ],
  recommended_improvements: [
    "回答时先说清谁付款、金额范围、资金存放位置，再说明这笔资金如何覆盖学费和生活费。",
    "如上传材料，应让材料支持已经陈述的事实，而不是把上传当作继续面签的前置条件。",
  ],
  requested_documents: [],
  requested_document_labels: [],
  case_board: MOCK_CASE_BOARD,
  advisory_context: {
    score_summary: {
      category_fit: 78,
      document_readiness: 64,
      narrative_consistency: 72,
      confidence: 70,
    },
  },
  prompt_trace: {
    prompt_pack_id: "ds160.interviewer",
    prompt_version: "v2",
    provider: "openai",
    model: "gpt-5.4",
  },
  turn_decision: {
    decision: "continue_interview",
    reason: "case_board_next_move",
    current_key_proof: null,
    next_move_claim_refs: MOCK_CASE_BOARD.next_move?.claim_refs,
    next_move_evidence_refs: MOCK_CASE_BOARD.next_move?.evidence_refs,
  },
}

export const MOCK_INTERNAL_REPORT: InternalReport = {
  session_id: MOCK_SESSION_ID,
  visa_family: "f1",
  runtime_view_state: {
    decision: "continue_interview",
    governor_decision: "continue_interview",
    current_key_question: MOCK_USER_REPORT.current_key_question,
    current_key_proof: null,
    requested_documents: [],
    allowed_next_actions: ["answer_question", "upload_key_proof"],
    case_board: MOCK_CASE_BOARD,
  },
  case_board: MOCK_CASE_BOARD,
  runtime_trace: [
    {
      node_name: "case_memory_projector",
      summary: "claims=4 evidence_cards=3 open_proof_points=1 conflicts=0",
    },
    {
      node_name: "build_next_action",
      summary: "next_move=ask_followup basis=case_board",
    },
  ],
  score_history: [
    {
      scoring_stage: "interview_turn",
      category_fit: 78,
      document_readiness: 64,
      narrative_consistency: 72,
      confidence: 70,
      missing_evidence: ["funding_source_explanation"],
      risk_flags: [],
      summary: "open_proof_points=1 risk_flags=0",
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
