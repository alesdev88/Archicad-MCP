from pathlib import Path

from archicad_mcp.schemes.xml_io import (
    dumps_scheme_tree,
    load_scheme_tree,
    save_scheme_tree,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def test_round_trip_is_byte_identical():
    original = FIXTURE.read_text(encoding="utf-8")
    tree = load_scheme_tree(FIXTURE)
    assert dumps_scheme_tree(tree) == original


def test_round_trip_keeps_the_declaration_verbatim():
    tree = load_scheme_tree(FIXTURE)
    first_line = dumps_scheme_tree(tree).splitlines()[0]
    assert first_line == '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>'


def test_self_closing_tags_have_no_leading_space():
    tree = load_scheme_tree(FIXTURE)
    assert " />" not in dumps_scheme_tree(tree)


def test_output_ends_with_exactly_one_newline():
    tree = load_scheme_tree(FIXTURE)
    text = dumps_scheme_tree(tree)
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_save_writes_utf8_bytes(tmp_path):
    tree = load_scheme_tree(FIXTURE)
    out = tmp_path / "out.xml"
    save_scheme_tree(tree, out)
    assert out.read_bytes() == FIXTURE.read_bytes()
