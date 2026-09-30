"""Agent 端到端时延基准 — 进程内直接运行 run_agent_loop，记录各阶段耗时与 LLM 调用次数。

用法:
    .venv/Scripts/python.exe scripts/agent_latency_bench.py [username] [session_id] [message]

默认用全新 session（空知识库），消息引导模型只调 list_cards（本地读，不联网），
单次运行固定消耗 2~3 次 LLM 调用，用于对比优化前后。
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

DEFAULT_USER = "perfbench"
DEFAULT_SESSION = f"bench-{int(time.time()) % 100000}"
DEFAULT_MESSAGE = (
    "请用 list_cards 工具查询这个知识库现在有多少张卡片，然后直接告诉我数字。不要联网搜索。"
)


async def main() -> None:
    from backend.agent.loop import run_agent_loop

    username = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_USER
    session_id = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_SESSION
    message = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_MESSAGE

    print(f"== 用户={username} 会话={session_id} 消息={message[:40]}... ==", flush=True)
    t0 = time.time()
    event_count = 0
    text_tokens = 0
    tool_calls = []
    llm_round_logs = []

    async for raw in run_agent_loop(username, session_id, message):
        event_count += 1
        try:
            payload = json.loads(raw[len("data: "):].strip())
            kind = payload["type"]
            data = payload["data"]
            if kind == "text":
                text_tokens += 1
            elif kind == "tool_call":
                tool_calls.append(data.get("name"))
                print(f"  [EVENT] tool_call -> {data.get('name')}", flush=True)
            elif kind == "tool_result":
                print(
                    f"  [EVENT] tool_result {data.get('tool')} "
                    f"success={data.get('success')}",
                    flush=True,
                )
            elif kind == "complete":
                print(f"  [EVENT] complete {data}", flush=True)
        except Exception:
            pass

    dt = time.time() - t0
    print("=" * 60, flush=True)
    print(f"总耗时:        {dt:.1f}s", flush=True)
    print(f"SSE 事件数:    {event_count}", flush=True)
    print(f"text 事件数:   {text_tokens}", flush=True)
    print(f"工具调用:      {tool_calls}", flush=True)
    # 从日志里无法直接拿到 LLM 调用次数，靠 loop 日志统计（[AgentLoop] 第 N 轮决策）
    print("=" * 60, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
