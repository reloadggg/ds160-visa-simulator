# DS-160 Generic Consistency Upgrade Plan

日期：2026-04-20

状态：Draft

> **For implementer:** 继续使用 TDD。所有新增能力都先补夹具或测试，再写实现。

## 1. 这份计划要解决什么

这份计划不是重做 `v1.5`。

这份计划要解决的是：

`在 v1.5 已经具备会话记忆、证据链、面签官 owner、状态机和 prompt 配置化的基础上，把系统从“局部 heuristics + 模型自由发挥”升级成“强模型 + 泛化 prompt + 抽象风险骨架 + 案例校准集”的后续增强包。`

一句话总结：

`v1.5 解决的是系统底座，这份计划解决的是判断质量与迭代效率。`

## 2. 和原 14 个任务是什么关系

结论只有一句：

`前 14 个 task 是底盘，这份计划是在那个底盘上补泛化判断能力。`

它们不是替代关系，也不是冲突关系。

如果后续升级绕开下面这些结构，才算偏离了 v1.5：

1. `session_turns`
2. `current_focus_json`
3. `interviewer_state_json`
4. `evidence_refs`
5. `Governor`
6. `continue_interview / verify_key_issue / waiting_key_proof / high_risk_review / simulated_refusal`

只要这些硬边界继续保留，强模型和泛化 prompt 就是在顺着 v1.5 演进。

## 3. 原 14 个任务的映射

### 3.1 绝对底座，不能靠 prompt 替代

这些任务必须继续由工程结构负责：

1. Task 1：建立本次会话记忆底座
2. Task 2：拆掉 gate 硬拦截，改成支持层
3. Task 3：修正连续会话语义
4. Task 4：建立 interviewer 主判断层
5. Task 5：建立面签状态模型
6. Task 7：消息入口走真实 turn history
7. Task 10：高风险与拒签分层收口
8. Task 13：打分退回辅助层

这些任务解决的是：

1. 系统是否记得上下文
2. 系统是否真的有 owner
3. 系统是否有产品状态 contract
4. 系统是否有 terminal guardrail

这些都不是 prompt 能替代的。

### 3.2 适合继续被 generic upgrade 强化

这些任务天然适合继续升级：

1. Task 6：材料理解层
2. Task 8：一次只追一个关键点
3. Task 9：材料 + 口头解释的冲突核实逻辑
4. Task 11：材料上传反馈进入主流程
5. Task 12：签证官提示词配置化
6. Task 14：最终回归与文档收口

它们分别对应后续增强包的 4 个方向：

1. 通用结构化信号
2. 泛化 prompt
3. 抽象风险骨架
4. 案例校准与评测

## 4. 这次升级的目标

### 4.1 目标

1. 不再按单个案例硬编码规则
2. 让强模型优先判断“类别、目的、时长、活动形态、核心证明、叙事”是否一致
3. 让系统把模型判断稳定沉淀成可落库、可回归、可解释的风险骨架
4. 让案例不直接变成在线规则，而先变成校准集与回归资产
5. 让后续产品更新主要改 prompt、骨架和案例，而不是频繁改业务分支代码

### 4.2 非目标

这轮不做下面这些：

1. 不重做 v1.5 主线
2. 不删除 `Governor`
3. 不把 terminal 决策完全放给模型自由发挥
4. 不引入跨会话人物记忆
5. 不把真实案例 RAG 直接接成主链路
6. 不按签证类别穷举一大堆案例规则

## 5. 设计原则

### 5.1 强模型负责理解，不负责越权

强模型主要负责：

1. 从结构化上下文中判断当前最关键冲突
2. 生成单焦点下一问或单焦点补件请求
3. 产出抽象风险 proposal

强模型不直接负责：

1. 改写 session contract
2. 绕过 owner
3. 绕过 terminal guardrail

### 5.2 prompt 要泛化，不要枚举

prompt 的目标不是写：

`如果 F1 + 7 天就怎样`

而是写：

`先审查签证类别、真实目的、停留尺度、活动形态、核心证明、既有叙事是否一致。`

### 5.3 案例先做校准资产，不先做在线判断底座

案例最先要服务的是：

1. eval
2. 回归
3. prompt 版本对比
4. 风险骨架校准

而不是先接成在线 RAG 主脑。

### 5.4 风险骨架必须统一

不能继续把风险语义散落在：

1. `consistency`
2. `scoring`
3. `interviewer_runtime`
4. `governor`

里各写一部分。

需要统一成少量抽象风险维度，再由不同层消费。

## 6. 需要保留的硬边界

下面这些边界必须继续由工程护栏负责，不能只靠 prompt：

1. 会话连续性与 turn history 落库
2. `current_focus` 与 `interviewer_state` 状态机
3. `DOCUMENTED / CLAIMED / CONFLICTED / UNKNOWN` 这类字段状态
4. `evidence_refs` 与 provenance
5. confirmed 高风险与 terminal 的证据要求
6. `GovernorDecision`
7. 单焦点输出 contract

## 7. 目标形态

升级后的主线应变成：

`本次会话记忆 -> 通用材料/口头信号 -> 抽象风险骨架 -> interviewer owner -> Governor 收口 -> 用户可理解结果`

这里的 4 个新关键词分别是：

1. 通用材料/口头信号
2. 抽象风险骨架
3. 泛化 prompt
4. 案例校准集

## 8. 实施任务

## Task 1: 建立案例校准集与 eval 底座

**目标：** 先给后续升级建立客观标尺，避免只凭感觉改 prompt。

**Files:**
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/fixtures/generic_consistency/README.md`
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/fixtures/generic_consistency/*.json`
- Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/e2e/test_generic_consistency_eval.py`
- Modify: `/home/feng/ds160_pr/docs/superpowers/plans/2026-04-19-ds160-agent-interviewer-v1.5-simulation-test-plan.md`

**要做的事：**

1. 定义统一案例模板
2. 首批补一组最小校准集，至少覆盖：
   - happy path
   - category-purpose mismatch
   - category-duration mismatch
   - category-evidence mismatch
   - record conflict
   - evasive answer
   - upload helpful / not helpful
3. 明确每个案例的期望输出：
   - 期望风险骨架
   - 期望当前状态
   - 期望下一步动作
   - 不应该发生的误判
4. 建最小 eval 用例，确保后续 prompt/骨架升级有对照基准

**测试重点：**

1. 新增案例夹具格式统一
2. eval 可以跑通
3. case 的期望输出能被程序读取

**完成标准：**

后续所有“模型更强了”或“prompt 更好了”的说法，都必须先过这套校准集。

---

## Task 2: 收拢抽象风险骨架合同

**目标：** 把分散在多个服务里的风险语义收拢成统一骨架。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/agents/schemas.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/consistency_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/scoring_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/governor_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_scoring_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_governor_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`

**要做的事：**

1. 统一少量通用风险码，建议至少包括：
   - `category_purpose_mismatch`
   - `category_duration_mismatch`
   - `category_evidence_mismatch`
   - `core_intent_unclear`
   - `record_conflict`
   - `evasive_answer`
2. 明确每类风险的：
   - severity
   - supported vs confirmed
   - evidence_refs 要求
   - 允许触发的状态收口
3. 清理分散的局部 heuristics，让它们尽量落回统一风险骨架
4. 保留 direct refusal 的红线边界，不让泛化升级把 terminal 做漂

**测试重点：**

1. 新风险码可进入 score/risk_flags
2. supported 和 confirmed 的证据要求清楚
3. 高风险与直接拒签仍然分层
4. terminal guardrail 不会被 prompt 越权

**完成标准：**

风险语义不再靠多个服务各自猜，而是有一套清楚的骨架合同。

---

## Task 3: 补通用结构化信号

**目标：** 给强模型和风险骨架提供少量稳定落点，不再纯靠自由语义理解。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/document_pipeline.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/multimodal_extraction_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/profile_recompute_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/domain/contracts.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_document_pipeline.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_profile_recompute_service.py`

**要做的事：**

1. 增加少量通用信号，优先考虑：
   - `stated_purpose_summary`
   - `duration_signal`
   - `activity_shape`
   - `core_evidence_presence`
2. 这些信号优先落在 `profile.ds160_view` 或通用摘要层，而不是先把 schema 做得很重
3. 让材料与口头解释都能贡献这些通用信号
4. 为后续 generic prompt 和风险骨架提供稳定输入

**测试重点：**

1. 通用信号可以从材料或对话中形成
2. 缺失时保持 unknown，而不是硬猜
3. profile 重算不会破坏现有 documented/claimed/conflicted 语义

**完成标准：**

系统已经拥有“少量稳定维度”，而不是完全依赖模型临场脑补。

---

## Task 4: 升级 generic prompt 体系

**目标：** 把当前 prompt 从“base + family”升级成真正可迭代的泛化判断框架。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/interviewer_prompts/base.yaml`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/interviewer_prompts/f1.yaml`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/interviewer_prompts/j1.yaml`
- Optionally Create: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/interviewer_prompts/shared_risk.yaml`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_prompt_registry.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_interviewer_prompt_registry.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_model_factory.py`

**要做的事：**

1. 把 prompt 重心从“签证家族 checklist”改成“先做一致性审查”
2. 明确要求 question/scoring/extractor 优先判断：
   - 类别是否与目的一致
   - 类别是否与时长一致
   - 类别是否与活动形态一致
   - 类别是否与核心证明一致
   - 新说法是否与既有叙事一致
3. 保留 family override，但不再靠 family 文件堆一堆碎规则
4. 给未来案例参考保留插槽，但这轮不把在线案例检索做成强依赖

**测试重点：**

1. registry 仍能稳定合并 base + override
2. 新 prompt 内容可以被 agent runtime 使用
3. future case slot 仍是可控插槽，而不是硬依赖

**完成标准：**

产品后续主要修改 prompt 结构与案例参考，而不是一遍遍改 agent 代码里的长 instructions。

---

## Task 5: 让 interviewer owner 消费新骨架

**目标：** 让真正的主判断层消费“通用结构化信号 + 风险骨架 + generic prompt”。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/message_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/agents/question_agent.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/agents/scoring_agent.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_messages_api.py`

**要做的事：**

1. 让 owner 读取新的风险骨架和通用信号
2. 优先围绕“当前最大主冲突”生成单焦点问题
3. 把 generic prompt 的泛化判断真正接到 owner 主线上
4. 保留 `current_focus`、`interviewer_state`、`allowed_next_actions` 这些工程 contract

**测试重点：**

1. owner 会优先追主冲突，而不是继续问枝节
2. 单焦点输出仍然稳定
3. 主流程不会因为模型更强而丢掉状态收口

**完成标准：**

系统的“主判断层”真正变成强模型与工程护栏的结合点。

---

## Task 6: 清理局部 heuristics，但保留硬护栏

**目标：** 把明显属于弱模型时代的局部 heuristics 尽量降级，同时保留必须存在的工程安全边界。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/interviewer_runtime_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/scoring_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/app/services/governor_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_scoring_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/unit/test_governor_service.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/test_tool_based_scoring.py`

**要做的事：**

1. 收敛关键词式小判断和局部计数器
2. 让 scoring 更像内部诊断层，而不是旧式半主脑
3. 保留：
   - schema 验证
   - single-focus contract
   - evidence_refs 要求
   - Governor terminal guardrail

**测试重点：**

1. 局部 heuristics 减少后，回归不倒退
2. terminal 仍然只由护栏和证据要求收口
3. scoring 不重新夺回控制权

**完成标准：**

系统从“很多小规则叠加”变成“强模型判断 + 少量硬护栏”。

---

## Task 7: 用案例校准集做最终回归

**目标：** 把“最终回归”从传统功能回归，升级成“功能回归 + 风险骨架回归 + prompt 版本回归”。

**Files:**
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/e2e/test_terminal_guardrails.py`
- Modify: `/home/feng/ds160_pr/.worktrees/ds160-simulator-v1/tests/integration/live/test_live_messages_api.py`
- Modify: `/home/feng/ds160_pr/docs/superpowers/plans/2026-04-19-ds160-agent-interviewer-v1.5-product-requirements.md`
- Modify: `/home/feng/ds160_pr/docs/superpowers/plans/2026-04-20-ds160-generic-consistency-upgrade-plan.md`

**要做的事：**

1. 跑完整非 live 回归
2. 跑最关键的校准 case
3. 在有凭据环境下跑最小 live 校准集
4. 记录 prompt 版本、模型版本、案例版本
5. 在文档里明确本轮升级结果和残留风险

**完成标准：**

后续每次升级都有清楚的对照依据，而不是“感觉这次更像真人了”。

## 9. 建议执行顺序

建议按下面顺序做：

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6
7. Task 7

原因：

先建立标尺，再统一骨架，再补通用信号，再升级 prompt，再接 owner，最后清理 heuristics 和收口回归。

## 10. 这次升级完成后，会和现在有什么区别

升级前，系统更像：

`有主线的面签官系统，但很多判断还依赖局部 heuristics 和模型临场发挥。`

升级后，系统应该变成：

`有主线、有证据、有状态、有骨架、有校准的面签官系统。`

具体差别体现在：

1. 更少依赖单个案例硬编码
2. 更稳定识别“类别-目的-时长-证明-叙事”不一致
3. 更容易通过 prompt 和案例集做迭代
4. 更容易解释“为什么这次进 high risk，而不是继续普通追问”
5. 更不容易因为模型换说法就让系统行为飘掉

## 11. 案例输入模板

后续新增案例时，统一至少提供：

1. `case_id`
2. `declared_family`
3. `真实目的`
4. `用户口头说法`
5. `材料关键信息`
6. `停留时长/项目尺度`
7. `是否有核心证明`
8. `期望风险骨架`
9. `期望当前状态`
10. `期望下一步动作`
11. `不应出现的误判`

## 12. 这份计划的核心判断

这次后续增强最重要的一句话是：

`不要把系统升级成“更会说话的大模型”，而要把系统升级成“更会判断、而且判断可回归的大模型面签官”。`
