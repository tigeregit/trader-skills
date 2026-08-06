# notes/ — 知识方法总结

本目录放已确立、长期有效的方法/规范/设计。入口：本文件。维护规则见 `.agents/README.md`。

## 当前架构（权威）

本项目经多次重构，当前形态：**能力代理服务端 + asgk CLI + skill 纯文档**。下表区分「当前权威」与「历史记录」——历史文档保留作决策背景，但实现以当前权威为准。

### 当前权威文档（反映最新架构，改对应模块时读）

| 文档 | 内容 | 何时读 |
|------|------|--------|
| [capability-proxy-design.md](capability-proxy-design.md) | 能力代理架构设计（服务端持有全部上游知识，客户端零上游知识，22 能力） | 理解当前整体架构 |
| [capability-proxy-plan.md](capability-proxy-plan.md) | 能力代理迁移计划（T1~T12 梯队） | 追溯迁移进度 |
| [cli-merge-into-server.md](cli-merge-into-server.md) | CLI 并入 server 包的决策（纯 HTTP 客户端、10 大类、共享端口配置） | 改 CLI 命令结构/映射表时 |
| [test-method.md](test-method.md) | 测试方法论（pi+locust 双层策略）⚠️ 见下方过时提示 | 规划测试方法论时 |
| [sync-with-ref.md](sync-with-ref.md) | 与上游 ref 的同步记录 | 上游更新时同步 |

### 历史记录（已被取代，保留作背景）

| 文档 | 状态 | 何时读 |
|------|------|--------|
| [design.md](design.md) | ⚠️ 三轴重构（sgw 时代）→ 已被能力代理取代 | 理解演进脉络 |
| [gateway-design.md](gateway-design.md) | ⚠️ 透明网关 sgw → 已被 asgk-server 取代（sgw DEPRECATED） | 理解限流/缓存设计渊源 |
| [asgk-contract.md](asgk-contract.md) | ⚠️ 客户端库契约（@source/em_get）→ 客户端库已删除，CLI 是纯 HTTP 客户端 | 追溯函数签名/返回结构约定 |
| [data-source-risk-control.md](data-source-risk-control.md) | ⚠️ 直连风险复核 → 结论仍有效（已并入服务端熔断） | 理解风控源为何必须经服务端 |
| [akshare-integration-analysis.md](akshare-integration-analysis.md) | 方案选型历史记录 | 理解为何选 ref 蓝本移植 |
| [akshare-merge-design.md](akshare-merge-design.md) | akshare 移植实施记录 | 追溯接口实现决策 |
| [akshare-port-feasibility.md](akshare-port-feasibility.md) | 方案 C 可行性探索 | 追溯难点接口处理 |
| [testing.md](testing.md) | ⚠️ 测试操作手册 → 描述的是已删除的客户端库测试（em_get/scripts），不再可执行 | 仅作历史参考 |

## ⚠️ 过时提示

以下文档内容基于**已删除/废弃**的旧架构，引用时注意：

- **testing.md / test-method.md 的操作部分**：基于 `skills/a-stock-data/scripts/`（已删）的客户端库 + `em_get`/`ASGK_GW`（已移除）+ sgw 网关（已废）。当前测试在 `packages/asgk-server/` 下用 `uv run pytest`（187 个测试），不再用文档里的 pi+locust+scripts 流程。
- **asgk-contract.md**：描述的 `@source`/`em_get` 客户端库已整体删除。CLI 现是纯 HTTP 客户端（`packages/asgk-server/asgk_server/cli/`），命令映射见 `cli/commands.py`。
- **gateway-design.md**：sgw 已 DEPRECATED，限流/缓存逻辑已由 asgk-server 吞噬（见 `capability-proxy-design.md`）。

## 阅读路径

- **新人理解当前架构**：capability-proxy-design.md → cli-merge-into-server.md → `packages/asgk-server/README.md`
- **要改 CLI 命令/加子命令**：cli-merge-into-server.md → `asgk_server/cli/commands.py`（映射表）
- **要加服务端能力**：capability-proxy-design.md → 仿 `asgk_server/capabilities/quote.py` 写新能力 → `capabilities/__init__.py` 注册
- **要跑测试**：`cd packages/asgk-server && uv run pytest`（187 测试）
- **理解演进脉络**：design.md（sgw 三轴）→ gateway-design.md（透明代理）→ capability-proxy-design.md（能力代理）→ cli-merge-into-server.md（CLI 并入）
