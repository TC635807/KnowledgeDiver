"""入口：python -m backend.mcp [--username X] [--session Y]

默认以 stdio 方式服务 MCP（stdout 仅协议消息）。
  --list-tools   打印只读工具清单后退出（人工核验用，非 MCP 模式）
  --print-executor-enabled  打印 executor 实际启用集合后退出（验证只读兜底）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from backend.mcp.runtime import build_gateway


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m backend.mcp",
        description="KnowledgeDiver 只读 MCP server（本地 stdio）",
    )
    parser.add_argument("--username", help="KnowledgeDiver 用户名（或环境变量 KD_MCP_USERNAME）")
    parser.add_argument("--session", default=None, help="会话 ID（或 KD_MCP_SESSION，默认 default）")
    parser.add_argument("--list-tools", action="store_true", help="打印只读工具清单后退出")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # 日志必须走 stderr：stdout 是 JSON-RPC 通道
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="[mcp] %(levelname)s %(message)s")
    args = _parse_args(argv)

    try:
        gateway = build_gateway(args.username, args.session)
    except Exception as exc:  # noqa: BLE001
        print(f"[mcp] 启动失败: {exc}", file=sys.stderr)
        return 2

    if args.list_tools:
        # 便捷核验模式：显式输出为 JSON（此时不服务 MCP 协议）
        print(json.dumps({"tools": gateway.list_tools()}, ensure_ascii=False, indent=2))
        return 0

    from backend.mcp.server import McpServer
    from backend.mcp.stdio import serve_stdio

    server = McpServer(gateway)
    logging.info(
        "只读 MCP server 就绪：用户=%s 工具=%s",
        args.username, ",".join(gateway.tool_names()),
    )
    serve_stdio(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
