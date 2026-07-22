# reports.py 测试指导

第一个经网关的业务模块（东财研报）的验收手册。重点验证**全链路**：`@source → em_get(tier=P) → 网关限流+缓存 → 结构化数据`。

> 前置：在 `skills/a-stock-data/scripts/` 目录下操作。网关用单独终端启动。

## 一、启动网关（终端 A，保持开着）

```bash
cd skills/a-stock-data/scripts
uv run sgw-proxy --port 7700
```
✅ 看到 `listening on 127.0.0.1:7700`。

后续命令在**终端 B**，先设环境变量让 asgk 走网关：

```bash
export ASGK_GW=http://127.0.0.1:7700
cd skills/a-stock-data/scripts
```

## 二、导入 + @source 元数据

验证模块可导入，且 @source 声明的档位/数据源正确：

```bash
uv run python -c "
import asgk
from asgk._contract import registry
print('已注册:', [m.name for m in registry()])
m = asgk.eastmoney_reports._asgk_meta
print(f'eastmoney_reports: tier={m.tier} via={m.via} cli={m.cli}')
"
```
✅ 预期：
```
已注册: ['reports.eastmoney_reports', 'reports.eastmoney_industry_reports']
eastmoney_reports: tier=P via=gateway cli=report
```

## 三、个股研报：真实取数 + 字段校验

经网关取茅台研报，验证返回结构化数据、业务字段非空：

```bash
uv run python -c "
import asgk
reports = asgk.eastmoney_reports('600519', max_pages=1)
print(f'条数: {len(reports)}')
r = reports[0]
print(f\"标题: {r.get('title','')[:40]}\")
print(f\"机构: {r.get('orgSName')}\")
print(f\"评级: {r.get('emRatingName')}\")
print(f\"今年EPS预测: {r.get('predictThisYearEps')}\")
"
```
✅ 预期：条数 100，标题/机构/评级/EPS预测 均有值（如「飞天茅台年内二次提价...」/「群益证券」/「持有」/「68.91」）。

## 四、行业研报

```bash
uv run python -c "
import asgk
reports = asgk.eastmoney_industry_reports('*', max_pages=1)
print(f'条数: {len(reports)}')
r = reports[0]
print(f\"行业: {r.get('industryName')} (码 {r.get('industryCode')})\")
print(f\"标题: {r.get('title','')[:40]}\")
"
```
✅ 预期：条数 100，industryName 非空（如「基础建设」）。

## 五、P 档缓存（核心）

P 档（研报发布即定稿）应 30 天缓存。连续取两次同一只票，第二次应命中缓存——**外网只打 1 次**：

```bash
uv run python -c "
import asgk
r1 = asgk.eastmoney_reports('000858', max_pages=1)  # 五粮液
r2 = asgk.eastmoney_reports('000858', max_pages=1)  # 同参数,应缓存命中
print(f'两次条数一致: {len(r1)==len(r2)}')
print('→ 第二次应瞬间返回(缓存命中,零外网)')
"
```
✅ 预期：条数一致，第二次明显更快（命中缓存）。

**确认外网次数**——回终端 A 看网关日志，或另开查询：

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('东财外网请求:',d['group_reqs']['eastmoney'],'缓存命中:',d['cache']['hits'])"
```
✅ 预期：`缓存命中` ≥1（第二次取命中）；`东财外网请求` 应明显少于总调用次数。

## 六、不经网关的对照（可选）

不设 ASGK_GW 时应直连（向后兼容）：

```bash
unset ASGK_GW
uv run python -c "
import asgk
reports = asgk.eastmoney_reports('600519', max_pages=1)
print(f'直连条数: {len(reports)} (应与经网关一致)')
"
export ASGK_GW=http://127.0.0.1:7700  # 测完恢复
```
✅ 预期：条数一致（经网关 = 直连，数据无损）。

## 验收判定表

| 步骤 | 通过条件 | 失败含义 |
|------|---------|---------|
| 二 | 注册2函数，tier=P/via=gateway | @source 声明错 |
| 三 | 条数>0，字段非空 | 取数或解析坏 |
| 四 | 行业研报 industryName 非空 | 行业研报解析坏 |
| **五** | **第二次缓存命中，外网只1次** | **P档缓存没生效** |
| 六 | 直连条数一致 | 数据不一致 |

**第五步是关键**——它验证 P 档缓存生效（研报静态数据 30 天缓存，1000 agent 查同一票只打 1 次外网）。

## 收尾

终端 A 按 `Ctrl+C` 关网关，`rm -f sgw_fingerprint.jsonl` 清理。
