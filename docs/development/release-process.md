# 发布维护流程

> 文档状态：Current
> 权威范围：维护者发布门禁、验证和发布后检查
> 维护触发：引入版本发布、CI/CD、Schema 或协议版本机制

当前项目尚未建立自动版本发布流水线。本流程面向维护者，规定发布前必须证明什么。
用户或运维人员实际执行升级与回滚时，以[升级与回滚](/docs/operations/upgrade-and-rollback.md)为准。

## 1. 发布前检查

1. 更新功能需求、路线图和相关 API/Architecture 文档。
2. 运行完整测试和外部依赖测试。
3. 检查 Schema 版本、迁移脚本和回滚方案。
4. 检查 CLI 与 Core 是否必须同版本。
5. 检查配置新增项是否已写入 `.env.example` 和配置参考。
6. 备份本地状态和必要的 PostgreSQL 数据。

## 2. 验证命令

```shell
python -B -m unittest discover -s tests -t . -v
git diff --check
```

需要 PostgreSQL 时：

```powershell
$env:LEARN_AGENT_RUN_POSTGRES_INTEGRATION_TESTS = "1"
python -B -m unittest tests.optional.test_memory_store -v
```

## 3. 发布说明必须包含

- 用户可见变化与兼容性影响。
- 新增、变更和弃用的 CLI、RPC、事件与配置。
- Schema 版本和迁移方向。
- 已知限制与残余风险。
- 升级前备份要求和可执行回滚方式。
- 自动测试与人工验收结果。

## 4. 发布后验证

1. 从干净环境执行安装与首次启动。
2. 从上一支持版本执行升级。
3. 验证旧 Session、消息、记忆和 pending Execution。
4. 验证 CLI/Core 同版本通信和主要 Agent 路径。
5. 验证按发布说明执行回滚能够恢复服务。

## 5. 当前发布缺口

- 没有语义化版本、Changelog 和自动 Release。
- 没有数据库迁移演练环境。
- 没有协议版本协商。
- 没有自动备份与回滚命令。
- GitHub Actions 尚未执行完整测试。

这些缺口统一登记在[路线图](/docs/product/roadmap-and-known-limitations.md)。
