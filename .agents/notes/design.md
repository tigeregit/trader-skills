# a-stock-data 转化设计

将 `ref/a-stock-data`（上游 A 股数据工具包）转化为符合本项目部署场景的 skill。

- 上游形态：单文件 `SKILL.md`（127KB / 2815 行），10 层 43 端点 15 数据源。
- 上游场景：**单 agent**，整文件入上下文，进程内 `em_get()` 限流。
- 本项目场景：**单 IP 下 100～1000 个 agent 并发**。

核心矛盾三点：token 效率、并发流量管控、代码复用。用「三轴重构」对应解决。

> 三轴均已落地（P0-P4 完成）。详细设计见 [gateway-design.md](gateway-design.md)（网关/缓存/分档）和 [asgk-contract.md](asgk-contract.md)（接口契约）。

## 一、矛盾分析

| 矛盾点 | 上游现状 | 本项目要求 |
|--------|---------|-----------|
| Token 效率 | 127KB 全量进上下文 | 按需加载，单次只用相关层 |
| 并发风控 | 进程内 `em_get()`（模块级计数） | 1000 进程各自独立限流器，东财/同花顺必封 IP |
| 代码复用 | 实现内嵌 markdown，agent 每次重拼 | 沉淀为共享库，agent 只调用 |

### 上游限流失效机理

上游 `em_get()` 用模块级变量维护「上次请求时间」：单进程内有效（1 req/s）；多进程（1000 agent 各自独立进程）各自计数互不可见 → 全局并发无上限 → 封 IP。

结论：必须引入**跨进程的共享限流点**，即网关。

## 二、轴 1：Token 效率 —— 拆分 + 按需加载

把上游 127KB 单文件拆为 SKILL.md 路由层（92 行）+ 12 个 reference 文件（按需加载）。原全量载入 → 单次仅载入路由表 + 命中的 1～2 个 reference，token 降幅约 80-90%。

## 三、轴 2：并发流量管控 —— 共享网关

本地流量网关（sgw 包），所有风控源（东财/同花顺）请求经网关，网关在单 IP 全局层面串行限流 + 缓存。无风控源（腾讯/百度/新浪/mootdx/巨潮）直连。

详细设计见 [gateway-design.md](gateway-design.md)（限流组/五档缓存/分档机制/CLI 接入）。

## 四、轴 3：代码沉淀 —— asgk 共享库

上游内嵌的实现沉淀为 asgk 包（15 模块 43 函数），agent 只 `from asgk import ...` 调用。reference 文件是该层函数的使用说明 + 调用示例。

接口规范见 [asgk-contract.md](asgk-contract.md)（两层函数模型/@source 装饰器/签名规范/端点映射）。

## 五、落地路线（已完成）

| 阶段 | 内容 | 产物 | 状态 |
|------|------|------|------|
| P0 | 流量网关 | sgw 包 + asgk/em_proxy | ✅ |
| P1 | scripts 共享库 | asgk 15模块43函数 | ✅ |
| P2 | references 拆分 | references/ 12文件 | ✅ |
| P3 | SKILL.md 路由层 | SKILL.md 92行 | ✅ |
| P4 | 集成测试 | test-method.md + testing.md | ✅ L1完成/L2待做 |

## 六、与上游的关系

- `ref/a-stock-data` 是**只读蓝本**，本项目是**改造产物**，不向上游回写。
- 上游版本演进时，`git submodule update` 拉取后 diff 对照，同步接口修复进 asgk。
- 本项目保持**中立**：不引入交易策略，只提供取数能力。
