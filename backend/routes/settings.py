"""运行时设置路由：AI（OpenAI 兼容）接口配置。

开源版把「必须手改 .env 才能用」改成前端可配——**直接就地改写项目根目录的 .env**，
不引入任何额外配置文件：
  GET    /api/settings/ai         读取当前生效配置（API Key 只返回脱敏值）
  PUT    /api/settings/ai         覆写 .env 中的 AI_API_URL / AI_API_KEY / AI_MODEL（立即生效）
  POST   /api/settings/ai/test    用生效(或本次提交的)配置发一次最小请求验证连通性
  POST   /api/settings/ai/reset   删除 .env 里的这三行，回到代码默认值

安全约定：
  - 任何响应都不回传完整 API Key，只回传 api_key_set 与脱敏串；
  - .env 原本就被 .gitignore 排除，不新增任何入库文件；
  - 该接口需要登录（与其他业务路由一致）。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.ai.openai_provider import normalize_api_base_url
from backend.config import (
    KD_REMOTE_CONNECT_TIMEOUT,
    KD_REMOTE_READ_TIMEOUT,
    KD_SERVER_URL_DEFAULT,
    LOCAL_ACCOUNT,
    LOCAL_ACCOUNT_USER,
)
from backend.models.user import User
from backend.remote.proxy import proxy as remote_proxy
from backend.routes.auth import get_current_user
from backend.services.ai_settings import (
    DEFAULTS,
    ENV_KEYS,
    ENV_PATH,
    effective_settings,
    key_source,
    mask_key,
    read_env_file,
    reset_env_settings,
    update_env_settings,
    write_env_keys,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_TEST_TIMEOUT = 20.0
_MAX_ERROR_CHARS = 300


class AISettingsPayload(BaseModel):
    """AI 配置更新请求体（写入 .env）。

    api_key 传 null 表示从 .env 里删除该行（回落代码默认值）；空串/缺省表示保持不变。
    reset=true 等价于 POST /reset。
    """

    api_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    reset: bool = False


class AITestPayload(BaseModel):
    """连接测试请求体。字段缺省时用当前生效配置。"""

    api_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


def _view(settings: Optional[dict] = None) -> dict:
    """对外视图：只暴露必要字段 + 脱敏 Key，并说明每个字段是否被 .env 覆盖。"""
    settings = settings or effective_settings()
    file_values = read_env_file()
    return {
        "api_url": settings["api_url"],
        "model": settings["model"],
        "api_key_set": bool(settings["api_key"]),
        "api_key_masked": mask_key(settings["api_key"]),
        "api_key_source": key_source(),
        "overrides": [
            field for field, key in ENV_KEYS.items() if file_values.get(key, "").strip()
        ],
        "env_path": str(ENV_PATH),
        "defaults": dict(DEFAULTS),
    }


@router.get("/api/settings/ai")
async def get_ai_settings_view(current_user: User = Depends(get_current_user)):
    """读取当前生效的 AI 配置（Key 脱敏）。"""
    return _view()


@router.put("/api/settings/ai")
async def put_ai_settings(
    payload: AISettingsPayload,
    current_user: User = Depends(get_current_user),
):
    """把 AI 配置写回 .env（其它行与注释原样保留），写完立即生效。"""
    if payload.reset:
        return _view(reset_env_settings())

    patch: dict = {}
    if payload.api_url is not None:
        url = payload.api_url.strip()
        if url and not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(status_code=400, detail="API 地址需以 http:// 或 https:// 开头")
        patch["api_url"] = url
    if payload.api_key is not None:
        # 空串 = 保持不变；显式 null 才从 .env 删除该行
        patch["api_key"] = payload.api_key.strip() if payload.api_key.strip() else None
    if payload.model is not None and payload.model.strip():
        patch["model"] = payload.model.strip()

    try:
        settings = update_env_settings(patch)
    except Exception as e:
        logger.error("[Settings] 写入 .env 失败: %s", e)
        raise HTTPException(status_code=500, detail=f"写入 .env 失败: {e}")

    logger.info(
        "[Settings] AI 配置已写回 .env（字段: %s）",
        ", ".join(sorted(patch)) or "无",
    )
    return _view(settings)


@router.post("/api/settings/ai/reset")
async def post_ai_settings_reset(current_user: User = Depends(get_current_user)):
    """删除 .env 中的 AI 配置行，回到代码默认值。"""
    logger.info("[Settings] 已从 .env 删除 AI 配置行")
    return _view(reset_env_settings())


@router.post("/api/settings/ai/test")
async def post_ai_settings_test(
    payload: Optional[AITestPayload] = None,
    current_user: User = Depends(get_current_user),
):
    """用给定（或当前生效）配置做一次最小 chat 请求，验证地址/Key/模型是否可用。"""
    from openai import AsyncOpenAI

    payload = payload or AITestPayload()
    effective = effective_settings()
    api_url = (payload.api_url or effective["api_url"]).strip()
    api_key = (payload.api_key or effective["api_key"]).strip()
    model = (payload.model or effective["model"]).strip()

    if not api_url or not model:
        return {"ok": False, "message": "API 地址与模型名不能为空"}
    if not api_key:
        return {"ok": False, "message": "API Key 为空，请先填写"}

    started = time.perf_counter()
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=normalize_api_base_url(api_url),
        timeout=_TEST_TIMEOUT,
        max_retries=0,
    )
    try:
        await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            stream=False,
        )
    except Exception as e:
        detail = str(e).strip().replace("\n", " ")[:_MAX_ERROR_CHARS] or e.__class__.__name__
        logger.warning("[Settings] AI 连接测试失败: %s", detail)
        return {"ok": False, "message": f"连接失败：{detail}", "model": model}
    finally:
        try:
            await client.close()
        except Exception:
            pass

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info("[Settings] AI 连接测试成功（%s, %dms）", model, latency_ms)
    return {
        "ok": True,
        "message": f"连接成功（{model}，{latency_ms}ms）",
        "model": model,
        "latency_ms": latency_ms,
    }


# ═══════════════════════════════════════════════════════════════════
# 「账号与服务器」设置（05-实施契约 §2.5）
# GET  /api/settings/server       读取服务器地址与来源
# PUT  /api/settings/server       写回 .env（复用 ai_settings 的就地改写，空串=删除键）
# POST /api/settings/server/test  探活（/api/meta 失败回落 /api/hub），5s 超时且不抛异常
# ═══════════════════════════════════════════════════════════════════

SERVER_ENV_KEY = "KD_SERVER_URL"
_PROBE_TIMEOUT = 5.0
# 测试可注入：settings._probe_transport = httpx.MockTransport(handler)
_probe_transport: Optional[httpx.AsyncBaseTransport] = None


class ServerSettingsPayload(BaseModel):
    """服务器地址更新请求体。空串 = 删除该键、回到代码默认值。"""

    server_url: Optional[str] = None


class ServerTestPayload(BaseModel):
    """探活请求体。缺省时用当前生效地址。"""

    server_url: Optional[str] = None


def _clean_url(raw: str) -> str:
    return (raw or "").strip().rstrip("/")


def _is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _effective_server_url() -> tuple[str, str]:
    """(生效地址, 来源)。来源 = env_file | env | default。"""
    file_values = read_env_file()
    if SERVER_ENV_KEY in file_values:
        return _clean_url(file_values[SERVER_ENV_KEY]), "env_file"
    env_value = os.environ.get(SERVER_ENV_KEY)
    if env_value is not None:
        return _clean_url(env_value), "env"
    return KD_SERVER_URL_DEFAULT, "default"


def _server_view() -> dict:
    server_url, source = _effective_server_url()
    return {
        "server_url": server_url,
        "server_url_source": source,
        "defaults": {
            "server_url": KD_SERVER_URL_DEFAULT,
            "connect_timeout": KD_REMOTE_CONNECT_TIMEOUT,
            "read_timeout": KD_REMOTE_READ_TIMEOUT,
        },
        "connect_timeout": KD_REMOTE_CONNECT_TIMEOUT,
        "read_timeout": KD_REMOTE_READ_TIMEOUT,
        "local_account_user": LOCAL_ACCOUNT_USER,
        "local_account_enabled": LOCAL_ACCOUNT,
        "proxy_enabled": remote_proxy.enabled,
    }


@router.get("/api/settings/server")
async def get_server_settings(current_user: User = Depends(get_current_user)):
    """读取「账号与服务器」配置（不含任何密钥）。"""
    return _server_view()


@router.put("/api/settings/server")
async def put_server_settings(
    payload: ServerSettingsPayload,
    current_user: User = Depends(get_current_user),
):
    """把服务器地址写回项目根目录的 .env，并让代理立即生效（无需重启）。"""
    raw = (payload.server_url or "").strip()
    if raw == "":
        # 空串 = 删除该键，回到代码默认值
        write_env_keys({SERVER_ENV_KEY: None})
        remote_proxy.set_base_url(KD_SERVER_URL_DEFAULT)
        logger.info("[Settings] 已从 .env 删除 %s，回落默认 %s", SERVER_ENV_KEY, KD_SERVER_URL_DEFAULT)
        return _server_view()

    url = _clean_url(raw)
    if not _is_http_url(url):
        raise HTTPException(status_code=400, detail="服务器地址需以 http:// 或 https:// 开头")

    try:
        write_env_keys({SERVER_ENV_KEY: url})
    except Exception as e:
        logger.error("[Settings] 写入 .env 失败: %s", e)
        raise HTTPException(status_code=500, detail=f"写入 .env 失败: {e}")

    remote_proxy.set_base_url(url)
    logger.info("[Settings] 服务器地址已写回 .env: %s", url)
    return _server_view()


async def _probe_server(base_url: str) -> dict:
    """探活：先 /api/meta，失败回落 /api/hub；任何异常都不上抛。"""
    started = time.perf_counter()
    probes = [
        base_url + "/api/meta",
        base_url + "/api/hub?page=1&page_size=1",
    ]
    failures: list[str] = []
    last_status: Optional[int] = None

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        async with httpx.AsyncClient(
            timeout=_PROBE_TIMEOUT,
            follow_redirects=True,
            transport=_probe_transport,
        ) as client:
            for url in probes:
                try:
                    resp = await client.get(url)
                except httpx.HTTPError as exc:
                    failures.append(f"{url} -> {type(exc).__name__}")
                    continue
                last_status = resp.status_code
                if 200 <= resp.status_code < 300:
                    return {
                        "ok": True,
                        "status": resp.status_code,
                        "latency_ms": elapsed(),
                        "remote": base_url,
                    }
                failures.append(f"{url} -> {resp.status_code}")
    except Exception as e:  # pragma: no cover - 兜底，探活永不抛
        failures.append(type(e).__name__)

    return {
        "ok": False,
        "status": last_status,
        "latency_ms": elapsed(),
        "remote": base_url,
        "detail": "；".join(failures) or "探活失败",
    }


@router.post("/api/settings/server/test")
async def post_server_test(
    payload: Optional[ServerTestPayload] = None,
    current_user: User = Depends(get_current_user),
):
    """测试与官方服务器的连通性（超时 5s，失败返回 ok=false 而不是抛错）。"""
    payload = payload or ServerTestPayload()
    target = _clean_url(payload.server_url or "")
    if not target:
        target, _ = _effective_server_url()
    if not _is_http_url(target):
        return {
            "ok": False,
            "status": None,
            "latency_ms": None,
            "remote": target,
            "detail": "服务器地址需以 http:// 或 https:// 开头",
        }
    return await _probe_server(target)


__all__ = ["router"]
