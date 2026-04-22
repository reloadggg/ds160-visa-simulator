# Backend Development Guidelines

> 本项目后端开发的可执行规范与跨层合同。

---

## Overview

当前仓库的 backend 规范分两层：

- 通用规范：目录、数据库、错误处理、质量要求
- 可执行合同：当运行时、API、持久化、测试消费者需要共同对齐时，优先读取合同文档，而不是只看原则说明

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | 模块组织与文件布局 | Placeholder |
| [Database Guidelines](./database-guidelines.md) | ORM、查询与迁移约束 | Placeholder |
| [Error Handling](./error-handling.md) | 错误传播、API 返回与常见陷阱 | Placeholder |
| [Quality Guidelines](./quality-guidelines.md) | 测试、回归、评审与禁止模式 | Placeholder |
| [Logging Guidelines](./logging-guidelines.md) | 结构化日志与日志级别 | Placeholder |
| [Interviewer Runtime Contracts](./interviewer-runtime-contracts.md) | LLM-first interviewer 主循环、turn decision、trace、消费者对齐合同 | Active |
| [Multimodal Upload Contracts](./multimodal-upload-contracts.md) | 上传评估、document candidates、前端纠偏与文件 API 合同 | Active |

## Pre-Development Checklist

当改动以下路径时，必须先读对应合同：

- `app/services/interview_runtime_service.py`
- `app/services/interviewer_runtime_service.py`
- `app/services/interviewer_turn_projector_service.py`
- `app/platform/turn_record.py`
- `app/services/advisory_review_service.py`
- `app/services/boundary_policy_service.py`
- `app/services/risk_watch_service.py`
- `app/services/score_state_builder.py`
- `app/evals/replay_runner.py`
- `app/cli/main.py`
- `app/agents/question_agent.py`
- `app/agents/schemas.py`
- `app/domain/runtime.py`
- `app/api/routers/openai_compat.py`
- `app/services/report_service.py`
- `app/services/message_service.py`

先读：

1. [Interviewer Runtime Contracts](./interviewer-runtime-contracts.md)
2. [Quality Guidelines](./quality-guidelines.md)
3. [Error Handling](./error-handling.md)

当改动以下路径时，必须先读上传合同：

- `app/domain/evidence.py`
- `app/services/multimodal_extraction_service.py`
- `app/services/file_service.py`
- `app/services/document_pipeline.py`
- `app/services/gate_runtime_service.py`
- `app/api/routers/files.py`
- `chainlit_app.py`

先读：

1. [Multimodal Upload Contracts](./multimodal-upload-contracts.md)
2. [Quality Guidelines](./quality-guidelines.md)

## Quality Check

提交前至少完成：

- `uv run python -m compileall app chainlit_app.py`
- `uv run pytest -q tests/unit tests/integration -m "not live_llm"`
- 如 `.env` 可用，再执行 `set -a; source .env; set +a; uv run pytest tests/integration/live -q -m live_llm`

跨层合同改动还必须检查：

- `turn_decision`、`advisory_context`、`prompt_trace` 是否被所有消费者同步消费
- 上传链路是否仍允许“模型候选输出 + 用户纠偏”，而不是退回强制人工前置类型
- live 测试是否只断言稳定信号，不把模型合理波动写死为唯一行为

---

## Notes

这个目录优先记录“真实合同”和“真实测试点”，不是抽象最佳实践。
