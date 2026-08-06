# 交易时序（time）

交易前必须确认：当前时间、是否交易日、是否在可交易时段。`time` 大类 4 个子命令覆盖这些判断。

## 子命令

### now — 当前日期时间

```bash
asgk time now
```

返回当前本地时间，含星期几、是否周末。**纯本地计算，不调服务端。**

字段：`datetime` / `date` / `time` / `weekday`(中文) / `weekday_num`(0=周一) / `is_weekend`

### trade_day — 是否交易日

```bash
asgk time trade_day              # 判定今天
asgk time trade_day 2026-08-04   # 判定指定日期（YYYY-MM-DD）
```

经服务端交易日历判定（mootdx 上证指数日K反推，含节假日剔除，缓存 30 天）。

返回字段：
- `is_trade_day`: true/false/null（null=无法判定）
- `tentative`: true 时表示「今日尚未收盘，按工作日判定」（节假日待确认）
- `note`: 判定依据（交易日/节假日/周末/今日尚未收盘/未来日期不支持）
- `latest_known`: 交易日历覆盖到的最新日期

**判定逻辑**：
- 历史日期在交易日历里 → 确定 true
- 历史日期不在历且是周末 → 确定 false（周末）
- 历史日期不在历且是工作日 → 确定 false（节假日，如国庆/春节）
- 今天是工作日但K线尚未产生（盘前/盘中）→ tentative true（按工作日兜底）
- 未来日期（晚于今天）→ null，不支持（无法预知未来节假日）

**数据范围**：仅支持历史 + 今天。未来日期不支持（免费源无可靠节假日通知）。

### trade_session — 当前是否交易时段

```bash
asgk time trade_session
```

判定当前时刻处于哪个时段。**纯本地计算，不判节假日**（用 trade_day 判节假日）。

A股时段（周一~周五）：
| session | 时段 | is_tradable |
|---|---|---|
| `pre_open` | 09:15-09:25 盘前集合竞价 | false |
| `morning` | 09:25-11:30 上午连续竞价 | **true** |
| `midday` | 11:30-13:00 午间休市 | false |
| `afternoon` | 13:00-15:00 下午连续竞价 | **true** |
| `closed` | 其余（夜间/周末） | false |

`is_tradable=true` 当且仅当 morning/afternoon（连续竞价可成交）。

### status — 合并状态

```bash
asgk time status
```

一次性返回 now + trade_session + trade_day 的合并结果，适合交易前一次 check。

字段：`datetime` / `date` / `weekday` / `is_weekend` / `session` / `is_tradable` / `is_trade_day` / `trade_day_note`

## 示例

```bash
# 盘前 check：今天能交易吗？
asgk time status --format json
# {date, weekday, session: "pre_open", is_tradable: false,
#  is_trade_day: true (tentative), ...}

# 查某天是否交易日（历史回测用）
asgk time trade_day 2026-01-01   # 元旦 → false, note: 节假日
asgk time trade_day 2026-08-04   # → true

# 当前能否下单（需 is_tradable=true 且 is_trade_day=true）
asgk time status --format json | grep -E "is_tradable|is_trade_day"
```

## 注意

- `trade_day` 经服务端（mootdx TCP 出网），首次调用稍慢；后续命中 30 天缓存。
- `now`/`trade_session` 纯本地计算，即时返回，不依赖服务端。
- `status` 会调服务端拿 trade_day；服务端不可达时仍返回时间+时段，trade_day 标未判定。
- 交易日历基于上证指数日K（K线只在交易日产生），准确覆盖历史节假日；未来日期无法判定。
