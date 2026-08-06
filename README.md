# trader-skills

赋予 AI agent **A股信息收集**与**交易操作**能力的 skill 集合。面向**单 IP 下 100~1000 个 agent 并发**场景设计，经能力代理服务端统一出网，解决风控源封 IP 问题。

> 本项目只提供取数与下单能力，**不定义交易策略**。

## 核心特性

- **能力代理服务端（asgk-server）**：单进程持有全部上游知识（URL/编码/字段映射/签名），按数据域暴露 22 个具名能力（`POST /v1/<capability>`）。全局限流 ≤1 req/s + 六档缓存 + singleflight + 403/429 熔断，单 IP 多 agent 并发不封 IP。
- **asgk CLI**：纯 HTTP 客户端，10 大类 × 68 子命令两层结构（`asgk quote realtime 600519`）。一个 `uv tool install` 同时装出服务端 + CLI 两个 bin。
- **skill 纯文档化**：SKILL.md 路由层 + 按需加载的 references，零代码。token 效率比单文件全量载入提升 80~90%。
- **双后端启动**：Linux 自动用 systemd；macOS/WSL/容器用 nohup 后台进程。三重单例锁防多开。

## 目录结构

```
trader-skills/
├── packages/            跨 skill 共享基础设施
│   ├── asgk-server/     能力代理服务端 + asgk CLI（22 能力 + 流量内核）
│   └── sgw/             透明流量网关（DEPRECATED，仅历史参考）
├── skills/              各 skill（最终产物，纯文档）
│   └── a-stock-data/    A股数据 skill
│       ├── SKILL.md     路由层 + 决策表
│       └── references/  22 个按需加载的领域文件
├── ref/                 参考实现（submodule，只读）
│   └── a-stock-data/    上游蓝本（Apache 2.0）
└── .agents/             协作文档（notes/todo/temp）
```

## 快速开始

```bash
# 1. clone（skill 通常只含 skills/，需 clone 全仓装服务端）
git clone https://gitee.com/suncebf1998/trader-skills.git
cd trader-skills

# 2. 一键安装（装出 asgk-server + asgk 两个 bin，自动启动服务，配置好 CLI）
./packages/asgk-server/scripts/asgk-server-service.sh install

# 3. 取数
asgk quote realtime 600519          # 茅台实时行情（PE/PB/市值）
asgk time status                    # 当前时间 + 是否交易时段 + 是否交易日
asgk --list                         # 全部 10 大类 × 68 子命令
```

### 10 大类速查

| 类别 | 示例 | 说明 |
|------|------|------|
| `quote` | `asgk quote realtime 600519` | 行情（实时/K线/盘口/逐笔） |
| `base` | `asgk base report 600519` | 基本面（财报/F10/业绩/PE历史） |
| `report` | `asgk report list 600519` | 研报（评级/EPS/估值/PEG） |
| `flow` | `asgk flow fundflow 600519` | 资金（资金流/融资融券/股东/分红） |
| `signal` | `asgk signal hot` | 信号（热点/龙虎榜/板块/筹码） |
| `event` | `asgk event mgmt` | 事件（高管/回购/调研/解禁/互动易） |
| `risk` | `asgk risk zt 2026-08-07` | 风控（质押/商誉/涨停池/炸板率） |
| `news` | `asgk news telegraph` | 资讯（电报/新闻/热榜/公告） |
| `deriv` | `asgk deriv opt_greek OP10004257` | 衍生（期权/公告研报 PDF） |
| `time` | `asgk time status` | 交易时序（时间/交易日/时段） |

## 可用性检查

用前先 check（POSIX）：
```bash
command -v asgk >/dev/null 2>&1 && { asgk quote realtime --sources >/dev/null 2>&1 && echo "✓ ready" || echo "✗ server down → asgk-server-service.sh start"; } || echo "✗ no asgk → install.md"
```

Windows PowerShell：
```powershell
if (Get-Command asgk -ErrorAction SilentlyContinue) { asgk quote realtime --sources *> $null; if ($LASTEXITCODE -eq 0) { "✓ ready" } else { "✗ server down" } } else { "✗ no asgk" }
```

详见 [安装与启动](skills/a-stock-data/references/install.md)。

## 文档

- [AGENTS.md](AGENTS.md) — 项目约定与 agent 行为规范
- [packages/asgk-server/README.md](packages/asgk-server/README.md) — 服务端 22 能力清单与部署
- [skills/a-stock-data/SKILL.md](skills/a-stock-data/SKILL.md) — A股数据 skill 路由层
- [.agents/notes/](.agents/notes/) — 设计文档、接口契约、测试方法（入口 README.md）

## 测试

```bash
cd packages/asgk-server && uv run pytest     # 187 个测试（153 服务端 + 34 CLI）
```

## 许可证

**禁止商用**。详见 [LICENSE](LICENSE)。

本项目衍生自 [a-stock-data](https://github.com/simonlin1212/a-stock-data)（Apache 2.0），按其要求保留原始版权声明（见 [NOTICE](NOTICE)）。
