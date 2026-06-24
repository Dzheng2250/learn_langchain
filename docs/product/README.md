# 产品文档索引

> 文档状态：Current
> 权威范围：项目目标、功能状态、路线图和产品边界文档入口
> 维护触发：项目定位、功能范围、用户场景或路线图变化

本文是 `docs/product/` 的目录级入口。它回答“这个项目要解决什么问题、当前支持什么、还不支持什么”。

## 本文负责

- 组织项目概述、功能需求和路线图文档。
- 明确产品文档与架构、API、运维文档的边界。
- 帮助读者先判断系统能力范围，再下钻到技术文档。

## 本文不负责

- 不解释当前代码如何实现；实现见 `/docs/architecture/`。
- 不定义 RPC、CLI 或流式事件字段；外部契约见 `/docs/api/`。
- 不记录设计取舍原因；设计原因见 `/docs/decisions/`。
- 不记录部署和排障步骤；运维流程见 `/docs/operations/`。

## 文档分组

| 文档 | 负责内容 |
|---|---|
| [项目概述](/docs/product/project-overview.md) | 项目定位、目标用户、核心原则和系统边界 |
| [功能需求与实现状态](/docs/product/functional-requirements.md) | 已实现功能、部分实现功能和未实现功能 |
| [路线图与已知限制](/docs/product/roadmap-and-known-limitations.md) | 后续方向、当前限制和不承诺能力 |

## 推荐阅读顺序

| 目标 | 阅读顺序 |
|---|---|
| 判断项目是否符合使用场景 | [项目概述](/docs/product/project-overview.md) -> [功能需求与实现状态](/docs/product/functional-requirements.md) |
| 判断某能力是否已经支持 | [功能需求与实现状态](/docs/product/functional-requirements.md) -> [路线图与已知限制](/docs/product/roadmap-and-known-limitations.md) |
| 准备设计新功能 | [项目概述](/docs/product/project-overview.md) -> [路线图与已知限制](/docs/product/roadmap-and-known-limitations.md) -> 对应 Architecture/API 文档 |

## 写作约束

产品文档只描述需求、状态和边界。不要把实现细节、数据库表、函数调用链或部署命令写进产品文档。
如果功能尚未实现，必须标记为限制或计划，不能写成当前能力。
