"""asgk_server.capabilities — 真实数据能力包。

每个模块注册一个或多个 @capability，把上游知识（URL/编码/字段映射/协议）下沉到
服务端。导入本包即触发注册（server.py 启动时 import）。

- quote：腾讯实时行情（具名语义能力）
- datacenter：东财数据中心统一查询（15 函数共用，具名语义）
- limitup_pool：东财涨停四池（zt/zb/dt/yzt，pool_type 参数区分）
"""
from . import clist  # noqa: F401  industry_rank / board_constituents
from . import datacenter  # noqa: F401
from . import em_hot  # noqa: F401
from . import fund_flow  # noqa: F401
from . import holders  # noqa: F401
from . import limitup_pool  # noqa: F401
from . import news  # noqa: F401
from . import push2  # noqa: F401  stock_info / concept_blocks
from . import quote  # noqa: F401
from . import reports  # noqa: F401
from . import ths_signal  # noqa: F401
