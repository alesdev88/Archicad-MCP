"""Child-process entry point for run_script.

Reads {"code", "port"} as JSON on stdin and writes exactly one JSON document to
the reply pipe. Started as `python -I -X utf8 -m archicad_mcp.scripting.runner`:
-I keeps a stray PYTHONPATH or user site-packages out of the interpreter that
runs the caller's code.
"""
from __future__ import annotations

import json
import os
import sys

RESULT_NOTE = "result could not be converted to JSON; returned as text"


def _text(value) -> str:
    try:
        return repr(value)
    except Exception:  # noqa: BLE001 - a broken __repr__ must not lose the plan
        return f"<{type(value).__name__} that could not be shown>"


def main() -> int:
    # The reply gets its own copy of the pipe, and file descriptor 1 is pointed
    # at stderr. Rebinding sys.stdout alone is not enough: a subprocess, a C
    # extension, os.system or sys.__stdout__ all write to descriptor 1, and one
    # stray line there would corrupt the reply.
    reply_fd = os.dup(1)
    os.dup2(2, 1)
    # UTF-8 both ways, whatever the platform's locale: Windows pipes default to
    # a code page that cannot carry Slovenian property values.
    reply_stream = os.fdopen(reply_fd, "w", encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    # Anything that writes to sys.stdout during setup (a library, a warning)
    # goes to stderr too.
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
        "stdout": outcome.stdout,
        "error": outcome.error,
        "traceback": outcome.traceback,
        # A script that raised offers nothing for apply: half a plan is worse
        # than none.
        "operations": [] if failed else recorder.operations,
        "skipped": [] if failed else recorder.skipped,
    }
    try:
        reply["result"] = to_jsonable(outcome.result)
    except (TypeError, ValueError, RecursionError):
        # Tuple keys or a circular structure: default=str cannot help there.
        # The text still says what the script returned, and the recorded
        # operations must not be lost over it.
        reply["result"] = _text(outcome.result)
        reply["result_note"] = RESULT_NOTE
    try:
        text = json.dumps(reply, default=str, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError) as exc:
        # Only recorded command params can get here (the rest is text or
        # already converted), and a plan that cannot be carried is no plan.
        text = json.dumps({"error": f"the script's recorded operations could not "
                                    f"be converted to JSON: {exc}"})
    reply_stream.write(text)
    reply_stream.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
