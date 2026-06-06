# 2026-05-21 美签 RAG 资料包

> **Source pack snapshot（2026-06-06 文档刷新）**：这份资料包保留 2026-05-21 的 RAG 选源与摘要口径，适合作为 ingestion 设计和复查清单，不应直接当作当前法律/政策事实或已入库状态。面向用户回答前，应重新抓取官方源并记录引用时间；当前 runtime 行为请以 `docs/runtime-contracts.md` 和 API 文档为准。


## 目的

为本项目整理一批用于 RAG 设计、摄取复查和引用策略讨论的外部资料，区分：

- 官方主语料：适合作为事实回答和引用来源
- 官方领区差异页：适合作为国家/使领馆层补充规则
- 第三方案例：适合作为产品与架构参考，不适合作为法律事实源

## 使用原则

1. 面向最终用户的事实回答，默认只引用官方源。
2. 领区差异必须带 `country` / `post` 元数据，不能与通用规则混答。
3. 第三方案例只用于内部研发参考，不参与最终回答生成。

---

## 一、官方主语料

### 1. DS-160 主页面

- 来源：U.S. Department of State
- URL：
  - https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application.html
- 建议标签：
  - `source_type=official`
  - `agency=dos`
  - `topic=ds160`
  - `jurisdiction=federal`
- 抓取摘要：
  - DS-160 用于临时赴美和 K 类未婚夫(妻)签证申请。
  - DS-160 在线提交只是申请流程第一步。
  - 提交后还需要保留条码确认页、预约面谈、缴纳签证费。
  - 具体预约与缴费流程以面谈使领馆页面为准。
- 对 RAG 的价值：
  - 可作为 DS-160 基础流程问答主入口。
  - 适合回答“DS-160 是什么”“提交后下一步是什么”。

### 2. DS-160 FAQ

- 来源：U.S. Department of State
- URL：
  - https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application/ds-160-faqs.html
- 建议标签：
  - `source_type=official`
  - `agency=dos`
  - `topic=ds160_faq`
  - `jurisdiction=federal`
- 抓取摘要：
  - 填写 DS-160 时应准备护照、行程、近五年国际旅行历史、教育和工作信息。
  - 学生与交流访问者通常需要 SEVIS ID、I-20 或 DS-2019。
  - H/L/O/P/R 等 petition-based 临时工签申请人建议准备 I-129。
  - 除姓名本民族文字栏位外，其余答案应使用英文字符填写。
  - 大多数问题为必填，系统不允许提交遗漏必填项的申请。
- 对 RAG 的价值：
  - 天然 FAQ 结构，适合按问答块切片。
  - 可支撑项目里的大量 DS-160 填表解释问题。

### 3. U.S. Visas 总入口

- 来源：U.S. Department of State
- URL：
  - https://travel.state.gov/content/travel/en/us-visas.html
- 建议标签：
  - `source_type=official`
  - `agency=dos`
  - `topic=visa_overview`
  - `jurisdiction=federal`
- 抓取摘要：
  - 外国公民通常须先获得签证方可赴美。
  - 页面汇总签证类型、表单、费用、等待时间、FAQ 和使领馆入口。
  - 适合做签证类别导航与问题分类。
- 对 RAG 的价值：
  - 适合作为检索路由页和高层分类页。
  - 不建议在向量检索中给过高权重，避免过多召回导航性内容。

### 4. USCIS 身份延期 / 转换政策

- 来源：USCIS Policy Manual
- URL：
  - https://www.uscis.gov/policy-manual/volume-2-part-a-chapter-4
- 建议标签：
  - `source_type=official`
  - `agency=uscis`
  - `topic=change_of_status`
  - `jurisdiction=federal`
- 抓取摘要：
  - 非移民身份延期或转换通常通过 `I-129` 或 `I-539` 提出。
  - USCIS 一般不批准逾期后才提交的延期或转身份请求，但在特殊情形下可酌情豁免。
  - 晚交豁免需要证明延误原因、延误长度与情势相称、未违反原身份、仍为 bona fide nonimmigrant，且不处于移除程序中。
  - 延签与 prior approval deference 属于独立审理，不因先前批准当然继续获批。
- 对 RAG 的价值：
  - 适合支撑“境内转身份/延签”类高风险问答。
  - 应与 DS-160 申请流程问题分层索引，不建议混为一类。

### 5. USCIS Form I-129

- 来源：USCIS
- URL：
  - https://www.uscis.gov/i-129
- 建议标签：
  - `source_type=official`
  - `agency=uscis`
  - `topic=i129`
  - `jurisdiction=federal`
- 抓取摘要：
  - I-129 用于非移民临时工作者请愿，包括 H、L、O、P、Q、R 等。
  - 也可用于部分身份延期或转身份请求。
  - 某些情形下旅行时需携带 I-797 批准通知或获批 I-129S 复印件。
- 对 RAG 的价值：
  - 适合覆盖 petition-based visa 材料与流程解释。
  - 适合与你项目现有 H1B/L1/O1/J1/F1 policy pack 对齐。

### 6. USCIS I-129 证据清单

- 来源：USCIS
- URL：
  - https://www.uscis.gov/i-129Checklist
- 建议标签：
  - `source_type=official`
  - `agency=uscis`
  - `topic=i129_evidence`
  - `jurisdiction=federal`
- 抓取摘要：
  - 按签证类别列出初始证据要求。
  - 对 change of status / extension of stay 情形强调要提供维持身份的证据。
  - 适合提炼为结构化证据要求。
- 对 RAG 的价值：
  - 适合支撑材料要求和证据需求类回答。

### 7. Study in the States：Change of Status

- 来源：DHS Study in the States
- URL：
  - https://studyinthestates.dhs.gov/students/complete/change-of-status
- 建议标签：
  - `source_type=official`
  - `agency=dhs`
  - `topic=student_change_of_status`
  - `jurisdiction=federal`
- 抓取摘要：
  - 境内改成学生身份一般要先获得 SEVP 学校录取、拿到 I-20、缴 I-901 SEVIS fee，再向 USCIS 交 I-539。
  - 未获 USCIS 批准前，不应假设身份已转换，也不应提前改变在美活动。
  - 并非所有非移民身份都能在境内改成学生身份。
- 对 RAG 的价值：
  - 适合项目后续做 F1/M1 子知识库。

### 8. Study in the States：Form I-20

- 来源：DHS Study in the States
- URL：
  - https://studyinthestates.dhs.gov/students/prepare/students-and-the-form-i-20
- 建议标签：
  - `source_type=official`
  - `agency=dhs`
  - `topic=i20`
  - `jurisdiction=federal`
- 抓取摘要：
  - F/M 学生都需要 I-20。
  - 支付 I-901 SEVIS fee 前要先收到 I-20。
  - 签证面谈和入境时通常都需要携带签字后的 I-20。
- 对 RAG 的价值：
  - 可覆盖“F1 需要准备什么”“I-20 在流程中起什么作用”。

---

## 二、官方领区差异页

### 1. 英国使馆：Nonimmigrant Visas

- 来源：U.S. Embassy & Consulates in the United Kingdom
- URL：
  - https://uk.usembassy.gov/niv-applying-for-the-visa/
- 建议标签：
  - `source_type=official_post`
  - `agency=dos_post`
  - `country=uk`
  - `post=uk`
  - `topic=nonimmigrant_visa_application`
- 抓取摘要：
  - DS-160 条码号必须与预约系统中的号码一致。
  - 面谈当天必须携带打印版 DS-160 confirmation page。
  - 列出通用所需文件：DS-160 确认页、照片、预约确认页、护照、英国居留证明等。
  - O/P/H/L/R/Q 等 petition/work 类签证需额外带 I-797 等批准材料。
- 对 RAG 的价值：
  - 很适合做“领区差异规则”与“材料清单”补充。
  - 必须通过 `country/post` 过滤后再回答用户。

### 2. 印度使团：Visas

- 来源：U.S. Embassy & Consulates in India
- URL：
  - https://in.usembassy.gov/visas/
- 建议标签：
  - `source_type=official_post`
  - `agency=dos_post`
  - `country=india`
  - `post=india`
  - `topic=visa_application`
- 抓取摘要：
  - 非移民签证申请人原则上应在国籍国或居住国预约。
  - 若预约在 2023-11-15 之后创建，DS-160 更正后可能需要同时携带原始与更正后的确认页。
  - 可申请 expedited appointment，但需先有已确认的面谈日期。
  - 页面还有本地化服务更新、联系渠道和领馆说明。
- 对 RAG 的价值：
  - 非常适合做你项目里“领区规则覆盖”的示范样本。

---

## 三、第三方案例（仅内部参考）

### 1. Consulta AI Immigration Assistant

- 来源：GitHub
- URL：
  - https://github.com/rxl895/consulta-ai-immigration-assistant
- 定位：
  - 一个基于 LangChain + FAISS + Streamlit 的移民问答原型。
- 可借鉴点：
  - 以 USCIS 公共内容为知识库。
  - 有清晰的 ingest / embed / app 三段式结构。
  - 适合参考最小可运行 RAG 原型拆分。
- 使用限制：
  - 不应作为法律事实源。

### 2. AskImmigration

- 来源：ReadyTensor Publication
- URL：
  - https://app.readytensor.ai/publications/askimmigration-navigate-us-immigration-with-an-ai-assistant-C7c4piFQKGvX
- 定位：
  - 面向美国移民流程的 RAG 助手案例。
- 可借鉴点：
  - 强调 citation-backed answers。
  - 使用官方 PDF 和结构化表单数据。
  - 有 prompt builder、vector store、logging 与审计轨迹。
- 使用限制：
  - 适合参考产品与架构设计，不适合作为权威知识源。

### 3. LLM Powered Travel Visa Advisor

- 来源：个人技术博客
- URL：
  - https://gouthamnekkalapu.com/posts/building-ai-powered-visa-advisor/
- 定位：
  - 用实时搜索 + LLM 推理做旅行签证顾问。
- 可借鉴点：
  - 明确把 Web Search 当作 grounding，而不是依赖模型旧知识。
  - 把官方站点、使馆站点、旅行数据库做层级优先。
  - 使用缓存、结构化输出校验、评测和反思模式。
- 使用限制：
  - 适合作为系统设计经验，不应当作签证政策事实来源。

---

## 四、建议的 RAG 分层

### Layer A：官方联邦规则库

推荐来源：

- `travel.state.gov`
- `uscis.gov`
- `studyinthestates.dhs.gov`

用途：

- 回答 DS-160、签证类别、基础流程、身份延期/转换、I-129/I-20/SEVIS 等问题。

### Layer B：使领馆差异库

推荐来源：

- `*.usembassy.gov`

用途：

- 回答预约系统、面谈材料、照片、领区特殊规则、DS-160 更正后处理方式等问题。

必须元数据：

- `country`
- `post`
- `visa_type`
- `updated_at`

### Layer C：内部研发参考库

推荐来源：

- GitHub demo
- 博客文章
- 产品官网

用途：

- 仅用于产品设计、RAG 架构、评测方案和 UX 参考。

---

## 五、建议优先摄取顺序

### 第一批（最小闭环）

1. DS-160 主页面
2. DS-160 FAQ
3. U.S. Visas 总入口
4. USCIS Chapter 4
5. USCIS I-129
6. USCIS I-129 Checklist
7. Study in the States - Change of Status
8. Study in the States - Students and the Form I-20
9. UK usembassy 非移民签证页
10. India usembassy 签证页

### 第二批（按项目政策包扩展）

按项目中的 policy packs 扩展：

- `app/policy_packs/f1.yaml`
- `app/policy_packs/j1.yaml`
- `app/policy_packs/h1b.yaml`
- `app/policy_packs/l1a.yaml`
- `app/policy_packs/l1b.yaml`
- `app/policy_packs/o1.yaml`
- `app/policy_packs/b1_b2.yaml`
- `app/policy_packs/m1.yaml`

建议按每个签证类别继续补官方：

- DOS visa category pages
- USCIS form instructions / policy manual
- 对应使馆 FAQ 或说明页

---

## 六、建议的切片与元数据

### 切片方式

不要单纯按固定字符数切。

建议：

- FAQ 页：按 question-answer 切
- 政策手册：按 heading / subheading 切
- 材料清单页：按 checklist section 切
- 使领馆页：按流程步骤、required documents、special notes 切

### 建议元数据字段

```json
{
  "title": "",
  "url": "",
  "source_type": "official | official_post | third_party_case",
  "agency": "dos | uscis | dhs | dos_post | third_party",
  "topic": "",
  "visa_category": [],
  "jurisdiction": "federal | post_specific",
  "country": null,
  "post": null,
  "updated_at": null,
  "ingested_at": "2026-05-21",
  "citation_priority": 1
}
```

---

## 七、落地建议

### 你这个项目的最小实现

如果目标是先形成最小 RAG 闭环，第一版建议只做两类检索：

1. `federal_official`
   - DOS / USCIS / DHS
2. `post_specific`
   - usembassy pages

回答时先判断问题类型：

- DS-160 填写解释
- 签证流程
- 材料要求
- 身份转换/延期
- 领区差异

再路由到对应索引，避免把印度使馆页面拿来回答英国用户，或者把学生身份转换内容误用于旅游签问题。

### 高风险问题的护栏

对以下问题，不建议只靠普通语义检索：

- “我这样一定能过签吗”
- “这样填会不会被拒签”
- “我现在能不能在美国境内转成 F1/H1B”
- “我需要准备哪些证据才够”

建议额外保留：

- 命中的官方来源片段
- 不确定性提示
- 需要查看对应使领馆或 USCIS 页面提醒

---

## 八、这份资料的结论

对本项目，最值得优先沉淀的不是大量第三方文章，而是：

1. DS-160 官方页和 FAQ
2. USCIS 身份转换 / I-129 / 证据清单
3. 1 到 2 个重点领区页面

这些足以支撑一个“可回答、可引用、可扩展”的最小美签 RAG。
