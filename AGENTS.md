# AGENTS.md

本项目赋予 agent **A股信息收集**与**交易操作**的能力。本项目**不定义具体交易风格或方法**，只提供取数与下单的技能。

## 1. 目标与边界

- 提供：行情/研报/信号/资金面/财务/公告/新闻等数据的获取技能，以及交易下单技能。
- 不提供：交易策略、择时逻辑、持仓建议——这些由调用方（agent/用户）自行决定。
- 技能保持中立：只返回数据/执行指令，不对结果做投资解读。

## 2. 部署约束（关键）

本项目 skill 将在**相同 IP 下被 100～1000 个 agent 并发使用**。这是与普通单 agent skill 最大的差异，所有设计须服从这一约束：

- **流量管控优先**：带风控的数据源（东财、同花顺等）一旦被多 agent 并发打，必然封 IP。所有此类请求**必须走共享流量网关**（见 `docs/design.md` 网关方案），agent 不得各自直连。
- **不封 IP 的源可直连**：通达信(mootdx TCP)/腾讯/百度等无 IP 风控的源保持直连，避免网关成为单点瓶颈。
- **缓存优先**：同一只票的静态/低频数据（如 PE、股本、公告）多 agent 共享一个缓存层，命中即不打外网。
- **代码复用**：数据获取逻辑沉淀为共享脚本库（`skills/<name>/scripts/`），agent 调用而非每次重新拼装脚本，降低 token 消耗与重复请求。

## 3. 目录结构

```
trader-skills/
├── AGENTS.md            本文件：项目约定与 agent 行为规范
├── skills/              所有直接可用的 skill（最终产物）
│   └── <skill-name>/
│       ├── SKILL.md     路由层（精简，<300 行）
│       ├── references/  按需加载的领域细节（分层/分源）
│       ├── scripts/     可直接调用的共享代码
│       └── assets/      模板、配置等静态资源
├── ref/                 submodule，参考实现，**拒绝从零开始**
│   └── a-stock-data/    A股数据上游参考（见 §6）
└── docs/                设计文档、方案说明
```

- `skills/` 放**直接可用**的 skill，是交付物。
- `ref/` 放**参考源**（submodule），只读对照，不直接用于生产。新 skill 应基于 ref 中的相似功能改造，而非从零编写。

## 4. Skill 编写规范

遵循 ZCode 的 **progressive disclosure（渐进式披露）** 原则以最大化 token 效率：

1. **路由层精简**：`SKILL.md` 只放「要什么数据 → 读哪个文件 / 调哪个脚本」的路由表与触发描述，目标 < 300 行。
2. **分层拆分**：把领域细节按数据层/数据源拆到 `references/`，model 按需读取单层，不把全部端点一次性灌入上下文。
3. **代码进 scripts/**：可运行实现沉淀为脚本/库，SKILL 里只给调用示例，不内嵌长实现。
4. **描述要主动**：`description` 字段写清「做什么 + 何时触发」，略带推力，避免 under-trigger。
5. **新增/修改 skill 前先看 `ref/`**：是否已有相似实现可改造。

## 5. Commit Rule

### commit format

```
<type>([scope]): <subject>
```

- Types: `feat`, `fix`, `improve`, `refactor`, `test`, `docs`, `style`, `chore`, `revert`, `upgrade`, `log`, `debug`
- Scope is recommended (module name, e.g. `PointClouds`, `OnlineChief`)
- Subject: imperative mood, lowercase start, max 30 chars, no period
- Combine at most two types with `&` if needed: `refactor&feat(RenderMask): ...`

### commit scope rules

- One commit = one logical reason to change
- If a commit description requires "and", split it into multiple commits
- Commits must leave the codebase in a working state — never commit broken intermediate states

## 6. 参考源 (ref/)

- `ref/a-stock-data`：A股全栈数据工具包（10 层 / 43 端点 / 15 数据源），作为 `skills/a-stock-data` 的**改造蓝本**。
  - 上游：https://github.com/simonlin1212/a-stock-data
  - 它是**单 agent 单文件**形态（127KB SKILL.md 全量入上下文），需按 `docs/design.md` 三轴重构（拆分加载 / 流量网关 / 代码沉淀）转化为本项目形态。

## 测试方法 [TODO]

使用 pi agent 的测试，需要查看 pi agent 的官网。
