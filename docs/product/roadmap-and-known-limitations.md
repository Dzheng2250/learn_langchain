# 路线图与已知限制

> 文档状态：Current
> 权威范围：已知缺陷、能力缺口、技术债与后续优先级
> 维护触发：发现新缺陷、完成规划项或改变优先级

本文统一登记尚未解决的问题。专项文档可以解释细节，但不应建立互相冲突的独立待办清单。

## 1. 当前稳定能力

- 本地用户级 Core daemon 与 CLI。
- 基于 Textual 的 TUI 客户端（`learn-agent tui`）—— 实时流式 token、工具步骤、暂停恢复和上下文用量展示。
- Workspace 隔离的 Session、消息、记忆、工具和 Skill。
- Agent 工具循环、子 Agent 委托和多维预算控制。
- SQLite 权威状态、后台维护与 checkpoint 恢复。
- JSON-RPC 流式事件、Provider 错误分类、Telemetry 和 System Trace。
- 默认不依赖 PostgreSQL 的测试与运行链路。

## 2. 高优先级缺口

| ID | 问题 | 影响 | 当前处置 |
|---|---|---|---|
| GAP-001 | 没有执行中任务取消协议 | 客户端断开后任务仍继续消耗资源 | 记录为已知行为；未来增加 cancel RPC 与协作式取消 |
| GAP-002 | 没有高风险工具人工审批通道 | 已认证客户端触发的 Agent 可以执行受控命令 | 依赖容器沙箱、Workspace 隔离和预算；未来引入 permission flow |
| GAP-003 | 没有 Session 列表、历史读取和记忆管理 RPC | 新前端必须绕过协议才能实现完整管理界面 | 禁止绕过；优先扩展公开 RPC |
| GAP-004 | 没有自动 SQLite 备份和恢复命令 | 本地状态损坏时依赖人工复制 | 由运维手册规定人工流程；未来增加快照命令 |
| GAP-005 | 协议没有版本协商 | CLI/TUI 与 Core 不同版本可能不兼容 | 当前要求同版本部署 |

## 3. 中优先级缺口

| ID | 问题 | 影响 | 当前处置 |
|---|---|---|---|
| GAP-006 | 记忆检索主要依赖关键词 | 语义改写后的召回能力有限 | 保留 Retriever 边界，未来评估 pgvector |
| GAP-007 | Branch Schema 已存在但无公开操作 | 无法正式编辑历史或派生对话分支 | 不对外承诺该能力 |
| GAP-008 | Tool Artifact 策略未覆盖所有工具 | 新工具可能将大结果直接放入消息 | 新工具 Review 必须检查输出上限 |
| GAP-009 | Core 未注册为操作系统服务 | 重启系统后需手动启动 | 使用显式 `learn-agent start` |
| GAP-010 | 缺少异步客户端库 | TUI 需要自行实现异步通信或包装同步 client | 已通过 `src/tui/client.py` 的 `AsyncCoreClient` 解决，详见 [TUI 架构](/docs/architecture/tui-architecture.md) |
| GAP-011 | daemon token 没有独立过期与撤销机制 | 凭据生命周期依赖 daemon 停止和重新启动 | 保持 loopback 与用户级文件权限；未来增加显式轮换策略 |

## 4. 工程治理缺口

| ID | 问题 | 当前状态 |
|---|---|---|
| ENG-001 | GitHub Actions 未运行项目自动测试 | 当前 workflow 只执行 AI Review |
| ENG-002 | 未配置统一 formatter、linter 和类型检查 | 依赖 Review 与 unittest |
| ENG-003 | 没有正式版本发布、迁移演练和回滚自动化 | 使用手工发布检查清单 |
| ENG-004 | 缺少长时间稳定性与性能基准 CI | 已定义非功能测试方案，尚未全部实现 |

## 5. 文档冲突与冗余登记

- `README.md` 只作为快速入口，不是完整功能或配置权威来源。
- `docs/architecture/*` 说明当前实现，不作为外部协议契约。
- `docs/decisions/*` 解释为什么选择方案，不应复制当前接口字段。
- `docs/history/*` 可能包含过时数量、旧路径和已完成整改，不得作为当前行为依据。
- `学习文档.md` 是学习过程记录，可能包含历史实现，权威性低于 `docs/` 当前文档。
- `面试答辩文档.md` 面向展示，不是实现、协议或运维依据。

详细权威关系见[文档登记表](/docs/governance/document-register.md)。

## 6. 路线图建议

### 下一阶段

1. Session 与记忆管理 RPC。
2. 高风险工具权限确认协议。
3. SQLite 自动快照、恢复验证和清理命令。
4. 项目测试 GitHub Actions、formatter 和静态检查。

### 后续阶段

1. 任务取消与事件续传。
2. 语义记忆检索。
3. Session 分支、编辑和版本控制。
4. OpenTelemetry 或其他外部观测导出。
