# 测试操作手册

本项目测试的操作指南。方法论见 `test-method.md`（pi + mock/replay
双层策略），本文件是可直接执行的命令手册。

> 所有命令基于 uv。前置：`cd skills/a-stock-data/scripts && uv sync`。

---

## 已验证基线（2026-08-01）

### P1 熔断状态与双平台有限验证

验证提交 `f287499`。两平台均先执行 frozen sync，再运行完整离线套件；
“跨平台”只表示以下两个 OS/架构组合，不外推到其他系统。

| 项目 | macOS 本机 | `iamsbb2` |
|---|---|---|
| 系统 / 架构 | macOS 26.5.1 / ARM64 | Ubuntu 24.04.4 / Linux x86_64 |
| Python / uv | 3.12.12 / 0.9.27 | 3.13.12 / 0.10.6 |
| sgw 离线测试 | 74 passed | 74 passed |
| asgk 离线测试 | 122 passed | 122 passed |
| 实际进程重启 | 两次 `ready=true`，出网 0 | 两次 `ready=true`，出网 0 |
| 状态文件权限 | DB/标记均 `0600` | DB/标记均 `0600` |

状态测试包含：403/429 与累计 5xx 跨重启、120 秒 canary 租约、精确
10m/30m/1h/6h/12h/24h 退避、单介质与双介质损坏、1000 并发单存储探针、
cache-only、敏感字段白名单及单次尝试配置。所有上游均为 mock。

真实 canary 严格串行，总预算和实际出网均为 3，网关统一使用
`--max-attempts 1`：

| 次序 / 主机 | 来源 / 调用 | 结果 | 网关证据 |
|---|---|---|---|
| 1 / macOS | Eastmoney `eastmoney_reports("600519", 1)` | 100 条；首条 2026-07-23 | eastmoney=1，errors=0 |
| 2 / Linux | 10jqka `ths_eps_forecast("600519")` | 3 条；2026 EPS 均值 68.7 | 10jqka=1，errors=0 |
| 3 / Linux | SZSE `margin_detail_szse("20260731")` | 合法 XLSX，但仅表头、0 条 | exchange=1，errors=0 |

三次均无 403/429、验证码、重试或熔断。SZSE 响应为 3638 字节，工作表名
“融资融券交易明细”，维度 1×1，唯一单元格为“证券代码”；因此传输和空集解析
通过，但该次 canary **没有证明非空明细解析**，且受总预算约束未更换日期重试。

pi 按主机分别选用可用模型：macOS 使用 `openai-codex/gpt-5.6-luna`，返回同一
100 条研报，cache hits +1 且 eastmoney 出网仍为 1。`iamsbb2` 不要求 GPT，
原生使用 `ark-coding/glm-5.2`；模型连通返回 `glm-ok`，随后加载 skill 并只运行
`test_chip.py` 与 `test_em_proxy.py`，JSON 事件记录确认唯一 bash tool call，结果
`10 passed in 0.41s`，无失败、错误或跳过。该 Linux 验证全程离线，没有增加
家庭 IP 的真实上游请求。

### Linux x64 依赖与百度 K 线

在 `iamsbb2` 上对提交 `e3b961e` 执行依赖安装和单次真实 canary，
快进到 `65ddf8a` 后重跑最终离线测试：

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts
~/.local/bin/uv sync --frozen
~/.local/bin/uv run pytest asgk/tests -q
```

| 项目 | 结果 |
|---|---|
| 系统 | Ubuntu 24.04 / Linux 7.0.0-28-generic / x86_64 |
| Python / uv | Python 3.13.12 / uv 0.10.6 |
| 原生依赖 | `curl-cffi 0.15.0` + `mini-racer 0.14.1` 安装成功 |
| sgw 完整测试（当时基线） | 62 passed |
| asgk 完整测试 | 122 passed |
| 百度真实 canary | 600519：18 字段 / 2001 行 / 2018-05-07～2026-07-31 / MA5、10、20 齐全 |

百度 canary 仅串行请求一次，未重试、未并发。

### 端点库存 CI

```bash
uv run --frozen --project packages/sgw pytest \
  packages/sgw/tests/test_endpoint_inventory.py -q
```

结果：25 个 `em_get` 调用 URL 模式全部可静态解析，完整覆盖
22 个 approved 端点政策；无未分类调用、无孤儿政策。GitHub Actions
`.github/workflows/endpoint-inventory.yml` 在 push / pull request 时自动执行。

---

## Part A：网关验收（机制验证）

验证网关的限流、缓存、转发、禁止直连保护。

### A1. 单元自检（不联网）

```bash
cd skills/a-stock-data/scripts
uv run python -c "from asgk import em_get; print('asgk OK')"
uv run python -c "from sgw.proxy import Gateway, load_config, DEFAULT_CONFIG; g=Gateway(load_config(DEFAULT_CONFIG)); print('组:', list(g.buckets), '东财域名:', sum(1 for v in g.domain_group.values() if v=='eastmoney'))"
```
✅ `asgk OK` / `组: ['eastmoney', '10jqka'] 东财域名: 10`

### A2. 启动网关（终端 A）

```bash
cd packages/sgw
uv run sgw-proxy --port 7700
```
✅ `[sgw_proxy] listening on 127.0.0.1:7700`

后续命令在终端 B。

### A3. 健康检查

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -m json.tool
```
✅ JSON，`group_reqs` 全 0

### A4. 代理 + 缓存

```bash
URL='https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
curl -s -D - -H "X-Cache-Tier: S" "http://127.0.0.1:7700/?u=$URL&secid=1.600519&lmt=1&klt=101&fields1=f1&fields2=f51,f52" -o /dev/null | grep -i X-Cache
curl -s -D - -H "X-Cache-Tier: S" "http://127.0.0.1:7700/?u=$URL&secid=1.600519&lmt=1&klt=101&fields1=f1&fields2=f51,f52" -o /dev/null | grep -i X-Cache
```
✅ `MISS` -> `HIT-MEM`（响应头 X-Cache 取值：HIT-MEM 内存命中 / HIT-DISK 磁盘命中 / MISS 打外网，见 A8）

### A5. 并发限流（核心）

```bash
uv run python -c "
import requests, concurrent.futures, time
GW='http://127.0.0.1:7700'
URL='https://push2.eastmoney.com/api/qt/slist/get'
codes=['600519','000858','601318','688017','002594']
t0=time.time()
def hit(i):
    requests.get(GW, params={'u':URL,'spt':3,'security_code':codes[i],'fields':'f12,f14'}, headers={'X-Cache-Tier':'R'}, timeout=30)
    return round(time.time()-t0,2)
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
    print('完成时刻:', sorted(ex.map(hit, range(5))))
"
```
✅ 完成时刻递增（间隔≈1s）= 限流生效。全 <0.1s = 限流失效。

### A6. 禁止直连保护

```bash
unset ASGK_GW
export ASGK_ALLOW_DIRECT=1  # 模拟部署环境残留的旧变量，当前必须无效
ASGK_ENV=/dev/null uv run python -c "
from asgk import em_get
try:
    em_get('https://push2.eastmoney.com/test', tier='R')
    print('❌ 未抛异常')
except RuntimeError as e:
    print(f'✅ 抛异常: {str(e)[:40]}')
"
```
✅ `✅ 抛异常: ASGK_GW 未设置`

### A7. 透明性（字节级一致）

禁止为了透明性验证从家庭 IP 直连风控源。改用 mock 响应验证 URL、请求头和响应
字节转发，覆盖见 `packages/sgw/tests/test_cachekey.py`、`test_headers.py`。

### A8. 磁盘持久化（P/L 档，§3.4.8）

验证 P/L 档缓存落 SQLite、重启后恢复、命中来源可区分。

```bash
# 1. 写入一条 P 档（MISS -> 落盘）
URL='https://reportapi.eastmoney.com/report/list'
curl -s -D - -H "X-Cache-Tier: P" "http://127.0.0.1:7700/?u=$URL&industryCode=*&pageSize=1&fields=f11" -o /dev/null | grep -i X-Cache
✅ X-Cache: MISS

# 2. 内存命中
curl -s -D - -H "X-Cache-Tier: P" "http://127.0.0.1:7700/?u=$URL&industryCode=*&pageSize=1&fields=f11" -o /dev/null | grep -i X-Cache
✅ X-Cache: HIT-MEM

# 3. 看 /__stats：disk_cache.size > 0
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('disk_cache:',d['disk_cache'])"
✅ disk_cache.size > 0

# 4. 重启网关，验证 load_all 恢复（启动日志可见 loaded N entries）
pkill -f sgw-proxy; sleep 2
cd ~/Documents/trader-skills/packages/sgw && uv run sgw-proxy --port 7700 2>&1 | grep "disk cache"
✅ [sgw_proxy] disk cache: ... (loaded N entries in ...ms)

# 5. 重启后请求同 URL：应命中（HIT-MEM，因 load_all 已灌入内存），且东财外网请求 0
curl -s -D - -H "X-Cache-Tier: P" "http://127.0.0.1:7700/?u=$URL&industryCode=*&pageSize=1&fields=f11" -o /dev/null | grep -i X-Cache
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('em_reqs:',d['group_reqs']['eastmoney'],'disk_load_count:',d['disk_load_count'])"
✅ X-Cache: HIT-MEM  且  em_reqs: 0（零外网）  disk_load_count > 0
```

> HIT-DISK 路径（内存清空后命中磁盘）由单元测试覆盖（`packages/sgw/tests/test_cache.py::test_disk_hit_after_mem_clear`），手动验收聚焦「重启恢复」这一核心价值。

> 仅 P/L 档落盘；S 盘中易脏、R/N 不缓存。db 默认 `packages/sgw/sgw/cache/sgw_cache.db`，生产用 `--cache-dir` 指定。
---

## Part B：L1 pi agent 端到端（真实 agent 验证）

用 pi + ark-coding 验证 skill 在真实 agent 下的触发、路由、取数、网关全链路。

> SKILL.md 已自解释 asgk 的执行方式（`cd scripts && uv run`），prompt 里无需重复路径。

### B1. 搭建独立工作目录

```bash
WORK=~/Documents/trader-skills/.agents/temp/pi-agent-work
mkdir -p $WORK && cd $WORK
echo 'ASGK_GW=http://127.0.0.1:7700' > .env
```

### B2. 连通预检

```bash
pi --provider ark-coding --model ark-coding/ark-code-latest --no-tools -p "回复：连通OK"
```
✅ `连通OK`

### B3. 单 agent 端到端（核心）

```bash
cd ~/Documents/trader-skills/.agents/temp/pi-agent-work
pi --provider ark-coding --model ark-coding/ark-code-latest \
   --skill ~/Documents/trader-skills/skills/a-stock-data \
   -p "用 a-stock-data skill 查贵州茅台(600519)的实时行情(PE/PB/市值)和最新研报标题。取真实数据。"
```
✅ 真实数据 + 正确分流（行情直连、研报经网关）

### B4. 验证网关

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('东财外网:',d['group_reqs']['eastmoney'],'缓存:',d['cache']['size'])"
```
✅ `东财外网 > 0`

### B5. 多 agent 并发

```bash
cd ~/Documents/trader-skills/.agents/temp/pi-agent-work
pi --provider ark-coding --model ark-coding/ark-code-latest \
   --skill ~/Documents/trader-skills/skills/a-stock-data \
   -p "并行3任务用 asgk 的 eastmoney_reports 查研报数：1.000858 2.601318 3.000858(验证缓存)。只返回条数。"
```

### B6. 清理

```bash
rm -rf ~/Documents/trader-skills/.agents/temp/pi-agent-work
rm -f ~/Documents/trader-skills/skills/a-stock-data/scripts/query_*.py
rm -rf ~/Documents/trader-skills/packages/sgw/sgw/logs   # 测试产生的指纹日志
pkill -f sgw-proxy 2>/dev/null; pgrep -af sgw-proxy || echo "无残留 ✓"
```

### B7. glm-5.2 模型端到端（步骤与坑）

B3 默认用 `ark-code-latest`。若要指定 `glm-5.2`，步骤相同但有一个**必须先解决的坑**。

**坑：glm-5.2 报 `developer role` 400 错误**

glm-5.2 在 models.json 里 `"reasoning": true`，pi 对 reasoning 模型默认用 `developer` role 发 system prompt（OpenAI o1 风格），但 glm-5.2 的 Ark Coding 端点只认 `system`/`assistant`/`user`/`tool`，请求直接 400：

```
400: ... invalid value: `developer`, supported values: `system`,`assistant`,`user`,`tool`
```

根因在 pi-ai 的 `openai-completions.js`：`useDeveloperRole = model.reasoning && compat.supportsDeveloperRole`，ark-coding baseUrl 不在「非标准 provider」名单里导致 `supportsDeveloperRole` 检测为 true。

**修法**：编辑 `~/.pi/agent/models.json`，给 glm-5.2 加 `compat` 覆盖（先备份）：

```bash
cp ~/.pi/agent/models.json ~/.pi/agent/models.json.bak
# 在 glm-5.2 的模型定义对象里加入：
#   "compat": { "supportsDeveloperRole": false },
```

这是 pi 客户端配置，不在本项目仓库内。

**步骤**（修坑后）：

```bash
# 1. 启网关（终端 A）
cd ~/Documents/trader-skills/packages/sgw && uv run sgw-proxy --port 7700

# 2. 连通预检（用 glm-5.2）
pi --provider ark-coding --model ark-coding/glm-5.2 --no-tools -p "回复：连通OK"
✅ `连通OK`

# 3. 端到端取数 + 生成报告
WORK=~/Documents/trader-skills/.agents/temp/pi-wanhua
mkdir -p $WORK && cd $WORK
echo 'ASGK_GW=http://127.0.0.1:7700' > .env
pi --provider ark-coding --model ark-coding/glm-5.2 \
   --skill ~/Documents/trader-skills/skills/a-stock-data --approve \
   -p "用 a-stock-data skill 获取<标的>(<代码>)的真实数据...写一份投资建议 markdown..."
```

✅ 报告生成 + 数据真实 + 网关有外网请求记录。

> 注：东财 `push2his` 子域偶发 `RemoteDisconnected`（服务端问题，非本项目 bug），asgk 会降级到新浪备用源，属 failover 正常行为。

---

## Part C：L2 mock/replay 压测

```bash
uv run --project packages/sgw pytest packages/sgw/tests/test_policy.py -q
```

该测试包含 1000 并发同键请求、凭据落盘扫描以及 403/429 熔断验证，mock
`requests.get`，不会访问真实来源。

### 历史 locust 步骤（禁止对真实来源执行）

以下步骤只作为历史记录。即使入口是 localhost，网关仍会访问真实上游，因此不能
把“有限流”当作压测安全保证。若复用脚本，必须将上游替换为离线 replay server。

### C1. 创建压测脚本

```bash
mkdir -p ~/Documents/trader-skills/.agents/temp/locust
cat > ~/Documents/trader-skills/.agents/temp/locust/sgw_stress.py << 'EOF'
from locust import HttpUser, task, between
import random
EM_URL = "https://push2.eastmoney.com/api/qt/slist/get"
HOT = ["600519", "000858", "601318", "300750", "002594"]
COLD = [f"{random.choice(['600','000','300','002'])}{random.randint(100,999):03d}" for _ in range(50)]
class GatewayUser(HttpUser):
    wait_time = between(0.5, 2.0)
    @task(7)
    def hot(self):
        self.client.get("/", params={"u": EM_URL, "spt": 3, "security_code": random.choice(HOT), "fields": "f12,f14"}, headers={"X-Cache-Tier": "S"}, name="hot(cached)")
    @task(3)
    def cold(self):
        self.client.get("/", params={"u": EM_URL, "spt": 3, "security_code": random.choice(COLD), "fields": "f12,f14"}, headers={"X-Cache-Tier": "R"}, name="cold(limited)")
EOF
```

### C2. 阶梯加压（10→100→300，每轮 30s）

网关保持运行（Part A/B 的终端 A）。**每轮前记录 stats 基线，压测后核对外网请求数**：

```bash
cd ~/Documents/trader-skills

# 10 用户小试
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('基线 东财:',d['group_reqs']['eastmoney'])"
uv run --project skills/a-stock-data/scripts locust -f .agents/temp/locust/sgw_stress.py --headless -u 10 -r 5 -t 30s --host http://127.0.0.1:7700 2>&1 | tail -5
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('压后 东财:',d['group_reqs']['eastmoney'],'缓存命中:',d['cache']['hits'])"

# 确认外网增量 <30 后，逐步加压到 100、300（改 -u 即可）
```

### C3. 安全判定

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('东财外网:',d['group_reqs']['eastmoney'],'| 缓存命中:',d['cache']['hits'],'| 限流等待:',d['bucket_waits']['eastmoney'])"
```

✅ **通过条件**（以 300 用户为例）：
- 东财外网 < 100 次（≤1req/s 兜底，整轮测试约 30s 新增 ~24 次）
- 缓存命中 >> 外网请求（热门标的命中缓存）
- 0 失败

❌ 外网请求飙升（如 >200/分钟）→ **立即 Ctrl+C 停 locust**，限流可能失效。

### C4. 清理

```bash
rm -rf ~/Documents/trader-skills/.agents/temp/locust
rm -rf ~/Documents/trader-skills/packages/sgw/sgw/logs
pkill -f sgw-proxy 2>/dev/null
```

---

## 验收判定

| 部分 | 关键步骤 | 通过条件 |
|------|---------|---------|
| A 网关 | A5 限流 | 完成时刻递增（间隔≈1s） |
| A 网关 | A6 禁止直连 | 未设网关时抛异常 |
| A 网关 | A7 透明性 | mock 上游下 URL/headers/body 转发一致 |
| A 网关 | A8 磁盘持久化 | 重启后 HIT-MEM 且 em_reqs=0，disk_load_count>0 |
| **B agent** | **B3 端到端** | **真实数据 + 正确分流** |
| **B agent** | **B4 网关** | **东财外网 > 0** |
| B agent | B7 glm-5.2 | 先修 developer role 坑，连通+取数正常 |
| **C 压测** | **mock/replay** | **1000同键并发最多1次模拟出网；真实外网=0** |
