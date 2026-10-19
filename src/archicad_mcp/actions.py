from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import element_payload

_HIGHLIGHT_COLOR = [50, 255, 100, 100]      # green-ish, semi-transparent
_OTHER_COLOR = [0, 0, 255, 128]


def highlight_elements(conn: ArchicadConnection, guids: list[str]) -> dict:
    if not guids:
        return {"highlighted": 0}
    conn.tapir("HighlightElements", {
        "elements": element_payload(guids),
        "highlightedColors": [_HIGHLIGHT_COLOR for _ in guids],
        "wireframe3D": True,
        "nonHighlightedColor": _OTHER_COLOR,
    })
    return {"highlighted": len(guids)}


def clear_highlight(conn: ArchicadConnection) -> None:
    """Remove every highlight, with the tint and 3D wireframe that came with it."""
    conn.tapir("HighlightElements", {"elements": [], "highlightedColors": []})


def fit_in_window(conn: ArchicadConnection, guids: list[str]) -> bool:
    """Zoom the front window to the elements. False when this Tapir has no
    FitInWindow (1.3.1) or Archicad refuses; True says only that Archicad
    accepted it, since it also answers success for elements it cannot show."""
    if not guids or not conn.tapir_command_available("FitInWindow"):
        return False
    try:
        conn.tapir("FitInWindow", {"elements": element_payload(guids)})
    except APIErrorBase:
        return False
    return True


def create_issues(conn: ArchicadConnection, rule_id: str, message: str,
                  guids: list[str]) -> dict:
    response = conn.tapir("CreateIssue", {"name": f"[{rule_id}] {message}"})
    issue_id = response.get("issueId")
    attached = 0
    if issue_id and guids:
        conn.tapir("AttachElementsToIssue", {
            "issueId": issue_id,
            "elements": element_payload(guids),
            "type": "Highlight",
        })
        attached = len(guids)
    return {"issue_created": bool(issue_id), "attached": attached}
