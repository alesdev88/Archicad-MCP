"""Child-process entry point for run_script.

Reads {"code", "port"} as JSON on stdin and writes exactly one JSON document to
stdout. Started as `python -I -m archicad_mcp.scripting.runner`: -I keeps a
stray PYTHONPATH or user site-packages out of the interpreter that runs the
caller's code.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    # UTF-8 both ways, whatever the platform's locale: Windows pipes default to
    # a code page that cannot carry Slovenian property values.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    reply_stream = sys.stdout
    # Only the reply may reach the parent's pipe; anything else that writes to
    # stdout during setup (a library, a warning) goes to stderr instead.
    sys.stdout = sys.stderr
    request = json.loads(sys.stdin.read())

    from archicad_mcp.connection import ArchicadConnection
    from archicad_mcp.gateway.registry import build_registry
    from archicad_mcp.scripting.api import Recorder, ScriptAPI
    from archicad_mcp.scripting.execute import execute, to_jsonable

    recorder = Recorder()
    ac = ScriptAPI(ArchicadConnection(request["port"]), build_registry(), recorder)
    outcome = execute(request["code"], ac)
    failed = outcome.error is not None
    reply = {
        "result": to_jsonable(outcome.result),
        "stdout": outcome.stdout,
        "error": outcome.error,
        "traceback": outcome.traceback,
        # A script that raised offers nothing for apply: half a plan is worse
        # than none.
        "operations": [] if failed else recorder.operations,
        "skipped": [] if failed else recorder.skipped,
    }
    json.dump(reply, reply_stream, default=str, ensure_ascii=False)
    reply_stream.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
