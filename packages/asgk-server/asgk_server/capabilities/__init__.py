"""asgk_server.capabilities — 真实数据能力包。

每个模块注册一个或多个 @capability，把上游知识（URL/编码/字段映射/协议）下沉到
服务端。导入本包即触发注册（server.py 启动时 import）。

- quote：腾讯实时行情（具名语义能力）
- datacenter：东财数据中心统一查询（15 函数共用，具名语义）
- limitup_pool：东财涨停四池（zt/zb/dt/yzt，pool_type 参数区分）
"""
from . import baidu_kline  # noqa: F401  baidu_kline_with_ma (curl_cffi)
from . import chip  # noqa: F401  chip_distribution (cyq.js)
from . import clist  # noqa: F401  industry_rank / board_constituents
from . import cls_telegraph  # noqa: F401  cls_telegraph (md5(sha1) sign)
from . import cninfo  # noqa: F401  announcements / irm
from . import datacenter  # noqa: F401
from . import docs  # noqa: F401  announce_pdf / report_pdf (binary)
from . import em_hot  # noqa: F401
from . import fund_flow  # noqa: F401
from . import holders  # noqa: F401
from . import legulegu  # noqa: F401  market_pe_lg / market_pb_lg (CSRF session)
from . import limitup_pool  # noqa: F401
from . import mootdx  # noqa: F401  bars/quotes/transaction/finance/f10 (TCP pool)
from . import news  # noqa: F401
from . import push2  # noqa: F401  stock_info / concept_blocks
from . import quote  # noqa: F401
from . import reports  # noqa: F401
from . import sina_finance  # noqa: F401  sina_financial_report
from . import sina_option  # noqa: F401  codes / tquote / greeks
from . import ths_signal  # noqa: F401
