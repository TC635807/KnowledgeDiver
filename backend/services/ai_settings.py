"""AI 运行时配置：前端「API 配置」直接读写项目根目录的 .env。

不引入任何额外配置文件——保存即就地覆写 .env 里的三行：
    AI_API_URL / AI_API_KEY / AI_MODEL
其余行、注释、缩进、顺序一律原样保留；写完同步 os.environ，
因此**无需重启**即刻生效（读取方 load_config() / AgentLLM 每次都重新求值）。

优先级：.env 文件值  >  进程环境变量  >  backend/config.py 里的代码默认值
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Dict, Optional

from backend.config import (
    AI_API_KEY_DEFAULT,
    AI_API_URL_DEFAULT,
    AI_MODEL_DEFAULT,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 默认改写项目根目录的 .env；KD_ENV_FILE 可指向其它文件（测试/多环境部署用）
ENV_PATH = Path(os.getenv("KD_ENV_FILE") or (_PROJECT_ROOT / ".env"))

# 前端可配置的字段 -> .env 键名（模型只有一个，卡片生成与 Agent 共用）
ENV_KEYS: Dict[str, str] = {
    "api_url": "AI_API_URL",
    "api_key": "AI_API_KEY",
    "model": "AI_MODEL",
}
DEFAULTS: Dict[str, str] = {
    "api_url": AI_API_URL_DEFAULT,
    "api_key": AI_API_KEY_DEFAULT,
    "model": AI_MODEL_DEFAULT,
}

# 匹配 KEY=value（容忍 export 前缀、等号两侧空格）
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

_lock = threading.Lock()


def _unquote(raw: str) -> str:
    """去掉 .env 值两侧的引号（并还原转义）。"""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = value.replace('\\"', '"').replace("\\\\", "\\")
    return value


def _quote(value: str) -> str:
    """需要时给值加引号（含空白、# 或引号）。"""
    if value == "" or re.search(r"[\s#'\"]", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def read_env_file() -> Dict[str, str]:
    """解析 .env，返回 {键: 值}；文件不存在或不可读时返回 {}。"""
    try:
        if not ENV_PATH.exists():
            return {}
        text = ENV_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[AISettings] 读取 %s 失败: %s", ENV_PATH, e)
        return {}
    values: Dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        values[m.group(1)] = _unquote(m.group(2))
    return values


def effective_settings() -> Dict[str, str]:
    """当前生效配置（.env > 进程环境 > 代码默认）。"""
    file_values = read_env_file()
    result: Dict[str, str] = {}
    for field, key in ENV_KEYS.items():
        value = file_values.get(key)
        if value is None or value.strip() == "":
            value = os.environ.get(key) or DEFAULTS[field]
        result[field] = (value or "").strip()
    return result


def get_ai_setting(field: str) -> str:
    """取单个字段的生效值（未知字段返回空串）。"""
    if field not in ENV_KEYS:
        return ""
    return effective_settings()[field]


def update_env_settings(patch: Dict[str, object]) -> Dict[str, str]:
    """就地改写 .env 中的 AI 配置，并同步 os.environ（立即生效）。

    patch 语义：
      - 缺省 / 空串   -> 保持原值（前端输入框留空的语义）
      - None          -> 删除该键（回落进程环境 / 代码默认值）
      - 其它值        -> 覆写
    """
    with _lock:
        updates: Dict[str, Optional[str]] = {}
        for field, key in ENV_KEYS.items():
            if field not in patch:
                continue
            value = patch[field]
            if value is None:
                updates[key] = None
            else:
                text = str(value).strip()
                if text == "":
                    continue
                updates[key] = text
        if updates:
            _rewrite_env(updates)
            for key, value in updates.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    return effective_settings()


def reset_env_settings() -> Dict[str, str]:
    """删除 .env 中的三行 AI 配置，回到进程环境 / 代码默认值。"""
    return update_env_settings({field: None for field in ENV_KEYS})


def write_env_keys(updates: Dict[str, Optional[str]]) -> None:
    """就地改写 .env 中的任意键（None = 删除该行），并同步 os.environ。

    AI 之外的新配置（例如 KD_SERVER_URL）复用它，避免再造一套 .env 写入逻辑：
    其它行/注释原样保留、原子写入、写完立即生效。
    """
    with _lock:
        _rewrite_env(updates)
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _rewrite_env(updates: Dict[str, Optional[str]]) -> None:
    """保留其它内容，只替换/新增/删除目标行；原子写入并保持文件权限。"""
    path = ENV_PATH
    existed = path.exists()
    try:
        original = path.read_text(encoding="utf-8") if existed else ""
    except Exception as e:
        logger.warning("[AISettings] 读取 %s 失败，将新建: %s", path, e)
        original = ""

    lines = original.splitlines()
    seen = set()
    out = []
    for line in lines:
        m = _LINE_RE.match(line)
        if m and m.group(1) in updates:
            key = m.group(1)
            seen.add(key)
            value = updates[key]
            if value is not None:
                out.append(f"{key}={_quote(value)}")
            # value is None -> 删除该行
            continue
        out.append(line)

    missing = {k: v for k, v in updates.items() if k not in seen and v is not None}
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append("# ── 前端「API 配置」写入 ──────────────────────────────────────────────")
        out.extend(f"{key}={_quote(value)}" for key, value in missing.items())

    text = "\n".join(out).rstrip("\n") + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    if existed:
        try:
            mode = path.stat().st_mode & 0o777
        except Exception:
            mode = None
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode if mode is not None else 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def mask_key(key: Optional[str]) -> str:
    """密钥脱敏：保留前 6 位与后 4 位。"""
    if not key:
        return ""
    if len(key) <= 12:
        return "*" * len(key)
    return f"{key[:6]}{'*' * 6}{key[-4:]}"


def key_source() -> str:
    """当前 Key 来源：env_file / env / default。（Key 一律存在 .env 或进程环境里）"""
    file_values = read_env_file()
    if file_values.get("AI_API_KEY", "").strip():
        return "env_file"
    if os.environ.get("AI_API_KEY", "").strip():
        return "env"
    return "none"
