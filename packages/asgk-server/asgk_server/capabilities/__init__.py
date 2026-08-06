"""asgk_server.capabilities — 真实数据能力包。

每个模块注册一个或多个 @capability，把上游知识（URL/编码/字段映射/协议）下沉到
服务端。导入本包即触发注册（server.py 启动时 import）。

- quote：腾讯实时行情（具名语义能力）
- datacenter：东财数据中心统一查询（15 函数共用，具名语义）
- emquery：通用 URL 查询（§3.4 em_get 枢纽，18+ push2 函数共用）
"""
from . import datacenter  # noqa: F401
from . import emquery  # noqa: F401
from . import quote  # noqa: F401
