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


def test_a_recorded_write_is_dropped_when_the_script_then_raises():
    # DeleteElements with no elements is a valid, recordable write that
    # sends nothing even if it were applied.
    reply = run_child('ac.cmd("DeleteElements", {"elements": []})\n'
                      'raise RuntimeError("x")', PORT, 30)
    assert reply["error"] == "RuntimeError: x"
    assert reply["operations"] == []


def test_a_subprocess_writing_to_stdout_does_not_corrupt_the_reply():
    reply = run_child("import subprocess, sys\n"
                      "subprocess.run([sys.executable, '-c', \"print('hi')\"])\n"
                      "result = 1", PORT, 30)
    assert reply["result"] == 1
    assert reply["error"] is None


def test_writing_to_the_original_stdout_does_not_corrupt_the_reply():
    reply = run_child("import sys\nsys.__stdout__.write('junk')\n"
                      "sys.__stdout__.flush()\nresult = 1", PORT, 30)
    assert reply["result"] == 1


def test_a_result_json_cannot_carry_comes_back_as_text_with_the_plan():
    reply = run_child('ac.cmd("DeleteElements", {"elements": []})\n'
                      'result = {(1, 2): 3}', PORT, 30)
    assert reply["result"] == "{(1, 2): 3}"
    assert "could not be converted" in reply["result_note"]
    assert reply["operations"] == [{"kind": "command", "name": "DeleteElements",
                                    "params": {"elements": []}}]


def test_a_circular_result_comes_back_as_text():
    reply = run_child("a = []\na.append(a)\nresult = a", PORT, 30)
    assert reply["result"] == "[[...]]"
    assert "result_note" in reply


def test_bytes_that_are_not_utf8_on_stderr_do_not_break_the_reply():
    reply = run_child("import sys\nsys.stderr.buffer.write(b'\\xe8\\n')\n"
                      "sys.stderr.flush()\nresult = 1", PORT, 30)
    assert reply["result"] == 1


def test_a_missing_interpreter_is_an_error_not_an_exception(monkeypatch):
    import archicad_mcp.scripting.child as child_mod
    monkeypatch.setattr(child_mod.sys, "executable", "/nonexistent/python")
    reply = run_child("result = 1", PORT, 30)
    assert "could not start" in reply["error"]


def test_an_invalid_timeout_is_an_error_not_an_exception():
    reply = run_child("result = 1", PORT, float("nan"))
    assert "error" in reply
