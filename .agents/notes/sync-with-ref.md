# 与上游 ref/a-stock-data 的同步

本文件记录 ref（上游蓝本）→ 本项目 skill 的转换关系，供上游更新时同步参照。

## 基本信息

| 项 | 值 |
|----|-----|
| 上游仓库 | https://github.com/simonlin1212/a-stock-data |
| 当前同步的 git hash | `9ed665cc9773457bc23fed6b770b2b5a8cede40f` |
| 上游版本 | V3.4.0（127KB SKILL.md / 10层 / 43端点 / 15数据源） |
| 同步日期 | 2026-07-23 |
| 本项目产物 | `skills/a-stock-data/`（SKILL.md 路由层 + references/ 12文件 + asgk 库 15模块43函数 + sgw 网关） |

## 转换目的

上游是**单 agent 单文件**形态（127KB SKILL.md 全量入上下文，进程内限流）。本项目面向**单 IP 下 100~1000 个 agent 并发**，需解决：

1. **Token 效率**：127KB → 路由层 92 行 + 按需加载 reference（降 80~90%）
2. **并发封 IP**：进程内限流 → 共享网关全局限流（≤1 req/s）
3. **代码复用**：内嵌 markdown → asgk 共享库（agent 只 import 调用）

详见 [design.md](design.md)。

## 转换方法（端点级映射）

上游每个端点 → 本项目一个 asgk 函数，按统一契约转换：

| 上游形态 | 本项目形态 | 位置 |
|---------|-----------|------|
| SKILL.md 内嵌代码块 | `asgk/asgk/<layer>.py` 函数 | `skills/a-stock-data/scripts/asgk/` |
| `em_get(url)` 进程内限流 | `em_get(url, tier=)` 经网关 | `asgk/em_proxy.py`（默认禁止直连） |
| 无档位 | `@source(tier="P/L/S/R/N")` 声明 | `asgk/_contract.py` |
| 返回 DataFrame/Response | 返回 `list[dict]`/`dict` | 各模块 |
| 10 层全量 SKILL.md | 12 个 reference 文件（按需加载） | `skills/a-stock-data/references/` |
| 单 SKILL.md 路由 | 92 行 SKILL.md 路由层 | `skills/a-stock-data/SKILL.md` |

### 模块对应表

| 上游 Layer | 上游端点数 | 本项目模块 | 函数数 |
|-----------|-----------|-----------|--------|
| L1 行情 | 5 | quote.py | 5 |
| L2 研报 | 3(+iwencai) | reports.py | 3 |
| L3 信号 | 8 | signal.py | 8 |
| L4 资金面 | 5 | capital.py | 5 |
| L5 新闻 | 3 | news.py | 3 |
| L6 基础数据 | 4 | base.py | 4 |
| L7 公告 | 1(+mootdx摘要) | announce.py | 1 |
| L8 打板 | 6 | limitup.py | 6 |
| L9 期权 | 3 | option.py | 3 |
| L10 舆情 | 4 | sentiment.py | 4 |
| 估值公式 | 4 | valuation.py | 4 |
| 共用 helper | - | _datacenter.py / _contract.py / em_proxy.py / client.py | - |

### 已校正的上游问题（P4 发现）

| 端点 | 上游问题 | 本项目校正 |
|------|---------|-----------|
| holder_num_change | reportName `RPT_HOLDERNUMLATEST` 返回融资融券字段 | 改 `RPT_F10_EH_HOLDERNUM` + 字段名 `HOLDER_TOTAL_NUM` |
| eastmoney_global_news | `sortEnd=""` 报 Required parameter | sortEnd 传当天日期 |
| mootdx_bars | mootdx 0.11.7 返回 0 条（库 bug） | 标注，日K 用 `baidu_kline_with_ma` 替代 |

## 上游更新时的同步流程

当上游 ref/a-stock-data 有新版本时：

```bash
# 1. 拉取上游更新
cd ref/a-stock-data && git pull origin main
cd ..

# 2. 查看变化
git -C ref/a-stock-data log 9ed665c..HEAD --oneline  # 本次 hash 见本文件"基本信息"
diff <(git -C ref/a-stock-data show 9ed665c:SKILL.md) ref/a-stock-data/SKILL.md | head -100

# 3. 逐项同步到本项目
#    - 新端点 → 新增 asgk 函数（按 asgk-contract.md 契约）+ reference 文件条目
#    - 接口修复 → 更新对应 asgk 函数
#    - 失效处理 → 更新 failover.md
```

### 同步检查清单

- [ ] 新增端点：加 asgk 函数 + @source 声明 + reference 条目 + SKILL.md 路由表
- [ ] 接口参数变更：更新 asgk 函数，smoke test 验证
- [ ] reportName/字段变更：参考"已校正"表，确认是否再失效
- [ ] 新增数据源域名：加到 `packages/sgw/sgw/config.toml` 对应限流组
- [ ] 上游版本号：更新本文件"基本信息"的 hash + 版本 + 日期

## 不同步的内容

- 上游的进程内限流（`_em_last_call`）——本项目用网关全局限流替代
- 上游的 pandas DataFrame 返回——本项目统一返回 list[dict]
- 上游的 V3.x 版本变更日志——只同步实质性的端点/接口变化
