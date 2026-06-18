# 开发指南

> 文档状态：Current
> 权威范围：本地开发环境、代码边界和日常开发流程
> 维护触发：开发工具、目录结构或默认验证命令变化

## 1. 环境准备

```shell
python -m pip install -e .
python -c "from shutil import copyfile; copyfile('.env.example', '.env')"
learn-agent-core init-user-config --from-env .env
```

PostgreSQL 是可选能力。普通开发和默认测试不要求启动：

```shell
docker compose up -d postgres
```

## 2. 推荐阅读顺序

1. [项目概述](/docs/product/project-overview.md)
2. [系统架构总览](/docs/architecture/system-overview.md)
3. 与当前修改相关的 Architecture/API 文档
4. [变更管理清单](/docs/development/change-management.md)
5. [测试结构与运行指南](/docs/quality/testing-guide.md)

## 3. 代码依赖方向

```text
CLI -> IPC models
Transport -> RpcRouter
Handlers -> Application Services
Agent -> Workspace Runtime / Ports / Maintenance
Tools -> Workspace-bound capabilities
CoreApp -> 组合和生命周期
```

禁止的依赖：

- CLI 导入 Core Agent、Tool 或数据库内部实现。
- Transport 直接调用 Agent、Tool 或 State。
- Tool 读取全局 Workspace 或修改 RPC 状态。
- Agent 直接负责 daemon 启停或终端输出。
- Application Service 直接 import `sqlite3` 或 `src.core.adapters.*`。
- Telemetry/Trace 决定业务结果。

内部存储能力应先定义在 `src/core/ports/`，具体实现放在 `src/core/adapters/`，
并由 `CoreApp` 注入。完整规则见
[面向接口的 Core 设计](/docs/architecture/interface-driven-core.md)。

## 4. 日常开发流程

1. 将问题写成明确需求、缺陷或已知限制。
2. 找到负责该行为的权威文档和代码边界。
3. 先增加或调整测试，确认风险和预期行为。
4. 小范围实现，不进行无关重构。
5. 运行相关分类测试，再运行完整测试。
6. 更新文档登记、功能需求和已知限制。
7. 执行代码审查，重点检查安全、并发、持久化和兼容性。

## 5. Vibe Coding 使用规则

- AI 生成方案前必须先读取当前代码和权威文档。
- 不接受只根据类名或旧文档推断的实现。
- AI 生成的 SQL、路径操作、并发与清理代码必须人工重点审查。
- 大规模重构必须先确定边界、迁移方式和回归测试。
- 不能因为测试通过就认为设计正确；必须检查失败模式和残余风险。
- 新术语和复杂方案必须写入易懂文档。

## 6. 临时数据与本地状态

- `.agent_runtime/`：仓库兼容运行目录，已忽略。
- `.test_tmp/`：测试临时目录，已忽略。
- 用户级 `state.db`、`checkpoints.db`、Trace、Telemetry：位于平台用户数据目录。
- 不要在测试中使用真实用户级状态。

## 7. 调试路径

- CLI 错误：先检查用户提示，再检查 daemon 状态。
- Core 错误：检查 daemon 日志。
- 一次请求的跨层问题：使用 `learn-agent trace`。
- Session 无法继续：使用 `learn-agent session status`。
- 数据一致性问题：检查 state、Execution、maintenance 和 checkpoint 状态。

详细日常操作见[运维 Runbook](/docs/operations/runbook.md)。
