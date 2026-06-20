# 文档治理规范

> 文档状态：Current
> 权威范围：文档分类、状态、冲突处理和维护流程
> 维护触发：文档结构或治理规则变化

## 1. 目标

文档必须帮助读者回答以下问题：

- 系统为什么存在，必须支持什么？
- 当前代码如何工作？
- 外部调用者可以依赖什么？
- 为什么选择当前方案？
- 如何开发、测试、部署、恢复和发布？
- 哪些能力尚未实现，哪些内容已经过时？

## 2. 文档类型与权威性

当多篇文档描述同一内容时，按以下优先级判断：

1. `api/`：外部协议、RPC、事件和兼容性契约。
2. `product/`：功能需求、范围、状态和路线图。
3. `architecture/`：当前内部实现和依赖关系。
4. `operations/`：部署、备份、恢复和日常运维步骤。
5. `reference/`：配置、枚举、路径等事实清单。
6. `decisions/`：设计选择、替代方案和取舍。
7. `development/`：开发流程、扩展方式和发布检查。
8. `quality/`：测试结构、非功能需求和验收方案。
9. `history/`：历史 Review 和已完成整改，仅供追溯。

代码和 Schema 是最终事实来源，但代码变化必须同步更新对应权威文档。

## 3. 文档状态

每篇新增文档应在标题后声明状态：

```text
文档状态：Current | Draft | Historical | Deprecated
权威范围：该文档负责回答什么
维护触发：什么变化发生时必须更新
```

- `Current`：当前实现或流程的权威说明。
- `Draft`：讨论中，不得作为稳定契约。
- `Historical`：只记录历史，不代表当前行为。
- `Deprecated`：仍保留链接，但已被其他文档替代。

旧文档尚未全部补齐状态头时，以[文档登记表](/docs/governance/document-register.md)为准。

## 4. 冲突处理

发现文档冲突时：

1. 确认真实代码、测试和数据库 Schema 行为。
2. 根据文档类型确定哪篇应成为权威来源。
3. 修改非权威文档为摘要和链接，不复制完整规则。
4. 无法立即修复时，在[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)登记。
5. 若行为本身不合理，先记录缺陷，再单独修改代码；不得通过改文档掩盖缺陷。

## 5. 变更同步矩阵

| 变更 | 必须同步检查 |
|---|---|
| 新增或修改 RPC | `src/ipc/models.py`、Handler、`api/rpc-reference.md`、协议测试 |
| 新增流式事件 | `api/streaming-events.md`、CLI/TUI 渲染、兼容测试 |
| 新增 CLI 命令 | `api/cli-reference.md`、README、CLI 测试 |
| 新增 Tool/Skill/Provider | `api/extension-guide.md`、安全模型、单元与集成测试 |
| 修改状态表或迁移 | 数据库架构、备份恢复、升级回滚、迁移测试 |
| 修改用户可见功能 | 功能需求、路线图、相关 API |
| 修改延迟或后台边界 | 非功能需求、非功能测试、架构文档 |
| 修改部署配置 | `.env.example`、配置参考、部署和运维文档 |

## 6. 写作规则

- 当前事实、设计原因和未来计划必须分开描述。
- 每篇 Current 文档必须有清晰职责边界：它负责回答什么、不负责回答什么。
- 不在多篇文档复制字段清单；非权威文档使用链接。
- 复杂术语首次出现时给出通俗解释。
- 命令必须标明前置条件、风险和预期结果。
- 真实数据数量、日期和一次性迁移结果只能放入 `history/` 或明确标注为历史快照。
- 文档链接使用 `/docs/...` 或仓库根路径。
- 新文档使用[文档模板](/docs/governance/document-template.md)。
- 重要跨模块取舍使用[设计决策记录模板](/docs/governance/decision-record-template.md)。
- 中文 Markdown 必须保存为 UTF-8。Windows PowerShell 读取中文文档时应显式使用
  `-Encoding UTF8`，否则可能按系统 ANSI/GBK 解码并显示为乱码；这不代表文件内容损坏。

## 7. 文档设计原则

文档结构应与代码结构一样遵守模块边界。新增或重写文档时，按以下原则判断内容是否放对位置：

### 7.1 单一职责

一篇文档只回答一类问题。

- Agent 执行文档负责解释 turn、slice、tool、模型调用和暂停恢复。
- State 文档负责解释数据库、事务、消息链、checkpoint 和后台维护。
- API 文档负责解释外部调用者能发送什么、会收到什么。
- Decision 文档负责解释为什么选择某方案，不作为当前实现的唯一事实来源。

如果一段内容需要详细解释另一个模块，应改成摘要加链接，不应复制完整规则。

### 7.2 依赖倒置

高层架构文档应依赖抽象概念，不应直接依赖底层实现细节。

例如，Agent 文档可以写“调用 `TurnFinalizer` 完成最小提交”，但不应展开
`messages.raw`、SQLite 事务或 Outbox 表结构。那些细节属于 State 文档。

### 7.3 接口优先

当代码引入 `ports/`、adapter 或 DI 容器后，文档也应优先描述接口和职责，再链接到具体实现。

例如，Core 组合根文档应说明 `CoreContainer` 装配 `ConversationHistoryStore`，而不是把
`SQLiteConversationHistoryStore` 的 SQL 行为写入 Core 文档正文。

### 7.4 当前事实与历史原因分离

`architecture/` 写当前代码如何工作；`decisions/` 写为什么这么选；`history/` 写 PR review、
旧方案和一次性整改。Current 文档不得把历史 review 过程写成当前运行机制。

## 8. 自动治理

`tests/contracts/test_documentation.py` 当前检查：

- 必需文档是否存在。
- RPC 注册与 RPC 参考是否双向一致。
- `learn-agent` 与 `learn-agent-core` 命令是否出现在 CLI 参考。
- 本地 Markdown 根路径、相对路径和锚点链接是否有效。
- 核心权威文档是否声明 `Current`，所有受治理文档是否声明状态。
- 所有 Current API 文档是否声明“本文负责 / 本文不负责”。
- 所有 Current Architecture 文档是否声明“本文负责 / 本文不负责”。
- 测试目录是否符合分类规则。

后续应继续增加：

- 配置变量与配置参考同步检查。
- 文档登记表覆盖检查。
- 其余 Current Development、Operations、Product、Quality、Reference 和 Governance 文档必须声明“本文负责 / 本文不负责”。
- 架构文档不得越界定义其他模块的权威细节，例如 Agent 文档不得定义数据库表结构。
