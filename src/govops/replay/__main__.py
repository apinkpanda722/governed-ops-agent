"""Run the replay MCP server on localhost only."""

import argparse
from pathlib import Path

import uvicorn

from govops.replay.fake_a import create_app
from govops.replay.recordings import RecordingSet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--tokens-file", type=Path, required=True)
    parser.add_argument("--recordings", type=Path, default=Path("data/replay/a"))
    args = parser.parse_args(argv)
    app = create_app(
        recordings=RecordingSet.load(args.recordings), tokens_file=args.tokens_file
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
