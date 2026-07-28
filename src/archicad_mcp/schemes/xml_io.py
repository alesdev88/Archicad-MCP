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
    return ET.parse(path)


def dumps_scheme_tree(tree: ET.ElementTree) -> str:
    body = ET.tostring(tree.getroot(), encoding="unicode")
    return DECLARATION + _SELF_CLOSING.sub("/>", body) + "\n"


def save_scheme_tree(tree: ET.ElementTree, path: Path) -> None:
    path.write_text(dumps_scheme_tree(tree), encoding="utf-8")
