# Core Architecture

`src/core` 按业务领域组织核心能力。跨领域依赖应通过目标领域的公共模块或 `__init__.py`，避免重新形成根目录式平铺结构。

## 模块边界

```text
agent/
  graph.py       创建父 Agent LLM、节点和 LangGraph
  runtime.py     CLI 对话循环、会话编排和后台任务

memory/
  models.py      长期记忆数据模型
  errors.py      记忆领域异常
  policy.py      长期记忆提取触发策略
  extractor.py   LLM 记忆候选提取与安全过滤
  store.py       PostgreSQL 会话、消息和长期记忆存储

hooks/
  models.py      AgentEvent、事件上下文和 sink 协议
  serialization.py 事件脱敏、截断和序列化
  sinks.py       控制台、JSONL 和 PostgreSQL sink
  events.py      emit_event、事件上下文和领域 helper

tools/
  weather.py        天气工具
  workspace.py      受限工作区文件读取
  summarization.py  大文件 map-reduce 总结
  skills.py         skill 工具适配器
  commands.py       本地和容器命令执行
  registry.py       父 Agent 与子 Agent 工具集合
  observed.py       集中记录工具边界事件的 ToolNode
  implementations.py 旧导入路径的兼容导出层

database/
  queries.py     参数化 SQL 常量和 schema 加载器
  sql/schema.sql 数据库表和索引

context/
  models.py      上下文状态模型
  manager.py     有界上下文构建与压缩

skills/
  models.py      skill manifest 和文档模型
  parser.py      SKILL.md metadata 解析
  store.py       skill 文件发现和读取

streaming/
  events.py      LangGraph step/token/done 事件
  sse.py         SSE 格式适配

subagent/
  graph.py       非递归子 Agent 图和委派工具
```

## 依赖规则

1. `agent` 是组合层，可以依赖其他领域。
2. `tools/registry.py` 只负责工具集合，不实现工具业务。
3. 普通工具函数不手写通用 `tool_started/tool_finished`，由 `ObservedToolNode` 统一记录。
4. `memory/policy.py` 保持纯函数，便于独立测试。
5. SQL 结构集中在 `database/`，运行时值始终使用 psycopg 参数绑定。
6. 根目录不新增核心业务模块，只保留入口、文档、测试和项目配置。
