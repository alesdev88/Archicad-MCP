from __future__ import annotations

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
