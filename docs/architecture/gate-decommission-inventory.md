# Legacy Gate Decommission Inventory

日期：2026-05-25

本清单用于收口 AI-native Case Understanding 改造后的旧 Gate 残留。结论是：Gate 可以继续作为兼容投影、迁移桥和旧客户端字段，但不能再是用户能否继续对话、Agent 下一问、上传主反馈或前端主视图的事实源。

## Runtime Ownership

| 术语 / 字段 | 当前允许的 owner | 去留判断 | 禁止事项 |
| --- | --- | --- | --- |
| `case_understanding` | `FileService` / `ParseWorker` | 新上传任务主 kind | 不允许回退成新建 `gate_parse` |
| `gate_parse` | `ParseWorker` fallback | 只用于消费历史队列里的旧任务 | 不允许新上传继续 enqueue |
| `gate_progress` | `GateRuntimeService` compatibility projection | API / 前端 fallback / 旧测试可读 | 不允许作为能否聊天的条件 |
| `required_documents` | `GateRuntimeService` / legacy package API | 旧材料包和兼容视图 | 不允许作为 Case Board 主数据模型 |
| `remaining_required_documents` | runtime mapper / reports / OpenAI-compatible metadata | 旧消费者兼容字段；新语义应来自 proof points 和 next move | 不允许表达“材料齐了才允许聊” |
| `waiting_for_parse` | legacy status fallback | 只能提示“案例理解正在更新，可以继续对话” | 不允许提示用户等待解析完成才能继续 |
| `ready_for_interview` | legacy gate status | 仅代表旧 Gate 投影完成度 | 不允许作为 `MessageService.handle_user_turn()` 的硬前置 |
| `requested_documents` | Interview next move compatibility | 只有 Agent 确实要求某份证据时可投影 | 不允许由 Gate missing list 默认生成下一问 |

## Code Search Classification

### 新主路径

- `app/services/file_service.py`
  - 新上传只创建 `CASE_UNDERSTANDING_JOB_KIND = "case_understanding"`。
  - 返回 `understanding_status`、`case_board_delta`、`evidence_cards`。
  - `gate_progress` 只作为 legacy projection 返回。
- `app/workers/parse_worker.py`
  - 先 claim `case_understanding`。
  - 再 claim `gate_parse` 仅用于历史队列迁移。
  - 解析完成后更新 Material Understanding、Case Memory，并触发 material-change graph refresh。
- `app/services/case_memory_service.py`
  - Case Memory 是 claims / evidence / proof points / conflicts 的产品级状态中心。
- `app/services/graph_case_state_builder.py`
  - LangGraph prompt 读取 Case Memory / Case Board，而不是从 Gate missing list 决定下一问。

### 兼容投影

- `app/services/gate_runtime_service.py`
  - 保留 `gate_status_json` 和 `gate_progress` 的组装能力。
  - 必须排除 deleted / tombstoned document。
  - 不拥有用户可见主回复。
- `app/services/graph_response_mapper.py`
  - 可以投影 `remaining_required_documents` 和 `gate_progress` 给旧前端/API。
  - 不能改写 graph 的 `assistant_message`。
- `app/platform/turn_record.py`、`app/platform/runtime_ledger.py`、`app/services/runtime_view_contract_service.py`
  - 保留旧字段用于 trace、ledger、OpenAI-compatible metadata。
  - 字段含义是“本轮建议/兼容摘要”，不是 Gate readiness。
- `web/lib/api/types.ts`、`web/lib/api/mappers.ts`
  - 保留旧字段类型，优先消费 `case_board` / `case_board_delta`。

### 前端 fallback

- `web/hooks/use-session-workbench.ts`
  - `waiting_for_parse` 只能映射成“案例理解正在更新”。
  - 上传反馈优先展示理解到的事实、证据片段、冲突和未知项。
- `web/components/ds160/analysis-panel.tsx`
  - 主视图是 Case Board，不是缺材料 checklist。
- `web/components/ds160/materials-panel.tsx`
  - 材料列表展示 evidence / claims / proof points / conflicts / unknowns。

### 旧测试和历史文档

- `tests/unit/test_gate_runtime_service.py`、`tests/unit/test_gate_service.py`、`tests/unit/test_runtime_models.py`
  - 这些测试覆盖 legacy Gate projection，可保留到后续删除 Gate 服务。
- `tests/e2e/test_simulation_flow.py`、`tests/integration/test_parse_worker*.py`
  - 仍可断言 legacy response 字段，但不能断言“未 ready 就不能聊”。
- `docs/superpowers/**`
  - 属于历史计划和旧阶段记录，不代表当前产品合同。

## OCR Ownership

申请人材料主路径不再使用 OCR：

- `app/integrations/parsers.py` 对图片返回 `parser_name="multimodal_required"`。
- `pyproject.toml` / `uv.lock` 已移除 `pytesseract` 依赖。
- `tests/unit/test_parsers.py` 覆盖图片上传不会调用 OCR。

允许残留：

- 历史文档中的 `pytesseract` / OCR 记录。
- Debug synthetic material bundle 里的 `OCR Extract` 文本块，它是调试夹具正文，不是申请人材料解析路径。
- Replay eval 中的 no-OCR 防回归 marker。

## Decommission Rules

新增功能必须遵守：

1. 新上传任务只使用 `case_understanding`。
2. 新 UI 只把 Gate 字段当 fallback，不做主卡片和主引导。
3. 新 Agent prompt 以 Case Memory / Case Board / policy / recent turns 为输入。
4. 新测试不得要求 `ready_for_interview` 后才能开始聊天。
5. 拒签模拟、高风险复盘、冲突澄清由 Governor / Agent 基于事实和证据处理，不由材料包完整度决定。

## Later Delete Candidates

后续可以在单独 PR 中删除或收缩：

- `gate_parse` fallback，当线上旧队列清空后删除。
- `GateRuntimeService` 中 Gate-owned ready/missing/uploaded 状态，替换为 Case Board projection。
- `required_documents` package API，改为 proof point template / evidence needs API。
- 前端所有只服务旧缺材料 checklist 的 mapper 分支。
