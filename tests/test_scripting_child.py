"""run_child spawns a real interpreter. Scripts here never touch Archicad."""
import time

from archicad_mcp.scripting.child import run_child

PORT = 19743  # valid for multiconn, and constructing a connection does not connect


def test_result_round_trips():
    reply = run_child("result = {'a': 1}", PORT, 30)
    assert reply["result"] == {"a": 1}
    assert reply["error"] is None
    assert reply["operations"] == [] and reply["skipped"] == []


def test_non_ascii_survives_the_pipe():
    reply = run_child("result = 'Pozicija vrat, čšž'\nprint('ščž')", PORT, 30)
    assert reply["result"] == "Pozicija vrat, čšž"
    assert reply["stdout"] == "ščž\n"


def test_an_exception_comes_back_as_an_error():
    reply = run_child("1 / 0", PORT, 30)
    assert reply["error"].startswith("ZeroDivisionError")
    assert "ZeroDivisionError" in reply["traceback"]
    assert reply["operations"] == []


def test_sys_exit_is_reported():
    reply = run_child("import sys\nsys.exit(3)", PORT, 30)
    assert reply["error"] == "script called exit(3)"


def test_a_hard_exit_reports_the_exit_code_and_stderr():
    # Flushed by hand: os._exit skips the flush, and stderr is only line-buffered.
    reply = run_child("import os, sys\nsys.stderr.write('boom\\n')\nsys.stderr.flush()\nos._exit(3)",
                      PORT, 30)
    assert "code 3" in reply["error"]
    assert "boom" in reply["stderr"]


def test_an_endless_script_is_stopped_at_the_timeout():
    started = time.monotonic()
    reply = run_child("while True:\n    pass", PORT, 2)
    assert reply == {"error": "script timed out after 2 s and was stopped"}
    assert time.monotonic() - started < 15


def test_a_huge_print_does_not_corrupt_the_reply():
    reply = run_child("print('x' * 200000)\nresult = 1", PORT, 30)
    assert reply["result"] == 1
    assert len(reply["stdout"]) == 200001
