"""asgk_server.capabilities — 真实数据能力包。

每个模块注册一个或多个 @capability，把上游知识（URL/编码/字段映射/协议）下沉到
服务端。导入本包即触发注册（server.py 启动时 import）。

T2 先落 quote（腾讯实时行情）；T3~T10 各梯队逐步填充其余能力。
"""
from . import quote  # noqa: F401  导入即注册 quote 能力
