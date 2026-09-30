"""注册 kbtest 测试用户（幂等）——让 e2e 建出的软件工程知识库可在 Web UI 中查看。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.storage.sqlite_user_store import SqliteUserStore

USERNAME = "kbtest"
PASSWORD = "kbtest123"

store = SqliteUserStore()
if store.get_user(USERNAME) is not None:
    print(f"用户 {USERNAME} 已存在，跳过")
else:
    store.create_user(USERNAME, PASSWORD)
    print(f"已创建用户 {USERNAME}（密码 {PASSWORD}）")
