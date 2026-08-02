# sgw — A股数据共享流量网关

单进程 HTTP 代理，供单 IP 下 100~1000 个 agent 并发共享。解决风控源（东财/同花顺）在高并发下的封 IP 问题。

## 它做什么

```
agent (×1000)                         外网
   │  风控源(东财/同花顺)               │
   └─► localhost:7700 (sgw) ──────────►  eastmoney.com / 10jqka.com.cn
            │  全局令牌桶限流(≤1 req/s)
            │  五档缓存(P/L/S/R/N)
            │  同请求合并 / 403、429立即熔断
```

- **全局限流**：按域名组令牌桶（东财组 ≤1 req/s、同花顺组独立），跨进程生效——无论多少 agent 进程并发，外网出口收敛到一个
- **端点级失败关闭**：每个允许访问的 host/path 都必须声明准入状态、IP 风险和响应作用域；未登记端点不出网
- **五档缓存**：静态数据(P档30天) / 日级(S档) / 实时(R档不缓存)，1000 agent 查同一票只打 1 次外网
- **并发 miss 合并**：同一缓存身份的并发调用只允许一个 leader 出网，R/N 档也会复用同一时刻的结果
- **家庭 IP 熔断**：首次 403/429 立即关闭整个来源，冷却期只读缓存，结束后仅放行一个 canary
- **熔断跨重启**：SQLite 主库保存各来源状态；canary 出网前持久化 120 秒探针租约，重启不能绕过熔断
- **状态库安全闩**：状态介质异常时只读缓存，按 10m/30m/1h/6h/12h/24h 探测存储恢复，不探测真实上游
- **凭据隔离**：公共响应不按 Cookie/CSRF/Referer 分裂缓存；敏感头和策略标注的敏感 query 不写缓存键、SQLite、指纹日志
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
# 默认（端口 7700，运行时文件写入包内 logs/cache/state）
uv run sgw-proxy

# 指定端口
uv run sgw-proxy --port 8080

# 生产环境：指定指纹日志目录（按天自动拆分 sgw_fp_YYYYMMDD.jsonl）
uv run sgw-proxy --fp-dir /var/log/sgw

# 生产环境：指定磁盘缓存目录（P/L 档持久化，sgw_cache.db）
uv run sgw-proxy --cache-dir /var/lib/sgw

# 生产环境：熔断状态必须写到独立、持久、权限受控的目录
uv run sgw-proxy --state-dir /var/lib/sgw/state

# 人工真实 canary：单次尝试，禁止网关自动重试
uv run sgw-proxy --max-attempts 1

# 指定配置文件
uv run sgw-proxy -c /path/to/config.toml
```

## systemd user 服务

Linux 上可用便利脚本把 sgw 安装为当前用户的 systemd 服务，不需要 root：

```bash
./scripts/sgw-service.sh install
./scripts/sgw-service.sh run
./scripts/sgw-service.sh status
./scripts/sgw-service.sh restart
./scripts/sgw-service.sh stop
./scripts/sgw-service.sh uninstall
```

`install` 使用 `uv tool install` 安装独立的 `sgw-proxy`，生成并 enable
`~/.config/systemd/user/sgw.service`，但不会自动启动。`uninstall` 会停止服务、移除
unit 并卸载 uv tool，但保留指纹日志、缓存和熔断状态，避免误删运行数据。

安装后也可以绕过脚本，直接使用标准 systemd 命令：

```bash
systemctl --user start sgw.service
systemctl --user stop sgw.service
systemctl --user restart sgw.service
systemctl --user status sgw.service
journalctl --user-unit sgw.service -f
```

默认监听 `127.0.0.1:7700`。安装时可通过环境变量覆盖：

```bash
SGW_PORT=8080 ./scripts/sgw-service.sh install
SGW_FP_DIR=/data/sgw/logs \
SGW_CACHE_DIR=/data/sgw/cache \
SGW_STATE_DIR=/data/sgw/state \
./scripts/sgw-service.sh install
```

默认运行目录遵循 XDG：指纹和熔断状态位于
`${XDG_STATE_HOME:-$HOME/.local/state}/sgw`，磁盘缓存位于
`${XDG_CACHE_HOME:-$HOME/.cache}/sgw`。如需退出登录后仍运行，管理员需为该用户启用
linger：`loginctl enable-linger <user>`。macOS 没有 systemd，脚本的服务管理命令会
明确拒绝执行；可继续使用前述 `uv run sgw-proxy` 前台方式。

## agent 侧配置

agent 通过 `ASGK_GW` 环境变量找到网关（asgk 库自动读取）：

```bash
# 方式A：环境变量（部署用）
export ASGK_GW=http://127.0.0.1:7700

# 方式B：.env 文件（开发用，asgk 从 cwd 自动加载）
echo 'ASGK_GW=http://127.0.0.1:7700' > .env
```

> 未设 `ASGK_GW` 时，asgk 的 `em_get` 会抛异常。不存在风控源直连 fallback；网关不可用时必须失败关闭。

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

# 端点策略；未匹配的 host/path 默认 unknown 并拒绝
[[endpoint]]
name = "eastmoney-report-list"
host = "reportapi.eastmoney.com"
path = "/report/list"
review_status = "approved"
ip_risk = "controlled"
response_scope = "public"
credential_mode = "none"
cache_mode = "shared"

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

[circuit]
cooldown_seconds = 300
failure_threshold = 3
probe_lease_seconds = 120

[state]
enabled = true
dir = "state"          # 生产用 --state-dir 覆盖
backoff_seconds = [600, 1800, 3600, 21600, 43200, 86400]
```

## 观测

```bash
# 计数器（请求数/缓存命中/限流等待/错误数/磁盘缓存）
curl -s http://127.0.0.1:7700/__stats | python3 -m json.tool
```

`/__stats` 还返回 `singleflight_followers`、各来源的 `circuits` 和
`state_safety`（异常起止时间、退避档、下次存储探测时间），以及内存/磁盘缓存、
限流等待和错误计数。状态文件只保存组名、截止时间、计数和状态码，不保存 URL、
Cookie 或 CSRF。

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

# 1000 并发、熔断和凭据验证只使用 mock 上游，禁止用家庭 IP 做真实压测
uv run pytest tests/test_policy.py tests/test_endpoint_inventory.py \
  tests/test_circuit_state.py -q
```

`test_endpoint_inventory.py` 静态扫描 asgk 内所有 `em_get` URL，必须与
approved 端点政策双向完整对应。同一检查会在 GitHub Actions 中自动运行。

## 技术选型

- 同步 Python + 标准库 `http.server`（零重型依赖，瓶颈是外网东财限到 1 req/s 而非网关并发）
- 透明代理（`?u=<原始URL>` 转发），agent 代码改动最小
- 详细设计见项目 `.agents/notes/gateway-design.md`

## 许可证

禁止商用（PolyForm Noncommercial），见项目根 [LICENSE](../../LICENSE)。
