# Learn LangChain Agent

本项目是一个基于 LangChain、LangGraph 和 PostgreSQL 的本地学习型 Agent。

## 运行

```powershell
D:\app\anaconda\envs\agent_learn\python.exe agent_loop.py
```

根目录的 `agent_loop.py` 只是 CLI 启动入口，核心功能位于 `src/core/`。

## 核心结构

```text
src/core/
  agent/       父 Agent 图定义与运行循环
  common/      通用调试工具
  config/      项目配置
  context/     短期上下文压缩
  database/    SQL 查询与 schema
  hooks/       事件模型、sink 与 helper
  memory/      长期记忆模型、策略和存储
  skills/      本地 skill 加载
  streaming/   LangGraph 流式事件和 SSE
  subagent/    非递归子 Agent
  tools/       工具实现、注册表和 ToolNode wrapper
```

详细模块边界见 [`src/core/README.md`](src/core/README.md)。

## 测试

不依赖数据库的测试：

```powershell
D:\app\anaconda\envs\agent_learn\python.exe -B -m unittest tests.test_agent_sql tests.test_agent_hooks tests.test_memory_extraction_policy
```

包含 PostgreSQL 的完整测试：

```powershell
D:\app\anaconda\envs\agent_learn\python.exe -B -m unittest tests.test_agent_sql tests.test_agent_hooks tests.test_memory_store tests.test_memory_extraction_policy
```
