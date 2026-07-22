# L1 pi agent 端到端测试指南

用 pi（ark-coding 模型）验证本项目 skill 在真实 agent 下的全链路：触发 → 路由 → asgk 取数 → 网关。

## 设计

pi 的工作目录 = 启动时的 cwd（无 `--cwd` 选项）。为隔离测试环境，用独立工作目录：

| 组件 | 位置 | 加载方式 |
|------|------|---------|
| agent 工作目录 | 独立 temp 目录 | pi 启动时的 cwd |
| skill | `skills/a-stock-data` | `--skill` 显式指定（不依赖全局软链） |
| asgk 库 | `skills/a-stock-data/scripts` | 提示里告知路径，agent 用 `cd $SCRIPTS && uv run` |
| 网关地址 | 工作目录的 `.env` | asgk 自动加载（cwd 向上查找） |

> 前置：pi 已装、ark-coding 已配、pi-parallel-agents 已装（`pi install https://github.com/messense/pi-parallel-agents`）。

## 一、启动网关（终端 A，保持开着）

```bash
cd ~/Documents/trader-skills/skills/a-stock-data/scripts
uv run sgw-proxy --port 7700
```
✅ 看到 `[sgw_proxy] listening on 127.0.0.1:7700`

## 二、搭建独立工作目录（终端 B）

```bash
WORK=/tmp/pi-agent-work
SCRIPTS=~/Documents/trader-skills/skills/a-stock-data/scripts
SKILL=~/Documents/trader-skills/skills/a-stock-data

mkdir -p $WORK && cd $WORK
echo "ASGK_GW=http://127.0.0.1:7700" > .env
```

`.env` 让 asgk 自动找到网关（包括 pi-parallel-agents 的子 agent）。

## 三、连通预检

```bash
pi --provider ark-coding --model ark-coding/ark-code-latest --no-tools -p "回复：连通OK"
```
✅ 预期：`连通OK`

## 四、单 agent 端到端（核心）

在工作目录跑 pi，`--skill` 加载 skill，提示里告知 asgk 的 scripts 路径：

```bash
cd /tmp/pi-agent-work

SCRIPTS=~/Documents/trader-skills/skills/a-stock-data/scripts
pi --provider ark-coding --model ark-coding/ark-code-latest \
   --skill ~/Documents/trader-skills/skills/a-stock-data \
   -p "用 a-stock-data skill 查贵州茅台(600519)的实时行情(PE/PB/市值)和最新研报标题。
asgk 库在 $SCRIPTS 目录（uv 项目），执行代码时用 'cd $SCRIPTS && uv run python -c \"...\"'。
取真实数据，不要编造。"
```
✅ 预期：
- pi 触发 skill，按路由表选对函数（行情 `tencent_quote` 直连、研报 `eastmoney_reports` 经网关）
- 返回真实数据（茅台 PE≈19.72、PB≈7.01、研报标题非空）

❌ "无法导入 asgk" → agent 没用 `cd $SCRIPTS && uv run`，检查提示里的路径
❌ "ASGK_GW 未设置" → 确认 `.env` 在工作目录、网关在跑

## 五、验证网关

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('东财外网:',d['group_reqs']['eastmoney'],'缓存:',d['cache']['size'])"
```
✅ 预期：`东财外网 > 0`（研报经网关），`缓存 > 0`（P 档存入）

## 六、多 agent 并发

```bash
cd /tmp/pi-agent-work

SCRIPTS=~/Documents/trader-skills/skills/a-stock-data/scripts
pi --provider ark-coding --model ark-coding/ark-code-latest \
   --skill ~/Documents/trader-skills/skills/a-stock-data \
   -p "并行3任务用 asgk 的 eastmoney_reports 查研报数：1.000858(五粮液) 2.601318(平安) 3.000858(验证缓存)。
asgk 在 $SCRIPTS，用 'cd $SCRIPTS && uv run python -c \"...\"' 执行。只返回条数。"
```
✅ 预期：3 任务返回研报条数（≈100）。查网关缓存命中：

```bash
curl -s http://127.0.0.1:7700/__stats | python3 -c "import sys,json;d=json.load(sys.stdin);print('东财外网:',d['group_reqs']['eastmoney'],'缓存命中:',d['cache']['hits'])"
```

## 七、清理

```bash
# 终端 A: Ctrl+C 关网关
rm -rf /tmp/pi-agent-work
pkill -f sgw-proxy 2>/dev/null; pgrep -af sgw-proxy || echo "无残留 ✓"
```

## 验收判定

| 步骤 | 通过条件 | 失败含义 |
|------|---------|---------|
| 三 | pi 回复确认 | ark-coding 配置错 |
| **四** | **真实数据 + 正确分流** | **skill 加载/asgk 导入失败** |
| **五** | **东财外网 > 0** | **没走网关（查 .env）** |
| 六 | 3 任务返回 + 缓存命中 | 并发或 .env 问题 |

**第四、五步是关键**——验证本项目产物在真实 agent 下全链路打通。
