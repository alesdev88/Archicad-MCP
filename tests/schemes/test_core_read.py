import os
import shutil
import sys

import pytest

from archicad_mcp.core.schemes import read_schedule_scheme
from tests.schemes.conftest import FIXTURE


def test_reports_scheme_header():
    out = read_schedule_scheme(str(FIXTURE))
    assert out["name"] == "Sample Door Scheme"
    assert out["scheme_id"] == "9001"
    assert out["column_count"] == 3


def test_describes_each_column_binding_in_words():
    out = read_schedule_scheme(str(FIXTURE))
    rows = {c["caption"]: c for c in out["columns"]}
    assert rows["Door ID"]["binds_to"] == "property"
    assert rows["Quantity"]["binds_to"] == "builtin"
    assert rows["Fire Resistance"]["binds_to"] == "gdl_param"
    assert rows["Fire Resistance"]["detail"] == "Fire Rating Param"
    assert rows["Door ID"]["index"] == 0


def test_reports_criteria():
    out = read_schedule_scheme(str(FIXTURE))
    assert len(out["criteria"]) == 2
    assert out["criteria"][0]["target"] == "D8F07689-9CFA-4FBE-AEB4-0A60B8E667EE"


def test_missing_file_returns_an_error_envelope():
    out = read_schedule_scheme("/nonexistent/nope.xml")
    assert "error" in out
    assert "not found" in out["error"].lower()


def test_non_scheme_xml_returns_an_error_envelope(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text('<?xml version="1.0"?>\n<BuildingInformation/>\n', encoding="utf-8")
    out = read_schedule_scheme(str(bad))
    assert "error" in out
    assert "Scheme_Settings" in out["error"]


def test_scheme_without_a_root_header_item_is_rejected(tmp_path):
    bad = tmp_path / "rootless.xml"
    bad.write_text('<?xml version="1.0"?>\n<Scheme_Settings ID="1" Name="X">'
                   "<Header_Items/></Scheme_Settings>\n", encoding="utf-8")
    out = read_schedule_scheme(str(bad))
    assert "error" in out
    assert "root Header_Item" in out["error"]


def test_malformed_xml_returns_an_error_envelope(tmp_path):
    bad = tmp_path / "broken.xml"
    bad.write_text("<Scheme_Settings><unclosed>\n", encoding="utf-8")
    out = read_schedule_scheme(str(bad))
    assert "error" in out
    assert "not valid XML" in out["error"]


def test_directory_path_returns_a_distinct_error_envelope(tmp_path):
    out = read_schedule_scheme(str(tmp_path))
    assert "error" in out
    assert "directory" in out["error"].lower()
    assert "not found" not in out["error"].lower()


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permission semantics, and root bypasses them",
)
def test_unreadable_file_returns_an_error_envelope_instead_of_raising(tmp_path):
    unreadable = tmp_path / "unreadable.xml"
    shutil.copy(FIXTURE, unreadable)
    os.chmod(unreadable, 0o000)
    try:
        out = read_schedule_scheme(str(unreadable))
    finally:
        # Restore permissions so pytest can clean up tmp_path afterwards.
        os.chmod(unreadable, 0o644)
    assert "error" in out
    assert "could not be read" in out["error"]


def test_extremely_long_path_returns_an_error_envelope_instead_of_raising():
    # On Python 3.12 and 3.13, Path.is_dir()/is_file() raise OSError
    # (ENAMETOOLONG) for a path this long instead of returning False, and
    # that used to escape _load uncaught. On 3.14+ pathlib swallows it and
    # is_dir()/is_file() just return False, which the existing "not found"
    # branch already handles. Either way this path does not exist, so an
    # error envelope is the correct result on every supported version.
    long_path = "/" + "a" * 5000
    out = read_schedule_scheme(long_path)
    assert "error" in out


def test_nonexistent_home_directory_returns_an_error_envelope_instead_of_raising():
    # Path.expanduser() raises RuntimeError("Could not determine home directory.")
    # when given a path like ~nosuchuser/scheme.xml that references a nonexistent
    # user. This must be caught and returned as an error envelope, not raised
    # uncaught, or it breaks the "always returns a dict" contract.
    out = read_schedule_scheme("~nosuchuser12345/scheme.xml")
    assert "error" in out
    assert "could not be resolved" in out["error"].lower()
