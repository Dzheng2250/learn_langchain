# 配置、领域常量与 Prompt 的管理边界

> 文档状态：Current Decision
> 权威范围：运行配置、领域常量和 Prompt 的归属边界与方案取舍
> 维护触发：配置组织、领域枚举或 Prompt 管理边界变化

## 本文负责

- 配置、领域常量和 Prompt 分离管理的原因与边界。

## 本文不负责

- 不维护环境变量和默认值清单；见配置参考。
- 不记录具体模块的当前实现细节。


本文解释“配置代码如何组织”，不作为完整参数清单。全部环境变量、默认值、单位和调整风险见
> [`/docs/reference/configuration-reference.md`](/docs/reference/configuration-reference.md)。

## 为什么要优化

程序中出现固定字符串和固定数字并不一定是问题。真正的问题是：一个值的
**归属不明确**，导致多个模块各自复制、修改后彼此不一致。

本项目将固定内容分为三类：

1. **运行配置**：部署者或运维人员可能需要调整，例如维护任务轮询间隔。
2. **领域常量**：描述系统允许存在的业务状态，例如 Execution 的
   `running`、`completed`。
3. **实现细节**：仅服务于一个模块的内部格式，例如一段日志预览长度。

这三类内容不应全部塞入一个全局 `constants.py`，否则只是把混乱移动了位置。

## 采用的管理边界

### 类型化运行配置

部署者可调整的运行策略应进入功能域配置对象，在 Core 启动时完成解析和校验，再通过依赖注入交给
业务组件。组件不得在执行过程中反复读取环境变量，也不得自行决定生产默认值。

这使测试可以注入短超时或小容量，生产配置也可以独立调整。当前变量、默认值和作用域由
[配置参考](/docs/reference/configuration-reference.md)维护。

### 领域枚举

Execution、Checkpoint、Maintenance 等业务状态使用类型化枚举表达，并在持久化边界拒绝未知值。
枚举保持字符串兼容性，但调用代码不再散布容易拼错的魔法字符串。

数据库约束和迁移形式属于状态实现事实，由[本地状态 Schema](/docs/reference/local-state-schema.md)维护。

### Prompt 模块

Prompt 是模型行为策略，不是部署配置，也不应混入图循环或工具执行代码。父 Agent、子 Agent、
记忆提取和上下文摘要的 Prompt 按用途分离，并具有可测试的构建入口。

当前 Prompt 位置与版本由[Agent 执行架构](/docs/architecture/agent-execution-architecture.md)及源码维护，
Decision 不复制文件清单。

## 兼容入口为何允许暂时存在

一次性移除旧配置导出会把结构重构与行为变化混在同一提交中。兼容入口可以在有限周期内保留，但
新业务代码必须依赖类型化配置和构造函数注入；迁移顺序与剩余债务由
[接口化重构技术债务](/docs/development/interface-refactor-backlog.md)维护。

## 设计原则

- **单一职责**：配置对象只描述一个功能域的运行策略。
- **依赖倒置**：业务组件接收配置与接口，不自行读取环境或全局状态。
- **开闭原则**：新增策略通过注册或注入扩展，不修改无关调度流程。
- **组合优于继承**：Composition Root 组合配置、Repository、Scheduler 和 Handler。
- **防御性持久化**：类型约束调用边界，存储约束最终数据。

## 当前实现与后续工作的权威来源

本文不维护已拆出的配置类、兼容导出或下一批迁移清单：

- 当前配置事实见[配置参考](/docs/reference/configuration-reference.md)；
- DI 与接口边界见[接口驱动的 Core](/docs/architecture/interface-driven-core.md)；
- 未完成重构见[接口化重构技术债务](/docs/development/interface-refactor-backlog.md)。