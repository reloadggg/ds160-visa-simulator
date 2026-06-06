# 2026-05-21 美签 RAG Source Manifest（100+ 候选源）

> **Source inventory snapshot（2026-06-06 文档刷新）**：这是一份 2026-05-21 的候选源清单，用于说明当时的 RAG source discovery 范围；它不是“所有 URL 当前可抓取/已入库”的证明，也不是面向用户的操作说明。使用前应重新校验 URL、抓取状态和官方页面更新时间。当前 runtime 行为请以 `docs/runtime-contracts.md` 和 API 文档为准。


## 说明

这份 manifest 的目标不是“每一页都已全文抓取”，而是先把 **可用于 RAG 的 100+ 候选源池** 建起来，供后续批量 ingestion。

状态定义：

- `fetched`：2026-05-21 已通过 Exa 拉过正文或做过较完整读取
- `discovered`：2026-05-21 已通过 Exa 检索发现，适合复核后加入后续抓取队列
- `blocked`：Exa 检索到了 URL，但页面在当时返回 technical difficulties / forbidden，后续需复核后重试

---

## 总量

- 联邦官方页：18
- USCIS / 政策手册 / 表单页：13
- DHS Study in the States：2
- 使领馆 / 本地签证页：37
- Reciprocity 国家页：30
- 第三方案例：3

**合计：103 个候选源**

---

## A. 联邦官方页（18）

1. `fetched` https://travel.state.gov/content/travel/en/us-visas.html
2. `fetched` https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application.html
3. `fetched` https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application/ds-160-faqs.html
4. `discovered` https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/global-visa-wait-times.html
5. `discovered` https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/fees/fees-visa-services.html
6. `discovered` https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/frequently-asked-questions.html
7. `discovered` https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/all-visa-categories.html
8. `discovered` https://travel.state.gov/content/travel/en/us-visas/tourism-visit.html
9. `discovered` https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visitor.html
10. `discovered` https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visa-waiver-program.html
11. `discovered` https://travel.state.gov/content/travel/en/us-visas/study.exchange.html
12. `discovered` https://travel.state.gov/content/travel/en/us-visas/study/student-visa.html
13. `discovered` https://travel.state.gov/content/travel/en/us-visas/study/exchange.html
14. `discovered` https://travel.state.gov/content/travel/en/us-visas/employment/temporary-worker-visas.htmls.html
15. `discovered` https://travel.state.gov/content/travel/en/us-visas/employment/treaty-trader-investor-visa-e.html
16. `discovered` https://travel.state.gov/content/travel/en/us-visas/business/b-1-fact-sheet.html
17. `discovered` https://travel.state.gov/content/travel/en/News/visas-news/important-update-on-waivers-of-the-interview-requirement-for-certaing-nonimmigrant-visa-applicants.html
18. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country.html

---

## B. USCIS / 政策手册 / 表单页（13）

1. `fetched` https://www.uscis.gov/policy-manual/volume-2-part-a
2. `fetched` https://www.uscis.gov/policy-manual/volume-2-part-a-chapter-4
3. `fetched` https://www.uscis.gov/policy-manual/volume-2-part-f-chapter-8
4. `discovered` https://www.uscis.gov/policy-manual/volume-2-part-p
5. `discovered` https://www.uscis.gov/policy-manual/volume-2-part-p-chapter-4
6. `discovered` https://www.uscis.gov/policy-manual/volume-2-part-m-chapter-8
7. `fetched` https://www.uscis.gov/i-129
8. `fetched` https://www.uscis.gov/i-129Checklist
9. `discovered` https://www.uscis.gov/i-539
10. `discovered` https://www.uscis.gov/i-539Checklist
11. `discovered` https://www.uscis.gov/forms/all-forms/how-do-i-request-premium-processing
12. `discovered` https://www.uscis.gov/sites/default/files/document/forms/i-539.pdf
13. `discovered` https://www.uscis.gov/sites/default/files/document/forms/i-129sinstr.pdf

---

## C. DHS Study in the States（2）

1. `fetched` https://studyinthestates.dhs.gov/students/complete/change-of-status
2. `fetched` https://studyinthestates.dhs.gov/students/prepare/students-and-the-form-i-20

---

## D. 使领馆 / 本地签证页（37）

1. `fetched` https://uk.usembassy.gov/niv-applying-for-the-visa/
2. `discovered` https://uk.usembassy.gov/nonimmigrant-visas-faqs/
3. `discovered` https://uk.usembassy.gov/visas-2/
4. `discovered` https://uk.usembassy.gov/visa-faqs-information-for-non-immigrant-visa-or-esta-applicants/
5. `discovered` https://uk.usembassy.gov/visas/nonimmigrant-visas/
6. `fetched` https://in.usembassy.gov/visas/
7. `discovered` https://it.usembassy.gov/nonimmigrant-visas/
8. `discovered` https://id.usembassy.gov/visas/nonimmigrant-visas/
9. `discovered` https://kr.usembassy.gov/visas/
10. `discovered` https://kr.usembassy.gov/visas/important-visa-information/
11. `discovered` https://sg.usembassy.gov/visas/
12. `discovered` https://np.usembassy.gov/visas/
13. `discovered` https://pl.usembassy.gov/nonimmigrant-visas/
14. `discovered` https://pl.usembassy.gov/visas/
15. `discovered` https://fr.usembassy.gov/visas/
16. `discovered` https://fr.usembassy.gov/visas/e-visa-process/
17. `discovered` https://lu.usembassy.gov/how-to-apply-for-a-nonimmigrant-visa/
18. `discovered` https://lu.usembassy.gov/visa-faqs/
19. `discovered` https://cz.usembassy.gov/nonimmigrant-visas/
20. `discovered` https://sk.usembassy.gov/nonimmigrant-visas/
21. `discovered` https://ee.usembassy.gov/attention-correct-ds-160-barcodes-are-required-for-all-nonimmigrant-visa-interviews/
22. `discovered` https://ge.usembassy.gov/visas/important-visa-information/
23. `discovered` https://om.usembassy.gov/visas/nonimmigrant-visas/
24. `discovered` https://mt.usembassy.gov/visas/
25. `discovered` https://zm.usembassy.gov/visas/important-visa-information/
26. `discovered` https://na.usembassy.gov/visas/important-visa-information/
27. `discovered` https://br.usembassy.gov/frequently-asked-questions-on-non-immigrant-visas/
28. `discovered` https://xk.usembassy.gov/frequently-asked-questions-nonimmigrant-visa/
29. `discovered` https://cd.usembassy.gov/visas/
30. `discovered` https://cg.usembassy.gov/visas/
31. `discovered` https://cm.usembassy.gov/visas/
32. `discovered` https://tl.usembassy.gov/visas/
33. `discovered` https://pg.usembassy.gov/visas/
34. `discovered` https://sz.usembassy.gov/visas/
35. `discovered` https://to.usembassy.gov/visas/
36. `blocked` https://ph.usembassy.gov/visas/
37. `blocked` https://nl.usembassy.gov/visas/

---

## E. Reciprocity 国家页（30）

1. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/China.html
2. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/India.html
3. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Japan.html
4. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Canada.html
5. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/France.html
6. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Germany.html
7. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Brazil.html
8. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Bangladesh.html
9. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Singapore.html
10. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/SriLanka.html
11. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Australia.html
12. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/RussianFederation.html
13. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/SaudiArabia.html
14. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Bahrain.html
15. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Israel.html
16. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Turkey.html
17. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Greece.html
18. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Monaco.html
19. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Macedonia.html
20. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/MoldovaRepublicof.html
21. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Albania.html
22. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Kyrgyzstan.html
23. `discovered` http://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/LaoPeoplesDemocraticRepublic.html
24. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/SouthAfrica.html
25. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Egypt.html
26. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Senegal.html
27. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/SierraLeone.html
28. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Guyana.html
29. `discovered` http://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/NewCaledonia.html
30. `discovered` https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Pakistan.html

---

## F. 第三方案例（3）

1. `fetched` https://github.com/rxl895/consulta-ai-immigration-assistant
2. `fetched` https://app.readytensor.ai/publications/askimmigration-navigate-us-immigration-with-an-ai-assistant-C7c4piFQKGvX
3. `fetched` https://gouthamnekkalapu.com/posts/building-ai-powered-visa-advisor/

---

## 后续摄取建议（需重新校验后执行）

### 第一阶段：复核并抓取 30 页正文

优先顺序：

1. 已有 `fetched` 的 13 页
2. 联邦官方页中最核心的 10 页
3. 使领馆差异页里访问量高的 7 页

### 第二阶段：复核后扩到 60-80 页正文

- 批量抓使领馆页
- 抓更多 USCIS 表单 / checklist / policy manual 子页

### 第三阶段：复核后扩到 100+ 实际文档块

- 大规模纳入 `Reciprocity and Civil Documents by Country`
- 重点国家优先：中印日韩、新加坡、法国、德国、英国、巴西、沙特、南非、土耳其

### RAG 层次建议

1. `federal_official`
2. `post_specific`
3. `country_reciprocity`
4. `third_party_reference`

不要把四层混成一个索引。
