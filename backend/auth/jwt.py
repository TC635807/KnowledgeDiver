"""
JWT 令牌生成与验证模块。

提供 access token 的创建和解析功能，
使用 HS256 算法签名，默认有效期 24 小时。
"""

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt

from backend.config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_HOURS

ALGORITHM = JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * JWT_EXPIRATION_HOURS


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """创建 JWT access token。

    Args:
        data: 要编码的数据（需包含 "sub" 字段对应用户名）
        expires_delta: 可选的自定义过期时间

    Returns:
        编码后的 JWT 字符串
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """解析并验证 JWT token。

    Args:
        token: JWT 字符串

    Returns:
        解码后的 payload（dict），解析失败返回 None
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
