# Agent 生命周期 Hook 架构

> 文档状态：Current
> 权威范围：系统级 Hook 事件、执行语义、配置发现与安全边界
> 维护触发：HookPoint、HookDispatcher、外部处理器或 Agent 生命周期接入点变化时

## 本文负责

本文定义用户无需修改 Core 源码即可接入的 Agent 生命周期 Hook，以及 Hook 与权限、Telemetry、Trace 的职责边界。

## 本文不负责

- 不定义工具静态能力、沙箱和持久审批规则；见 [Tool 安全与审批](/docs/architecture/tool-security-and-approval.md)。
- 不把 Hook 输出作为 Execution State、消息历史或审计事实的权威来源。
- 不保证任意外部脚本安全；项目级 Hook 默认关闭，必须由部署者显式信任并启用。

## 十个固定相位

| HookPoint | 接入位置 | 可用动作 |
|---|---|---|
| `SessionStart` | Session 首次创建或 Execution 恢复 | continue |
| `UserPromptSubmit` | 创建 Execution 前 | continue、replace、reject |
| `PreToolUse` | 工具 schema 与权限校验前 | continue、replace、reject |
| `PermissionRequest` | 策略返回 ASK 后 | allow_once、ask_user、deny |
| `PostToolUse` | 工具成功或失败后 | continue |
| `PreCompact` | 摘要模型调用前 | continue、replace、reject |
| `PostCompact` | 摘要窗口 CAS 提交后 | continue |
| `SubagentStart` | 子 Agent 启动前 | continue、replace、reject |
| `SubagentStop` | 子 Agent 返回后 | continue、replace |
| `Stop` | Turn 最终提交前 | continue、reject |

`Stop` 拒绝会阻止最终提交并进入现有错误恢复路径；首版不自动追加反馈并重启模型循环，避免形成无界继续执行。

## 执行模型

```text
业务边界
  -> HookRuntimeRegistry.get(workspace)
  -> HookRegistry.matcher + priority
  -> HookDispatcher 顺序执行决策 Hook
  -> 重新校验业务 schema / 权限硬边界
  -> 业务操作
  -> Telemetry 记录 hook_finished / hook_failed
```

Hook 可以影响流程；Telemetry 只能观察。Hook 不能覆盖 Workspace 路径、网络、沙箱或显式拒绝规则。Trace 只负责跨层诊断时间线。

## 配置与命令协议

用户级默认读取平台配置目录下的 `hooks.json`，例如 Windows 上通常是 `C:\Users\<user>\AppData\Local\learn-agent\hooks.json`。Core 不会自动创建该文件；需要使用 `learn-agent hooks init` 生成模板，或手动创建。`learn-agent hooks path` 可查看当前会读取哪些文件，`learn-agent hooks validate` 可在不执行 Hook 命令的前提下验证配置格式。也可通过 `LEARN_AGENT_HOOK_CONFIG_FILES` 使用系统路径分隔符配置多个额外文件。

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "^run_command_in_container$",
      "hooks": [{
        "id": "command-policy",
        "type": "command",
        "command": ["python", "C:/hooks/check_command.py"],
        "timeout": 10,
        "failure_mode": "closed",
        "priority": 10
      }]
    }]
  }
}
```

命令处理器不使用 shell。Core 向 stdin 写入 UTF-8 JSON，脚本向 stdout 返回：

```json
{"action":"continue","payload":null,"reason":""}
```

修改输入使用 `replace` 和完整替换后的 `payload`。未知事件、非法动作、重复 ID、非法正则或非 argv 数组会在 Runtime 创建时失败。

## 失败与安全策略

- `failure_mode=open`：记录 `hook_failed` 后继续，适合通知和统计。
- `failure_mode=closed`：转换为拒绝，适合凭据扫描和安全策略。
- 决策 Hook 按 `priority, hook_id` 稳定排序，避免并发修改冲突。
- 外部命令有独立超时；非零退出、超时和非法 JSON 按 failure mode 处理。
- Hook Telemetry 只记录 ID、相位、动作、耗时和错误类型，不记录完整 Prompt、工具参数或结果。
- `PermissionRequest` Hook 只能自动 `allow_once`，不能创建 Session/Workspace 永久授权。

## 资源活动证据

Tool Hook 不负责生成资源访问事实。结构化 Tool 与执行器 Adapter 通过 `ResourceActivityRecorder`
记录读取、摘要、写入和 staged change；`PreToolUse` 可读取 `resource_evidence`，`PostToolUse`
可读取本次调用的 `resource_activity_ids`。Hook 可以告警或收紧策略，但不能修改或删除权威账本。
