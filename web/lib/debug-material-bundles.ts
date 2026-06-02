import type { DebugMaterialBundleScenario, VisaFamily } from "@/lib/api/types"

export interface DebugMaterialBundleOption {
  scenario: DebugMaterialBundleScenario
  visaFamilies: VisaFamily[]
  label: string
  shortLabel: string
  description: string
}

export const DEFAULT_DEBUG_MATERIAL_BUNDLE_SCENARIO: DebugMaterialBundleScenario =
  "normal_f1_bundle"

export const DEBUG_MATERIAL_BUNDLE_OPTIONS: DebugMaterialBundleOption[] = [
  {
    scenario: "normal_f1_bundle",
    visaFamilies: ["F-1"],
    label: "完整基准材料",
    shortLabel: "基准材料",
    description: "DS-160、护照、I-20、录取信、父母存款和亲属关系保持一致。",
  },
  {
    scenario: "normal_j1_bundle",
    visaFamilies: ["J-1"],
    label: "J-1 交流访问材料",
    shortLabel: "J-1 材料",
    description: "DS-160、护照、DS-2019、项目邀请、资金证明和 SEVIS 缴费信息保持一致。",
  },
  {
    scenario: "normal_b1_b2_bundle",
    visaFamilies: ["B-1/B-2"],
    label: "B-1/B-2 访问材料",
    shortLabel: "访问材料",
    description: "DS-160、护照、行程目的、邀请/会议信息、在职证明和资金材料保持一致。",
  },
  {
    scenario: "normal_h1b_bundle",
    visaFamilies: ["H-1B"],
    label: "H-1B 工作材料",
    shortLabel: "H-1B 材料",
    description: "DS-160、护照、I-797、雇主信、LCA 和学历/履历材料保持一致。",
  },
  {
    scenario: "school_mismatch_bundle",
    visaFamilies: ["F-1"],
    label: "学校材料不一致",
    shortLabel: "学校不一致",
    description: "I-20 与录取通知书使用不同学校名称，适合测试材料交叉核验。",
  },
  {
    scenario: "identity_mismatch_bundle",
    visaFamilies: ["F-1"],
    label: "身份号码不一致",
    shortLabel: "身份不一致",
    description: "DS-160 与护照首页的证件号码不同，适合测试身份字段核验。",
  },
  {
    scenario: "funding_shortfall_bundle",
    visaFamilies: ["F-1"],
    label: "资金覆盖不足",
    shortLabel: "资金不足",
    description: "I-20 首年费用高于银行可用余额，适合测试资金能力判断。",
  },
  {
    scenario: "sponsor_chain_gap_bundle",
    visaFamilies: ["F-1"],
    label: "股权款链路不完整",
    shortLabel: "资金链不完整",
    description: "银行证明显示股权转让款，但缺少配套转让、登记、税务或流水材料。",
  },
  {
    scenario: "claim_vs_document_bundle",
    visaFamilies: ["F-1"],
    label: "口头资金来源不一致",
    shortLabel: "口头不一致",
    description: "申请人口头称自费，材料显示父母资助，适合测试问答与材料一致性。",
  },
]

export function getDebugMaterialBundleOptionsForVisaFamily(
  visaFamily?: VisaFamily | null,
): DebugMaterialBundleOption[] {
  if (!visaFamily) {
    return DEBUG_MATERIAL_BUNDLE_OPTIONS
  }
  const scopedOptions = DEBUG_MATERIAL_BUNDLE_OPTIONS.filter((option) =>
    option.visaFamilies.includes(visaFamily),
  )
  return scopedOptions.length ? scopedOptions : DEBUG_MATERIAL_BUNDLE_OPTIONS
}

export function getDefaultDebugMaterialBundleScenarioForVisaFamily(
  visaFamily?: VisaFamily | null,
): DebugMaterialBundleScenario {
  return (
    getDebugMaterialBundleOptionsForVisaFamily(visaFamily)[0]?.scenario ??
    DEFAULT_DEBUG_MATERIAL_BUNDLE_SCENARIO
  )
}

export function getDebugMaterialBundleOption(
  scenario: DebugMaterialBundleScenario | string,
): DebugMaterialBundleOption {
  return (
    DEBUG_MATERIAL_BUNDLE_OPTIONS.find((option) => option.scenario === scenario) ??
    DEBUG_MATERIAL_BUNDLE_OPTIONS[0]
  )
}
