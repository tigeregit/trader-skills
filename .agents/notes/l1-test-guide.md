# L1 pi agent 端到端测试指南

用 pi（ark-coding 模型）验证本项目 skill 在真实 agent 下的全链路：触发 → 路由 → asgk 取数 → 网关。每个步骤标注 ✅ 预期结果。

> 前置已就绪：pi 已装、ark-coding 已配（`~/.pi/agent/models.json`）、skill 软链指向本项目（`~/.pi/skills/a-stock-data`）、pi-parallel-agents 已装。

## 一、启动网关（终端 A，保持开着）

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts
uv run sgw-proxy --port 7700
```
✅ 看到 `[sgw_proxy] listening on 127.0.0.1:7700`

## 二、配置 .env（让子 agent 也能找到网关）

在 `skills/a-stock-data/scripts/` 下创建 `.env`（解决 pi-parallel-agents 子 agent 不继承环境变量的问题）：

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts
echo 'ASGK_GW=http://127.0.0.1:7700' > .env
```

后续 pi 在此目录树下跑，asgk 会自动从 `.env` 读到网关地址（环境变量优先级更高，但 `.env` 兜底）。

## 三、连通预检（终端 B）

确认 pi + ark-coding 可用（不依赖 skill）：

```bash
pi --provider ark-coding --model ark-coding/ark-code-latest --no-tools -p "回复：连通OK"
```
✅ 预期：`连通OK`（或类似确认回复）

## 四、单 agent 端到端（核心）

让 pi 加载 skill，实际用 asgk 取真实数据。**必须在 scripts 目录下**（pi 的 bash 工具要能 `uv run` asgk）：

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts
export ASGK_GW=http://127.0.0.1:7700
pi --provider ark-coding --model ark-coding/ark-code-latest \
   -p "用 a-stock-data skill 查贵州茅台(600519)的实时行情(PE/PB/市值)和最新研报标题。请实际写 Python 代码调用 asgk 库取真实数据，不要编造。网关已在 localhost:7700 运行。"
```
✅ 预期：
- pi 正确触发 skill（按 description 加载 SKILL.md）
- 行情用 `tencent_quote`（直连），研报用 `eastmoney_reports`（经网关）
- 返回真实数据（茅台 PE≈19.72、PB≈7.01、研报标题非空）

❌ 若 pi 说"无法导入 asgk" → 确认在 scripts 目录下、`uv sync` 已执行。

## 五、验证网关侧（确认请求走了网关）

回到终端 B 查网关计数：

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('东财外网请求:',d['group_reqs']['eastmoney'],'| 缓存命中:',d['cache']['hits'],'| 缓存大小:',d['cache']['size'])"
```
✅ 预期：`东财外网请求 > 0`（研报经网关），`缓存大小 > 0`（P 档存入）
❌ 若东财请求=0 → 请求没走网关，检查 ASGK_GW/.env 配置。

## 六、多 agent 并发（验证网关共享）

用 pi-parallel-agents 让多个 agent 并行查不同股票：

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts
export ASGK_GW=http://127.0.0.1:7700
pi --provider ark-coding --model ark-coding/ark-code-latest \
   -p "并行执行3个任务，每个用 asgk 的 eastmoney_reports 查研报数量：1.查000858(五粮液) 2.查601318(平安) 3.查000858(五粮液,验证缓存)。只返回每只研报条数。"
```
✅ 预期：3 个任务都返回研报条数（≈100）。
然后查网关：

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('东财外网请求:',d['group_reqs']['eastmoney'],'缓存命中:',d['cache']['hits'])"
```
✅ 预期：东财请求数应增加（新增 000858/601318），000858 重复查询应缓存命中。
❌ 若请求数未增 → 子 agent 没走网关（确认 `.env` 已创建且在 scripts 目录跑）。

## 七、清理

终端 A 按 `Ctrl+C` 关网关。清理 agent 生成的临时脚本和运行时日志：

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts
rm -f query_*.py sgw_fingerprint.jsonl
```

## 验收判定表

| 步骤 | 通过条件 | 失败含义 |
|------|---------|---------|
| 三 | pi 回复确认 | ark-coding 配置错 |
| **四** | **真实数据 + 正确分流（直连/网关）** | **skill 触发或 asgk 导入失败** |
| **五** | **东财请求 > 0** | **没走网关** |
| 六 | 3 任务返回 + 缓存命中 | 并发或 .env 配置问题 |

**第四、五步是关键**——验证本项目产物（SKILL.md + asgk 库 + 网关）在真实 agent 下全链路打通。

## 已知行为
- pi 可能自行生成 `query_*.py` 脚本（agent 的 bash 工具产物），测后清理。
- pi-parallel-agents 子 agent 不继承临时 `export`，必须用 `.env`（第二步）。
- ark-code-latest 是控制台动态切换别名，实际模型取决于控制台当前选择。
