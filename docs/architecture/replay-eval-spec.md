# Replay Eval Spec

日期：2026-05-24
状态：v1 草案，可执行合同先行

## 目标

Replay eval 必须证明对话逻辑能跑通，而不是只证明字段能序列化。

每条 replay 需要比较：

- 输入消息。
- graph state。
- retrieval plan。
- citation bundle。
- agent output。
- guard result。
- final response。
- SSE event sequence。

## Fixture 类型

首批 corpus 至少包含 10 类，每类不少于 3 条：

1. 正常 F-1 面谈补资料。
2. 用户追问“哪里不一致”。
3. 学校/项目材料冲突。
4. 资金证明不足。
5. 无官方 citation 的政策问题。
6. 政策来源过期。
7. 用户材料删除后再次检索。
8. debug synthetic bundle 泄漏风险。
9. 高风险但未闭合拒签条件。
10. provider 失败 / schema invalid。

## 机器判定指标

必须可自动判断：

- `assistant_message_author` 唯一。
- 连续 10 轮不 500。
- 不连续重复同一模板超过 2 次。
- 用户问“哪里不一致”后，回复必须包含具体 what。
- 高风险回复必须包含 what / why / next。
- policy claim 必须有 official citation。
- case conflict claim 必须有 case evidence citation。
- 无证据时必须走 `unable_to_confirm` 或等价降级。
- replay 能定位失败 graph node。

## 输出格式

每次 replay run 输出：

- `fixture_id`
- `run_id`
- `passed`
- `failed_checks`
- `node_failures`
- `used_citation_ids`
- `assistant_message_author`
- `llm_call_count`
- `token_estimate`
- `latency_ms`

## Live LLM

默认 replay eval 不依赖真实 LLM。

真实模型联调必须：

- 使用显式 marker。
- 只断言稳定合同。
- 不把合理自然语言波动写死为唯一答案。

## 验收

- focused replay eval 可本地运行。
- 失败能定位到 graph node 或 guard rule。
- 可区分 schema failure、citation failure、provider failure、logic failure。
