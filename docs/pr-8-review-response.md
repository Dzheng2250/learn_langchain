# PR #8 Review 回复

本文记录部署、通用模型配置与诊断会话 PR 的 review 处理结论。

## 总体处理原则

- 对确认存在的 Bug 直接修复并增加回归测试。
- 对不改变行为的可维护性建议直接采纳。
- 对会破坏明确配置语义或只提供表面安全感的建议，不机械采纳，并说明取舍。

## 逐条回复

### 1. `migration.py` 中 `str(None)` 产生 `"None"`

**接受，已修复。**

密码读取使用：

```python
password = str(config.get("password") or "")
```

连接 URL 未提供密码或密码值为 `None` 时，不再设置 `PGPASSWORD`。现有
`test_backup_supports_connection_url_without_password` 覆盖该行为。

### 2. `PostgresEventSink(password="")` 是否应回退默认密码

**不采纳建议中的修改，补充注释与测试。**

这里需要区分：

- `password=None`：调用方未提供密码，使用共享默认数据库配置。
- `password=""`：调用方明确要求无密码连接。

将实现改为 `password if password else MEMORY_DB_PASSWORD` 会把显式空密码偷偷替换为默认密码，
破坏调用方意图，并与无密码连接 URL 的支持冲突。

新增测试锁定显式空密码语义，并将内部连接选择变量重命名为
`_use_default_connection`。

### 3. 使用 `__import__("os")`

**接受，已修复。**

改为普通 `import os` 和 `os.environ.copy()`，降低阅读成本。

### 4. `env_bool` 缺少 `y/n`

**接受，已实现。**

现在支持：

```text
true / false
yes / no
y / n
on / off
1 / 0
```

并新增短布尔值解析测试。

### 5. `_use_shared_connection` 命名不直观

**接受，已修复。**

重命名为 `_use_default_connection`。该名称更准确表达“所有构造参数均未显式提供时，使用项目
共享数据库配置”的判断。

### 6. 本地开发默认密码为 `postgres`

**当前不改动，接受风险并保持明确边界。**

当前 Compose 是面向本地开发的快速部署方案：

- PostgreSQL 端口只绑定 `127.0.0.1`。
- README 和部署文档明确要求非本地环境修改密码。
- `.env.example` 会被复制为不提交的用户级配置。

把公开默认值改为公开的 `CHANGE_ME` 并不会形成真实安全边界；强制随机密码则会破坏当前
“复制配置后即可启动”的本地学习体验，并需要额外的密钥生成与同步机制。

未来若支持远程或生产部署，应单独提供 production Compose/profile，并要求 Docker Secret、
外部 Secret Manager 或启动时生成的高强度凭据。当前 `compose.yaml` 不应被解释为生产部署方案。

## 验证

```shell
python -B -m unittest discover -s tests -v
docker compose config --quiet
git diff --check
```
