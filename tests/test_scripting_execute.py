"""execute() runs code with `ac` bound; failures are reported, never raised."""
from archicad_mcp.scripting.execute import cap_result, cap_text, execute, to_jsonable

AC = object()


def test_result_and_print_come_back():
    outcome = execute("print('hi')\nresult = {'n': ac is not None}", AC)
    assert outcome.result == {"n": True}
    assert outcome.stdout == "hi\n"
    assert outcome.error is None and outcome.traceback is None


def test_result_defaults_to_none():
    assert execute("x = 1", AC).result is None


def test_an_exception_is_reported_with_its_traceback():
    outcome = execute("print('before')\n1 / 0", AC)
    assert outcome.error == "ZeroDivisionError: division by zero"
    assert "ZeroDivisionError" in outcome.traceback
    assert outcome.stdout == "before\n"


def test_a_syntax_error_is_reported():
    outcome = execute("def (", AC)
    assert outcome.error.startswith("SyntaxError")


def test_sys_exit_is_reported_not_propagated():
    outcome = execute("import sys\nsys.exit(3)", AC)
    assert outcome.error == "script called exit(3)"


def test_traceback_keeps_only_the_last_lines():
    code = "def f(n):\n    return f(n - 1) if n else 1 / 0\nf(50)"
    outcome = execute(code, AC)
    assert len(outcome.traceback.splitlines()) <= 30


def test_to_jsonable_stringifies_what_json_cannot_carry():
    assert to_jsonable({"s": {1}, "n": 2}) == {"s": "{1}", "n": 2}


def test_cap_text():
    assert cap_text("abc", 5) == ("abc", False)
    text, cut = cap_text("x" * 12, 5)
    assert cut is True
    assert text == "xxxxx... [truncated, 7 more characters]"


def test_cap_result_keeps_small_values_as_values():
    assert cap_result({"a": [1, 2]}, 100) == ({"a": [1, 2]}, False)


def test_cap_result_turns_large_values_into_capped_text():
    value, cut = cap_result(list(range(1000)), 20)
    assert cut is True
    assert isinstance(value, str) and value.startswith("[0, 1, 2")
    assert "truncated" in value
