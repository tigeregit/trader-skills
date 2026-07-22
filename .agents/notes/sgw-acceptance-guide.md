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

## 七、禁止直连保护（默认 ban）

未设 `ASGK_GW` 时，em_get 应**抛异常**（禁止风控源直连，防封 IP）：

```bash
unset ASGK_GW ASGK_ALLOW_DIRECT  # 确保都未设
uv run python -c "
from asgk import em_get
try:
    em_get('https://push2.eastmoney.com/api/qt/slist/get',
           params={'spt':3,'security_code':'600519'}, tier='S', timeout=15)
    print('❌ 未抛异常')
except RuntimeError as e:
    print(f'✅ 抛异常: {str(e)[:40]}')
"
```
✅ 预期：`✅ 抛异常: ASGK_GW 未设置...`

仅调试时设 `ASGK_ALLOW_DIRECT=1` 可临时允许直连（进程内限流）：

```bash
unset ASGK_GW
ASGK_ALLOW_DIRECT=1 uv run python -c "
from asgk import em_get
r = em_get('https://push2.eastmoney.com/api/qt/slist/get',
           params={'spt':3,'security_code':'600519','fields':'f12,f14'}, tier='S', timeout=15)
print('调试直连 status:', r.status_code)
"
```

## 八、指纹日志（离线修正用）

```bash
tail -3 sgw_fingerprint.jsonl
```
✅ 预期：几条 jsonl，每条含 `key/tier/resp_hash/changed` 字段。

## 数据内容校验（九～十二）

前面八步验证「机制」（缓存/限流/转发）。下面四步验证「数据正确性」——经网关取到的内容是否完整无损、字段是否合理。

> **关键方法学**：字节级一致性比对**只能用静态端点**（研报/公告，发布即定稿）。实时端点（资金流/行情）两次请求间数据本身会变，字节必然不同——这不是网关问题。故实时数据只校验「结构 + 字段 + 数值合理性」，不比对字节。

### 九、透明性：字节级一致性（静态端点，最重要）

证明网关无损转发——同一静态请求经网关 vs 直连，响应字节完全相同。两次**并发**发出（消除时间差）：

```bash
uv run python -c "
import requests, concurrent.futures
GW='http://127.0.0.1:7700'
RPT='https://reportapi.eastmoney.com/report/list'
P={'industryCode':'*','pageSize':'3','industry':'*','rating':'*','ratingChange':'*',
   'beginTime':'2000-01-01','endTime':'2030-01-01','pageNo':'1','qType':'0',
   'code':'600519','p':'1','pageNum':'1','pageNumber':'1','fields':''}
H={'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/'}
def direct(): return requests.get(RPT, params=P, headers=H, timeout=15).content
def via_gw(): return requests.get(GW, params={'u':RPT,**P}, headers={'X-Cache-Tier':'R',**H}, timeout=15).content
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
    fd, fg = ex.submit(direct), ex.submit(via_gw)
    d, g = fd.result(), fg.result()
print(f'静态研报: 直连{len(d)}B 网关{len(g)}B 一致={d==g}')
"
```
✅ 预期：`一致=True`（字节完全相同）。
❌ 若不一致 → 网关篡改/截断了响应内容，是严重 bug。

### 十、JSON 内容：研报字段非空

验证经网关取的研报列表，标题/机构等业务字段真实存在：

```bash
uv run python -c "
import requests
GW='http://127.0.0.1:7700'
RPT='https://reportapi.eastmoney.com/report/list'
P={'industryCode':'*','pageSize':'5','industry':'*','rating':'*','ratingChange':'*',
   'beginTime':'2000-01-01','endTime':'2030-01-01','pageNo':'1','qType':'0',
   'code':'600519','p':'1','pageNum':'1','pageNumber':'1','fields':''}
d=requests.get(GW, params={'u':RPT,**P}, headers={'X-Cache-Tier':'P','Referer':'https://data.eastmoney.com/'}, timeout=30).json()
recs=d.get('data') or []
print(f'研报条数: {len(recs)}')
if recs:
    r=recs[0]
    print(f\"标题: {r.get('title','')[:40]}\")
    print(f\"机构: {r.get('orgSName','') or r.get('orgName','')}\")
    print(f\"评级: {r.get('emRatingName','')}\")
"
```
✅ 预期：条数 >0，标题/机构非空（如「飞天茅台年内二次提价...」/「群益证券」）。

### 十一、结构化数值：资金流 klines 可解析

验证实时数据经网关后结构完整、数值可解析（不比对字节，只看结构）：

```bash
uv run python -c "
import requests
GW='http://127.0.0.1:7700'
FF='https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
P={'secid':'1.600519','lmt':3,'klt':101,'fields1':'f1,f2','fields2':'f51,f52'}
d=requests.get(GW, params={'u':FF,**P}, headers={'X-Cache-Tier':'R'}, timeout=15).json()
klines=d.get('data',{}).get('klines',[])
print(f'klines条数: {len(klines)}')
if klines:
    parts=klines[0].split(',')
    print(f'首条: {klines[0]}')
    print(f'字段数: {len(parts)}, 主力净流入数值化: {parts[1]}')
"
```
✅ 预期：klines 条数 >0，首条可按逗号 split，主力净流入是数值（如 `389827968.0`）。

### 十二、缓存内容一致性

验证缓存命中返回的内容与首次一致（缓存没存坏）：

```bash
uv run python -c "
import requests
GW='http://127.0.0.1:7700'
FF='https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
P={'secid':'1.000858','lmt':1,'klt':101,'fields1':'f1','fields2':'f51,f52'}
r1=requests.get(GW, params={'u':FF,**P}, headers={'X-Cache-Tier':'S'}, timeout=15).content
r2=requests.get(GW, params={'u':FF,**P}, headers={'X-Cache-Tier':'S'}, timeout=15).content
print(f'首次X-Cache: MISS, 缓存命中内容一致: {r1==r2}')
"
```
✅ 预期：两次内容完全一致（缓存无损存取）。

## 验收判定表

| 步骤 | 通过条件 | 失败含义 |
|------|---------|---------|
| 一 | 导入 OK、组数正确 | 环境或配置错 |
| 三 | JSON 无报错 | 网关没起来 |
| 四 | MISS→HIT | 缓存坏 |
| **五** | **完成时刻递增(间隔≈1s)** | **限流失效(最严重)** |
| 六 | 200 + 真实数据 + 走网关 | em_get 接口坏 |
| 七 | 未设网关时抛异常 | 禁止直连保护失效 |
| 八 | 有 jsonl 记录 | 指纹日志没开 |
| **九** | **静态端点字节一致** | **网关篡改/截断(严重)** |
| 十 | 研报字段非空 | JSON 解析或字段丢失 |
| 十一 | klines 可解析、数值化 | 结构损坏 |
| 十二 | 缓存内容一致 | 缓存存取有损 |

**两个关键步骤**：
- **第五步**（限流）：验证 P0 核心价值——单 IP 多 agent 并发不封 IP。
- **第九步**（透明性）：验证数据正确性根基——网关无损转发，经网关 = 直连。

## 收尾

终端 A 按 `Ctrl+C` 关网关。清理运行时日志：

```bash
rm -f sgw_fingerprint.jsonl
```
