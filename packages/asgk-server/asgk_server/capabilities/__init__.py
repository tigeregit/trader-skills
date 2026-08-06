"""asgk_server.capabilities — 真实数据能力包。

每个模块注册一个或多个 @capability，把上游知识（URL/编码/字段映射/协议）下沉到
服务端。导入本包即触发注册（server.py 启动时 import）。

- quote：腾讯实时行情（具名语义能力）
- datacenter：东财数据中心统一查询（15 函数共用，具名语义）
- limitup_pool：东财涨停四池（zt/zb/dt/yzt，pool_type 参数区分）
"""
from . import datacenter  # noqa: F401  导入即注册 datacenter 能力
from . import fund_flow  # noqa: F401  导入即注册 fund_flow 能力
from . import limitup_pool  # noqa: F401  导入即注册 limitup_pool 能力
from . import push2  # noqa: F401  导入即注册 stock_info / concept_blocks 能力
from . import quote  # noqa: F401  导入即注册 quote 能力
