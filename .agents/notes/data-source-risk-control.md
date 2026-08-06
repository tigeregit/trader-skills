# 数据源 IP 风控实测与分流前提复核

本文件记录 2026-08-06 对 asgk 全部「直连源」做的网络资料核实结论。

> **核心结论**：asgk 原标注 `via="direct"` 的 18 个函数所访问的源，**在网络资料中
> 没有任何一个被证实「100~1000 agent 并发直连安全」**。多处代码注释（如
> 「通达信不封 IP」「腾讯/百度均不封 IP」「hexin.cn 不封 IP」）是把
> 「单发低频可用」误当成了「无风控」，与外部证据矛盾。本结论动摇了
> `AGENTS.md §2`「腾讯/百度/新浪/mootdx 直连」的分流前提。

## 一、为什么要复核

项目目标场景是「单 IP 下 100~1000 agent 并发」（`AGENTS.md §2`）。原有分流策略是：

- 东财(12子域)/同花顺(4子域) → 经 sgw 网关（限流+缓存）
- 腾讯/百度/新浪/mootdx(TCP) → **直连**（注释声称「不封 IP」）

「不封 IP」的判定**没有统一来源**：部分是代码注释自述，部分是行业经验，只有
乐咕/深交所在 `akshare-merge-design.md §7 决策10` 做过单发（间隔 10s、2~3 次）
保守验证，**从未做并发验证**。本次用网络资料（akshare/efinance 社区 issue、
CSDN/知乎/腾讯云实战文、curl_cffi 官方 FAQ、反爬攻略）交叉核实每个直连源。

## 二、逐源核实结论

| 函数 | 源(域名) | 实际风控 | 社区安全水位 | 100~1000并发 | 原注释 | 评级 |
|------|----------|---------|-------------|------------|--------|------|
| tencent_quote | qt.gtimg.cn | WAF + IP封 | ~5 req/s | 超20倍 | "不封IP"❌ | 🔴高 |
| full_valuation | qt.gtimg.cn | 同上 | 同上 | 同上 | "直连"❌ | 🔴高 |
| baidu_kline_with_ma | finance.pae.baidu.com | TLS指纹 + IP频率 | 个位数QPS | 超数十倍 | "直连"❌ | 🔴高 |
| mootdx_bars/quotes/transaction | mootdx TCP 7709 | 连接数+频率+IP黑名单 | 个位数连接 | 远超 | "不封IP"❌ | 🔴高 |
| mootdx_f10/finance | mootdx TCP 7709 | 同上 | 同上 | 远超 | "直连"❌ | 🔴高 |
| sina_financial_report | quotes.sina.cn | IP频率 + Referer强校验 | ≤1 req/s | 超数十倍 | "直连"❌ | 🔴高 |
| sina_option_*(3个) | hq.sinajs.cn | IP频率 + Referer | ≤1 req/s | 超数十倍 | "直连"❌ | 🔴高 |
| cninfo_announcements | cninfo.com.cn | WAF + mcode AES签名 | ≤2并发/3s | 远超 | "不在风控组"❌ | 🔴高 |
| cninfo_irm | irm.cninfo.com.cn | WAF + 登录态 | ≤2并发 | 远超 | "直连"❌ | 🔴高 |
| cls_telegraph | cls.cn | sign签名 + 软风控返空 | 1req/10s | 超 | "直连零key"⚠️ | 🟡中 |
| market_pe_lg/pb_lg | legulegu.com | token+CSRF(并发未知) | 仅单发验证 | 未知 | "验证无风控"⚠️ | 🟡未知 |
| hsgt_realtime | data.hexin.cn | **同花顺共用风控(hexin-v)** | ~5次/cookie | 远超 | "不封IP"❌❌ | 🔴最高危(已修) |

评级说明：🔴高 = 资料明确有IP风控且并发必触顶；🟡中 = 有软风控/签名但封IP证据弱；
🟡未知 = 无并发数据，保守视为有风险。

## 三、三个被推翻的认知错误

### 错误1：「私有TCP协议 = 无IP风控」是伪命题（mootdx 5个函数）

`client.py:3` 注释「通达信 TCP(7709) 不封 IP」。实际：TCP 只免疫了 HTTP/WAF
应用层反爬（UA/Cookie/JS挑战），但通达信服务端确有：
- **单 IP 连接数限制**（社区实测阈值个位数，[CSDN问答](https://ask.csdn.net/questions/9713804)）
- **频率风控**（高频主动断开，需 60-120s 间隔）
- **IP 黑名单**（mootdx 默认连接池仅 10，海外 IP 全超时）

100~1000 agent 直连单台服务器会秒级触顶，被封 30-60 分钟。

### 错误2：「hexin.cn 不封 IP」是错误注释，且自相矛盾（hsgt_realtime）✅已修

`signal.py` 原注释「data.hexin.cn 直连不封 IP」，但 `gateway-design.md` 又把
同花顺 4 子域列为强风控源。核实：**hexin.cn 与 10jqka.com.cn 同属同花顺系、
共用 hexin-v 风控**（"hexin" = 母公司"核新"）。一处被封会连累
`zx.10jqka.com.cn`(热点)等**成片失联**。
（[反爬攻略](https://pathsoflight.org/writing/cn-finance-anti-scraping-guide)、
[实测约5页封IP](https://blog.csdn.net/CY19980216/article/details/86647597)）

**已于 2026-08-06 修复**：`hsgt_realtime` 改经网关，data.hexin.cn 归入 10jqka
限流组（config.toml），`.hexin.cn` 加入 PROXIED_DOMAIN_SUFFIXES。

### 错误3：「腾讯/百度不封IP」是断言，无验证（tencent/baidu）

社区多来源证实两者都有 WAF/IP风控，安全水位约 5 req/s。
（[腾讯云](https://cloud.tencent.com/developer/article/2659683)、
[curl_cffi官方FAQ](https://github.com/lexiforest/curl_cffi)：
「指纹只是众多因素之一，IP频率限制独立生效」）

## 四、证据来源（多源交叉验证）

| 源 | 关键证据 |
|----|---------|
| 腾讯 | [腾讯云](https://cloud.tencent.com/developer/article/2659683)「频繁请求封IP」；[知乎](https://zhuanlan.zhihu.com/p/1938025669129929523)「频率不能太高否则封IP」 |
| 百度 | [curl_cffi FAQ](https://github.com/lexiforest/curl_cffi)「TLS指纹≠无IP限制」；[akshare#6100](https://github.com/akfamily/akshare/issues/6100)「高并发IP被拉黑」 |
| 通达信 | [CSDN问答](https://ask.csdn.net/questions/9713804)「同一IP连接数超限被拒绝」；mootdx默认连接池仅10 |
| 新浪 | [akshare#5762](https://github.com/akfamily/akshare/issues/5762)「需sleep(4)才稳定」；hq.sinajs.cn 2019起强制Referer |
| 巨潮 | [知乎](https://zhuanlan.zhihu.com/p/636171614)「hisAnnouncement需mcode AES签名」；社区共识≤2并发/3s |
| 同花顺hexin | [pathsoflight](https://pathsoflight.org/writing/cn-finance-anti-scraping-guide)「三家中最严」；与10jqka共用hexin-v |
| 财联社 | v1接口已上sign签名校验(md5(sha1))；旧nodeapi接口已下线(收紧信号) |

## 五、对项目架构的影响与现状

本次复核后，asgk 直连源从 18 个降为 17 个（hsgt_realtime 已改经网关）。
**剩余 17 个仍标注 `via="direct"` 的源在 100~1000 并发场景下全部有风险**，
但本次按决策「先记录不改代码」保留现状，待实际并发量临近时再改造。

### 已修复

- `hsgt_realtime`（data.hexin.cn）：改经网关，归入 10jqka 限流组。commit 见 git log。

### 已迁移经网关（2026-08-06，11 个函数）

经资料核实风控后，以下源已全部改经 sgw 网关（限流组 + cache），盘中实测通过：
- **腾讯**（tencent 组）：tencent_quote、full_valuation
- **新浪**（sina 组）：sina_financial_report、sina_option_codes/tquote/greeks
- **财联社**（cls 组）：cls_telegraph
- **巨潮**（cninfo 组）：cninfo_announcements、cninfo_irm（form-POST 能力）
- **百度**（baidu 组）：baidu_kline_with_ma（网关 curl_cffi 指纹出网）
- **同花顺 hexin**（10jqka 组）：hsgt_realtime（data.hexin.cn 归入同花顺组）

网关为此新增两项能力：form-encoded POST 转发、curl_cffi Chrome 指纹出网。

### 仍直连（按风险排序，待处理）

1. **mootdx 5个函数**（bars/quotes/transaction/f10/finance）：走 TCP 二进制协议
   （端口 7709），非 HTTP。现有 HTTP 网关无法透明代理，需网关内嵌 mootdx 客户端
   （协议转换支路）——改动大且要处理线程安全/长连接保活。暂不动。
2. **legulegu 2个函数**（market_pe_lg/pb_lg）：CSRF 两步流（取 session cookie +
   带 cookie 调 API）要求两次请求共享同一会话，当前无状态网关无法保证 cookie 跨
   请求配对（caller 模式透传 Set-Cookie 不够：第二次请求的 cookie 与第一次页面
   请求的 session 不匹配）。需网关 session 模式（持有 cookie）才能支持，工作量大。
   legulegu 风控为软风控（🟡中），暂保留直连，待网关支持 session 模式后迁移。

### 与 AGENTS.md §2 的关系

`AGENTS.md §2` 原写「不封 IP 的源可直连：通达信/腾讯/百度等」。本复核已证明
这些源均有 IP 风控。经 2026-08-06 迁移后，**除 mootdx(TCP) 和 legulegu(需session)
外，其余原直连源已全部经网关**。§2 的「直连」前提现已大幅收窄，剩余两项各有
明确的技术障碍（TCP 协议 / 会话状态），非简单配置可解决。

## 六、复核方法与局限

- **方法**：6 个并行调研 agent，每个负责一组同源函数，用 WebSearch 搜社区资料
  （akshare/efinance issue、CSDN/知乎/腾讯云实战文、官方 FAQ、反爬攻略），
  多源交叉验证。
- **局限**：所有「安全水位」数字（5 req/s、个位数连接等）均为社区经验值，
  非官方 SLA；各源未公开官方 QPS 阈值。结论「高频会封」方向可靠，具体倍数仅供量级参考。
- **未做真机压测**：本项目原则是不对真实数据源做并发压测（`gateway-design.md`
  顶部约束）。本结论基于二手资料，若要确证某源并发阈值，需在授权下做小步加压。
- **迁移验证**：2026-08-06 盘中对每个迁移源做了单发实测（真实数据 + /__stats
  计数确认走网关 + 未配网关 fail-closed），但未做并发压测。
