# sgw 网关验收指南（uv 版）

P0 交付物（`skills/a-stock-data/scripts/`）的终端验收手册。每步标注 ✅ 预期结果，逐条核对即可判断是否通过。

> 所有命令基于 uv 管理。假设当前在仓库根目录 `trader-skills/`。

## 准备

```bash
cd skills/a-stock-data/scripts
uv sync          # 按 uv.lock 还原依赖与 venv（首次/clone 后必跑）
```

`uv sync` 无报错即环境就绪。若提示找不到 uv，先装：`curl -LsSf https://astral.sh/uv/install.sh | sh`。

## 一、单元自检（不需联网，先排错）

```bash
uv run python -c "from asgk import em_get; print('asgk OK:', em_get.__name__)"
```
✅ 预期：`asgk OK: em_get`

```bash
uv run python -c "import sgw_proxy as s; g=s.Gateway(s.load_config(s.DEFAULT_CONFIG)); print('组:', list(g.buckets)); print('东财域名数:', sum(1 for v in g.domain_group.values() if v=='eastmoney'))"
```
✅ 预期：`组: ['eastmoney', '10jqka']` 且 `东财域名数: 9`

## 二、启动网关

在**终端 A**启动（保持开着）：

```bash
uv run sgw-proxy --port 7700
```
✅ 预期输出：
```
[sgw_proxy] listening on 127.0.0.1:7700
[sgw_proxy] groups: ['eastmoney', '10jqka']
[sgw_proxy] fingerprint log: .../sgw_fingerprint.jsonl
```

后续命令在**终端 B**跑。

## 三、健康检查

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -m json.tool
```
✅ 预期：返回 JSON，`group_reqs` 全 0，`cache.size` 为 0。

## 四、代理 + 缓存（最直观）

```bash
URL='https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
# 第1次
curl -s -D - -H "X-Cache-Tier: S" \
  "http://127.0.0.1:7700/?u=$URL&secid=1.600519&lmt=1&klt=101&fields1=f1&fields2=f51,f52" \
  -o /dev/null | grep -i 'X-Cache'
# 第2次
curl -s -D - -H "X-Cache-Tier: S" \
  "http://127.0.0.1:7700/?u=$URL&secid=1.600519&lmt=1&klt=101&fields1=f1&fields2=f51,f52" \
  -o /dev/null | grep -i 'X-Cache'
```
✅ 预期：第一次 `X-Cache: MISS`，第二次 `X-Cache: HIT`。

想看真实数据内容，把任一条命令的 `-o /dev/null` 去掉，会看到茅台资金流 JSON。

## 五、并发限流（核心，必测）

这是 P0 灵魂——验证多并发被串行化到 ≤1 req/s。一次性发 5 个**不同标的**（避免缓存）：

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
    print('5并发完成时刻:', sorted(ex.map(hit, range(5))))
"
```
✅ 预期：完成时刻像 `[1.x, 2.x, 3.x, 4.x, 5.x]` 递增（间隔≈1s）。
❌ 若 5 个全 <0.1s 完成 → **限流失效**（最严重，需修）。

## 六、em_get 走网关（agent 实际调用方式）

```bash
ASGK_GW=http://127.0.0.1:7700 uv run python -c "
from asgk import em_get
r = em_get('https://push2.eastmoney.com/api/qt/slist/get',
           params={'spt':3,'security_code':'600519','fields':'f12,f14'}, tier='S', timeout=15)
print('status:', r.status_code, '| X-Cache:', r.headers.get('X-Cache'))
print('数据:', r.text[:80])
"
```
✅ 预期：`status: 200`，首次 `X-Cache: MISS`（再跑变 HIT），有真实 JSON 数据。

## 七、em_get 直连模式（向后兼容）

不设 `ASGK_GW` 时应直连上游（进程内限流）：

```bash
unset ASGK_GW  # 确保未设
uv run python -c "
from asgk import em_get
r = em_get('https://push2.eastmoney.com/api/qt/slist/get',
           params={'spt':3,'security_code':'600519','fields':'f12,f14'}, tier='S', timeout=15)
print('直连 status:', r.status_code, '| 无X-Cache头:', r.headers.get('X-Cache') is None)
"
```
✅ 预期：`status: 200`，`无X-Cache头: True`（直连不经网关，无此头）。

## 八、指纹日志（离线修正用）

```bash
tail -3 sgw_fingerprint.jsonl
```
✅ 预期：几条 jsonl，每条含 `key/tier/resp_hash/changed` 字段。

## 验收判定表

| 步骤 | 通过条件 | 失败含义 |
|------|---------|---------|
| 一 | 导入 OK、组数正确 | 环境或配置错 |
| 三 | JSON 无报错 | 网关没起来 |
| 四 | MISS→HIT | 缓存坏 |
| **五** | **完成时刻递增(间隔≈1s)** | **限流失效(最严重)** |
| 六 | 200 + 真实数据 + 走网关 | em_get 接口坏 |
| 七 | 200 + 无 X-Cache 头 | 直连兼容坏 |
| 八 | 有 jsonl 记录 | 指纹日志没开 |

**第五步是关键**——它验证 P0 解决的核心问题：单 IP 多 agent 并发，外网出口被串行化，不封 IP。

## 收尾

终端 A 按 `Ctrl+C` 关网关。清理运行时日志：

```bash
rm -f sgw_fingerprint.jsonl
```
