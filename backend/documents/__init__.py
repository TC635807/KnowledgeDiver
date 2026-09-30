"""文档解析包。

提供文件上传后解析为纯文本的工具函数。
"""

from .parser import parse_file, ParseError

__all__ = ["parse_file", "ParseError"]
