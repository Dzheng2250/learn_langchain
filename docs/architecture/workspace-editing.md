# Workspace 编辑架构

> 文档状态：Current  
> 权威范围：结构化文件编辑工具、补丁协议、同批次冲突、写入提交和恢复语义  
> 维护触发：修改 Workspace 写工具、`ToolSpec` 可见性、资源解析、安全审批或 Tool Ledger 时

本文说明 Agent 如何安全修改已有文本文件，以及为什么多处修改必须通过一次
`apply_workspace_patch` 表达。工具总目录见[内置 Tool 使用与维护手册](/docs/development/built-in-tools-maintenance.md)，审批规则见[Tool 安全与审批](/docs/architecture/tool-security-and-approval.md)。

## 本文负责

- 定义 Workspace 结构化编辑工具的选择原则和 Patch 协议。
- 定义补丁匹配、批次冲突、提交回滚及 Tool Ledger 恢复语义。
- 说明模型、工具维护者和前端在编辑流程中的接口边界。

## 本文不负责

- 不定义通用工具审批规则、Hook 生命周期或权限规则优先级。
- 不定义 staged command 的容器实现和命令沙箱资源限制。
- 不承诺跨文件系统的绝对事务原子性，也不替代版本控制系统。

## 1. 设计动机

模型一次回复中的多个工具调用都基于同一份已读文件。若它连续产生三个
`replace_workspace_text`，第一个调用会立即改变真实文件，后两个参数却仍然描述旧版本：目标文本可能消失、重复或发生语义漂移。框架不能安全猜测这些调用是否相互依赖。

新的编辑契约是：

```text
同一文件、同一已读快照上的多处修改
  -> 一次 apply_workspace_patch

依赖前一次写入结果的修改
  -> 等待 ToolMessage
  -> 重新读取文件
  -> 再提交新 patch
```

该思想与 Codex 的 `apply_patch` 相同：补丁一次描述多个 hunk，解析器先定位原始行，再生成最终内容。项目使用 LangChain JSON Tool，因此补丁作为必填字符串参数传递，而不是 Freeform Tool。

## 2. 模型可见工具

```python
apply_workspace_patch(patch: str) -> str
```

它只更新已存在的 UTF-8 普通文件，不负责创建、移动或删除。工具选择如下：

| 需求 | Tool |
|---|---|
| 新建文件或显式整文件覆盖 | `write_workspace_file` |
| 已有文件的一处或多处修改 | `apply_workspace_patch` |
| 创建目录 | `create_workspace_directory` |
| 移动路径 | `move_workspace_path` |
| 删除路径 | `delete_workspace_path` |
| 命令产生批量变更 | `stage_command_changes`，审查后 `apply_staged_changes` |

`replace_workspace_text` 仅保留在执行工具集合中，用于恢复升级前的 checkpoint；新模型请求不会再看到它。`ToolRegistry.model_tools_for()` 生成模型 Schema，`execution_tools_for()` 生成 ToolNode 执行集合。Subagent 仍然只读。

## 3. Patch 协议

完整补丁必须使用以下信封：

```text
*** Begin Patch
*** Update File: src/calculator.py
@@ class Calculator
 class Calculator:
-    def add(self, a, b): return a + b
+    def add(self, a: float, b: float) -> float:
+        return a + b
@@ def subtract
 def subtract(a, b):
-    return a+b
+    return a - b
*** End Patch
```

语法约束：

- 每个目标使用一个 `*** Update File: <Workspace 相对路径>` 区块。
- 同一路径不能重复出现；同文件的所有修改必须放在该区块内。
- 每个 hunk 以 `@@` 开始；后面可带类名、函数名等唯一语义锚点，也可为空。
- 内容行必须以空格、`-` 或 `+` 开头，分别表示上下文、删除和添加。
- hunk 至少包含一个真实增删，并且必须包含可定位的上下文或删除行。
- 只做精确行匹配。上下文不存在、出现多次、hunk 重叠或顺序倒置都会拒绝整个 patch。
- 首版不接受 `Add File`、`Delete File`、`Move File`，这些动作必须使用专用工具。

纯插入也需要上下文，例如：

```text
@@ def run
 def run():
+    validate()
     execute()
```

若目标文本重复，应增加上下文或填写更具体的 `@@` 锚点，不能依赖“替换第一个匹配”。错误只返回路径、hunk 序号和原因，不返回文件正文。

## 4. 快照匹配与提交

执行流程：

```text
Lark 解析完整协议
  -> 解析并校验全部相对路径
  -> 按稳定顺序获取进程内路径锁
  -> 每个文件读取一次原始 bytes
  -> 所有 hunk 在该不可变行快照上定位
  -> 反向应用替换区间，生成每个文件的最终内容
  -> 校验文件数量、hunk 数量和最终字节数
  -> 提交前复核 SHA-256
  -> 为所有目标创建同目录临时文件并 fsync
  -> 按路径顺序 os.replace
```

所有文件验证成功前不会写入任何目标。后续替换失败时，已经提交的文件使用原始 bytes 反向回滚。该机制覆盖普通异常，但文件系统不存在跨多个文件的真正事务：进程在提交中途崩溃或回滚失败时，调用进入 `uncertain`，由 Tool Ledger 对账，不能自动假定成功或重试。

换行处理以原始文件为准：保留 CRLF/LF 和文件末尾换行状态。单文件最终只执行一次目标替换，因此前面 hunk 增删的行数不会改变后面 hunk 的定位基准。

## 5. 安全、审批与 Hook

`ToolSpec.resource_resolver` 在 PreToolUse Hook 完成参数替换和 schema 校验后解析全部目标路径。解析出的资源集合是后续边界的共同事实：

- `CapabilityEnforcer` 对每个路径检查 Workspace 逃逸、符号链接、junction 和敏感目录。
- Policy 使用所有目标的公共父目录生成持久规则范围。
- Approval 只展示路径、文件数、hunk 数和补丁字符数，不保存或发送补丁正文。
- Hook 修改 `patch` 后必须重新解析；Hook 不能伪造可信资源集合或绕过硬边界。
- Tool Ledger 使用所有目标的 before/after digest，而不是只读取顶层 `path` 参数。

同一 assistant 回复中，`LedgerBackedToolNode` 会在副作用发生前比较所有 Workspace mutation 的资源集合。两个调用若命中同一路径，冲突组全部返回 `resource_conflict` 工具错误；互不相交的写操作继续串行执行。这样不会先执行第一个调用再发现后续参数已过期。

## 6. 恢复与观测

Tool Ledger 使用 `execution_id + tool_call_id + args_hash` 标识一次补丁调用，并保存每个资源的状态：

- 当前全部等于 `after`：重放已保存的精确 ToolMessage，不再次修改文件。
- 当前全部等于 `before`：允许按恢复策略重新执行。
- before/after 混合或出现未知 digest：进入 `tool_recovery_required`。

成功结果只包含文件数、hunk 数、增删行数和有限路径列表。每个文件分别写入 `ResourceActivity`，包含 before/after digest、行数、字节数和该文件的 hunk 统计。Telemetry、流式事件、CLI/TUI 与审批记录都不能携带完整 patch。

## 7. 维护检查

修改编辑协议时必须同时检查：

1. Lark 语法与 parser DTO 是否同步。
2. Tool docstring、Parent Prompt 和本文示例是否仍准确。
3. `resource_resolver` 是否覆盖全部目标路径。
4. 审批、流式事件和前端是否仍只显示安全摘要。
5. Ledger 的 before/after 集合能否区分全部完成、全部未执行和部分提交。
6. 多文件失败注入、外部竞态、CRLF、歧义匹配和旧 checkpoint 兼容测试是否通过。

禁止重新向模型暴露 `replace_workspace_text`，也禁止让 patch 支持隐式模糊匹配。新增创建、移动或删除语法前，必须重新评估审批、回滚与对账语义，不能只扩展 parser。
