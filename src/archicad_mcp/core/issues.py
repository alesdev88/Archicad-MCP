from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import element_payload


def manage_issues(conn: ArchicadConnection, action: str, name: str | None = None,
                  issue_id: str | None = None, comment: str | None = None,
                  guids: list[str] | None = None, bcf_path: str | None = None) -> dict:
    if action == "list":
        return {"issues": conn.tapir("GetIssues").get("issues", [])}
    if action == "create":
        if not name:
            return {"error": "manage_issues action 'create' requires 'name'."}
        response = conn.tapir("CreateIssue", {"name": name})
        return {"issue_id": response.get("issueId", {}).get("guid")}
    if action == "comment":
        if not issue_id or not comment:
            return {"error": "action 'comment' requires 'issue_id' and 'comment'."}
        conn.tapir("AddCommentToIssue", {"issueId": {"guid": issue_id}, "text": comment})
        return {"commented": issue_id}
    if action == "attach":
        if not issue_id or not guids:
            return {"error": "action 'attach' requires 'issue_id' and 'guids'."}
        conn.tapir("AttachElementsToIssue", {"issueId": {"guid": issue_id},
                                             "elements": element_payload(guids),
                                             "type": "Highlight"})
        return {"attached": len(guids)}
    if action == "export_bcf":
        if not bcf_path:
            return {"error": "action 'export_bcf' requires 'bcf_path'."}
        conn.tapir("ExportIssuesToBCF", {"exportPath": bcf_path, "useExternalId": False,
                                         "alignBySurveyPoint": True})
        return {"exported": bcf_path}
    if action == "import_bcf":
        if not bcf_path:
            return {"error": "action 'import_bcf' requires 'bcf_path'."}
        conn.tapir("ImportIssuesFromBCF", {"importPath": bcf_path,
                                           "alignBySurveyPoint": True})
        return {"imported": bcf_path}
    return {"error": f"Unknown action '{action}'. Valid: list, create, comment, "
                     "attach, export_bcf, import_bcf."}
