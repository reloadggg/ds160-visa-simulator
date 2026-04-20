# DS-160 模拟器领域规则交接文档 v1

## 1. 文档目的

这份文档不是法律意见，也不是给终端用户看的签证教程。

它的目标是给后续参与规则整理的人一个统一起点，解决当前项目里最难迭代的几个问题：

1. `DS-160`、口头回答、上传材料，到底谁更可信，应该如何分层。
2. 什么情况下系统只能说“用户声称如此”，什么情况下才能说“已有文档支持”。
3. 什么字段必须补证，什么字段可以先继续 interview。
4. 打分和 Governor 不能只靠模型感觉，必须回到明确的证据规则。

这份文档面向：

- 领域专家
- 产品经理
- 规则作者
- 后续实现这些规则的工程师

## 2. 当前项目为什么难迭代

当前系统已经把“用户自述”和“文档证据”在代码层做了初步分离，但领域规则还不够清楚，所以很容易出现下面这些问题：

1. `DS-160` 被当成“普通材料名”使用，但没有明确定义它在事实判定里的法律地位。
2. `funding_proof`、`itinerary_or_trip_purpose`、`employer_letter` 这些材料类型虽然存在，但“满足到什么程度才算够”没有正式 rubric。
3. 当前打分中的 `document_readiness / narrative_consistency / confidence` 还没有绑定到严格的证据阈值。
4. 当前系统虽然避免了“未知=否定”，但还没有把“自述”“签字申请表”“第三方证明”“官方批准件”明确分层。
5. 结果上就会让人感觉系统在“靠模型基座能力推断”，而不是“靠可追溯规则裁决”。

## 3. 官方基线

下面这些是本项目规则设计时应优先参考的官方来源。

### 3.1 DS-160 的官方属性

美国国务院 `DS-160 FAQ` 明确说明：

- 申请人电子签署 `DS-160` 时，认证自己已经阅读并理解问题，答案“据其所知和所信是真实且正确的”。
- 包含虚假或误导性陈述的申请，可能导致永久拒签或拒绝入境。
- 指纹采集当天，申请人还会再次确认 `DS-160` 与面谈陈述是真实完整的。

官方来源：

- [DS-160 FAQs](https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application/ds-160-faqs.amp.html)

这意味着：

1. `DS-160` 不是普通聊天输入。
2. `DS-160` 是申请人正式签署的非移民签证申请表。
3. 但它依然主要是申请人自行申报的信息，不等于领馆或第三方已经独立核验。

### 3.2 DS-160 填表时需要准备的信息

同一份官方 `DS-160 FAQ` 还说明，申请人填写 `DS-160` 时通常需要准备：

- 护照
- 已有的旅行行程
- 近五年的美国旅行记录
- 简历 / 教育和工作经历
- 学生与交流访问类别的 `I-20 / DS-2019 / SEVIS ID`
- 申请型临时工作签证的 `I-129`

这意味着：

1. `DS-160` 本身就应被视为多种底层事实的汇总容器。
2. 但系统不应因为 `DS-160` 出现了某个字段，就自动认为该字段已被完全独立证明。

### 3.3 F/M 学生签证官方最低材料与补充材料

美国国务院学生签证页面列出，面谈前至少要准备：

- 护照
- `DS-160` 确认页
- 缴费凭证
- 照片
- `Form I-20`

同时，领事官还可能要求额外材料，用于证明：

- 学术准备
- 完成学业后离境意图
- 如何支付全部教育、生活和旅行成本

官方来源：

- [Student Visa](https://travel.state.gov/content/travel/en/us-visas/study/student-visa.html?lv=true)

### 3.4 J-1 交流访问签证官方最低材料与补充材料

美国国务院交流访问签证页面列出，面谈前至少要准备：

- 护照
- `DS-160` 确认页
- 缴费凭证
- 照片
- `Form DS-2019`
- 某些 `Trainee / Intern` 类别还需 `DS-7002`

同时还可能被要求补充：

- 旅行目的
- 旅行后离境意图
- 支付旅行成本的能力

另外，`J-1` 可能触发两年回国居住要求，尤其在政府资助、住院医师培训、技能清单等场景下。

官方来源：

- [Exchange Visitor Visa](https://travel.state.gov/content/travel/en/us-visas/study/exchange.html)

### 3.5 B-1/B-2 访问签证官方最低材料与补充材料

美国国务院访问签证页面列出，面谈前至少要准备：

- 护照
- `DS-160` 确认页
- 缴费凭证
- 照片

可能被要求补充的证据包括：

- 旅行目的
- 旅行后离境意图
- 支付全部旅行成本的能力

官方页面还特别强调：

- 工作或家庭关系证据可以帮助证明旅行目的和回国约束；
- 邀请函或 `Affidavit of Support` 不是办理访问签证的必需材料，也不是决定是否签发的核心因素。

官方来源：

- [Visitor Visa](https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visitor.html?wpappninja_v=24wrihrp9&wpmobileexternal=true)

### 3.6 H/L/O 等临时工作签证官方最低材料

美国国务院临时工作签证页面列出，面谈前至少要准备：

- 护照
- `DS-160` 确认页
- 缴费凭证
- 照片
- `I-129` 或 `I-797` 上的 petition receipt / action 信息
- `L blanket` 还需 `I-129S`

页面还说明：

- 大多数临时工作签证申请人都要证明临时停留后的回国意图；
- 但 `H-1B` 与 `L` 类别有特殊处理，不按普通非移民临时意图逻辑简单套用。

官方来源：

- [Temporary Worker Visas](https://travel.state.gov/content/travel/en/us-visas/employment/temporary-worker-visas.htmls.html)

### 3.7 非移民意图的官方法条基线

美国国务院 `Ineligibilities and Waivers: Laws` 页面列出：

- `INA 214(b)`：除特定豁免类别外，非移民申请人原则上先被推定为移民，直到其令领事官满意地证明自己符合相应非移民类别要求。
- `INA 221(g)`：如果申请、随附材料不完整，或领事官认为申请人可能不符合条件，则签证不能签发。

官方来源：

- [Ineligibilities and Waivers: Laws](https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/waivers.html)

这两条对系统设计的意义非常直接：

1. “材料不足”本身就足以阻止进入下一阶段。
2. `need_more_evidence` 不应该被视为异常分支，而应视为默认分支之一。

## 4. 对 DS-160 的项目级解释

本项目下一版应明确采用下面这个解释：

### 4.1 DS-160 是什么

`DS-160` 应被视为：

- 申请人正式签署的非移民签证申请表
- 高权重的“签字自述”
- 不是领馆已经核验完成的官方结论

### 4.2 DS-160 证明了什么

`DS-160 confirmation page` 至少证明：

- 申请人已经提交过 `DS-160`
- 申请流程进入了可预约 / 可面谈的前置状态之一

如果系统后续支持读取完整 `DS-160 PDF` 或字段导出，`DS-160` 还可以证明：

- 申请人在正式申请表上如何陈述自己的身份、旅行目的、教育、就业、资助、联系人等事实

### 4.3 DS-160 不能单独证明什么

除非另有明确规则，`DS-160` 单独存在时，不应被视为以下事实已经被独立核验：

- 资金来源真实存在
- 学校录取真实有效
- 雇主关系真实有效
- 申请人一定具备足够回国约束
- 申请人口头说法与所有底层文件都一致

### 4.4 推荐的证据权重层级

为便于后续规则实现，建议新增“证据来源层级”，而不是只用一个 `documented`。

建议层级如下：

1. `oral_claim`
   - 用户口头回答
   - 可信度最低
   - 只能写入 `claimed`

2. `signed_application_claim`
   - `DS-160`
   - 比普通口头回答更高
   - 但仍属于申请人自陈

3. `third_party_supporting_document`
   - 学校录取信
   - 银行流水
   - sponsor letter
   - employer letter

4. `official_or_petition_record`
   - 护照生物页
   - `I-20`
   - `DS-2019`
   - `I-797`
   - `I-129S`

5. `cross_verified_fact`
   - 至少两个高权重来源一致
   - 或一个高权重官方文件 + 一个一致的正式申请表字段

当前代码只有：

- `unknown`
- `claimed`
- `documented`
- `confirmed`
- `conflicted`

见 [contracts.py](../../../app/domain/contracts.py)。

建议下一版把“`DS-160` 的签字自述”和“第三方 / 官方文件证据”分开，不要都塞进 `documented`。

## 5. 当前项目应采用的事实状态解释

结合现有代码，建议后续统一按下面解释：

### 5.1 `unknown`

- 还没有可靠信息
- 或有信息但不足以进入 profile 主事实

### 5.2 `claimed`

- 仅来自用户口头说法
- 或来自弱可信、不可核验输入
- 不能直接视为“已证实”

### 5.3 `documented`

建议下一版限定为：

- 至少存在一份可解析的、与该字段直接相关的文档证据
- 且当前没有发现文档内部冲突

### 5.4 `confirmed`

建议只在以下情形使用：

- 关键字段被两个以上独立高权重来源支持
- 或业务上确实需要“已核实”状态

### 5.5 `conflicted`

- 口头说法和文档冲突
- 文档与文档冲突
- 当前不能稳定产出单一可信值

## 6. 什么情况下必须补证

下一版规则建议把“补证触发条件”拆成四类。

### 6.1 基线必备材料缺失

当前代码中的基线包来自 `app/policy_packs/*.yaml`：

- `f1`: `ds160`, `passport_bio`, `i20`, `admission_letter`, `funding_proof`
- `j1`: `ds160`, `passport_bio`, `ds2019`, `funding_proof`
- `b1_b2`: `ds160`, `passport_bio`, `itinerary_or_trip_purpose`
- `h1b`: `ds160`, `passport_bio`, `i797`, `employer_letter`
- `l1a/l1b`: `ds160`, `passport_bio`, `i797`, `employer_letter`
- `o1`: `ds160`, `passport_bio`, `i797`, `evidence_of_achievement`
- `m1`: `ds160`, `passport_bio`, `school_letter`, `funding_proof`

这部分可继续保留，但必须让领域专家确认：

1. 这些基线包是否符合每个签证家族最常见场景。
2. 是否需要按场景细分，例如：
   - `F-1 self-funded`
   - `F-1 parent-sponsored`
   - `J-1 government-funded`
   - `B-1 conference`
   - `B-2 family visit`

### 6.2 关键字段仅为口头或 DS-160 自述

以下字段即使已经出现在口头回答或 `DS-160` 中，也仍应按“需要进一步证据”处理：

1. `funding.primary_source`
   - 只要不是明显的自费且金额风险极低，就应要求资助证明

2. `education.school_name / program_name / sevis_id`
   - `F/M/J` 类别不能只靠口头说法或 `DS-160`
   - 应由 `I-20 / DS-2019 / school letter / admission letter` 支持

3. `employment.employer`
   - `H/L/O` 不应只靠 `DS-160`
   - 应与 `I-797 / I-129 / employer letter` 对照

4. `travel purpose`
   - `B1/B2` 不应只靠一句“旅游/商务”
   - 至少应形成更具体的行程、会议、探亲或活动说明

### 6.3 高重要字段出现冲突

以下任一冲突都应触发 `need_more_evidence` 或 `high_risk_review`：

- 护照姓名和 `DS-160` 姓名不一致
- 护照号和 `DS-160` 护照号不一致
- `I-20 / DS-2019` 的学校、项目、`SEVIS ID` 与申请人自述不一致
- 口头说“父母资助”，但文档显示奖学金或个人存款
- 口头说“旅游”，但行程 / 邀请 / 会议材料指向商务活动

### 6.4 使馆 / 领馆本地化要求

官方页面反复强调“具体流程和附加材料以使领馆网站为准”。

因此规则系统下一版必须预留：

- `post_specific_overrides`
- `country_specific_rules`
- `embassy_checklist`

否则所有规则都会被迫写成“全球平均值”，很难真正迭代。

## 7. 文档类型解读建议

下面是面向本项目的材料解释建议。

### 7.1 `ds160`

建议解释：

- 证明申请人已正式提交签证申请表
- 若支持完整表单解析，则它是“签字自述的结构化载体”

不应单独视为：

- 已核验身份
- 已核验资金
- 已核验学校或雇主关系

推荐可抽字段：

- `full_name`
- `passport_number`
- `nationality`
- `travel_purpose`
- `school/program/employer/contact` 等表单信息

推荐系统行为：

- `DS-160 confirmation page` 只满足“已提交”门槛
- 完整 `DS-160` 字段页可满足“signed_application_claim”层

### 7.2 `passport_bio`

建议解释：

- 身份类高权重文件
- 应优先支持：
  - `full_name`
  - `passport_number`
  - `nationality`

推荐系统行为：

- 只要清晰可读，就应强于口头自述和 `DS-160` 同名字段

### 7.3 `i20`

建议解释：

- `F/M` 学生签证核心资格文件之一
- 应提取：
  - `SEVIS ID`
  - `school_name`
  - `program_name`
  - 费用相关字段

推荐系统行为：

- 对 `F/M`，没有 `I-20` 时不要进入“文档已就绪”

### 7.4 `ds2019`

建议解释：

- `J-1` 核心资格文件之一
- 应提取：
  - `SEVIS ID`
  - `sponsor_name`
  - `program_name`
  - funding / category / home residency 风险线索

推荐系统行为：

- `J-1` 没有 `DS-2019` 时不能视为主证据已就绪

### 7.5 `admission_letter / school_letter`

建议解释：

- 属于学校侧 supporting document
- 主要用于补强学校、项目、入学时间、录取状态

推荐系统行为：

- 可补强 `I-20` 前的学校与项目信息
- 但在学生签证里一般不应替代 `I-20`

### 7.6 `funding_proof`

建议解释：

- 不是单一文件，而是一类文件集合
- 可能包括：
  - 银行流水
  - sponsor letter
  - affidavit of support
  - scholarship letter
  - assistantship / fellowship / grant
  - tuition waiver

推荐系统行为：

- 先判断“能否支持谁出钱”
- 再判断“金额 / 连续性 / 可用性是否足够”
- 不要因为任意上传了一个 PDF 就认为资助已被证明

### 7.7 `itinerary_or_trip_purpose`

建议解释：

- `B1/B2` 的主线证据之一
- 重点不是“有一份邀请函”本身，而是能否支持旅行目的、时间、活动安排、费用承担、返回计划

推荐系统行为：

- 邀请函可作为 supporting material
- 但不应因为有邀请函就自动把 `purpose_of_trip` 视为已核实

### 7.8 `i797 / employer_letter / evidence_of_achievement`

建议解释：

- `H/L/O` 中应区分“资格批准件”和“叙述性 supporting document”

建议：

- `I-797` 归入高权重批准 / petition 证据
- `employer_letter` 归入雇主 supporting evidence
- `evidence_of_achievement` 归入 `O-1` 能力类 supporting evidence

## 8. 建议的字段级补证规则

建议后续让领域专家按字段而不是按“大类感觉”来写规则。

### 8.1 身份字段

字段：

- `/identity/full_name`
- `/identity/passport_number`
- `/identity/nationality`

建议规则：

- `passport_bio` 优先级最高
- `DS-160` 可作高权重自述对照
- 口头更正可以进入 `claimed history`，但不能直接覆盖护照值

### 8.2 教育 / 项目字段

字段：

- `/education/sevis_id`
- `/education/school_name`
- `/education/program_name`
- `/education/sponsor_name`

建议规则：

- `F/M` 以 `I-20` 为主
- `J-1` 以 `DS-2019` 为主
- 录取信 / 学校信只能补强，不能默认替代主资格文件

### 8.3 旅行目的字段

字段：

- `/visa_intent/travel_purpose`

建议规则：

- `B1/B2` 需要从 `DS-160 + itinerary / trip purpose evidence + oral explanation` 交叉判断
- `F/J/M` 应与学校 / 项目材料一致
- `H/L/O` 应与 petition / employer narrative 一致

### 8.4 资助字段

字段：

- `/funding/primary_source`

建议规则：

- 口头说“parents”只能进入 `claimed`
- `DS-160` 出现 sponsor 相关信息，最多升到“签字自述”
- 只有当 `funding_proof` 给出可解释且不冲突的证据后，才升级到 `documented`

## 9. 打分规则建议

当前打分字段建议保留，但定义必须更明确。

### 9.1 `category_fit`

建议表示：

- 申请人的陈述与目标签证类别在法律和事实层面是否匹配

不应主要由模型语感决定，而应主要看：

- 家族选择是否正确
- 主线材料是否与类别一致
- 旅行 / 学习 / 工作目的是否与该类签证匹配

### 9.2 `document_readiness`

建议表示：

- 当前是否已经具备进入下一阶段的最低材料条件

推荐粗粒度解释：

- `0-20`: 基线材料大面积缺失
- `21-40`: 已有部分材料，但关键主证据缺失
- `41-60`: 主证据已上传但未形成稳定字段
- `61-80`: 主证据已形成主要字段，仍缺少补强材料
- `81-100`: 基线材料已齐，关键字段无明显冲突

### 9.3 `narrative_consistency`

建议表示：

- 口头说法、`DS-160`、上传文件三者之间的一致程度

应重点惩罚：

- 关键字段反复变化
- 口头与文件冲突
- 正式申请表与 supporting docs 冲突

### 9.4 `confidence`

建议明确：

- 这不是模型主观把握度
- 应表示“系统当前结论的证据覆盖度和稳定度”

推荐由以下因素驱动：

- 关键字段是否有证据
- 证据来源层级是否足够高
- 是否存在冲突
- 是否依赖单一来源

## 10. Governor 建议

下一版建议把 Governor 的进入条件写死，不让模型自行发挥。

### 10.1 `need_more_evidence`

任一条件满足即进入：

- 基线包缺关键主证据
- 关键字段仅为 `claimed`
- 存在待解析材料
- 关键字段出现未解决冲突

### 10.2 `continue_interview`

至少要求：

- 基线主证据已齐
- 当前关键字段已进入 `documented` 或更高
- 没有未解决的高重要度冲突

### 10.3 `high_risk_review`

建议用于：

- 正式申请表与高权重文档冲突
- 关键陈述多次变更
- `J-1` 两年回国限制、`O-1` 成就充分性等复杂规则无法自动判断

### 10.4 `simulated_refusal`

建议保持极度保守，只用于：

- 申请人明确自认造假 / 欺诈
- 或高严重度结论有充分 `evidence_refs`

这也与当前代码方向一致：高风险确认结论必须带 `evidence_refs`。

## 11. 建议给领域专家确认的开放问题

后续找专业人士整理规则时，建议直接围绕下面问题逐条确认，而不是只让对方“看看流程对不对”。

### 11.1 关于 `DS-160`

1. 本项目是否应把 `DS-160` 视为“高权重签字自述”，而不是“已核实官方事实”？
2. `DS-160 confirmation page` 与完整 `DS-160` 字段页，是否应分成两个不同 `document_type`？
3. `DS-160` 中哪些字段可以单独进入 `documented`，哪些必须再补 supporting docs？

### 11.2 关于学生 / 交流访问类

1. `F-1` 是否必须要求 `I-20`，录取信是否只能补强不能替代？
2. `funding_proof` 对 `F-1 / J-1 / M-1` 的最低可接受形式各是什么？
3. `J-1` 是否要把 `government funded / 212(e)` 风险做成单独规则节点？

### 11.3 关于访问签证

1. `B1/B2` 的“旅行目的已足够明确”最低阈值是什么？
2. 邀请函、资助函、亲属说明分别应处于什么权重？
3. 访问类中的“家庭 / 工作约束”应该如何结构化采集？

### 11.4 关于工作签证

1. `H/L/O` 是否应把 `I-797` 设为硬门槛？
2. `employer_letter` 的字段最低要求是什么？
3. `O-1 evidence_of_achievement` 的最小支持集合如何定义？

### 11.5 关于打分

1. `document_readiness` 是否应该完全规则化，不再由模型自由评分？
2. `confidence` 是否应该改名为 `evidence_confidence` 或 `evidence_coverage`？
3. 是否需要把“材料已上传但未解析完”单独从分数中拆出去？

## 12. 下一版工程建议

### 12.1 新增证据来源类型

建议新增：

- `oral_claim`
- `signed_application_claim`
- `third_party_document`
- `official_document`
- `petition_record`
- `cross_verified`

### 12.2 把 `ds160` 拆成两个文档类型

建议拆为：

- `ds160_confirmation`
- `ds160_full_form`

原因：

- 两者证明能力完全不同
- 否则系统会混淆“已提交申请表”和“已可读到表内字段”

### 12.3 给每个字段绑定“最小可接受证据”

例如：

- `passport_number` -> `passport_bio` 或同等级官方文件
- `sevis_id` -> `I-20 / DS-2019`
- `funding.primary_source=parents` -> `funding_proof`
- `travel_purpose` -> `DS-160 + trip-specific support`

### 12.4 把分数改成规则驱动、模型辅助

建议模式：

- 模型负责抽取与摘要
- 规则负责状态迁移、补证触发、分数下限 / 上限、Governor 决策

## 13. 与当前代码的对齐关系

当前代码已经有一些正确方向，应继续保留：

1. `claimed` 与 `documented` 的初步分离
2. 文档解析后重算 profile，而不是上传即改事实
3. `conflicted` 状态
4. 高风险结论必须带 `evidence_refs`
5. `need_more_evidence` 作为主流程分支，而不是异常流程

但仍需补齐：

1. `DS-160` 的法律与证据层级定义
2. 各 `document_type` 的满足标准
3. 字段级补证矩阵
4. 使馆 / 国家差异化覆盖机制
5. 明确的打分 rubric

## 14. 本文档建议的最小落地顺序

如果只做三步，建议先做：

1. 让领域专家先确认“`DS-160` 作为何种证据层级”。
2. 给每个签证家族补一张“基线材料 + 条件补证”矩阵。
3. 把 `document_readiness` 完全改成规则分，而不是模型自由打分。

## 15. 官方链接汇总

- [DS-160 FAQs](https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application/ds-160-faqs.amp.html)
- [Student Visa](https://travel.state.gov/content/travel/en/us-visas/study/student-visa.html?lv=true)
- [Exchange Visitor Visa](https://travel.state.gov/content/travel/en/us-visas/study/exchange.html)
- [Visitor Visa](https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visitor.html?wpappninja_v=24wrihrp9&wpmobileexternal=true)
- [Temporary Worker Visas](https://travel.state.gov/content/travel/en/us-visas/employment/temporary-worker-visas.htmls.html)
- [Ineligibilities and Waivers: Laws](https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/waivers.html)
