# 测试操作手册

本项目测试的操作指南。方法论见 `test-method.md`（pi + locust 双层策略），本文件是可直接执行的命令手册。

> 所有命令基于 uv。前置：`cd skills/a-stock-data/scripts && uv sync`。

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
cd skills/a-stock-data/scripts
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
✅ `MISS` → `HIT`

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
unset ASGK_GW ASGK_ALLOW_DIRECT
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

```bash
uv run python -c "
import requests, concurrent.futures
GW='http://127.0.0.1:7700'
RPT='https://reportapi.eastmoney.com/report/list'
P={'industryCode':'*','pageSize':'3','industry':'*','rating':'*','ratingChange':'*','beginTime':'2000-01-01','endTime':'2030-01-01','pageNo':'1','qType':'0','code':'600519','p':'1','pageNum':'1','pageNumber':'1','fields':''}
H={'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/'}
def direct(): return requests.get(RPT, params=P, headers=H, timeout=15).content
def via_gw(): return requests.get(GW, params={'u':RPT,**P}, headers={'X-Cache-Tier':'R',**H}, timeout=15).content
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
    fd, fg = ex.submit(direct), ex.submit(via_gw)
    d, g = fd.result(), fg.result()
print(f'静态研报: 直连{len(d)}B 网关{len(g)}B 一致={d==g}')
"
```
✅ `一致=True`

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
rm -rf ~/Documents/trader-skills/skills/a-stock-data/scripts/sgw/sgw/logs   # 测试产生的指纹日志
pkill -f sgw-proxy 2>/dev/null; pgrep -af sgw-proxy || echo "无残留 ✓"
```

---

## 验收判定

| 部分 | 关键步骤 | 通过条件 |
|------|---------|---------|
| A 网关 | A5 限流 | 完成时刻递增（间隔≈1s） |
| A 网关 | A6 禁止直连 | 未设网关时抛异常 |
| A 网关 | A7 透明性 | 经网关=直连，字节一致 |
| **B agent** | **B3 端到端** | **真实数据 + 正确分流** |
| **B agent** | **B4 网关** | **东财外网 > 0** |
