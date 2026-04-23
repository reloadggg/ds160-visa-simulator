# Repository Guidelines

## Project Structure & Module Organization
仓库根目录主要存放设计文档，见 `docs/superpowers/`。实际可运行后端位于 `.worktrees/ds160-simulator-v1/`，开发和测试通常都在该目录执行。

在工作树内：
- `app/api/routers/`：FastAPI 路由与 OpenAI-compatible 入口
- `app/services/`：问答编排、抽取、打分、Governor、报告生成
- `app/domain/`：核心合同与状态模型
- `app/db/`、`app/repositories/`：SQLAlchemy 模型与持久化
- `app/policy_packs/`、`app/runtime_policies/`：签证规则与模型配置
- `tests/unit/`、`tests/integration/`、`tests/e2e/`：分层测试
- `tests/integration/live/`：真实 LLM 联调测试
- `fixtures/`：场景夹具与期望输出

## Build, Test, and Development Commands
以下命令从 `.worktrees/ds160-simulator-v1/` 运行：

- `uv sync --dev`：安装运行时与开发依赖
- `uv run uvicorn app.main:app --reload`：启动本地 API
- `uv run pytest -q`：运行默认测试集
- `uv run pytest -q -m "not live_llm"`：跳过真实模型测试
- `RUN_LIVE_LLM_TESTS=1 OPENAI_BASE_URL=... OPENAI_API_KEY=... uv run pytest tests/integration/live -q -m live_llm`：运行真实 LLM 测试

## Coding Style & Naming Conventions
- 使用 Python 3.12+、4 空格缩进、公开函数显式类型标注
- 文件、模块、YAML 键名使用 `snake_case`
- 类名使用 `PascalCase`
- pytest 测试函数命名为 `test_<behavior>()`
- 注释保持简短，只解释不直观的签证规则或护栏逻辑

## Testing Guidelines
使用 `pytest`。纯逻辑优先放 `tests/unit/`，涉及 API、数据库或路由拼装的改动放 `tests/integration/`，跨服务主流程放 `tests/e2e/`。新增政策包、评分规则或报告逻辑时，至少补一条对应回归测试。`live_llm` 测试必须保持可选，不应依赖仓库内明文密钥。

## Commit & Pull Request Guidelines
提交信息遵循现有前缀：`feat:`、`fix:`、`test:`、`docs:`。每个提交尽量单一目的，并附带相关测试。PR 应说明影响的流程、列出改动的 API 或配置项，并附上实际验证命令；后端改动优先提供请求/响应示例，而不是截图。

## Security & Configuration Tips
不要提交 `.env`、API Key 或供应商 URL 中的敏感参数。涉及拒签、冲突或证据缺失时，保持“未知不等于否定”，并继续通过 Governor 护栏做最终决策。
