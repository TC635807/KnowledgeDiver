"""
密码哈希与验证模块。

使用 bcrypt 算法（max 72 bytes）进行密码哈希存储和验证。
包含 UTF-8 多字节字符安全截断处理。
"""

import bcrypt


def _truncate_utf8(text: str, max_bytes: int = 72) -> bytes:
    """安全截断 UTF-8 字符串到指定字节数，避免截断多字节字符的中间字节。

    bcrypt 内部使用 Null-terminated C 字符串，超过 72 字节的部分会被静默忽略。
    此函数确保截断不会切在多字节字符中间。

    Args:
        text: 输入字符串
        max_bytes: 最大字节数（默认 72）

    Returns:
        截断后的 UTF-8 字节序列
    """
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return encoded

    truncated = encoded[:max_bytes]
    # UTF-8 延续字节以 10xxxxxx（0x80-0xBF）开头，移除不完整的尾部字节
    while truncated and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]

    return truncated


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码是否匹配 bcrypt 哈希值。

    Args:
        plain_password: 明文密码
        hashed_password: bcrypt 哈希字符串

    Returns:
        是否匹配
    """
    password_bytes = _truncate_utf8(plain_password, 72)
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)


def get_password_hash(password: str) -> str:
    """使用 bcrypt 对密码进行哈希。

    Args:
        password: 明文密码

    Returns:
        哈希后的字符串
    """
    password_bytes = _truncate_utf8(password, 72)
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')
