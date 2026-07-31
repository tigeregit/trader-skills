# TODO 入口

本目录存放项目的待办事项，每项一个 `.md` 文件。本文件是入口与维护规范。

## 当前清单

| 事项 | 状态 | 依赖 |
|---|---|---|
| — | — | — |

> **已归档**：
> - `gateway-mvp.md`（P0 流量网关）2026-07-22 完成，产物 `sgw/` 包
> - `scripts-library-port.md`（P1 共享库移植）2026-07-23 完成，产物 `asgk/` 15模块43函数
> - `references-split.md`（P2 references 拆分）2026-07-23 完成，产物 `references/` 12文件
> - `skill-router.md`（P3 SKILL.md 路由层）2026-07-23 完成，产物 `SKILL.md`（92行）
> - `skill-integration-test.md`（P4 集成测试）2026-07-23 完成，L1 pi端到端 + L2 locust压测通过
> - `circuit-state-persistence.md`（P1 熔断状态）2026-08-01 完成，74 项 sgw 测试通过
> - `gateway-deployment-canary.md`（P1 双平台 canary）2026-08-01 完成，真实出网 3 次
>
> 按归档规则，已完成的 TODO 从 todo 删除，产物落在 skills/。

状态图例：🔴 pending（未开始）／ 🟡 in-progress（进行中）／ 🟢 done（已完成，归档见上）

## 依赖关系

```
[P0 gateway-mvp ✓ 已交付] ─► scripts-library-port ─► references-split ─► skill-router ─► skill-integration-test
                                                                                   （按 notes/test-method.md 执行）

[P1 circuit-state-persistence ✓] ─► [P1 gateway-deployment-canary ✓]
```

`gateway-mvp` 是其余改造的基础设施，最先执行。`notes/test-method.md` 是正式测试方法文档（非待办），`skill-integration-test.md` 按它执行。

## 文件内容约定

每个 TODO 文件须包含：

- **来源**：指向 AGENTS.md 章节或 docs 文档中提出该事项的位置。
- **背景**：为什么有这个待办（问题/动机）。
- **待办**：可勾选的 checklist。
- **验收标准**：怎样算完成。
- **依赖**：前置 TODO（用文件名引用）。

## 维护方式

1. **新增 TODO**：在 `.agents/todo/` 新建 `<name>.md`（kebab-case，语义化），按上述内容约定填写，并在本文件「当前清单」表登记一行。
2. **状态流转**：开始时把本文件清单中的状态改为 🟡 in-progress；完成时改为 🟢 done。
3. **完成后归档**：
   - 若该 TODO 产出的是**正式文档/代码**（如确立了一套方法、实现了某个模块），其产物落到对应正式位置（`notes/`、`skills/` 等），**TODO 文件本身删除**并在清单移除该行——todo 目录只保留未完成项，不堆积。
   - 若该 TODO 产出的是**一次性动作**（如跑一次压测），完成后删除 TODO 文件并在清单移除。
   - 归档动作通过 commit 记录（commit message 注明完成的是哪个 TODO）。
4. **正文引用**：AGENTS.md / docs 中涉及待办的内容用 `[TODO: <name>]` 标注并指向本目录文件，不在正文展开待办细节。
5. **入口同步**：任何新增/删除/状态变更，必须同步更新本文件清单——本文件是 todo 目录的唯一权威索引。
