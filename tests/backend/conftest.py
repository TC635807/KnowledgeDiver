"""测试环境固定：不让 .env 中的实验开关影响单元测试。"""
import os

os.environ["AGENT_EVAL_MODE"] = "full"
os.environ["AGENT_EVAL_MAX_CARDS"] = "0"
os.environ["AGENT_EVAL_MIN_CARDS"] = "0"
os.environ.setdefault("JWT_SECRET", "test-secret")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def fastapi_app():
    """真实的客户端 FastAPI 应用（惰性导入，只有需要的测试才会付出 ~2s 导入成本）。"""
    from backend.main import app

    return app
