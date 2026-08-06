# 测试方法：pi agent + 双层压测

> **⚠️ 操作细节已过时**：本文的**方法论**（pi 端到端 + locust 压测的双层策略）
> 仍然有效，但操作命令中的 `ASGK_GW`/`skills/a-stock-data/scripts/`/`em_get`/
> sgw 引用基于已删除的客户端库。当前测试对象是 `packages/asgk-server`
> （`uv run pytest`，187 测试）。应用本方法论时，把测试对象替换为 asgk-server
> 服务端 + asgk CLI，把 `ASGK_GW` 换成 `ASGK_SERVER`。

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
| L2 纯网关压测 | pytest + mock/replay | single-flight、限流、熔断、缓存与队列边界 | 高并发（100～1000），禁止真实出网 |

L2 不经过 LLM 且上游必须替换为本地 mock/replay；L1 经过完整 agent loop，验证真实体验。两者互补。

## 二、环境准备

### 安装 pi

```bash
npm i -g @earendil-works/pi@latest      # 或从源码 build
pi extension add messense/pi-parallel-agents
```

### 配置模型 provider（实测：火山方舟 Coding Plan）

L1 测试需要 LLM 驱动 agent loop。实测可用：火山方舟 Coding Plan（套餐制、OpenAI 兼容）。

在 `~/.pi/agent/models.json` 配置 provider（API Key 留空待填，或用 `$ENV_VAR` 引用环境变量）：

```jsonc
{
  "providers": {
    "ark-coding": {
      "baseUrl": "https://ark.cn-beijing.volces.com/api/coding/v3",  // OpenAI 兼容入口（勿用 /api/v3，那个不走套餐额度）
      "apiKey": "",                                                    // 填入 Coding Plan API Key
      "authHeader": true,
      "api": "openai-completions",
      "models": [
        { "id": "glm-5.2", "reasoning": true, "thinkingFormat": "zai", "contextWindow": 128000, "cost": {"input":0,"output":0,"cacheRead":0,"cacheWrite":0} },
        { "id": "ark-code-latest", "reasoning": false, "contextWindow": 128000, "cost": {"input":0,"output":0,"cacheRead":0,"cacheWrite":0} }
        // 其余模型（doubao-seed-2.0-code / deepseek-v4-pro / kimi-k2.7-code）按需追加
      ]
    }
  }
}
```

要点（实测确认）：

- `cost` 全 0：Coding Plan 是套餐制，不按 token 计费。
- `ark-code-latest`：**控制台动态切换别名**——同一命令实际跑哪个底层模型取决于[开通管理页](https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement)当前选择（或 Auto 自动调度）。这影响 L1 可复现性：如需固定模型做对比，用具体 model id（如 `glm-5.2`）而非 `ark-code-latest`。
- `thinkingFormat: "zai"` 是 pi 内置的智谱 GLM thinking 协议；若 reasoning 模型响应异常可先去掉定位。

### 加载被测 skill

实测方式：把 skill 软链到 pi 的全局 skill 目录，使 pi 按 `name`+`description` 发现。**测试 ref 蓝本时**链 ref，**测试本项目产物时**链 skills：

```bash
mkdir -p ~/.pi/skills

# 测试 ref 蓝本（当前阶段）
ln -sfn $(pwd)/ref/a-stock-data ~/.pi/skills/a-stock-data

# 测试本项目产物（P3 完成后）
ln -sfn $(pwd)/skills/a-stock-data ~/.pi/skills/a-stock-data
```

确认发现：`ls ~/.pi/skills/` 应见 `a-stock-data`，且 `head ~/.pi/skills/a-stock-data/SKILL.md` 能读到 frontmatter。

### 网关就绪

L1/L2 都依赖网关在跑（`sgw` 包，见 `gateway-design.md`）：

```bash
 cd packages/sgw && uv run sgw-proxy   # 监听 localhost:7700
export ASGK_GW=http://localhost:7700                  # agent/asgk 读此变量
```

## 三、L1 端到端测试流程（pi）

### 3.1 单 agent 正确性验证

**第一步：连通预检**（不依赖 skill，排除 provider 配置问题）：

```bash
pi --provider ark-coding --model ark-coding/ark-code-latest --no-tools -p "请只回复四个字：连通测试"
# 期望输出：连通测试
```

**第二步：skill 触发 + 真实取数**（在项目目录下跑，pi 才有 bash 工具能执行取数代码；选零 key、不封 IP 的端点首测）：

```bash
pi --provider ark-coding --model ark-coding/ark-code-latest \
   -p "用 a-stock-data skill 的腾讯财经接口，查贵州茅台(600519)实时行情：现价、PE、PB、总市值。实际写代码调接口取真实数据。"
```

检查 `.pi/sessions/<id>/`（或 stdout）：
- skill 是否被触发（description 命中）。
- 是否真实调外网（腾讯 `qt.gtimg.cn`）拿到数据，而非编造。
- token 消耗（对比上游全量载入 127KB 的基线，验证轴1拆分的 token 节省）。
- 网关日志：该 session 触发了几次外网请求、几次缓存命中。

> **实测基线（2026-07-23，ark-code-latest，本项目产物）**：skill 软链指向 `skills/a-stock-data`（非 ref 蓝本）。
> - **单 agent**：pi 正确触发 SKILL.md，按路由表选对函数（行情 `tencent_quote` 直连、研报 `eastmoney_reports` 经网关），写出并执行 asgk 代码，取回茅台真实数据（PE19.72/PB7.01/100篇研报）。网关侧确认东财请求 1 次、P 档缓存生效。**全链路打通 ✓**
> - **历史风险记录**：早期 3 agent 测试曾因子进程未继承 `ASGK_GW` 而直连。当前实现已删除 `ASGK_ALLOW_DIRECT`，未配置网关会失败关闭，不再允许该路径。
>
> **⚠️ 部署发现**：`ASGK_GW` 环境变量必须确保对所有 agent 进程可见（写入 shell profile / systemd env / 容器 envfile），而非依赖父进程临时 export——pi-parallel-agents 的子 agent 不继承临时 export。这是多 agent 部署的必要配置。

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

## 四、L2 网关压测流程（mock/replay）

当前唯一允许的高并发验证方式是把 `requests.get` 替换为本地 mock/replay：

```bash
uv run --project packages/sgw pytest \
  packages/sgw/tests/test_policy.py \
  packages/sgw/tests/test_endpoint_inventory.py \
  packages/sgw/tests/test_circuit_state.py -q
```

固定验收：1000 个相同冷请求最多一次模拟出网；首次模拟 403/429 后，冷却期
不再调用上游；凭据不进入缓存键、SQLite 或指纹日志；未配置网关时不直连；
所有内部 `em_get` URL 必须唯一匹配 approved 端点政策，且不得有孤儿政策。
熔断状态和累计 5xx 必须跨重启保留；状态介质异常严格按
10m/30m/1h/6h/12h/24h 退避，1000 并发只运行一个存储探测，等待期缓存可读且
冷 miss 的模拟外网调用数为 0。
真实来源只允许单请求、低频、人工触发的 canary，禁止用来寻找封禁阈值。

### 历史 locust 方案（禁止对真实来源执行）

以下内容仅保留历史测试记录，不再作为可执行方法。若复用 locust，目标必须是
完全离线的 replay server，且 DNS/网络层应保证无法访问真实上游。

L1 受 LLM 成本/速率限制，跑不到 1000 并发。纯网关压测用 locust 直接打 HTTP。

### 4.1 安装

locust 作为 dev 依赖加入 uv 项目（在 `skills/a-stock-data/scripts/` 下）：

```bash
uv add --dev locust
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

## 五、历史阈值记录（不得主动复测）

测试后回填 `packages/sgw/sgw/config.toml`（网关配置）：

- 东财组全局 RPS 上限（从 L2 找到不封 IP 的安全值，对齐上游 `EM_MIN_INTERVAL=1.0`）。
- 同花顺组、财联社组各自的独立阈值。
- 各类数据 TTL（从 L1 缓存命中率 + L2 新鲜度平衡定档）。

记录在 `.agents/todo/skill-integration-test.md` 引用的校准报告里。

> **L2 实测结果（2026-07-23，locust 阶梯加压 10→100→300 用户）**：
>
> | 并发 | 时长 | 总请求 | 外网东财 | 缓存命中 | 0失败 | 结论 |
> |------|------|--------|---------|---------|-------|------|
> | 10 | 30s | 73 | 23 | 50 | ✓ | 限流+缓存生效 |
> | 100 | 30s | ~350 | +24(=54) | 293 | ✓ | 外网不随并发增长 |
> | 300 | 30s | 672 | +24(=92) | 965 | ✓ | **外网≤1req/s铁律不破** |
>
> **核心验证**：无论并发 10 还是 300，东财外网请求始终被压在 ≤1 req/s（网关令牌桶兜底）。热门标的缓存命中中位数 2ms。**测试全程外网 <100 次，远低于封 IP 阈值（>200/分钟）。**

## 六、局限与替代

- **pi-parallel-agents 的同构并发**：默认偏异构，需用 predefined agents 复制配置实现同构；若 N 很大（>100），配置文件管理繁琐，此时 L1 只跑小样本（验证正确性），大样本压测交给 L2。
- **LLM 成本**：L1 跑大并发成本高，故 L1 定位为「正确性 + 小并发」，L2 为「规模化压测」。
- **pi 版本变动**：pi 仍在演进，若 session 日志格式或 extension API 变化，需同步更新本文件的命令。以 https://pi.dev/docs 为准。

## 七、与待办的衔接

本文是**方法定义**，实际跑测试是待办 `.agents/todo/skill-integration-test.md`（依赖 P0-P3 产物就绪）。该待办完成时，按本文第五节产出阈值校准报告并回填 `sgw_config.toml`。
