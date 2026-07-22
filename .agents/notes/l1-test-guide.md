# L1 pi agent 端到端测试指南

用 pi（ark-coding 模型）验证本项目 skill 在真实 agent 下的全链路：触发 → 路由 → asgk 取数 → 网关。

## 设计

- **工作目录** = `skills/a-stock-data/scripts/`（asgk 的 uv 项目根，pi 的 bash 工具能 `uv run` 执行 asgk）
- **skill 加载** = `--skill` 显式指定（指向 `skills/a-stock-data`，不依赖全局 `~/.pi/skills/`，项目隔离）
- **网关地址** = `.env` 文件（asgk 自动加载，子 agent 也能继承）

> 前置：pi 已装、ark-coding 已配（`~/.pi/agent/models.json`）、pi-parallel-agents 已装（`pi install https://github.com/messense/pi-parallel-agents`）。

## 一、启动网关（终端 A，保持开着）

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts
uv run sgw-proxy --port 7700
```
✅ 看到 `[sgw_proxy] listening on 127.0.0.1:7700`

## 二、配置 .env（让 asgk 和子 agent 找到网关）

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts
echo 'ASGK_GW=http://127.0.0.1:7700' > .env
```

这一步关键：pi-parallel-agents 的子 agent 不继承临时 `export`，但 asgk 会从 cwd 的 `.env` 自动加载。环境变量优先级更高（部署用），`.env` 兜底（开发用）。

## 三、连通预检（终端 B）

```bash
pi --provider ark-coding --model ark-coding/ark-code-latest --no-tools -p "回复：连通OK"
```
✅ 预期：`连通OK`

## 四、单 agent 端到端（核心）

`--skill` 显式加载本项目 skill，在 scripts 目录跑（asgk 能 import）：

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts

SKILL_PATH=~/Documents/trader-skills/skills/a-stock-data
pi --provider ark-coding --model ark-coding/ark-code-latest \
   --skill "$SKILL_PATH" \
   -p "用 a-stock-data skill 查贵州茅台(600519)的实时行情(PE/PB/市值)和最新研报标题。请实际写 Python 代码调用 asgk 库取真实数据，不要编造。"
```
✅ 预期：
- pi 触发 skill（按 description 加载 SKILL.md）
- 行情用 `tencent_quote`（直连），研报用 `eastmoney_reports`（经网关）
- 返回真实数据（茅台 PE≈19.72、PB≈7.01、研报标题非空）

❌ 若 "无法导入 asgk" → 确认在 scripts 目录、`uv sync` 已执行。
❌ 若 "ASGK_GW 未设置" → 确认第二步 `.env` 已创建。

## 五、验证网关侧

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('东财外网:',d['group_reqs']['eastmoney'],'缓存:',d['cache']['size'])"
```
✅ 预期：`东财外网 > 0`（研报经网关），`缓存 > 0`（P 档存入）
❌ 若东财=0 → 没走网关，查 `.env`。

## 六、多 agent 并发（验证网关共享）

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts

SKILL_PATH=~/Documents/trader-skills/skills/a-stock-data
pi --provider ark-coding --model ark-coding/ark-code-latest \
   --skill "$SKILL_PATH" \
   -p "并行3任务用 asgk 的 eastmoney_reports 查研报数：1.000858(五粮液) 2.601318(平安) 3.000858(验证缓存)。只返回条数。"
```
✅ 预期：3 任务返回研报条数（≈100）。查网关确认缓存命中：

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('东财外网:',d['group_reqs']['eastmoney'],'缓存命中:',d['cache']['hits'])"
```
✅ 预期：000858 重复查询缓存命中。

## 七、清理

终端 A 按 `Ctrl+C` 关网关。清理临时产物：

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts
rm -f query_*.py sgw_fingerprint.jsonl
```

确保无残留进程：

```bash
pkill -f sgw-proxy 2>/dev/null; pgrep -af sgw-proxy || echo "无残留 ✓"
```

## 验收判定表

| 步骤 | 通过条件 | 失败含义 |
|------|---------|---------|
| 三 | pi 回复确认 | ark-coding 配置错 |
| **四** | **真实数据 + 正确分流（直连/网关）** | **skill 加载或 asgk 导入失败** |
| **五** | **东财外网 > 0** | **没走网关（查 .env）** |
| 六 | 3 任务返回 + 缓存命中 | 并发或 .env 问题 |

**第四、五步是关键**——验证本项目产物（SKILL.md + asgk + 网关）在真实 agent 下全链路打通。

## 关键设计说明

- **为何用 `--skill` 而非全局软链**：项目隔离，不同测试/项目不互相干扰；显式指定路径明确。
- **为何工作目录是 scripts/**：asgk 是 uv 项目，pi 的 bash 工具执行 `uv run python` 需在项目根。.env 也放这里让 asgk 自动加载。
- **`--skill` 路径指向 `skills/a-stock-data`**（SKILL.md 所在），pi 按 frontmatter 的 name+description 发现并加载。
