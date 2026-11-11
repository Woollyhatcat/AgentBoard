"""Run `python -m agentboard serve` or `python -m agentboard mcp`."""

import argparse
from pathlib import Path

from .http import create_server
from .mcp import serve_stdio
from .service import BoardService
from .service import dump
from .runtime import ensure_web, stop_web


def main():
    parser = argparse.ArgumentParser(description="AgentBoard 本地共享黑板")
    parser.add_argument("mode", choices=("serve", "start", "stop", "mcp"), nargs="?", default="serve")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--agent-id", default="local_agent")
    parser.add_argument("--agent-name", default="Agent")
    parser.add_argument("--no-web", action="store_true", help="MCP 仅提供数据工具，不自动启动网页")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口须为 1～65535。")
    if args.mode in ("start", "stop"):
        status = (ensure_web if args.mode == "start" else stop_web)(args.data_dir, args.port)
        print(dump(status), flush=True)
        if status["status"] == "unavailable":
            raise SystemExit(1)
        return
    service = BoardService(args.data_dir)
    if args.mode == "mcp":
        serve_stdio(service, {"id": args.agent_id, "kind": "agent", "display_name": args.agent_name},
                    web_port=None if args.no_web else args.port)
    else:
        server = create_server(service, port=args.port)
        print(f"AgentBoard: http://127.0.0.1:{args.port}", flush=True)
        print(f"数据目录: {service.data_dir}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
