"""AI 运行时配置测试：前端「API 配置」直接改写 .env（不新增配置文件）。

锁定：
- 不创建任何新文件，只就地改写 .env 的 AI_API_URL / AI_API_KEY / AI_MODEL 三行；
- 其它行、注释、顺序原样保留；已有键就地替换不重复追加；
- 空串 = 保持原值（前端"留空不改"），None = 删除该行；
- 写完后 os.environ 同步，backend/ai/config.load_config() 无需重启即可读到新值；
- 值含空白/引号/# 时自动加引号，读回仍然相等。
"""

import os
import stat
import sys
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-secret")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from backend.services import ai_settings  # noqa: E402

DEFAULTS = {
    "api_url": "https://default.example/v1",
    "api_key": "",
    "model": "default-model",
}


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    """把 .env 指向临时文件，并清掉进程环境里的干扰项。"""
    target = tmp_path / ".env"
    monkeypatch.setattr(ai_settings, "ENV_PATH", target)
    monkeypatch.setattr(ai_settings, "DEFAULTS", dict(DEFAULTS))
    for key in ("AI_API_URL", "AI_API_KEY", "AI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    return target


def test_missing_env_falls_back_to_defaults(env_file):
    assert not env_file.exists()
    assert ai_settings.effective_settings() == DEFAULTS
    assert ai_settings.read_env_file() == {}
    assert ai_settings.key_source() == "none"


def test_update_writes_only_target_keys(env_file):
    env_file.write_text(
        "# 注释保留\nJWT_SECRET=keep-me\nAI_MODEL=old-model\nAI_CONCURRENCY=2\n",
        encoding="utf-8",
    )

    ai_settings.update_env_settings({"model": "new-model", "api_key": "sk-1234567890abcdef"})

    text = env_file.read_text(encoding="utf-8")
    assert "# 注释保留" in text
    assert "JWT_SECRET=keep-me" in text
    assert "AI_CONCURRENCY=2" in text
    assert "AI_MODEL=new-model" in text
    assert "old-model" not in text
    assert text.count("AI_MODEL=") == 1  # 就地替换，不重复追加
    assert "AI_API_KEY=sk-1234567890abcdef" in text

    settings = ai_settings.effective_settings()
    assert settings["model"] == "new-model"
    assert settings["api_key"] == "sk-1234567890abcdef"
    assert settings["api_url"] == DEFAULTS["api_url"]
    assert set(ai_settings.read_env_file()) >= {"JWT_SECRET", "AI_MODEL", "AI_API_KEY", "AI_CONCURRENCY"}


def test_empty_string_keeps_existing_value(env_file):
    ai_settings.update_env_settings({"model": "keep-model"})
    ai_settings.update_env_settings({"model": "", "api_key": ""})
    assert ai_settings.effective_settings()["model"] == "keep-model"


def test_none_deletes_line_and_falls_back(env_file):
    ai_settings.update_env_settings({"model": "custom-model", "api_key": "sk-secret"})
    assert ai_settings.effective_settings()["model"] == "custom-model"

    ai_settings.update_env_settings({"model": None, "api_key": None})

    text = env_file.read_text(encoding="utf-8")
    assert "custom-model" not in text
    assert "sk-secret" not in text
    settings = ai_settings.effective_settings()
    assert settings["model"] == DEFAULTS["model"]
    assert settings["api_key"] == ""


def test_reset_removes_all_three_keys(env_file):
    ai_settings.update_env_settings({
        "api_url": "https://x.example/v1",
        "api_key": "sk-x",
        "model": "x-model",
    })
    env_file.write_text(
        env_file.read_text(encoding="utf-8") + "KEEP_ME=1\n", encoding="utf-8"
    )

    settings = ai_settings.reset_env_settings()

    text = env_file.read_text(encoding="utf-8")
    assert "AI_API_URL" not in text and "AI_API_KEY" not in text and "AI_MODEL" not in text
    assert "KEEP_ME=1" in text  # 其它配置一行不动
    assert settings == DEFAULTS


def test_values_with_spaces_and_hash_roundtrip(env_file):
    weird = "sk-with space#and\"quote"
    ai_settings.update_env_settings({"api_key": weird})

    # 文件里是带引号的形式，读回来必须完全相等
    assert ai_settings.read_env_file()["AI_API_KEY"] == weird
    assert ai_settings.effective_settings()["api_key"] == weird


def test_no_extra_files_are_created(env_file, tmp_path):
    ai_settings.update_env_settings({"model": "m"})
    created = sorted(p.name for p in tmp_path.iterdir())
    assert created == [".env"], f"只应改写 .env，实际生成了 {created}"


def test_env_file_permissions(env_file):
    ai_settings.update_env_settings({"api_key": "sk-perm"})
    mode = stat.S_IMODE(os.stat(env_file).st_mode)
    assert mode == 0o600, f"新建 .env 权限应为 0600，实际 {oct(mode)}"


def test_existing_permissions_are_preserved(env_file):
    env_file.write_text("AI_MODEL=old\n", encoding="utf-8")
    os.chmod(env_file, 0o640)
    ai_settings.update_env_settings({"model": "new"})
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o640


def test_os_environ_is_synced_so_changes_take_effect_without_restart(env_file):
    from backend.ai.config import load_config

    ai_settings.update_env_settings({
        "api_url": "https://local.example/v1",
        "api_key": "sk-local",
        "model": "local-model",
    })

    assert os.environ["AI_MODEL"] == "local-model"

    cfg = load_config()
    assert cfg.api_url == "https://local.example/v1"
    assert cfg.api_key == "sk-local"
    assert cfg.model == "local-model"

    # Agent 与卡片生成共用同一个模型
    from backend.agent.provider import AgentLLM

    llm = AgentLLM()
    assert llm._model == "local-model"


def test_mask_key():
    assert ai_settings.mask_key("") == ""
    assert ai_settings.mask_key("short") == "*****"
    masked = ai_settings.mask_key("sk-fake1234567890abcdefghijklmnop")
    assert masked.startswith("sk-fak") and masked.endswith("mnop")
    assert "e1234567890abcdefghijkl" not in masked
