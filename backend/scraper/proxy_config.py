"""
代理配置模块。

管理 HTTP 代理设置（用于访问被屏蔽的网站如 Wikipedia），
支持全局端口设置和环境变量配置。
"""

import os
from typing import Optional


_proxy_port: Optional[int] = None  # 全局代理端口


def set_proxy_port(port: int) -> None:
    """设置代理端口（全局）。"""
    global _proxy_port
    _proxy_port = port


def get_proxy_port() -> int:
    """获取代理端口（优先全局变量，回退环境变量 PROXY_PORT）。"""
    if _proxy_port is not None:
        return _proxy_port
    return int(os.environ.get("PROXY_PORT", "0"))


def get_proxy_url() -> Optional[str]:
    """返回代理 URL（格式 http://127.0.0.1:{port}），端口为 0 时返回 None（直连）。"""
    port = get_proxy_port()
    if port == 0:
        return None  # 禁用代理，直连
    return f"http://127.0.0.1:{port}"


def get_proxy_dict() -> Optional[dict]:
    """返回适用于 requests 库的代理字典格式。"""
    url = get_proxy_url()
    if url is None:
        return None
    return {"http": url, "https": url}
