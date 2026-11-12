"""Screenshot fallback for an already-connected MCP client with a cached catalog."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentboard.service import BoardService, dump


def main():
    parser = argparse.ArgumentParser(description="获取黑板PNG与世界坐标信息，不改变用户视角")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--board-id", default="board_local")
    parser.add_argument("--view", choices=("all","card","region"), default="all")
    parser.add_argument("--card-id")
    parser.add_argument("--region", type=float, nargs=4, metavar=("X","Y","W","H"))
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--theme", choices=("light","dark"), default="light")
    args = parser.parse_args()
    request = {"board_id":args.board_id,"view":args.view,"width":args.width,"height":args.height,"theme":args.theme}
    if args.card_id:
        request["card_id"] = args.card_id
    if args.region:
        request["region"] = dict(zip(("x","y","w","h"),args.region))
    result = BoardService(args.data_dir).call("get_board_screenshot",request)
    result.get("data",{}).pop("image_base64",None)
    sys.stdout.reconfigure(encoding="utf-8")
    print(dump(result),flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
