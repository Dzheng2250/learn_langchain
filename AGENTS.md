# Repository Guidelines

## 项目结构与模块组织

源码位于 `src/`。Core daemon、Agent 执行、状态、Telemetry、Trace、工具和 Workspace 逻辑位于 `src/core/`；CLI 命令位于 `src/cli/`；TUI 界面位于 `src/tui/`；配置入口位于 `src/config/`。测试按职责放在 `tests/unit`、`tests/integration`、`tests/contracts` 和 `tests/optional`。文档位于 `docs/`，并按架构、API、开发、运维、质量和治理分类维护。

## 构建、测试与本地开发命令

- `python -B -m unittest discover -s tests -v`：运行完整测试套件。
- `python -B -m unittest tests.unit.test_telemetry -v`：运行单个测试模块。
- `git diff --check`：检查空白和补丁格式问题。
- `learn-agent start`：启动本地 Core daemon。
- `learn-agent chat --session default`：通过 CLI 连接 daemon 并开始会话。

项目通过 `pyproject.toml` 管理 Python 包与依赖。新增依赖前先确认标准库或现有依赖是否已经覆盖需求。

## 代码风格与命名约定

Python 使用 4 空格缩进。公共 service、repository、port 和 adapter 边界应提供类型标注。优先使用显式导入，避免 `import *`。业务层依赖 ports/contracts，不直接依赖 SQLite、具体模型服务商或 transport 实现。模块采用清晰的 snake_case 命名，例如 `session_store.py`、`execution_service.py`；测试文件使用 `test_*.py`。

## 测试规范

项目使用 `unittest`。纯逻辑放入 unit tests，Core/state/agent 协作流程放入 integration tests，架构与文档约束放入 contract tests。测试名称应描述行为，例如 `test_buffered_sink_does_not_block_producer`。修改共享边界时，先运行相关测试，再运行完整测试。

## Commit 与 Pull Request 规范

提交信息遵循 Conventional Commits，可使用中文描述，例如 `refactor(hooks): 移除旧 telemetry 兼容包`。每个 commit 聚焦一个逻辑变更。PR 应包含变更摘要、验证命令、已知风险和兼容性影响；涉及 TUI 视觉变化时附截图。架构、Agent 行为或文档契约发生变化时，可在 PR 中 `@claude` 请求审查。

## 安全与 Agent 注意事项

不要提交 `.env`、密钥、运行时数据库、Trace 或 daemon 日志。新增配置时同步更新 `.env.example` 和配置参考文档。仓库存在 `.codegraph/` 时，理解代码前优先使用 CodeGraph。除非明确要求，不要提交 `todo`；维护中文文档时必须保持 UTF-8。
