from __future__ import annotations

import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Archicad writes this exact declaration. ElementTree's own declaration uses
# single quotes and drops standalone, so we emit ours and suppress its.
DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'

# ElementTree emits "<Foo />", Archicad writes "<Foo/>". Purely cosmetic to a
# parser, but we round-trip byte for byte so that a no-op edit provably changes
# nothing, which is what makes it safe to leave unmodelled sections alone.
#
# ElementTree writes comment and PI text unescaped, so a blanket substitution
# would rewrite a " />" that happens to sit inside one. Substitute only in the
# spans between them.
_COMMENT_OR_PI = re.compile(r"<!--.*?-->|<\?.*?\?>", re.DOTALL)


def _tighten_self_closing(text: str) -> str:
    parts, last = [], 0
    for match in _COMMENT_OR_PI.finditer(text):
        parts.append(text[last:match.start()].replace(" />", "/>"))
        parts.append(match.group(0))
        last = match.end()
    parts.append(text[last:].replace(" />", "/>"))
    return "".join(parts)


def load_scheme_tree(path: Path) -> ET.ElementTree:
    # The default TreeBuilder silently discards comments and processing
    # instructions. Archicad files may carry either, and a construct that
    # vanishes without an error is exactly what we must not do, so we ask
    # the parser to keep them as tree nodes instead. This only covers
    # comments and PIs nested inside the root element: CPython's TreeBuilder
    # only attaches an inserted comment when the element stack is non-empty,
    # so a document-level comment or PI (before or after the root element) is
    # still silently dropped. round_trips_exactly is what catches that case.
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    return ET.parse(path, parser=parser)


def dumps_scheme_tree(tree: ET.ElementTree) -> str:
    body = ET.tostring(tree.getroot(), encoding="unicode")
    return DECLARATION + _tighten_self_closing(body) + "\n"


def save_scheme_tree(tree: ET.ElementTree, path: Path) -> None:
    # write_text truncates path before writing a byte of the new content, so
    # a write that fails partway (a full disk, for instance) leaves an
    # existing destination empty or partial. Writing the full content to a
    # temporary file first and only then replacing the destination in one
    # step means the destination is either the old file or the new one,
    # never something in between. The temporary file must be in the same
    # directory as path, because os.replace is only guaranteed atomic within
    # a single filesystem.
    text = dumps_scheme_tree(tree)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(text, encoding="utf-8")
        # mkstemp creates the file with mode 0o600 for security, but os.replace
        # carries that mode to the destination. For scheme files shared between
        # users and imported back into Archicad, ordinary permissions are needed.
        if os.name != "nt":
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp_name, 0o666 & ~umask)
        os.replace(tmp_name, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def round_trips_exactly(path: Path) -> bool:
    """True when this file survives a no-op load and save unchanged.

    The guard for everything we do not model. Callers verify this before
    editing, so a file with a construct our serializer would rewrite is
    refused loudly instead of being silently mangled.
    """
    original = path.read_text(encoding="utf-8")
    tree = load_scheme_tree(path)
    return dumps_scheme_tree(tree) == original
