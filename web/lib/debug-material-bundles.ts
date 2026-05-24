import type { DebugMaterialBundleScenario } from "@/lib/api/types"

export interface DebugMaterialBundleOption {
  scenario: DebugMaterialBundleScenario
  label: string
  shortLabel: string
  description: string
}

export const DEFAULT_DEBUG_MATERIAL_BUNDLE_SCENARIO: DebugMaterialBundleScenario =
  "normal_f1_bundle"

export const DEBUG_MATERIAL_BUNDLE_OPTIONS: DebugMaterialBundleOption[] = [
  {
    scenario: "normal_f1_bundle",
    label: "完整基准材料",
    shortLabel: "基准材料",
    description: "DS-160、护照、I-20、录取信、父母存款和亲属关系保持一致。",
  },
  {
    scenario: "school_mismatch_bundle",
    label: "学校材料不一致",
    shortLabel: "学校不一致",
    description: "I-20 与录取通知书使用不同学校名称，适合测试材料交叉核验。",
  },
  {
    scenario: "identity_mismatch_bundle",
    label: "身份号码不一致",
    shortLabel: "身份不一致",
    description: "DS-160 与护照首页的证件号码不同，适合测试身份字段核验。",
  },
  {
    scenario: "funding_shortfall_bundle",
    label: "资金覆盖不足",
    shortLabel: "资金不足",
    description: "I-20 首年费用高于银行可用余额，适合测试资金能力判断。",
  },
  {
    scenario: "sponsor_chain_gap_bundle",
    label: "股权款链路不完整",
    shortLabel: "资金链不完整",
    description: "银行证明显示股权转让款，但缺少配套转让、登记、税务或流水材料。",
  },
  {
    scenario: "claim_vs_document_bundle",
    label: "口头资金来源不一致",
    shortLabel: "口头不一致",
    description: "申请人口头称自费，材料显示父母资助，适合测试问答与材料一致性。",
  },
]

export function getDebugMaterialBundleOption(
  scenario: DebugMaterialBundleScenario | string,
): DebugMaterialBundleOption {
  return (
    DEBUG_MATERIAL_BUNDLE_OPTIONS.find((option) => option.scenario === scenario) ??
    DEBUG_MATERIAL_BUNDLE_OPTIONS[0]
  )
}
