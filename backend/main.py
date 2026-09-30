"""
KnowledgeDiver FastAPI 应用入口。

注册所有 API 路由模块，配置日志，启动应用。
"""

import asyncio
import logging
from typing import override
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from backend.config import CORS_ORIGINS
from backend.rate_limit import limiter
from backend.scraper.fetcher import close_crawler
from backend.routes.pipeline import router as pipeline_router
from backend.routes.links import router as links_router
from backend.routes.classification import router as classification_router
from backend.routes.ai import router as ai_router
from backend.routes.cards import router as cards_router
from backend.routes.auth import router as auth_router
from backend.routes.sessions import router as sessions_router
from backend.routes.session_io import router as session_io_router
from backend.routes.share import router as share_router
from backend.routes.hub import router as hub_router
from backend.routes.documents import router as documents_router
from backend.agent.routes.agent import router as agent_router
try:
    from backend.routes.export import router as export_router
except Exception:
    export_router = None  # Optional for environments without export module at import time

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)

app = FastAPI()

# ── 安全中间件 ───────────────────────────────────────────────────


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为所有响应添加安全标头的 ASGI 中间件。"""

    @override
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'"
        )
        return response


# CORS — 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 安全标头
app.add_middleware(SecurityHeadersMiddleware)


# ── 速率限制 ──────────────────────────────────────────────────────

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── 全局异常处理器 ───────────────────────────────────────────────


@app.exception_handler(HTTPException)
async def http_exception_handler(
    _request: Request, exc: HTTPException
):
    """HTTPException → 结构化 JSON 响应（不泄露敏感信息）。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status_code": exc.status_code},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
):
    """Pydantic 校验失败 → 用户友好的中文错误提示。"""
    return JSONResponse(
        status_code=422,
        content={
            "detail": "输入数据验证失败，请检查后重试",
            "errors": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(
    _request: Request, _exc: Exception
):
    """未预期的异常 → 500，不向客户端泄露堆栈细节。"""
    logging.exception("Unhandled server error")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
    )


# 注册所有路由模块
app.include_router(auth_router)           # 认证：注册/登录/个人信息/头像
app.include_router(pipeline_router)       # 流水线：收集/延申搜索 SSE 流 + 任务管理
app.include_router(links_router)          # 链接：卡片双向链接 CRUD
app.include_router(classification_router) # 分类：AI 自动分类/重分类/树
app.include_router(ai_router)             # AI：流式文本生成
app.include_router(cards_router)          # 卡片：CRUD
app.include_router(sessions_router)       # 会话：CRUD + 移动卡片
app.include_router(session_io_router)     # 会话导入导出：ZIP 下载/上传
app.include_router(share_router)          # 分享：分享链接 token
app.include_router(hub_router)            # Hub 论坛：浏览/分享/导入/点赞/评论
app.include_router(documents_router)      # 文档上传：上传文件→AI 分析→三层卡片
app.include_router(agent_router)          # Agent：SSE 流式对话 + 工具调用
if export_router is not None:
    app.include_router(export_router)     # 导出：Markdown 导出


@app.get("/")
async def root():
    """根路径健康检查端点。"""
    return {"status": "KnowledgeDiver API"}


@app.on_event("startup")
async def startup():
    """预热 crawl4ai 浏览器，避免首次抓取被 init 阻塞 10~30s。"""
    from backend.scraper.fetcher import _get_crawler
    asyncio.create_task(_get_crawler())


@app.on_event("shutdown")
async def shutdown():
    """应用关闭时释放资源（crawl4ai 浏览器、连接池等）。"""
    logger = logging.getLogger(__name__)
    logger.info("[Shutdown] 正在释放资源...")
    await close_crawler()
    logger.info("[Shutdown] 资源释放完毕")
