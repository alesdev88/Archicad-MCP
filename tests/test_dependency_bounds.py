"""The dependencies a fresh install resolves, not the ones uv.lock pins."""

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _requirement(name: str) -> str:
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    return next(d for d in deps if re.match(rf"{name}\b", d.replace("-", "_")))


def test_multiconn_archicad_has_an_upper_bound():
    # multiconn-archicad 0.9.0 dropped multiconn_archicad.basic_types, which
    # connection.py imports; with no upper bound every fresh install of 0.7.0
    # took it and crashed at start. The lock and the tests stayed green, so
    # only a cap keeps users on a version the suite has run against.
    assert "<" in _requirement("multiconn_archicad")
