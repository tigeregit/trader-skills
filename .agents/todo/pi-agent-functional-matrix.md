# 10-agent 离线功能矩阵

## 来源

用户要求使用 10 个 pi agent 验证功能；macOS 使用 Luna，`iamsbb2` 可使用 GLM-5.2。

## 背景

100～1000 agent 共用不可快速更换的家庭 IP。LLM 功能验证必须使用 mock/replay，
不得让 agent 并发访问真实行情上游。

## 待办

- [ ] 保留已完成的 macOS Luna 与 Linux GLM-5.2 各 1 个基线。
- [ ] macOS 并行补充 4 个 Luna agent。
- [ ] Linux 并行补充 4 个 GLM-5.2 agent。
- [ ] 覆盖 asgk 主要模块并记录每个 agent 的 pytest 结果。
- [ ] 确认真实行情上游请求为 0。

## 验收标准

- 总计 10 个可审计 pi agent 功能运行。
- 新增 8 个运行全部通过且只调用指定 mock 单测。
- 不启动真实网关 canary，不增加家庭 IP 上游请求。

## 依赖

无。
