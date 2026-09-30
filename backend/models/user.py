"""
用户与认证数据模型。

定义 User、UserCreate、UserLogin、Token 等 Pydantic 模型，
包含用户名校验规则和字段约束。
"""

import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class User(BaseModel):
    """用户模型，包含认证信息。"""
    username: str                                        # 用户名
    hashed_password: str                                 # bcrypt 哈希后的密码
    created_at: datetime                                  # 注册时间
    avatar_url: Optional[str] = None                     # 头像 URL


class UserCreate(BaseModel):
    """用户注册请求模型。"""
    username: str = Field(..., min_length=1, max_length=16, description="用户名，1-16个字符")
    password: str = Field(..., min_length=1, max_length=20, description="密码，1-20个字符")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """校验用户名格式：仅允许字母、数字、下划线、短横线和点，禁止 . 和 ..。"""
        if not re.match(r'^[a-zA-Z0-9_\-.]+$', v):
            raise ValueError("用户名只能包含字母、数字、下划线、短横线和点")
        if v in ('.', '..'):
            raise ValueError("用户名不能为 . 或 ..")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """校验密码：20位以内，仅限 ASCII 字符。"""
        if not all(32 <= ord(c) <= 126 for c in v):
            raise ValueError("密码只能包含ASCII可见字符")
        return v


class UserLogin(BaseModel):
    """用户登录请求模型。"""
    username: str
    password: str


class Token(BaseModel):
    """JWT 令牌响应模型。"""
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """JWT 令牌解码后的数据。"""
    username: Optional[str] = None
