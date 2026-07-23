# sgw — A股数据共享流量网关

单进程 HTTP 代理，供单 IP 下 100~1000 个 agent 并发共享。解决风控源（东财/同花顺）在高并发下的封 IP 问题。

## 它做什么

```
agent (×1000)                         外网
   │  风控源(东财/同花顺)               │
   └─► localhost:7700 (sgw) ──────────►  eastmoney.com / 10jqka.com.cn
            │  全局令牌桶限流(≤1 req/s)
            │  五档缓存(P/L/S/R/N)
            │  403不重试 / 429退避
```

- **全局限流**：按域名组令牌桶（东财组 ≤1 req/s、同花顺组独立），跨进程生效——无论多少 agent 进程并发，外网出口收敛到一个
- **五档缓存**：静态数据(P档30天) / 日级(S档) / 实时(R档不缓存)，1000 agent 查同一票只打 1 次外网
- **P/L 档磁盘持久化**：研报/财报/分红等静态/季度数据落 SQLite，网关重启后恢复，冷启动不重打外网
- **透明代理**：经网关 = 直连，响应字节完全一致
- **响应指纹日志**：按天拆分，供离线分析修正分档规则

## 安装

```bash
cd packages/sgw
uv sync
```

需要 Python ≥3.11，[uv](https://docs.astral.sh/uv/) 包管理器。

## 启动

```bash
# 默认（端口 7700，指纹日志写到 sgw/logs/，磁盘缓存 sgw/cache/）
uv run sgw-proxy

# 指定端口
uv run sgw-proxy --port 8080

# 生产环境：指定指纹日志目录（按天自动拆分 sgw_fp_YYYYMMDD.jsonl）
uv run sgw-proxy --fp-dir /var/log/sgw

# 生产环境：指定磁盘缓存目录（P/L 档持久化，sgw_cache.db）
uv run sgw-proxy --cache-dir /var/lib/sgw

# 指定配置文件
uv run sgw-proxy -c /path/to/config.toml
```

## agent 侧配置

agent 通过 `ASGK_GW` 环境变量找到网关（asgk 库自动读取）：

```bash
# 方式A：环境变量（部署用）
export ASGK_GW=http://127.0.0.1:7700

# 方式B：.env 文件（开发用，asgk 从 cwd 自动加载）
echo 'ASGK_GW=http://127.0.0.1:7700' > .env
```

> 未设 `ASGK_GW` 时，asgk 的 em_get 会抛异常（禁止风控源直连）。仅调试时设 `ASGK_ALLOW_DIRECT=1` 临时允许直连。

## 配置

配置文件 `sgw/config.toml`：

```toml
[server]
port = 7700

# 限流组（每组独立令牌桶）
[[group]]
name = "eastmoney"
domains = ["push2.eastmoney.com", "datacenter-web.eastmoney.com", ...]  # 12 个子域
rps = 1.0              # 全局 ≤1 req/s
jitter = [0.1, 0.5]    # 随机抖动

[[group]]
name = "10jqka"
domains = ["basic.10jqka.com.cn", ...]  # 4 个子域
rps = 1.5

# 五档缓存 TTL
[cache]
P_ttl = 2592000        # 静态(研报/公告): 30天
L_ttl = 86400          # 季度(财报): 1天
S_ttl_afterclose = 43200  # 日级盘后: 12h
R_ttl = 0              # 实时: no-cache

# P/L 档磁盘持久化（重启恢复，避免冷启动重打外网）
[cache.persist]
enabled = true
dir = "cache"          # 相对包目录；生产用 --cache-dir 覆盖
tiers = ["P", "L"]     # 仅持久化这两档（S 盘中易脏、R/N 不缓存）
```

## 观测

```bash
# 计数器（请求数/缓存命中/限流等待/错误数/磁盘缓存）
curl -s http://127.0.0.1:7700/__stats | python3 -m json.tool
```

`/__stats` 返回字段：`cache`（内存缓存 size/hits/misses）、`disk_cache`（磁盘缓存 size/hits/misses，未启用为 null）、`disk_load_count`/`disk_load_ms`（启动时从磁盘回填的条目数与耗时）。

响应头 `X-Cache` 区分命中来源：`HIT-MEM`（内存命中）/ `HIT-DISK`（磁盘命中，已回填内存）/ `MISS`（打外网）。

## 验证

```bash
# 代理 + 缓存（两次请求，第二次应 X-Cache: HIT-MEM）
URL='https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
curl -s -D - -H "X-Cache-Tier: S" \
  "http://127.0.0.1:7700/?u=$URL&secid=1.600519&lmt=1&klt=101&fields1=f1&fields2=f51,f52" \
  -o /dev/null | grep X-Cache

# P 档磁盘持久化（重启后恢复）
URL='https://reportapi.eastmoney.com/report/list'
curl -s -D - -H "X-Cache-Tier: P" \
  "http://127.0.0.1:7700/?u=$URL&pageSize=1&industryCode=*&fields=f11" -o /dev/null | grep X-Cache  # MISS
# 重启网关后同请求 -> X-Cache: HIT-DISK；/__stats 可见 disk_load_count>0

# 并发限流（5并发不同标的，完成时刻应递增间隔≈1s）
uv run python -c "
import requests, concurrent.futures, time
t0=time.time()
def hit(i):
    requests.get('http://127.0.0.1:7700', params={'u':'https://push2.eastmoney.com/api/qt/slist/get','spt':3,'security_code':['600519','000858','601318','688017','002594'][i],'fields':'f12,f14'}, headers={'X-Cache-Tier':'R'}, timeout=30)
    return round(time.time()-t0,2)
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
    print('完成时刻:', sorted(ex.map(hit, range(5))))
"
```

## 技术选型

- 同步 Python + 标准库 `http.server`（零重型依赖，瓶颈是外网东财限到 1 req/s 而非网关并发）
- 透明代理（`?u=<原始URL>` 转发），agent 代码改动最小
- 详细设计见项目 `.agents/notes/gateway-design.md`

## 许可证

禁止商用（PolyForm Noncommercial），见项目根 [LICENSE](../../LICENSE)。
