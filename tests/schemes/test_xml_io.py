import os
import platform
from pathlib import Path

import pytest

from archicad_mcp.schemes.xml_io import (
    dumps_scheme_tree,
    load_scheme_tree,
    round_trips_exactly,
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


def test_save_leaves_an_existing_destination_untouched_when_the_write_fails_partway(
        tmp_path, monkeypatch):
    """save_scheme_tree used to call path.write_text(...) directly, which
    opens (and so truncates) the destination before writing a single byte of
    the new content. A write that fails partway, a full disk being the
    obvious example, used to leave an existing destination empty or partial.
    save_scheme_tree now writes to a temporary file in the same directory
    first and only replaces the destination once that write has fully
    succeeded, so a failure anywhere in the write step must leave the
    original destination exactly as it was, and must not leave a temporary
    file behind either.

    Simulated here by patching the write step (Path.write_text, which is
    what writes the temporary file) rather than actually filling the disk."""
    dest = tmp_path / "existing.xml"
    dest.write_text("existing content that must survive", encoding="utf-8")
    tree = load_scheme_tree(FIXTURE)

    def boom(self, *args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(Path, "write_text", boom)

    with pytest.raises(OSError):
        save_scheme_tree(tree, dest)

    assert dest.read_text(encoding="utf-8") == "existing content that must survive"
    assert list(tmp_path.iterdir()) == [dest]


def test_comment_survives_round_trip_byte_exactly(tmp_path):
    original = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
        '<Root><!-- keep me --><Child value="1"/></Root>\n'
    )
    path = tmp_path / "with_comment.xml"
    path.write_text(original, encoding="utf-8")

    tree = load_scheme_tree(path)

    assert dumps_scheme_tree(tree) == original


def test_processing_instruction_survives_round_trip_byte_exactly(tmp_path):
    original = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
        '<Root><?custom-instruction do="something"?><Child value="1"/></Root>\n'
    )
    path = tmp_path / "with_pi.xml"
    path.write_text(original, encoding="utf-8")

    tree = load_scheme_tree(path)

    assert dumps_scheme_tree(tree) == original


def test_round_trips_exactly_is_true_for_the_sample_fixture():
    assert round_trips_exactly(FIXTURE) is True


def test_round_trips_exactly_is_false_for_an_explicit_empty_tag(tmp_path):
    original = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
        "<Root><Tag></Tag></Root>\n"
    )
    path = tmp_path / "explicit_empty.xml"
    path.write_text(original, encoding="utf-8")

    assert round_trips_exactly(path) is False


def test_comment_containing_self_closing_text_round_trips_byte_exactly(tmp_path):
    original = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
        '<Root><!-- see <Foo /> above --><Child value="1"/></Root>\n'
    )
    path = tmp_path / "comment_with_self_closing_text.xml"
    path.write_text(original, encoding="utf-8")

    tree = load_scheme_tree(path)

    assert dumps_scheme_tree(tree) == original


def test_round_trips_exactly_is_false_for_a_document_level_comment(tmp_path):
    original = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
        "<!-- document level, outside the root element -->\n"
        "<Root><Child value=\"1\"/></Root>\n"
    )
    path = tmp_path / "document_level_comment.xml"
    path.write_text(original, encoding="utf-8")

    assert round_trips_exactly(path) is False


def test_round_trips_exactly_raises_unicodedecodeerror_for_a_non_utf8_file(tmp_path):
    """round_trips_exactly reads path as UTF-8, same assumption as every other
    read in this module, since Archicad's own exports are always UTF-8. A
    file that declares, and is actually written in, a different encoding
    (here ISO-8859-1, with a genuine 0xE9 byte) is not a "this server would
    rewrite it" problem, the usual meaning of this function returning False;
    it is a different, more fundamental problem. This raises rather than
    silently folding both cases into one boolean, so the one real caller
    (edit_schedule_scheme, core/schemes.py) can tell them apart and explain
    the encoding mismatch clearly instead of misreporting it as an
    unmodelled-XML-construct warning."""
    original = (
        '<?xml version="1.0" encoding="ISO-8859-1" standalone="no" ?>\n'
        '<Root><Child value="caf\xe9"/></Root>\n'
    )
    path = tmp_path / "latin1.xml"
    path.write_bytes(original.encode("ISO-8859-1"))

    with pytest.raises(UnicodeDecodeError):
        round_trips_exactly(path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions do not apply on Windows")
def test_saved_file_has_ordinary_permissions(tmp_path):
    """Saved scheme files should have ordinary permissions (0o666 masked by
    umask), not owner-only (0o600). This is important because these files are
    Archicad scheme exports that users import back into Archicad and may share
    with colleagues."""
    tree = load_scheme_tree(FIXTURE)
    out = tmp_path / "out.xml"
    save_scheme_tree(tree, out)

    umask = os.umask(0)
    os.umask(umask)
    expected_mode = 0o666 & ~umask

    actual_mode = out.stat().st_mode & 0o777
    assert actual_mode == expected_mode
