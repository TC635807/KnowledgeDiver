"""
头像服务模块。

处理头像文件的校验、保存和清理逻辑。
"""

from __future__ import annotations

import os
from pathlib import Path

AVATAR_DIR = Path(os.getcwd()) / "data" / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)

MAX_AVATAR_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def save_avatar(username: str, file_data: bytes, original_filename: str) -> str:
    """保存用户头像到文件系统，返回 avatar_url。

    自动从旧扩展名文件清理，校验文件格式和大小限制。
    Raises ValueError on invalid format or oversize.
    """
    ext = os.path.splitext(original_filename or "avatar.png")[1] or ".png"
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("不支持的图片格式")

    if len(file_data) > MAX_AVATAR_SIZE:
        raise ValueError("头像文件不能超过 5MB")

    # 清理所有旧扩展名的头像文件
    for old_ext in ALLOWED_EXTENSIONS:
        old_path = AVATAR_DIR / f"{username}{old_ext}"
        if old_path.exists():
            old_path.unlink()

    filename = f"{username}{ext}"
    filepath = AVATAR_DIR / filename
    with open(filepath, "wb") as f:
        f.write(file_data)

    return f"/api/auth/avatar/{username}"
