"""认证与用户管理路由。

提供用户注册、登录、个人信息获取和头像上传接口。
"""


import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Depends, status, UploadFile, File, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.models.user import UserCreate, UserLogin, Token, User
from backend.storage import SqliteUserStore
from backend.auth.jwt import create_access_token, decode_access_token
from backend.services.avatar import save_avatar, AVATAR_DIR
from backend.auth.password import verify_password
from backend.config import LOCAL_ACCOUNT, LOCAL_ACCOUNT_USER, RATE_LIMIT_AUTH
from backend.rate_limit import limiter

router = APIRouter()
security = HTTPBearer()


def get_user_store() -> SqliteUserStore:
    """获取用户存储仓库。"""
    return SqliteUserStore()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """解析 Bearer Token 并返回当前认证用户。"""
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证凭据无效",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证凭据无效",
            headers={"WWW-Authenticate": "Bearer"},
        )
    store = get_user_store()
    user = store.get_user(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ── 内置本地账号（客户端独有）────────────────────────────────────────
# v3 设计：开机即为内置账号签发一个长期本地 JWT，本地工作区始终有身份。
# 因此不需要 HTTPBearer(auto_error=False)，也不需要「无头即游客」放行分支。
LOCAL_TOKEN_EXPIRES_DAYS = 30


def ensure_local_user(store: SqliteUserStore | None = None) -> User:
    """确保内置本地账号存在并返回（幂等）。

    随机密码从不用于登录，只为满足 get_current_user 里 store.get_user() 必须存在。
    """
    store = store or get_user_store()
    user = store.get_user(LOCAL_ACCOUNT_USER)
    if user is not None:
        return user
    try:
        store.create_user(LOCAL_ACCOUNT_USER, secrets.token_urlsafe(32))
    except ValueError:
        # 并发下另一个请求刚创建成功 —— 直接读回即可
        pass
    user = store.get_user(LOCAL_ACCOUNT_USER)
    if user is None:  # pragma: no cover - 仅存储层异常时才会走到
        raise HTTPException(status_code=500, detail="内置本地账号创建失败")
    return user


@router.post("/api/auth/local-session")
async def local_session(store: SqliteUserStore = Depends(get_user_store)):
    """为内置本地账号签发长期本地 JWT。

    无鉴权（但受 Origin 校验 + 回环绑定保护）：这是前端启动时唯一的自动登录动作。
    """
    # 在函数体内读模块级开关 —— 便于运行时/测试覆盖
    if not LOCAL_ACCOUNT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="本地账号未启用")
    user = ensure_local_user(store)
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(days=LOCAL_TOKEN_EXPIRES_DAYS),
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": user.username,
    }


@router.post("/api/auth/register", response_model=Token)
@limiter.limit(RATE_LIMIT_AUTH)
async def register(request: Request, user_data: UserCreate, store: SqliteUserStore = Depends(get_user_store)):
    """用户注册接口。创建用户并返回 JWT 令牌。"""
    try:
        user = store.create_user(user_data.username, user_data.password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    access_token = create_access_token(data={"sub": user.username})
    return Token(access_token=access_token)


@router.post("/api/auth/login", response_model=Token)
@limiter.limit(RATE_LIMIT_AUTH)
async def login(request: Request, user_data: UserLogin, store: SqliteUserStore = Depends(get_user_store)):
    """用户登录接口。验证凭据并返回 JWT 令牌。"""
    user = store.verify_user(user_data.username, user_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user.username})
    return Token(access_token=access_token)


@router.get("/api/auth/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息。"""
    return {
        "username": current_user.username,
        "created_at": current_user.created_at,
        "avatar_url": current_user.avatar_url,
    }



@router.post("/api/auth/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    store: SqliteUserStore = Depends(get_user_store),
):
    """上传用户头像。支持 png/jpg/gif/webp，最大 5MB。"""
    contents = await file.read()
    try:
        avatar_url = save_avatar(current_user.username, contents, file.filename or "avatar.png")
    except ValueError as e:
        raise HTTPException(status_code=400, detail="头像上传失败")
    store.update_user(current_user.username, avatar_url=avatar_url)
    return {"status": "ok", "avatar_url": avatar_url}


@router.get("/api/auth/avatar/{username}")
async def get_avatar(username: str):
    """获取用户头像文件。"""
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        filepath = AVATAR_DIR / f"{username}{ext}"
        if filepath.exists():
            media_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                         ".gif": "image/gif", ".webp": "image/webp"}
            return FileResponse(str(filepath), media_type=media_map.get(ext, "image/png"))
    raise HTTPException(status_code=404, detail="Avatar not found")
