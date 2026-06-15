# Contributing

本项目使用 Vibe Coding 与人工 Review 协作开发。AI 可以生成实现、测试和文档，但最终变更必须由开发者
理解、验证并承担维护责任。

## 开始之前

1. 阅读[项目概述](/docs/product/project-overview.md)和[系统架构总览](/docs/architecture/system-overview.md)。
2. 确认需求已写入[功能需求](/docs/product/functional-requirements.md)或
   [路线图](/docs/product/roadmap-and-known-limitations.md)。
3. 阅读[开发指南](/docs/development/development-guide.md)和
   [变更管理清单](/docs/development/change-management.md)。

## 基本规则

- 不提交 `.env`、daemon token、运行数据库、Trace 或其他本地运行数据。
- 不修改或删除与当前任务无关的用户改动。
- 优先复用现有边界，不为一次修改创建无职责抽象。
- 所有外部输入必须验证，所有 IO 必须有超时或大小边界。
- 新增功能必须包含测试和文档更新。
- 默认测试不得依赖公网模型 API 或必须运行的 PostgreSQL。

## 验证

```shell
python -B -m unittest discover -s tests -t . -v
git diff --check
```

需要真实 PostgreSQL 时：

```powershell
$env:LEARN_AGENT_RUN_POSTGRES_INTEGRATION_TESTS = "1"
python -B -m unittest tests.optional.test_memory_store -v
```

## Commit 与 PR

- Commit 应描述一个清晰目的。
- PR 描述必须说明问题、方案、取舍、影响和验证结果。
- 行为变化与纯文档/重构尽量拆分。
- Review 发现的问题应写明是否接受、如何处理和残余风险。
- 不兼容协议或 Schema 变更必须提供升级与回滚说明。

完整流程见[发布流程](/docs/development/release-process.md)。
