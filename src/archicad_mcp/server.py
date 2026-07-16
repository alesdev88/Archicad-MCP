from __future__ import annotations

import argparse
import os
from pathlib import Path

from fastmcp import FastMCP


def build_server(
    mode: str = "full",
    rules_dir: Path | None = None,
    port: int | None = None,
) -> FastMCP:
    if mode not in ("verdicts", "full"):
        raise ValueError(f"mode must be 'verdicts' or 'full', got {mode!r}")
    mcp = FastMCP("archicad-mcp")

    @mcp.tool(name="ping", description="Health check: confirms the archicad-mcp server is running.")
    def ping() -> dict:
        return {"status": "ok", "server": "archicad-mcp"}

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="archicad-mcp")
    parser.add_argument("--mode", choices=["verdicts", "full"],
                        default=os.environ.get("ARCHICAD_MCP_MODE", "full"))
    parser.add_argument("--rules-dir", type=Path,
                        default=os.environ.get("ARCHICAD_MCP_RULES_DIR"))
    parser.add_argument("--port", type=int, default=None,
                        help="Archicad API port (19723-19743); auto-detected if omitted")
    args, _ = parser.parse_known_args()
    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    build_server(mode=args.mode, rules_dir=rules_dir, port=args.port).run()


if __name__ == "__main__":
    main()
