# 网关双平台部署 canary

## 来源

用户确认在 macOS ARM64 本机与 Ubuntu Linux x64 `iamsbb2` 做跨平台有限验证。

## 背景

真实风控来源不能并发压测。完整端点覆盖由 mock/replay 保证，真实
canary 总预算为 3 次，且任一风控信号都立即终止。

## 待办

- [ ] 两平台执行 frozen sync 和全部离线测试。
- [ ] 两平台验证状态库重启、缓存和禁止直连。
- [ ] macOS 串行一次 Eastmoney canary。
- [ ] Linux 串行一次 10jqka 和一次 SZSE canary。
- [ ] 每平台最多一个 gpt-5.6-luna pi agent 验证缓存命中。
- [ ] 更新测试文档并归档 TODO。

## 验收标准

- 真实上游请求不超过 3 次，严格串行且无自动重试。
- 任一 403/429/验证码/异常响应终止剩余 canary。
- pi agent 查询不增加网关外网计数。
- 文档明确本结论只覆盖 macOS ARM64 和 Ubuntu Linux x64。

## 依赖

`circuit-state-persistence.md`。
