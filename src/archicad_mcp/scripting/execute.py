"""Run one script with `ac` bound, and shape what comes back.

Kept free of process handling so that everything a script can do to its
namespace is testable in-process against FakeCore.
"""
from __future__ import annotations

import contextlib
import io
import json
import traceback
from dataclasses import dataclass

TRACEBACK_LINES = 30


@dataclass
class Outcome:
    result: object = None
    stdout: str = ""
    error: str | None = None
    traceback: str | None = None


def execute(code: str, ac) -> Outcome:
    """Run `code` with `ac` and `result` bound. Never raises for script errors."""
    buffer = io.StringIO()
    namespace = {"__name__": "__script__", "ac": ac, "result": None}
    outcome = Outcome()
    try:
        compiled = compile(code, "<script>", "exec")
        # Captured, because the process's stdout carries the reply document
        # and one stray print there would corrupt it.
        with contextlib.redirect_stdout(buffer):
            exec(compiled, namespace)  # noqa: S102 - running the caller's code is the tool
    except SystemExit as exc:
        outcome.error = f"script called exit({exc.code})"
    except Exception as exc:  # noqa: BLE001 - every script failure is reported
        outcome.error = f"{type(exc).__name__}: {exc}"
        lines = traceback.format_exc().rstrip().splitlines()
        outcome.traceback = "\n".join(lines[-TRACEBACK_LINES:])
    outcome.stdout = buffer.getvalue()
    outcome.result = namespace.get("result")
    return outcome


def to_jsonable(value):
    """JSON-safe copy of value; anything JSON cannot carry becomes str()."""
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def cap_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return f"{text[:limit]}... [truncated, {len(text) - limit} more characters]", True


def cap_result(value, limit: int) -> tuple[object, bool]:
    """The value itself when its JSON fits, otherwise its capped JSON text.

    A careless `result = all_the_data` must not flood the conversation, which
    is the whole reason scripts exist.
    """
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return value, False
    return cap_text(text, limit)
