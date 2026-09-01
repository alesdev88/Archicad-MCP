from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import element_payload

# Split out of a single manage_issues(action=...) for the reason given in
# selection.py: reading the issue list and writing to it are different
# operations with different risk, and a reviewer cannot tell them apart when
# they share a tool. Every write here lands in the project file.


def list_issues(conn: ArchicadConnection) -> dict:
    return {"issues": conn.tapir("GetIssues").get("issues", [])}


def create_issue(conn: ArchicadConnection, name: str) -> dict:
    if not name:
        return {"error": "create_issue requires a non-empty 'name'."}
    response = conn.tapir("CreateIssue", {"name": name})
    return {"issue_id": response.get("issueId", {}).get("guid")}


def add_issue_comment(conn: ArchicadConnection, issue_id: str, comment: str) -> dict:
    if not issue_id or not comment:
        return {"error": "add_issue_comment requires 'issue_id' and 'comment'."}
    conn.tapir("AddCommentToIssue", {"issueId": {"guid": issue_id}, "text": comment})
    return {"commented": issue_id}


def attach_elements_to_issue(conn: ArchicadConnection, issue_id: str,
                             guids: list[str]) -> dict:
    if not issue_id or not guids:
        return {"error": "attach_elements_to_issue requires 'issue_id' and 'guids'."}
    conn.tapir("AttachElementsToIssue", {"issueId": {"guid": issue_id},
                                         "elements": element_payload(guids),
                                         "type": "Highlight"})
    return {"attached": len(guids)}


def export_issues_bcf(conn: ArchicadConnection, bcf_path: str) -> dict:
    if not bcf_path:
        return {"error": "export_issues_bcf requires 'bcf_path'."}
    conn.tapir("ExportIssuesToBCF", {"exportPath": bcf_path, "useExternalId": False,
                                     "alignBySurveyPoint": True})
    return {"exported": bcf_path}


def import_issues_bcf(conn: ArchicadConnection, bcf_path: str) -> dict:
    if not bcf_path:
        return {"error": "import_issues_bcf requires 'bcf_path'."}
    conn.tapir("ImportIssuesFromBCF", {"importPath": bcf_path,
                                       "alignBySurveyPoint": True})
    return {"imported": bcf_path}
