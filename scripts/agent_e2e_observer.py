"""Agent 端到端全功能观察器 — 记录每个 SSE 事件的时间戳，用于验证工具链与时延。

用法:
    .venv/Scripts/python.exe scripts/agent_e2e_observer.py <username> <session_id> <message>

事件日志保存到 outputs/agent_e2e_<session_id>.jsonl，便于事后分析。
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

OUT_DIR = ROOT / "outputs"


async def main() -> None:
    from backend.agent.loop import run_agent_loop

    username = sys.argv[1]
    session_id = sys.argv[2]
    message = " ".join(sys.argv[3:])

    OUT_DIR.mkdir(exist_ok=True)
    log_path = OUT_DIR / f"agent_e2e_{session_id}.jsonl"
    with log_path.open("w", encoding="utf-8") as logf:
        t0 = time.time()
        stats: dict[str, int] = {}
        text_chars = 0
        print(f"== {username}/{session_id}: {message[:60]}... ==", flush=True)

        async for raw in run_agent_loop(username, session_id, message):
            ts = time.time() - t0
            logf.write(json.dumps({"t": round(ts, 2), "raw": raw}, ensure_ascii=False) + "\n")
            try:
                payload = json.loads(raw[len("data: "):].strip())
                kind = payload["type"]
                data = payload["data"]
                stats[kind] = stats.get(kind, 0) + 1
                if kind == "text":
                    content = data["content"]
                    text_chars += len(content)
                    line = content.replace("\n", " ")[:80]
                    print(f"[{ts:6.1f}s] text: {line}", flush=True)
                elif kind == "tool_call":
                    print(f"[{ts:6.1f}s] 🔧 {data.get('name')} args={data.get('args')}", flush=True)
                elif kind == "tool_result":
                    summary = (data.get("summary") or "").replace("\n", " ")[:100]
                    print(
                        f"[{ts:6.1f}s] ✅/❌ {data.get('tool')} success={data.get('success')} "
                        f"-> {summary}",
                        flush=True,
                    )
                elif kind == "heartbeat":
                    print(f"[{ts:6.1f}s] ♥ pending={data.get('pending')}", flush=True)
                elif kind == "complete":
                    print(f"[{ts:6.1f}s] ★ COMPLETE {data}", flush=True)
                elif kind == "error":
                    print(f"[{ts:6.1f}s] ⚠ ERROR {data}", flush=True)
            except Exception:
                pass

        dt = time.time() - t0
        print("=" * 60, flush=True)
        print(f"总耗时 {dt:.1f}s | 事件统计 {stats} | 文本 {text_chars} 字符", flush=True)
        print(f"事件日志: {log_path}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
