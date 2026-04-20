# DS-160 模拟器领域规则专家评审模板 v1

## 使用说明

这是一份给签证 / 规则领域专家填写的工作模板。

填写目标：

1. 明确 `DS-160` 在本项目中的证据定位。
2. 明确不同签证家族的基线材料、条件补证、冲突处理。
3. 明确哪些结论可以规则化，哪些必须进入人工复核。
4. 给后续工程实现提供可直接落地的规则输入。

填写建议：

- 每个问题尽量给出“必须 / 建议 / 可选 / 不需要”的明确结论。
- 如果存在使领馆、国家、签证场景差异，请直接写出差异条件。
- 如果当前项目里的材料命名不准确，请直接在“建议改名”栏说明。

关联背景文档：

- [DS-160 模拟器领域规则交接文档 v1](./2026-04-20-ds160-domain-rules-handoff.md)

## 基本信息

| 项目 | 填写内容 |
| --- | --- |
| 评审人 |  |
| 角色 / 领域 |  |
| 日期 |  |
| 适用范围 | 例如：F-1 / J-1 / B1/B2 / H-1B |
| 是否包含使领馆本地差异 | 是 / 否 |

## 1. DS-160 定位确认

### 1.1 结论勾选

- [ ] `DS-160 confirmation page` 只能证明“已提交申请表”
- [ ] 完整 `DS-160` 可作为“高权重签字自述”
- [ ] `DS-160` 不应单独视为“已核实官方事实”
- [ ] `DS-160` 中部分字段可直接进入 `documented`
- [ ] `DS-160` 中大部分关键字段仍需 supporting docs 补证

### 1.2 专家意见

| 问题 | 结论 | 备注 |
| --- | --- | --- |
| `DS-160 confirmation page` 是否应单独建一种 `document_type` |  |  |
| 完整 `DS-160` 是否应单独建一种 `document_type` |  |  |
| `DS-160` 是否应视为签字自述而非官方核验结论 |  |  |
| 哪些字段可仅凭完整 `DS-160` 进入较高可信状态 |  |  |
| 哪些字段绝不能只靠 `DS-160` 通过 |  |  |

### 1.3 建议补充说明

请填写：

-
-
-

## 2. 证据层级确认

请确认本项目是否采用以下证据层级。

| 证据层级 | 建议定义 | 是否认可 | 如不认可，请改写 |
| --- | --- | --- | --- |
| `oral_claim` | 用户口头回答 |  |  |
| `signed_application_claim` | `DS-160` 等签字申请表自述 |  |  |
| `third_party_supporting_document` | 学校信、银行流水、雇主信等 |  |  |
| `official_or_petition_record` | 护照、`I-20`、`DS-2019`、`I-797` 等 |  |  |
| `cross_verified_fact` | 两个及以上高权重来源一致 |  |  |

### 2.1 当前状态机是否足够

当前代码状态：

- `unknown`
- `claimed`
- `documented`
- `confirmed`
- `conflicted`

请确认：

| 问题 | 结论 | 备注 |
| --- | --- | --- |
| 这 5 个状态是否足够 |  |  |
| 是否需要把 `signed_application_claim` 单独映射到新状态 |  |  |
| `confirmed` 是否必须要求双重高权重证据 |  |  |
| `conflicted` 是否需要细分为“口头冲突 / 文档冲突 / 申请表冲突” |  |  |

## 3. 签证家族基线材料确认表

请逐项确认当前项目中的初始材料包是否合理。

| 签证家族 | 当前基线包 | 是否合理 | 应删减 / 应新增 | 是否需要场景拆分 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `f1` | `ds160`, `passport_bio`, `i20`, `admission_letter`, `funding_proof` |  |  |  |  |
| `j1` | `ds160`, `passport_bio`, `ds2019`, `funding_proof` |  |  |  |  |
| `b1_b2` | `ds160`, `passport_bio`, `itinerary_or_trip_purpose` |  |  |  |  |
| `h1b` | `ds160`, `passport_bio`, `i797`, `employer_letter` |  |  |  |  |
| `l1a` | `ds160`, `passport_bio`, `i797`, `employer_letter` |  |  |  |  |
| `l1b` | `ds160`, `passport_bio`, `i797`, `employer_letter` |  |  |  |  |
| `o1` | `ds160`, `passport_bio`, `i797`, `evidence_of_achievement` |  |  |  |  |
| `m1` | `ds160`, `passport_bio`, `school_letter`, `funding_proof` |  |  |  |  |

### 3.1 建议新增的场景拆分

| 签证家族 | 建议场景 | 场景说明 | 对基线材料的影响 |
| --- | --- | --- | --- |
| `f1` |  |  |  |
| `j1` |  |  |  |
| `b1_b2` |  |  |  |
| `h1b/l1/o1` |  |  |  |

## 4. 文档类型解读确认表

请确认下面这些 `document_type` 的业务定义是否准确。

| `document_type` | 当前建议定义 | 是否认可 | 建议主抽字段 | 是否可单独满足主线 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `ds160` | 申请人正式签字申请表 / 或确认页 |  |  |  |  |
| `passport_bio` | 护照身份页 |  |  |  |  |
| `i20` | 学生核心资格文件 |  |  |  |  |
| `ds2019` | `J-1` 核心资格文件 |  |  |  |  |
| `admission_letter` | 学校录取 / 补强材料 |  |  |  |  |
| `school_letter` | 学校补充证明材料 |  |  |  |  |
| `funding_proof` | 资金证明材料集合 |  |  |  |  |
| `itinerary_or_trip_purpose` | 行程 / 商务 / 探亲 / 旅行目的材料 |  |  |  |  |
| `i797` | petition / approval 核心材料 |  |  |  |  |
| `employer_letter` | 雇主 supporting material |  |  |  |  |
| `evidence_of_achievement` | `O-1` 能力证明集合 |  |  |  |  |

### 4.1 文档类型命名修正建议

| 当前名称 | 是否需要改名 | 建议新名称 | 原因 |
| --- | --- | --- | --- |
| `ds160` |  |  |  |
| `funding_proof` |  |  |  |
| `itinerary_or_trip_purpose` |  |  |  |
| `school_letter` |  |  |  |
| `evidence_of_achievement` |  |  |  |

## 5. 字段级补证矩阵

请按字段而不是按“整体感觉”确认补证逻辑。

| 字段路径 | 当前业务含义 | 最低可信来源 | 是否允许仅凭口头进入 `claimed` | 是否允许仅凭 `DS-160` 进入高可信状态 | 需要哪些补证 | 冲突时如何处理 |
| --- | --- | --- | --- | --- | --- | --- |
| `/identity/full_name` | 申请人姓名 |  |  |  |  |  |
| `/identity/passport_number` | 护照号 |  |  |  |  |  |
| `/identity/nationality` | 国籍 |  |  |  |  |  |
| `/visa_intent/travel_purpose` | 赴美目的 |  |  |  |  |  |
| `/education/sevis_id` | `SEVIS ID` |  |  |  |  |  |
| `/education/school_name` | 学校名称 |  |  |  |  |  |
| `/education/program_name` | 项目名称 |  |  |  |  |  |
| `/education/sponsor_name` | `J-1 sponsor` |  |  |  |  |  |
| `/funding/primary_source` | 主要资金来源 |  |  |  |  |  |

### 5.1 建议新增字段

| 字段路径 | 业务含义 | 是否建议新增 | 推荐证据来源 | 备注 |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |
|  |  |  |  |  |
|  |  |  |  |  |

## 6. 资金证明规则确认

这是当前最需要细化的一块，请尽量明确写。

### 6.1 资金来源判断

| 场景 | 是否足以认定 `funding.primary_source` | 最低材料要求 | 备注 |
| --- | --- | --- | --- |
| 仅口头说“父母资助” |  |  |  |
| `DS-160` 写了 sponsor 信息 |  |  |  |
| 有 sponsor letter 无银行流水 |  |  |  |
| 有银行流水但未说明与申请人关系 |  |  |  |
| 有奖学金 / 助研 / fellowship 信 |  |  |  |
| 多个资金来源混合 |  |  |  |

### 6.2 金额 / 充分性规则

| 问题 | 结论 | 备注 |
| --- | --- | --- |
| 是否必须判断资金“充足性” |  |  |
| 是否需要区分“能解释来源”与“金额足够”两个阶段 |  |  |
| 是否需要按签证家族分别设定资金证明标准 |  |  |
| 是否需要把“近 3 个月 / 6 个月银行流水”写成规则 |  |  |

## 7. 冲突处理规则确认

### 7.1 冲突场景

| 冲突场景 | 应进入什么状态 | 是否必须补证 | 是否应进入高风险复核 | 备注 |
| --- | --- | --- | --- | --- |
| 口头说法和 `DS-160` 不一致 |  |  |  |  |
| `DS-160` 与护照不一致 |  |  |  |  |
| `DS-160` 与 `I-20 / DS-2019` 不一致 |  |  |  |  |
| 口头资助说明和资助材料不一致 |  |  |  |  |
| 学校 / 项目名称在多份材料中不一致 |  |  |  |  |
| 雇主 / petition 信息不一致 |  |  |  |  |

### 7.2 冲突纠正

| 问题 | 结论 | 备注 |
| --- | --- | --- |
| 申请人口头更正一次后，是否可消除冲突 |  |  |
| 是否要求“更正 + supporting doc”才可恢复 |  |  |
| 是否要保留冲突历史供后续评分使用 |  |  |

## 8. 打分规则评审

### 8.1 维度定义

| 打分维度 | 当前理解 | 是否认可 | 建议改名 / 改定义 |
| --- | --- | --- | --- |
| `category_fit` | 与签证类别匹配度 |  |  |
| `document_readiness` | 材料就绪度 |  |  |
| `narrative_consistency` | 叙事一致性 |  |  |
| `confidence` | 当前结论的可信 / 覆盖程度 |  |  |

### 8.2 是否建议规则化

| 项目 | 应规则化 | 可模型辅助 | 不建议自动化 | 备注 |
| --- | --- | --- | --- | --- |
| `document_readiness` |  |  |  |  |
| `missing_evidence` |  |  |  |  |
| `narrative_consistency` |  |  |  |  |
| `risk_flags` |  |  |  |  |
| `continue_interview / need_more_evidence` |  |  |  |  |
| `simulated_refusal` |  |  |  |  |

### 8.3 分数阈值建议

| 场景 | 推荐阈值 / 条件 | 备注 |
| --- | --- | --- |
| 可以进入正式 interview |  |  |
| 必须继续补证 |  |  |
| 必须进入高风险复核 |  |  |
| 可以触发模拟拒签 |  |  |

## 9. Governor 规则评审

### 9.1 决策条件

| 决策 | 建议触发条件 | 是否认可 | 备注 |
| --- | --- | --- | --- |
| `need_more_evidence` | 基线缺失 / 字段仅为弱证据 / 有待解析材料 |  |  |
| `continue_interview` | 主证据齐全且无关键冲突 |  |  |
| `high_risk_review` | 高影响冲突或复杂法律判断 |  |  |
| `simulated_refusal` | 明确造假或充分证据支持的高严重度结论 |  |  |

### 9.2 严格约束

请勾选是否认可下面这些硬约束：

- [ ] 负面高风险结论必须带 `evidence_refs`
- [ ] 不能因为“分数低”就直接拒签
- [ ] `unknown` 不能被当成否定事实
- [ ] “材料已上传但未解析完”不能等同于“已补证成功”

## 10. 使领馆 / 地区差异

| 差异类型 | 是否存在 | 影响哪些签证家族 / 字段 | 应如何在系统中表达 |
| --- | --- | --- | --- |
| 国家差异 |  |  |  |
| 使馆 / 领馆差异 |  |  |  |
| 场景差异 |  |  |  |
| 年龄 / 学历 / 职业差异 |  |  |  |

## 11. 不应自动判断的事项

请列出你认为系统不应只靠规则或模型自动决定的事项。

| 事项 | 原因 | 建议处理方式 |
| --- | --- | --- |
|  |  |  |
|  |  |  |
|  |  |  |

## 12. 最终结论

### 12.1 必改项

请填写：

1.
2.
3.

### 12.2 建议项

请填写：

1.
2.
3.

### 12.3 暂不处理项

请填写：

1.
2.
3.

## 13. 工程落地建议

如果需要对接工程实现，请直接写成“可编码”的建议。

| 建议 | 优先级 | 推荐落地方式 | 备注 |
| --- | --- | --- | --- |
|  | P0 / P1 / P2 |  |  |
|  | P0 / P1 / P2 |  |  |
|  | P0 / P1 / P2 |  |  |

## 14. 参考官方链接

- [DS-160 FAQs](https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application/ds-160-faqs.amp.html)
- [Student Visa](https://travel.state.gov/content/travel/en/us-visas/study/student-visa.html?lv=true)
- [Exchange Visitor Visa](https://travel.state.gov/content/travel/en/us-visas/study/exchange.html)
- [Visitor Visa](https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visitor.html?wpappninja_v=24wrihrp9&wpmobileexternal=true)
- [Temporary Worker Visas](https://travel.state.gov/content/travel/en/us-visas/employment/temporary-worker-visas.htmls.html)
- [Ineligibilities and Waivers: Laws](https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/waivers.html)
