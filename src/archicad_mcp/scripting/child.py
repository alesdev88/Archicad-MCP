"""Start the runner in a fresh interpreter and collect its reply.

A child process rather than a thread: Python cannot stop a thread, so a
runaway loop would hold the server until restart, and a crash or exit in the
script would take the server with it.
"""
from __future__ import annotations

import json
import subprocess
import sys

STDERR_TAIL = 2000


def run_child(code: str, port: int, timeout_s: float) -> dict:
    """Run `code` against `port`. Returns the runner's reply, or {"error": ...}."""
    request = json.dumps({"code": code, "port": port}, ensure_ascii=False)
    try:
        # sys.executable is the server's own interpreter, which inside the
        # .mcpb bundle is the bundled runtime with this package installed.
        proc = subprocess.run(
            [sys.executable, "-I", "-m", "archicad_mcp.scripting.runner"],
            input=request, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # subprocess.run has already killed the child. Its captured print
        # output died with it, because the runner buffers it.
        return {"error": f"script timed out after {timeout_s:g} s and was stopped"}
    try:
        reply = json.loads(proc.stdout)
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        return {"error": f"script process exited with code {proc.returncode} "
                         "without a reply",
                "stderr": (proc.stderr or "")[-STDERR_TAIL:]}
    return reply
