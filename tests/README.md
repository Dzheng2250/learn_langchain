# Tests
测试按照运行范围分类：

- `unit/`：快速、隔离的组件测试。
- `integration/`：本地跨组件、SQLite、TCP 和后台任务测试。
- `contracts/`：文档、配置与架构边界检查。
- `optional/`：需要显式启用的外部服务测试。
- `support/`：公共测试辅助代码。
- `fixtures/`：固定测试数据。

完整分类规则、运行命令和新增测试归属方式见
[测试结构与运行指南](/docs/quality/testing-guide.md)。

```shell
python -B -m unittest discover -s tests -v
```
