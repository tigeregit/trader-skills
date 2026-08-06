"""asgk_server.capabilities — 真实数据能力包。

每个模块注册一个或多个 @capability，把上游知识（URL/编码/字段映射/协议）下沉到
服务端。导入本包即触发注册（server.py 启动时 import）。

T2 落 quote（腾讯实时行情）；T3 落 datacenter（东财数据中心统一查询，15 函数共用）；
T4~T10 各梯队逐步填充其余能力。
"""
from . import datacenter  # noqa: F401  导入即注册 datacenter 能力
from . import quote  # noqa: F401  导入即注册 quote 能力
