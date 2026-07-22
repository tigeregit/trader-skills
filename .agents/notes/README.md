# notes/ — 知识方法总结

本目录放已确立、长期有效的方法/规范/设计。入口：本文件。维护规则见 `.agents/README.md`。

## 文档索引

| 文档 | 内容 | 何时读 |
|------|------|--------|
| [design.md](design.md) | a-stock-data 转化总设计（三轴重构：token效率/网关/代码沉淀） | 理解项目整体思路 |
| [gateway-design.md](gateway-design.md) | 网关详细设计（限流组/五档缓存/分档机制/CLI接入/离线修正） | 改网关或缓存策略时 |
| [asgk-contract.md](asgk-contract.md) | asgk 接口契约（两层函数/@source/签名规范/43端点映射） | 新增/修改取数函数时 |
| [sync-with-ref.md](sync-with-ref.md) | 与上游 ref 的同步记录（git hash + 转换方法 + 更新流程） | 上游更新时同步 |
| [test-method.md](test-method.md) | 测试方法论（pi+locust 双层策略，L1端到端/L2压测） | 规划测试时 |
| [testing.md](testing.md) | 测试操作手册（网关验收 + pi端到端 + locust压测，可直接执行） | 实际跑测试时 |

## 阅读路径

- **新人理解项目**：design.md → gateway-design.md → asgk-contract.md
- **要跑测试**：test-method.md（方法论）→ testing.md（操作手册）
- **要改网关/缓存**：gateway-design.md
- **要加取数函数**：asgk-contract.md → 对应 reference 文件
- **上游更新了**：sync-with-ref.md → 按同步流程逐项检查
