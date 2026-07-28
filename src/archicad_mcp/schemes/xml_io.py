from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

# Archicad writes this exact declaration. ElementTree's own declaration uses
# single quotes and drops standalone, so we emit ours and suppress its.
DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'

# ElementTree emits "<Foo />", Archicad writes "<Foo/>". Purely cosmetic to a
# parser, but we round-trip byte for byte so that a no-op edit provably changes
# nothing, which is what makes it safe to leave unmodelled sections alone.
_SELF_CLOSING = re.compile(r" />")


def load_scheme_tree(path: Path) -> ET.ElementTree:
    # The default TreeBuilder silently discards comments and processing
    # instructions. Archicad files may carry either, and a construct that
    # vanishes without an error is exactly what we must not do, so we ask
    # the parser to keep them as tree nodes instead.
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    return ET.parse(path, parser=parser)


def dumps_scheme_tree(tree: ET.ElementTree) -> str:
    body = ET.tostring(tree.getroot(), encoding="unicode")
    return DECLARATION + _SELF_CLOSING.sub("/>", body) + "\n"


def save_scheme_tree(tree: ET.ElementTree, path: Path) -> None:
    path.write_text(dumps_scheme_tree(tree), encoding="utf-8")


def round_trips_exactly(path: Path) -> bool:
    """True when this file survives a no-op load and save unchanged.

    The guard for everything we do not model. Callers verify this before
    editing, so a file with a construct our serializer would rewrite is
    refused loudly instead of being silently mangled.
    """
    original = path.read_text(encoding="utf-8")
    tree = load_scheme_tree(path)
    return dumps_scheme_tree(tree) == original
