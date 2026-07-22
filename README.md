# trader-skills

赋予 AI agent **A股信息收集**与**交易操作**能力的 skill 集合。面向**单 IP 下 100~1000 个 agent 并发**场景设计，内置共享流量网关解决风控源封 IP 问题。

> 本项目只提供取数与下单能力，**不定义交易策略**。

## 核心特性

- **共享流量网关（sgw）**：所有风控源（东财/同花顺）请求经网关，全局限流 ≤1 req/s + 五档缓存，单 IP 多 agent 并发不封 IP
- **渐进式 skill 结构**：SKILL.md 路由层（<100行）+ 按需加载的 references，token 效率比单文件全量载入提升 80~90%
- **43 个取数端点**：行情/研报/信号/资金面/新闻/财务/公告/打板/期权/舆情/估值
- **禁止直连保护**：风控源默认禁止不经网关直连，未配网关地址时 fail-fast 抛异常

## 目录结构

```
trader-skills/
├── packages/            跨 skill 共享基础设施
│   └── sgw/             流量网关（限流/缓存/分档）
├── skills/              各 skill（最终产物）
│   └── a-stock-data/    A股数据 skill
│       ├── SKILL.md     路由层
│       ├── references/  12 个按需加载的领域文件
│       └── scripts/     asgk 库（15模块43函数）
├── ref/                 参考实现（submodule，只读）
│   └── a-stock-data/    上游蓝本（Apache 2.0）
└── .agents/             协作文档（notes/todo/temp）
```

## 快速开始

```bash
# 1. 安装依赖
cd packages/sgw && uv sync          # 网关
cd skills/a-stock-data/scripts && uv sync  # asgk 库

# 2. 启网关
cd packages/sgw && uv run sgw-proxy --port 7700

# 3. 取数（另开终端）
cd skills/a-stock-data/scripts
echo 'ASGK_GW=http://127.0.0.1:7700' > .env
uv run python -c "
from asgk import tencent_quote
q = tencent_quote(['600519'])
print(q['600519']['pe_ttm'], q['600519']['mcap_yi'], '亿')
"
```

## 文档

- [AGENTS.md](AGENTS.md) — 项目约定与 agent 行为规范
- [.agents/notes/](.agents/notes/) — 设计文档、接口契约、测试方法（入口 README.md）

## 许可证

**禁止商用**。详见 [LICENSE](LICENSE)。

本项目衍生自 [a-stock-data](https://github.com/simonlin1212/a-stock-data)（Apache 2.0），按其要求保留原始版权声明（见 [NOTICE](NOTICE)）。
