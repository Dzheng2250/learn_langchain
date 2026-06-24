# 质量与测试文档索引

> 文档状态：Current
> 权威范围：测试分类、非功能需求和质量验收文档入口
> 维护触发：测试结构、质量门禁、非功能指标或验收方式变化

本文是 `docs/quality/` 的目录级入口。它面向开发者和维护者，说明如何验证系统是否仍然可靠、可维护、低延迟和可恢复。

## 本文负责

- 组织测试指南、非功能需求和专项测试文档。
- 说明默认测试与可选外部依赖测试的边界。
- 明确质量文档与架构、API、运维文档之间的关系。

## 本文不负责

- 不定义产品功能范围；功能范围见 `/docs/product/`。
- 不定义内部实现细节；实现细节见 `/docs/architecture/`。
- 不定义部署步骤；部署和排障见 `/docs/operations/`。

## 文档分组

| 文档 | 负责内容 |
|---|---|
| [测试结构与运行指南](/docs/quality/testing-guide.md) | 测试目录分类、运行命令和新增测试归属 |
| [非功能需求](/docs/quality/non-functional-requirements.md) | 延迟、可靠性、可观测性、恢复、安全等质量目标 |
| [非功能测试](/docs/quality/non-functional-testing.md) | 如何验证非功能需求是否达标 |
| [本地优先状态测试](/docs/quality/local-first-testing.md) | 本地状态、最终提交、维护任务和恢复协调的专项测试 |

## 推荐阅读顺序

| 目标 | 阅读顺序 |
|---|---|
| 想知道该写哪类测试 | [测试结构与运行指南](/docs/quality/testing-guide.md) |
| 修改响应收尾、后台任务或 Trace | [非功能需求](/docs/quality/non-functional-requirements.md) -> [非功能测试](/docs/quality/non-functional-testing.md) |
| 修改 state.db、checkpoint 或维护任务 | [本地优先状态测试](/docs/quality/local-first-testing.md) -> [本地数据库与一致性](/docs/architecture/database-state-and-consistency.md) |

## 写作约束

质量文档应描述可验证的目标、测试范围和验收方法。不要在质量文档中复制实现细节；应链接到对应架构文档。
默认测试不得依赖 PostgreSQL、公网模型 API 或人工环境，外部依赖测试必须放入 `tests/optional/` 或显式跳过。
