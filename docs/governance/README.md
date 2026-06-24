# 文档治理索引

> 文档状态：Current
> 权威范围：文档治理、登记、模板和决策记录模板入口
> 维护触发：文档治理规则、模板或登记方式变化

本文是 `docs/governance/` 的目录级入口。它面向维护者，说明项目文档如何分类、登记、写作和防止漂移。

## 本文负责

- 组织文档治理规范、登记表和模板。
- 说明新增或修改文档时必须同步哪些治理文件。
- 明确治理文档与业务、架构、API 文档的边界。

## 本文不负责

- 不定义项目功能状态；产品状态见 `/docs/product/`。
- 不定义当前实现；实现说明见 `/docs/architecture/`。
- 不定义外部接口；接口契约见 `/docs/api/`。

## 文档分组

| 文档 | 负责内容 |
|---|---|
| [文档治理规范](/docs/governance/documentation-management.md) | 文档分类、权威优先级、冲突处理、同步矩阵和写作原则 |
| [文档登记表](/docs/governance/document-register.md) | 核心权威来源、特殊状态、重叠关系和文档债务 |
| [文档模板](/docs/governance/document-template.md) | 新增 Current 文档的最低结构要求 |
| [设计决策记录模板](/docs/governance/decision-record-template.md) | 新增 Decision 文档的推荐结构 |

## 推荐维护流程

新增或移动文档时：

1. 判断文档所属目录和权威范围。
2. 使用对应模板补齐状态、权威范围、维护触发、本文负责和本文不负责。
3. 更新该目录的 `README.md`。
4. 更新 [文档中心](/docs/README.md)。
5. 若文档是权威入口或替代旧文档，更新 [文档登记表](/docs/governance/document-register.md)。
6. 运行文档契约测试。

## 写作约束

治理文档只描述文档系统本身。不要在治理文档里解释业务实现、部署命令或 API 字段；这些内容必须回到对应权威文档。
