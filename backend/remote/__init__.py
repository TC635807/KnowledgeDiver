"""客户端内置反向代理网关（KD-main 独有）。

把「身份 / 论坛 / 迁移」三组**显式白名单**路径转发到官方服务器；
抓取 / AI / 卡片 / 向量 / Agent 等一律留在本地。
"""

from backend.remote.proxy import RemoteProxy, proxy

__all__ = ["RemoteProxy", "proxy"]
