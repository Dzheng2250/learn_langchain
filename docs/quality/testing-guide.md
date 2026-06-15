# 测试结构与运行指南

> 文档状态：Current
> 权威范围：测试分类、目录结构、运行方式和新增测试归属
> 维护触发：测试目录、默认测试边界或运行命令变化

本文说明测试代码如何分类、各类测试应验证什么，以及开发者应如何选择运行范围。

## 1. 优化目标

测试目录按照“测试范围和外部依赖”分类，而不是简单复制 `src/` 的模块结构。这样做主要解决三个问题：

1. 开发者能够判断一次测试需要多长时间、是否会启动线程或访问外部服务。
2. 默认测试套件不依赖 PostgreSQL、网络模型 API 等可选基础设施。
3. 新增测试时有明确归属，避免所有文件继续堆放在 `tests/` 根目录。

## 2. 目录结构

```text
tests/
  unit/          单组件、纯策略、Fake/Mock 测试
  integration/   多组件、本地 TCP、SQLite、后台线程链路测试
  contracts/     文档、配置和架构边界防漂移测试
  optional/      需要显式启用的外部基础设施测试
  support/       公共测试辅助代码，不包含测试用例
  fixtures/      固定测试输入和 Workspace 样本
```

### `unit/`

单元测试验证一个较小的行为边界。允许使用 Fake、Mock 和内存数据库，但不应连接真实外部服务。

典型内容：

- 配置解析和策略判断。
- Prompt、错误分类、SQL 拆分和路径验证。
- Telemetry、Trace、Workspace 等组件的独立行为。

目标：运行快速、失败定位明确。

### `integration/`

集成测试验证多个真实组件组合后的行为。它们可以使用本地 SQLite、TCP socket、线程池和后台任务，但仍不能依赖公网或必须存在的 PostgreSQL。

典型内容：

- CLI 与 Core 的 JSON-RPC 通信。
- Agent Service、最终提交和维护调度器协作。
- CoreApp 生命周期和本地状态恢复。

目标：验证组件之间的契约和资源生命周期。

### `contracts/`

契约测试用于阻止代码、文档和架构规则悄悄漂移。它们通常读取源码或文档，不执行业务链路。

典型内容：

- 所有 RPC 方法是否写入接口文档。
- 所有配置环境变量是否写入配置参考。
- 文档链接和测试目录结构是否有效。

目标：让“修改代码时必须同步更新的内容”由自动测试执行。

### `optional/`

可选测试连接真实外部基础设施，默认发现时允许被跳过。当前主要是 PostgreSQL 投影和迁移兼容验证。

目标：保留真实集成验证能力，同时不让默认测试因为未启动 PostgreSQL 而变慢或失败。

### `support/` 与 `fixtures/`

- `support/` 保存公共路径、Fake、临时目录等辅助函数。文件名不得以 `test_` 开头。
- `fixtures/` 保存测试需要的固定文件和 Workspace 样本，不放可执行测试代码。

## 3. 运行命令

运行默认完整套件：

```shell
python -B -m unittest discover -s tests -v
```

按类别运行：

```shell
python -B -m unittest discover -s tests/unit -t . -v
python -B -m unittest discover -s tests/integration -t . -v
python -B -m unittest discover -s tests/contracts -t . -v
```

运行单个模块或测试：

```shell
python -B -m unittest tests.unit.test_tracing -v
python -B -m unittest tests.integration.test_core_bus.CoreServerIntegrationTest -v
```

显式运行 PostgreSQL 测试：

```powershell
$env:LEARN_AGENT_RUN_POSTGRES_INTEGRATION_TESTS = "1"
python -B -m unittest tests.optional.test_memory_store -v
```

```bash
LEARN_AGENT_RUN_POSTGRES_INTEGRATION_TESTS=1 \
python -B -m unittest tests.optional.test_memory_store -v
```

## 4. 新测试的归类规则

新增测试时按以下顺序判断：

1. 是否需要真实外部服务？是则放入 `optional/`。
2. 是否验证文档、接口清单或依赖边界？是则放入 `contracts/`。
3. 是否组合了多个真实组件、线程、socket 或本地持久化？是则放入 `integration/`。
4. 其余测试放入 `unit/`。

不要因为被测源码位于 `src/core/database/`，就直接把测试判定为集成测试。使用 Fake connection 验证 SQL 事务仍然可以是单元测试；连接真实 PostgreSQL 才属于可选外部集成测试。

## 5. 编写要求

- 测试必须可重复执行，不依赖执行顺序。
- 默认测试不得要求公网、模型 API 或 PostgreSQL。
- 网络、线程、进程和数据库等待必须设置明确超时。
- 临时文件统一写入 `.test_tmp/` 或系统测试目录，不得污染仓库根目录。
- 测试必须清理自己创建的资源；清理失败不得掩盖原始断言失败。
- 只有公共辅助代码放入 `support/`，不要建立隐式共享全局状态。
- 测试文件移动后，使用 `tests.support.paths.REPOSITORY_ROOT` 获取仓库根目录，不依赖文件所在深度。

## 6. 与非功能测试的关系

本指南管理测试代码的物理结构和日常运行方式。延迟、并发、故障隔离、恢复与资源占用等验收标准，继续由
[非功能性测试与验收方案](/docs/quality/non-functional-testing.md) 定义。
