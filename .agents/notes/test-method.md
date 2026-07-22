# 测试方法：pi agent + 双层压测

服务于 `.agents/todo/gateway-mvp.md`、`.agents/todo/skill-integration-test.md`。本文是项目正式测试方法定义（非待办）。

## 一、结论：选用 pi agent，辅以 HTTP 压测

**pi agent 适用**（已调研 https://pi.dev 与 https://github.com/earendil-works/pi）：

- pi 是 session-based 的最小 agent harness，**支持 skills**：skill 放 `.pi/skills/<name>/SKILL.md`，pi 按 frontmatter 的 `name` + `description` 发现并按需加载——与本项目的 skill 模型**完全一致**，可直接加载本项目产出的 skill 做端到端测试。
- session 日志天然提供观测：`.pi/sessions/<id>/` 下含 transcript、逐 tool call 时序、token 计数、耗时。
- 扩展 `pi-parallel-agents`（https://github.com/messense/pi-parallel-agents）可动态并行 spawn 多个独立 session，模拟 N 个 agent 并发——每个 session = 独立进程/模型调用，正好对应本项目「单 IP 下 N agent 并发」场景。

**但单靠 pi 不够**：pi-parallel-agents 偏「不同 model 并行协作」，而本项目核心压测需求是「N 个同构 agent 同时打网关」。因此采用**双层策略**：

| 层 | 工具 | 测什么 | 场景 |
|----|------|--------|------|
| L1 端到端 | pi + pi-parallel-agents | agent 真实行为：skill 是否触发、token 是否省、流程是否跑通 | 少量并发（如 10～50）验证正确性 |
| L2 纯网关压测 | locust / 自写脚本 | 网关吞吐与限流：全局 RPS、缓存命中率、封 IP 边界 | 高并发（100～1000）压阈值 |

L2 不经过 LLM（省成本、纯确定性），直接对网关发请求；L1 经过完整 agent loop，验证真实体验。两者互补。

## 二、环境准备

### 安装 pi

```bash
npm i -g @earendil-works/pi@latest      # 或从源码 build
pi extension add messense/pi-parallel-agents
```

### 加载被测 skill

将本项目 skill 软链/拷贝到 pi 的 skill 目录，使 pi 能发现：

```bash
# 项目级（推荐，隔离）
mkdir -p .pi/skills
ln -s ../../skills/a-stock-data .pi/skills/a-stock-data

# 或用户级（全局）
ln -s ~/Documents/trader-skills/skills/a-stock-data ~/.pi/skills/a-stock-data
```

确认发现：`pi skills list` 应列出 `a-stock-data`。

### 网关就绪

L1/L2 都依赖 `gateway-mvp.md` 的网关在跑：

```bash
python skills/a-stock-data/scripts/sgw_proxy.py   # 监听 localhost:7700
export ASGK_GW=http://localhost:7700              # agent/asgk 读此变量
```

## 三、L1 端到端测试流程（pi）

### 3.1 单 agent 正确性验证

先用单 session 确认 skill 触发与流程正确，再上并发。

```bash
pi run -p "查一下 688017 的完整估值：行情、研报、资金面、PE消化时间"
```

检查 `.pi/sessions/<id>/`：
- skill 是否被触发（description 命中）。
- token 消耗（对比上游全量载入 127KB 的基线，验证轴1拆分的 token 节省）。
- 网关日志：该 session 触发了几次外网请求、几次缓存命中。

### 3.2 并发 agent 压测（pi-parallel-agents）

用 predefined agents 复制 N 份同构配置，模拟 N 个 agent 同时打同一 skill。

在 `.pi/agents/` 下定义 N 个相同配置（仅 name 不同）：

```jsonc
// .pi/agents/a01.json
{
  "model": "<同一个大模型>",
  "skills": ["a-stock-data"],
  "systemPrompt": "你是独立的交易信息 agent，按需调用 a-stock-data skill 取数。"
}
// .pi/agents/a02.json ... aNN.json  (复制 N 份)
```

批量生成 N 个配置：

```bash
N=50
for i in $(seq -w 1 $N); do
  cat > ".pi/agents/a${i}.json" <<EOF
{ "model": "anthropic/claude-sonnet", "skills": ["a-stock-data"], "systemPrompt": "独立交易信息 agent" }
EOF
done
```

发起并行压测（每个 agent 跑一个典型 prompt，可相同可不同）：

```bash
pi run --extension parallel-agents -p "对 600519 做快速调研：估值 + 北向资金 + 龙虎榜"
```

> 注：若要让各 agent 跑不同标的（更接近真实，降低缓存命中、放大外网压力），用 dynamic 模式在 prompt 里描述分发，或为每个 agent 配不同 prompt。

### 3.3 L1 观测指标（从 session 日志聚合）

| 指标 | 来源 | 意义 |
|------|------|------|
| skill 触发率 | session transcript | description 是否可靠触发 |
| 单 session token | session 日志 token 计数 | 验证轴1 token 节省 |
| 端到端延迟 | session 总耗时 | 用户体感 |
| 外网请求数 / 缓存命中 | 网关日志按 session 归集 | 验证轴2缓存效果 |
| 失败/降级次数 | session + 网关日志 | 是否触发备用源 |

聚合脚本（示意）：遍历 `.pi/sessions/` 提取上述字段，与网关日志按时间窗 join。

## 四、L2 网关压测流程（locust）

L1 受 LLM 成本/速率限制，跑不到 1000 并发。纯网关压测用 locust 直接打 HTTP。

### 4.1 安装

```bash
pip install locust
```

### 4.2 压测脚本 `tests/locust_sgw.py`

```python
from locust import HttpUser, task, between
import os, random

# 典型东财端点样本（行情/研报/资金流等）
EM_ENDPOINTS = [
    ("push2.eastmoney.com/api/qt/stock/get", {"secid": "1.600519"}),
    ("push2his.eastmoney.com/api/qt/stock/fflow/daykline/get", {"secid": "1.600519"}),
    ("reportapi.eastmoney.com/report/list", {"qType": 0, "pageSize": 10}),
]
CACHE_BUST = ["600519","000858","601318","688017","002594"]  # 部分相同(命中缓存) 部分不同

class GatewayUser(HttpUser):
    host = os.environ.get("ASGK_GW", "http://localhost:7700")
    wait_time = between(0.1, 0.5)   # 模拟 agent 思考间隔

    @task(7)  # 70% 读相同标的(测缓存命中)
    def hot_stock(self):
        host, params = random.choice(EM_ENDPOINTS)
        params = {**params, "secid": params.get("secid","1.600519").split(".")[0] + "." + random.choice(["600519","000858"])}
        self.client.get("/em", params={"u": f"https://{host}", **params})

    @task(3)  # 30% 读不同标的(测限流)
    def cold_stock(self):
        host, _ = random.choice(EM_ENDPOINTS)
        code = random.choice(CACHE_BUST)
        self.client.get("/em", params={"u": f"https://{host}", "secid": f"1.{code}"})
```

### 4.3 执行压测

```bash
# 阶梯加压：100 → 500 → 1000 用户
locust -f tests/locust_sgw.py --headless -u 1000 -r 50 --run-time 5m \
       --host http://localhost:7700
```

- `-u 1000`：模拟 1000 并发 agent。
- `-r 50`：每秒新增 50 用户（错峰，贴近真实部署）。
- Web UI（去掉 `--headless`）可实时看 RPS / 延迟 / 错误率。

### 4.4 L2 观测指标

| 指标 | 阈值目标 | 说明 |
|------|---------|------|
| 网关外网出口 RPS | 东财组 ≤1 req/s | 验证全局令牌桶生效（无论上游多少并发） |
| 缓存命中率 | 静态数据 >80% | 同标的热数据应大量命中 |
| 请求延迟 P50/P95 | P95 < 2s（缓存命中 <50ms） | 命中缓存应极快 |
| 错误率 | 429/403 = 0 | 全程不触发东财封 IP |
| 降级触发 | 主源失败时切备用源 | 验证 failover |

## 五、阈值校准产出

测试后回填 `sgw_config.toml`：

- 东财组全局 RPS 上限（从 L2 找到不封 IP 的安全值，对齐上游 `EM_MIN_INTERVAL=1.0`）。
- 同花顺组、财联社组各自的独立阈值。
- 各类数据 TTL（从 L1 缓存命中率 + L2 新鲜度平衡定档）。

记录在 `.agents/todo/skill-integration-test.md` 引用的校准报告里。

## 六、局限与替代

- **pi-parallel-agents 的同构并发**：默认偏异构，需用 predefined agents 复制配置实现同构；若 N 很大（>100），配置文件管理繁琐，此时 L1 只跑小样本（验证正确性），大样本压测交给 L2。
- **LLM 成本**：L1 跑大并发成本高，故 L1 定位为「正确性 + 小并发」，L2 为「规模化压测」。
- **pi 版本变动**：pi 仍在演进，若 session 日志格式或 extension API 变化，需同步更新本文件的命令。以 https://pi.dev/docs 为准。

## 七、与待办的衔接

本文是**方法定义**，实际跑测试是待办 `.agents/todo/skill-integration-test.md`（依赖 P0-P3 产物就绪）。该待办完成时，按本文第五节产出阈值校准报告并回填 `sgw_config.toml`。
