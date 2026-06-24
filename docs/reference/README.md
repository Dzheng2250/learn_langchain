# 参考文档索引

> 文档状态：Current
> 权威范围：稳定事实清单、配置和枚举类参考文档入口
> 维护触发：新增、移动、废弃或重命名参考文档

本文是 `docs/reference/` 的目录级入口。Reference 文档只保存稳定事实清单，例如环境变量、默认值、路径和配置项。

## 本文负责

- 组织配置和事实清单类文档。
- 说明 Reference 与 Architecture/API 的边界。
- 防止配置项说明散落到部署、架构或开发文档中。

## 本文不负责

- 不解释为什么选择某个配置方案；设计原因见 `/docs/decisions/`。
- 不说明如何部署；部署流程见 `/docs/operations/deployment.md`。
- 不解释代码如何读取配置；实现细节见 `/docs/architecture/` 或源码。

## 文档分组

| 文档 | 负责内容 |
|---|---|
| [配置参数参考](/docs/reference/configuration-reference.md) | 环境变量、默认值、作用域和配置边界 |
| [本地状态数据库 Schema 参考](/docs/reference/local-state-schema.md) | `state.db` 的表关系、字段职责、外键和索引 |

## 写作约束

Reference 文档应尽量保持事实化和可查找：

- 字段名、默认值、类型和影响范围必须准确。
- 不复制部署步骤，只链接到运维文档。
- 不复制架构解释，只链接到架构文档。
- 新增环境变量时必须同步更新配置参考和配置契约测试。
