"""Generate a self-contained HTML dashboard of every API command the MCP server
can reach: the official Archicad JSON API plus the Tapir add-on commands, grouped
logically, with coverage marked (which commands have a dedicated first-class tool
versus which are reachable only through the generic gateway tools).

Run it to refresh the page whenever the bundled definitions change:

    uv run python scripts/build_dashboard.py

Output: docs/api-dashboard.html (open it directly, or serve the folder with
`python3 -m http.server` and browse to /docs/api-dashboard.html).
"""
from __future__ import annotations

import datetime
import html
import json
import re
import subprocess
from pathlib import Path

from archicad_mcp.gateway.registry import build_registry

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "api-dashboard.html"
DEFS = ROOT / "src" / "archicad_mcp" / "gateway" / "definitions" / "command_definitions.js"
VERSION_FILE = ROOT / "src" / "archicad_mcp" / "gateway" / "definitions" / "tapir_version.json"
TAPIR_REPO = "ENZYME-APD/tapir-archicad-automation"

# Underlying API command -> the dedicated MCP tool(s) that surface it.
# Derived from src/archicad_mcp/core/*, actions.py and extract.py. A command that
# appears here is "first-class" (a curated, safety-checked tool wraps it); every
# other command in the catalog is reachable through execute_read_api_command or
# execute_write_api_command, whichever matches its access classification.
WRAPPED_BY: dict[str, list[str]] = {
    "API.GetAllElements": ["query_elements", "get_model_summary"],
    "API.GetSelectedElements": ["query_elements", "get_selection"],
    "API.GetTypesOfElements": ["get_element_data"],
    "API.GetPropertyIds": ["get_element_data"],
    "API.GetPropertyValuesOfElements": ["get_element_data"],
    "API.GetClassificationsOfElements": ["get_element_data"],
    "API.GetAllClassificationSystems": ["get_element_data"],
    "GetIFCPropertiesOfElements": ["get_element_data"],
    "GetDetailsOfElements": ["get_element_data"],
    "API.SetPropertyValuesOfElements": ["set_element_data"],
    "MoveElements": ["move_elements"],
    "DeleteElements": ["delete_elements"],
    "ChangeSelectionOfElements": ["set_selection", "clear_selection"],
    "CreateColumns": ["create_elements"],
    "CreateSlabs": ["create_elements"],
    "CreateZones": ["create_elements"],
    "CreatePolylines": ["create_elements"],
    "CreateObjects": ["create_elements"],
    "CreateMeshes": ["create_elements"],
    "GetIssues": ["list_issues"],
    "CreateIssue": ["create_issue", "create_issues_from_failures"],
    "AddCommentToIssue": ["add_issue_comment"],
    "AttachElementsToIssue": ["attach_elements_to_issue", "create_issues_from_failures"],
    "ExportIssuesToBCF": ["export_issues_bcf"],
    "ImportIssuesFromBCF": ["import_issues_bcf"],
    "API.GetProductInfo": ["get_project_info"],
    "GetProjectInfo": ["get_project_info"],
    "GetStories": ["get_project_info"],
    "GetHotlinks": ["get_project_info"],
    "GetGeoLocation": ["get_project_info"],
    "API.GetAttributesByType": ["list_attributes"],
    "API.GetLayerAttributes": ["list_attributes"],
    "API.GetBuildingMaterialAttributes": ["list_attributes"],
    "API.GetCompositeAttributes": ["list_attributes"],
    "API.GetSurfaceAttributes": ["list_attributes"],
    "API.GetProfileAttributes": ["list_attributes"],
    "API.GetZoneCategoryAttributes": ["list_attributes"],
    "PublishPublisherSet": ["publish"],
    "HighlightElements": ["highlight_failures"],
}

# The dedicated high-level MCP tools, transcribed from server.py. "mode" is which
# server mode exposes the tool; "mutates" flags tools that can change the model.
TOOLS: list[dict] = [
    # Discovery and model
    {"name": "list_instances", "cat": "Discovery & model", "mode": "both",
     "desc": "List running Archicad instances: port, version, open project, Tapir add-on availability. Call this first."},
    {"name": "get_project_info", "cat": "Discovery & model", "mode": "full",
     "desc": "Project info: Archicad version, project name, stories, hotlinks, geolocation presence (Tapir enriches)."},
    {"name": "get_model_summary", "cat": "Discovery & model", "mode": "both",
     "desc": "Aggregate element counts by type (always), optionally by layer and story. Counts only, never element data."},
    {"name": "list_attributes", "cat": "Discovery & model", "mode": "full",
     "desc": "List attribute names by type: Layer, BuildingMaterial, Composite, Surface, Profile, ZoneCategory."},
    # Elements
    {"name": "query_elements", "cat": "Elements", "mode": "full",
     "desc": "Query elements with AND-combined filters: element_type, layer, story, classification_system, selection_only. Returns GUIDs and counts."},
    {"name": "get_element_data", "cat": "Elements", "mode": "full",
     "desc": "Read type, layer, requested properties (Group/Name) and optionally classifications for given element GUIDs."},
    {"name": "set_element_data", "cat": "Elements", "mode": "full", "mutates": True,
     "desc": "Write element property values. Dry-run by default: returns planned changes without touching the model. Pass dry_run=false to commit."},
    {"name": "create_elements", "cat": "Elements", "mode": "full", "mutates": True,
     "desc": "Create elements (column/slab/zone/polyline/object/mesh) via Tapir. Dry-run by default. Other types: use execute_write_api_command."},
    {"name": "move_elements", "cat": "Elements", "mode": "full", "mutates": True,
     "desc": "Move elements by a vector {x,y,z} in meters. Refuses without confirm=true."},
    {"name": "delete_elements", "cat": "Elements", "mode": "full", "mutates": True,
     "desc": "Delete elements. Irreversible. Refuses without confirm=true."},
    {"name": "get_selection", "cat": "Elements", "mode": "full",
     "desc": "Return the GUIDs of the elements currently selected in Archicad."},
    {"name": "set_selection", "cat": "Elements", "mode": "full", "mutates": True,
     "desc": "Replace the current selection with the given element GUIDs."},
    {"name": "clear_selection", "cat": "Elements", "mode": "full", "mutates": True,
     "desc": "Deselect everything in the Archicad window."},
    # QA and rules
    {"name": "list_rules", "cat": "QA & rules", "mode": "both",
     "desc": "List loaded QA rules (id, type, severity, tags) and any rule-file load errors."},
    {"name": "run_rule", "cat": "QA & rules", "mode": "both",
     "desc": "Run one QA rule by id. Returns a verdict: pass/fail, failure count, failing element GUIDs."},
    {"name": "audit_delivery_readiness", "cat": "QA & rules", "mode": "both",
     "desc": "Run all loaded QA rules (optionally only those tagged with 'ruleset') against the open model. Returns a scored verdict."},
    {"name": "verify_ifc_export_readiness", "cat": "QA & rules", "mode": "both",
     "desc": "Run only the IFC-related QA rules to check IFC export readiness. Requires the Tapir add-on for IFC data."},
    {"name": "highlight_failures", "cat": "QA & rules", "mode": "both", "mutates": True,
     "desc": "Highlight the elements failing a rule in the Archicad window (requires Tapir add-on)."},
    {"name": "create_issues_from_failures", "cat": "QA & rules", "mode": "both", "mutates": True,
     "desc": "Create an Archicad issue from a rule's failures and attach the failing elements (requires Tapir add-on)."},
    # Issues and publishing
    {"name": "list_issues", "cat": "Issues & publishing", "mode": "full",
     "desc": "List the issues in the open project, with their ids (requires the Tapir add-on)."},
    {"name": "create_issue", "cat": "Issues & publishing", "mode": "full", "mutates": True,
     "desc": "Create a new issue in the open project and return its id (requires the Tapir add-on)."},
    {"name": "add_issue_comment", "cat": "Issues & publishing", "mode": "full", "mutates": True,
     "desc": "Add a text comment to an existing issue (requires the Tapir add-on)."},
    {"name": "attach_elements_to_issue", "cat": "Issues & publishing", "mode": "full", "mutates": True,
     "desc": "Attach elements to an existing issue as highlights (requires the Tapir add-on)."},
    {"name": "export_issues_bcf", "cat": "Issues & publishing", "mode": "full", "mutates": True,
     "desc": "Export every issue in the project to a BCF file (requires the Tapir add-on)."},
    {"name": "import_issues_bcf", "cat": "Issues & publishing", "mode": "full", "mutates": True,
     "desc": "Import issues into the project from a BCF file (requires the Tapir add-on)."},
    {"name": "publish", "cat": "Issues & publishing", "mode": "full", "mutates": True,
     "desc": "Fire an Archicad publisher set by name (Tapir)."},
    # Schedules. No API command wraps these: Archicad exposes no schedule API at
    # any level, so they work on an exported Scheme Settings XML file instead.
    # That is also why none of them appears in WRAPPED_BY.
    {"name": "read_schedule_scheme", "cat": "Schedules", "mode": "full",
     "desc": "Describe an exported schedule scheme XML: its criteria and its ordered columns. Reads the file only, never Archicad."},
    {"name": "edit_schedule_scheme", "cat": "Schedules", "mode": "full", "mutates": True,
     "desc": "Apply a YAML scheme spec to an exported schedule scheme XML. Dry-run by default; never overwrites the input."},
    {"name": "validate_schedule_scheme", "cat": "Schedules", "mode": "full",
     "desc": "Check an exported scheme's property bindings against the open project. Reads definitions only, not values."},
    # Gateway
    {"name": "list_api_commands", "cat": "Raw API gateway", "mode": "full",
     "desc": "Catalog of ALL available Archicad API commands (official JSON API + Tapir), optionally filtered by group."},
    {"name": "describe_api_command", "cat": "Raw API gateway", "mode": "full",
     "desc": "Full description and input schema for one API command. Call before either execute tool."},
    {"name": "execute_read_api_command", "cat": "Raw API gateway", "mode": "full",
     "desc": "Run one read-only Archicad API command by name. A command that changes the project is refused here."},
    {"name": "execute_write_api_command", "cat": "Raw API gateway", "mode": "full", "mutates": True,
     "desc": "Run one Archicad API command that changes the project. Refuses without confirm=true."},
    # GDL library parts. Full mode plus a configured GDL workspace folder;
    # unlike every other row above, "mode": "full" alone does not fully
    # describe the gate, but it is the field this table has for it.
    {"name": "list_gdl_sources", "cat": "GDL library parts", "mode": "full",
     "desc": "List the GDL workspace: source meshes, built .gsm files, textures, and the objects already described in assets.json."},
    {"name": "inspect_gdl_source", "cat": "GDL library parts", "mode": "full",
     "desc": "Parse a source mesh and report its material groups, face counts, bounding box, and detected units."},
    {"name": "build_gdl_object", "cat": "GDL library parts", "mode": "full", "mutates": True,
     "desc": "Compile a source mesh into a .gsm library part and write it, with its textures, into the GDL workspace."},
    {"name": "deploy_gdl_object", "cat": "GDL library parts", "mode": "full", "mutates": True,
     "desc": "Reload libraries, place the built library part, render it, and return the image. Deletes the placed instance again unless keep=true."},
]


def _defs_sync_date() -> str | None:
    """Date the bundled definitions were last synced, YYYY-MM-DD. Prefers the git
    commit that last touched the file; falls back to the file's mtime."""
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI", "--", str(DEFS)],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        if out:
            return out[:10]
    except Exception:
        pass
    try:
        return datetime.date.fromtimestamp(DEFS.stat().st_mtime).isoformat()
    except OSError:
        return None


def tapir_info() -> dict:
    """The Tapir version the bundled command definitions correspond to, plus the
    coordinates the page uses to check GitHub for a newer release at view time.
    The authoritative version is recorded by sync_tapir_defs.py into
    tapir_version.json (the release tag the definitions were pulled from). We fall
    back to the highest per-command 'since' stamp only for definitions synced
    before that file existed -- that stamp tracks the newest command ever added,
    so it under-reports releases that changed no command (e.g. 1.5.5)."""
    meta = {}
    try:
        meta = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    version = meta.get("version")
    if not version:
        text = DEFS.read_text(encoding="utf-8")
        triples = re.findall(r'"version":\s*"(\d+)\.(\d+)\.(\d+)"', text)
        highest = max((tuple(map(int, t)) for t in triples), default=None)
        version = ".".join(map(str, highest)) if highest else None
    return {
        "bundled_version": version,
        "synced": meta.get("synced") or _defs_sync_date(),
        "repo": TAPIR_REPO,
        "api": f"https://api.github.com/repos/{TAPIR_REPO}/releases/latest",
        "releases": f"https://github.com/{TAPIR_REPO}/releases",
    }


def build_data() -> dict:
    reg = build_registry()
    commands = []
    for c in sorted(reg.values(), key=lambda c: (c.group, c.name)):
        d = c.to_dict()
        d["has_schema"] = c.input_schema is not None
        d["wrapped_by"] = WRAPPED_BY.get(c.name, [])
        commands.append(d)
    counts = {
        "total": len(commands),
        "tapir": sum(1 for c in commands if c["kind"] == "tapir"),
        "official": sum(1 for c in commands if c["kind"] == "official"),
        "first_class": sum(1 for c in commands if c["wrapped_by"]),
    }
    counts["gateway_only"] = counts["total"] - counts["first_class"]
    return {"commands": commands, "tools": TOOLS, "counts": counts,
            "tapir": tapir_info()}


def render(data: dict) -> str:
    payload = json.dumps(data, separators=(",", ":"))
    return TEMPLATE.replace("/*__DATA__*/", payload)


def main() -> None:
    data = build_data()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(data), encoding="utf-8")
    c = data["counts"]
    t = data["tapir"]
    print(f"Wrote {OUT.relative_to(ROOT)}")
    print(f"  {c['total']} commands  ({c['official']} official, {c['tapir']} tapir)")
    print(f"  {c['first_class']} first-class, {c['gateway_only']} gateway-only")
    print(f"  {len(data['tools'])} dedicated MCP tools")
    print(f"  Tapir defs v{t['bundled_version']} (synced {t['synced']}); "
          "the page checks GitHub for a newer release when opened")


TEMPLATE = r"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Archicad MCP - API surface</title>
<style>
:root{
  --bg:#e8ebe6; --sheet:#fbfcf9; --panel:#f2f4ef; --line:#d1d6cd; --line-strong:#b9c0b3;
  --ink:#16191a; --muted:#5d666a; --faint:#8b938f;
  --tapir:#1f5fa8; --tapir-bg:rgba(31,95,168,.10);
  --official:#a9670f; --official-bg:rgba(169,103,15,.12);
  --ok:#2e7d51; --shadow:0 1px 0 rgba(0,0,0,.03),0 6px 22px -14px rgba(0,0,0,.25);
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
}
html[data-theme="dark"]{
  --bg:#0f1316; --sheet:#161b1f; --panel:#1c2226; --line:#282f34; --line-strong:#39424a;
  --ink:#e7ece9; --muted:#94a0a6; --faint:#6d777d;
  --tapir:#6aa6e8; --tapir-bg:rgba(106,166,232,.14);
  --official:#e0a45a; --official-bg:rgba(224,164,90,.14);
  --ok:#5cc98c; --shadow:0 1px 0 rgba(0,0,0,.2),0 10px 30px -18px rgba(0,0,0,.8);
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;
  background-image:linear-gradient(var(--line) 1px,transparent 1px),
    linear-gradient(90deg,var(--line) 1px,transparent 1px);
  background-size:34px 34px;background-position:-1px -1px;background-attachment:fixed}
html[data-theme="dark"] body{background-image:linear-gradient(rgba(255,255,255,.02) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.02) 1px,transparent 1px)}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 80px}

/* Header, styled like a drawing-sheet titleblock */
header.sheet{background:var(--sheet);border:1.5px solid var(--line-strong);
  box-shadow:var(--shadow);border-radius:3px;overflow:hidden}
.hd-top{display:flex;flex-wrap:wrap;align-items:baseline;gap:14px 18px;
  padding:18px 22px 14px;border-bottom:1px solid var(--line)}
.eyebrow{font-family:var(--mono);font-size:10.5px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--faint)}
h1{margin:2px 0 0;font-size:25px;font-weight:640;letter-spacing:-.015em;line-height:1.05}
.sub{color:var(--muted);font-size:12.5px;max-width:60ch}
.spacer{flex:1 1 40px}
.themebtn{font-family:var(--mono);font-size:11px;letter-spacing:.05em;color:var(--muted);
  background:var(--panel);border:1px solid var(--line);border-radius:2px;
  padding:6px 11px;cursor:pointer}
.themebtn:hover{border-color:var(--line-strong);color:var(--ink)}
/* titleblock cells */
.hd-stats{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--line)}
.cell{padding:11px 18px;border-right:1px solid var(--line);display:flex;
  flex-direction:column;gap:2px}
.cell:last-child{border-right:0}
.cell .k{font-family:var(--mono);font-size:10px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--faint)}
.cell .v{font-size:22px;font-weight:640;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.cell .v small{font-size:12px;font-weight:500;color:var(--muted);letter-spacing:0}
.tapir-fg{color:var(--tapir)} .official-fg{color:var(--official)}
/* Tapir version + update-check strip (titleblock footer row) */
.hd-tapir{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;
  padding:11px 18px;border-top:1px solid var(--line);background:var(--panel)}
.hd-tapir .tk{font-family:var(--mono);font-size:10px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--faint)}
.hd-tapir .tv{font-family:var(--mono);font-size:13px;font-weight:600;color:var(--ink)}
.hd-tapir .tv small{font-weight:400;color:var(--muted);font-size:11px;letter-spacing:0}
.tstatus{font-family:var(--mono);font-size:11.5px;display:inline-flex;align-items:center;
  gap:7px;padding:3px 9px;border-radius:2px;border:1px solid var(--line)}
.tstatus.checking{color:var(--muted)}
.tstatus.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 42%,var(--line));
  background:color-mix(in srgb,var(--ok) 9%,transparent)}
.tstatus.behind{color:var(--official);border-color:color-mix(in srgb,var(--official) 42%,var(--line));
  background:var(--official-bg)}
.tstatus.err{color:var(--faint)}
.tstatus a{color:inherit}
.tstatus code{font-family:var(--mono);background:var(--sheet);border:1px solid var(--line);
  border-radius:2px;padding:1px 5px}
.tspin{width:10px;height:10px;border:1.6px solid currentColor;border-right-color:transparent;
  border-radius:50%;display:inline-block;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.tbtns{margin-left:auto;display:inline-flex;align-items:center;gap:8px}
.recheck{font-family:var(--mono);font-size:10.5px;color:var(--muted);
  background:var(--sheet);border:1px solid var(--line);border-radius:2px;padding:4px 9px;cursor:pointer}
.recheck:hover{color:var(--ink);border-color:var(--line-strong)}
.recheck.update{color:var(--ink);border-color:var(--line-strong);font-weight:600}
.recheck.update:hover{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 50%,var(--line))}

/* Controls */
.controls{position:sticky;top:0;z-index:20;margin:14px 0 20px;padding:12px 14px;
  background:color-mix(in srgb,var(--sheet) 90%,transparent);
  backdrop-filter:blur(8px);border:1px solid var(--line);border-radius:3px;
  display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center;box-shadow:var(--shadow)}
.search{flex:1 1 260px;display:flex;align-items:center;gap:8px;background:var(--panel);
  border:1px solid var(--line);border-radius:2px;padding:7px 11px}
.search:focus-within{border-color:var(--tapir)}
.search input{border:0;background:transparent;color:var(--ink);font-size:13.5px;
  width:100%;outline:none;font-family:var(--sans)}
.search svg{flex:none;color:var(--faint)}
.seg{display:flex;border:1px solid var(--line);border-radius:2px;overflow:hidden}
.seg button{font-family:var(--mono);font-size:11px;letter-spacing:.03em;padding:7px 11px;
  background:var(--sheet);color:var(--muted);border:0;border-right:1px solid var(--line);
  cursor:pointer;white-space:nowrap}
.seg button:last-child{border-right:0}
.seg button[aria-pressed="true"]{background:var(--ink);color:var(--sheet)}
.seg button:not([aria-pressed="true"]):hover{color:var(--ink)}
.count-note{font-family:var(--mono);font-size:11px;color:var(--faint);margin-left:auto;
  flex:0 0 auto;min-width:15.5ch;text-align:right;font-variant-numeric:tabular-nums}

/* Legend */
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;margin:0 2px 20px;font-size:12px;color:var(--muted)}
.legend b{color:var(--ink);font-weight:600}
.lg{display:inline-flex;align-items:center;gap:7px}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
.dot.tapir{background:var(--tapir)} .dot.official{background:var(--official)}
.sq{width:10px;height:10px;flex:none;border:1.5px solid var(--ink);border-radius:2px}
.sq.fill{background:var(--ink)}

/* Group sheets */
.group{background:var(--sheet);border:1px solid var(--line-strong);border-radius:3px;
  margin:0 0 16px;box-shadow:var(--shadow);overflow:hidden}
.group.hide{display:none}
.g-title{display:flex;align-items:center;gap:14px;padding:12px 16px;
  border-bottom:1px solid var(--line);background:var(--panel);cursor:pointer;user-select:none}
.g-title h2{margin:0;font-size:15px;font-weight:620;letter-spacing:-.01em;flex:1 1 auto}
.g-kind{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;
  padding:3px 7px;border-radius:2px;border:1px solid transparent}
.g-kind.tapir{color:var(--tapir);background:var(--tapir-bg);border-color:var(--tapir-bg)}
.g-kind.official{color:var(--official);background:var(--official-bg)}
.g-meta{font-family:var(--mono);font-size:11px;color:var(--muted);
  font-variant-numeric:tabular-nums;white-space:nowrap}
.bar{width:104px;height:7px;border-radius:4px;background:var(--line);overflow:hidden;flex:none}
.bar > i{display:block;height:100%;background:var(--ok);border-radius:4px}
.chev{color:var(--faint);transition:transform .18s ease;flex:none}
.group.collapsed .chev{transform:rotate(-90deg)}
.group.collapsed .rows{display:none}

/* Command rows */
.rows{display:block}
.row{border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}
.row.hide{display:none}
.r-head{display:grid;grid-template-columns:16px minmax(210px,auto) 1fr auto;
  gap:12px;align-items:center;padding:9px 16px;cursor:pointer}
.r-head:hover{background:var(--panel)}
.r-name{font-family:var(--mono);font-size:12.5px;font-weight:520;color:var(--ink);
  display:flex;align-items:center;gap:8px;min-width:0}
.r-name .pfx{color:var(--official)}
.r-desc{color:var(--muted);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.r-right{display:flex;align-items:center;gap:8px;justify-self:end}
.tag{font-family:var(--mono);font-size:10px;letter-spacing:.02em;padding:2.5px 7px;
  border-radius:2px;white-space:nowrap;border:1px solid var(--line)}
.tag.wrap{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,var(--line))}
.tag.gw{color:var(--faint)}
.tag.schema{color:var(--muted)}
.tag.ver{color:var(--faint);border-style:dashed;border-color:var(--line)}
.markbox{width:11px;height:11px;border:1.5px solid var(--faint);border-radius:2px;flex:none}
.markbox.fc{background:var(--ok);border-color:var(--ok)}
/* detail */
.detail{display:none;padding:2px 16px 16px 44px;background:var(--panel);
  border-top:1px dashed var(--line)}
.row.open .detail{display:block}
.detail p{margin:12px 0 10px;color:var(--muted);font-size:12.5px;max-width:78ch}
.detail .callrow{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:center;margin:0 0 8px;
  font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.detail code.call{background:var(--sheet);border:1px solid var(--line);border-radius:2px;
  padding:3px 8px;color:var(--ink)}
.schemahead{font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--faint);margin:12px 0 6px;display:flex;align-items:center;gap:10px}
.copy{font-family:var(--mono);font-size:10px;color:var(--muted);background:var(--sheet);
  border:1px solid var(--line);border-radius:2px;padding:2px 8px;cursor:pointer}
.copy:hover{color:var(--ink);border-color:var(--line-strong)}
pre{margin:0;background:var(--sheet);border:1px solid var(--line);border-radius:3px;
  padding:12px 14px;overflow:auto;max-height:340px;font-family:var(--mono);font-size:11.5px;
  line-height:1.55;color:var(--ink)}
.noschema{font-family:var(--mono);font-size:11.5px;color:var(--faint)}

/* Tools panel */
.tools h2.stitle{font-size:16px;margin:34px 2px 4px;font-weight:640;letter-spacing:-.01em}
.tools .stub{color:var(--muted);font-size:12.5px;margin:0 2px 16px;max-width:80ch}
.toolgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.toolcat{background:var(--sheet);border:1px solid var(--line-strong);border-radius:3px;
  box-shadow:var(--shadow);overflow:hidden}
.toolcat > h3{margin:0;font-size:12px;font-family:var(--mono);letter-spacing:.06em;
  text-transform:uppercase;color:var(--muted);padding:11px 14px;border-bottom:1px solid var(--line);
  background:var(--panel)}
.tool{padding:10px 14px;border-bottom:1px solid var(--line)}
.tool:last-child{border-bottom:0}
.tool .tn{font-family:var(--mono);font-size:12.5px;font-weight:520;display:flex;align-items:center;gap:8px}
.tool .td{color:var(--muted);font-size:12px;margin-top:3px}
.mini{font-family:var(--mono);font-size:9px;letter-spacing:.08em;text-transform:uppercase;
  padding:2px 6px;border-radius:2px;border:1px solid var(--line);color:var(--faint)}
.mini.write{color:var(--official);border-color:var(--official-bg);background:var(--official-bg)}
.mini.both{color:var(--tapir);border-color:var(--tapir-bg);background:var(--tapir-bg)}

footer{margin:36px 2px 0;color:var(--faint);font-size:11.5px;font-family:var(--mono);
  display:flex;flex-wrap:wrap;gap:6px 16px}
footer a{color:var(--muted)}
.empty{display:none;text-align:center;color:var(--muted);padding:50px 20px;font-size:13px}
.empty.show{display:block}
@media (max-width:640px){
  .hd-stats{grid-template-columns:repeat(2,1fr)}
  .cell:nth-child(2n){border-right:0}
  .r-head{grid-template-columns:16px 1fr auto}
  .r-desc{display:none}
  .count-note{width:100%;margin:0;min-width:0;text-align:left}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>
<div class="wrap">

  <header class="sheet">
    <div class="hd-top">
      <div>
        <div class="eyebrow">Archicad MCP &middot; capability map</div>
        <h1>Archicad API surface</h1>
      </div>
      <div class="spacer"></div>
      <button class="themebtn" id="themebtn" type="button">theme</button>
    </div>
    <div style="padding:10px 22px 0">
      <p class="sub">Every command the MCP server can reach: the official Archicad JSON API and the Tapir add-on commands, grouped as the add-on groups them. Solid boxes are commands a dedicated, safety-checked tool already wraps. Hollow boxes are reachable through the generic <code style="font-family:var(--mono)">execute_read_api_command</code> / <code style="font-family:var(--mono)">execute_write_api_command</code> gateway but have no first-class tool yet.</p>
    </div>
    <div class="hd-stats" id="stats"></div>
    <div class="hd-tapir">
      <span class="tk">Tapir add-on</span>
      <span class="tv" id="tv"></span>
      <span class="tstatus checking" id="tstatus"><span class="tspin"></span> checking GitHub...</span>
      <span class="tbtns">
        <button class="recheck" id="recheck" type="button" hidden>recheck</button>
        <button class="recheck update" id="update" type="button"
          title="Copy the update command to your clipboard">update</button>
      </span>
    </div>
  </header>

  <div class="controls">
    <label class="search">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
      <input id="q" type="search" placeholder="Search commands and descriptions..." autocomplete="off" spellcheck="false">
    </label>
    <div class="seg" id="fam" role="group" aria-label="API family">
      <button data-v="all" aria-pressed="true">All</button>
      <button data-v="official">Official</button>
      <button data-v="tapir">Tapir</button>
    </div>
    <div class="seg" id="cov" role="group" aria-label="Coverage">
      <button data-v="all" aria-pressed="true">Any coverage</button>
      <button data-v="first">First-class</button>
      <button data-v="gateway">Gateway-only</button>
    </div>
    <span class="count-note" id="shown"></span>
  </div>

  <div class="legend">
    <span class="lg"><span class="dot tapir"></span><b>Tapir</b> add-on command</span>
    <span class="lg"><span class="dot official"></span><b>Official</b> JSON API</span>
    <span class="lg"><span class="sq fill"></span><b>First-class</b> tool wraps it</span>
    <span class="lg"><span class="sq"></span>Gateway-only (raw passthrough)</span>
  </div>

  <div id="groups"></div>
  <div class="empty" id="empty">No commands match those filters.</div>

  <section class="tools">
    <h2 class="stitle">Dedicated MCP tools</h2>
    <p class="stub">The curated, high-level tools the server exposes directly. These wrap the commands above into safe operations (dry-run defaults, confirmations, schema validation). Everything else runs through the raw gateway.</p>
    <div class="toolgrid" id="tools"></div>
  </section>

  <footer>
    <span>Generated from the bundled command definitions.</span>
    <span>Official JSON API docs: <a href="https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/" target="_blank" rel="noopener">archicadapi.graphisoft.com</a></span>
    <span>Refresh: <span style="color:var(--muted)">uv run python scripts/build_dashboard.py</span></span>
  </footer>
</div>

<script id="data" type="application/json">/*__DATA__*/</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const esc = s => (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const state = {q:"", fam:"all", cov:"all"};

// ---- Titleblock stats ----
(function(){
  const c = DATA.counts;
  document.getElementById("stats").innerHTML = `
    <div class="cell"><span class="k">Commands</span><span class="v">${c.total}</span></div>
    <div class="cell"><span class="k">By family</span><span class="v"><span class="official-fg">${c.official}</span> <small>official</small> &nbsp; <span class="tapir-fg">${c.tapir}</span> <small>tapir</small></span></div>
    <div class="cell"><span class="k">First-class tool</span><span class="v">${c.first_class} <small>/ ${c.total}</small></span></div>
    <div class="cell"><span class="k">Gateway-only</span><span class="v">${c.gateway_only}</span></div>`;
})();

// ---- Tapir version + live update check ----
(function(){
  const T = DATA.tapir || {};
  const tv = document.getElementById("tv");
  const st = document.getElementById("tstatus");
  const recheck = document.getElementById("recheck");
  const update = document.getElementById("update");
  // This page lives at <repo>/docs/; when opened from disk, derive the repo root
  // from the file URL so the copied command works from any cwd. Served over http
  // the viewer's checkout location is unknowable, so fall back to the bare command.
  const REPO_ROOT = location.protocol === "file:"
    ? decodeURIComponent(location.pathname).replace(/\/docs\/[^/]*$/, "").replace(/^\/([A-Za-z]:)/, "$1")
    : "";
  const CD_PREFIX = REPO_ROOT ? `cd "${REPO_ROOT}" && ` : "";
  const SYNC_CMD = CD_PREFIX + "uv run python scripts/sync_tapir_defs.py";
  // Sync the definitions AND regenerate this page, so a reload shows the new version.
  const UPDATE_CMD = SYNC_CMD + " && uv run python scripts/build_dashboard.py";
  const parse = v => String(v||"").replace(/^v/i,"").split(".").map(n=>parseInt(n,10)||0);
  const cmp = (a,b)=>{a=parse(a);b=parse(b);for(let i=0;i<3;i++){const d=(a[i]||0)-(b[i]||0);if(d)return d;}return 0;};

  tv.innerHTML = T.bundled_version
    ? `v${esc(T.bundled_version)} <small>bundled${T.synced?` &middot; synced ${esc(T.synced)}`:""}</small>`
    : `<small>bundled version unknown</small>`;

  const set = (cls, html) => { st.className = "tstatus "+cls; st.innerHTML = html; };
  const relLink = txt => `<a href="${esc(T.releases||"#")}" target="_blank" rel="noopener">${esc(txt)}</a>`;

  async function check(){
    if(!T.api || !T.bundled_version){ set("err","update check unavailable"); recheck.hidden=true; return; }
    set("checking",`<span class="tspin"></span> checking GitHub...`);
    recheck.hidden = true;
    try{
      const r = await fetch(T.api, {headers:{"Accept":"application/vnd.github+json"}, referrerPolicy:"no-referrer"});
      if(!r.ok) throw new Error("HTTP "+r.status);
      const j = await r.json();
      const latest = String(j.tag_name || j.name || "").replace(/^Tapir\s+/i,"").replace(/^v/i,"").trim();
      recheck.hidden = false;
      if(!latest){ set("err", `no release info &middot; ${relLink("open releases")}`); return; }
      if(cmp(T.bundled_version, latest) >= 0){
        set("ok", `&#10003; up to date &middot; latest release ${relLink("v"+latest)}`);
      } else {
        set("behind", `&#9888; update available &middot; latest ${relLink("v"+latest)} &middot; click <b>update</b> or run <code>${esc(SYNC_CMD)}</code>`);
      }
    }catch(e){
      recheck.hidden = false;
      set("err", `couldn't reach GitHub (offline or rate-limited) &middot; ${relLink("check releases")}`);
    }
  }
  async function copyCmd(text){
    try{ await navigator.clipboard.writeText(text); return true; }catch(_){}
    try{  // fallback for browsers / contexts without the async clipboard API
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.focus(); ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    }catch(_){ return false; }
  }
  update.addEventListener("click", async () => {
    const prevCls = st.className, prevHtml = st.innerHTML;
    const ok = await copyCmd(UPDATE_CMD);
    update.textContent = ok ? "copied ✓" : "copy failed";
    set(ok ? "ok" : "err",
      `${ok ? "&#10003; command copied &middot; " : ""}paste <code>${esc(UPDATE_CMD)}</code> in your terminal, then reload this page`);
    setTimeout(() => { update.textContent = "update"; st.className = prevCls; st.innerHTML = prevHtml; }, 5000);
  });
  recheck.addEventListener("click", check);
  check();
})();

// ---- Group the commands ----
const GROUPS = (() => {
  const m = new Map();
  for(const cmd of DATA.commands){
    if(!m.has(cmd.group)) m.set(cmd.group, []);
    m.get(cmd.group).push(cmd);
  }
  // Order: put Official JSON API last, Element Commands first (biggest Tapir set), then alpha.
  return [...m.entries()].sort((a,b)=>{
    const rank = g => g==="Official JSON API"?2 : g==="Element Commands"?0 : 1;
    return rank(a[0])-rank(b[0]) || a[0].localeCompare(b[0]);
  });
})();

function nameHtml(cmd){
  if(cmd.name.startsWith("API.")){
    return `<span class="pfx">API.</span>${esc(cmd.name.slice(4))}`;
  }
  return esc(cmd.name);
}

function rowHtml(cmd){
  const gw = c => c.access === "read" ? "execute_read_api_command" : "execute_write_api_command";
  const fc = cmd.wrapped_by.length>0;
  const cov = fc
    ? `<span class="tag wrap" title="Wrapped by: ${esc(cmd.wrapped_by.join(", "))}">${esc(cmd.wrapped_by[0])}${cmd.wrapped_by.length>1?" +"+(cmd.wrapped_by.length-1):""}</span>`
    : `<span class="tag gw">gateway</span>`;
  const schema = cmd.has_schema ? `<span class="tag schema">schema</span>` : "";
  const ver = cmd.version ? `<span class="tag ver" title="First included in Tapir v${esc(cmd.version)}">v${esc(cmd.version)}</span>` : "";
  const call = fc
    ? `<code class="call">${esc(cmd.wrapped_by[0])}(...)</code> <span>or</span> <code class="call">${gw(cmd)}("${esc(cmd.name)}", ...)</code>`
    : `<code class="call">${gw(cmd)}("${esc(cmd.name)}", { ...params })</code>`;
  const schemaBlock = cmd.input_schema
    ? `<div class="schemahead">Input schema <button class="copy" data-copy>copy</button></div>
       <pre>${esc(JSON.stringify(cmd.input_schema,null,2))}</pre>`
    : (cmd.kind==="official"
        ? `<div class="noschema">No bundled schema. Parameters follow the official JSON API docs.</div>`
        : `<div class="noschema">No parameters.</div>`);
  return `<div class="row" data-name="${esc(cmd.name.toLowerCase())}" data-desc="${esc((cmd.description||"").toLowerCase())}" data-kind="${cmd.kind}" data-fc="${fc?1:0}">
    <div class="r-head">
      <span class="markbox ${fc?"fc":""}" title="${fc?"First-class tool":"Gateway-only"}"></span>
      <span class="r-name"><span class="dot ${cmd.kind}" title="${cmd.kind}"></span>${nameHtml(cmd)}</span>
      <span class="r-desc">${esc(cmd.description||"")}</span>
      <span class="r-right">${ver}${schema}${cov}</span>
    </div>
    <div class="detail">
      <p>${esc(cmd.description||"No description provided by the add-on.")}</p>
      <div class="callrow">Call &nbsp; ${call}</div>
      ${schemaBlock}
    </div>
  </div>`;
}

function groupHtml([name, cmds]){
  const kind = cmds.every(c=>c.kind==="official") ? "official"
             : cmds.every(c=>c.kind==="tapir") ? "tapir" : "mixed";
  const fc = cmds.filter(c=>c.wrapped_by.length>0).length;
  const pct = Math.round(fc/cmds.length*100);
  const badge = kind==="mixed" ? "" : `<span class="g-kind ${kind}">${kind}</span>`;
  return `<section class="group" data-group="${esc(name)}">
    <div class="g-title" role="button" tabindex="0">
      <svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="m6 9 6 6 6-6"/></svg>
      <h2>${esc(name)}</h2>${badge}
      <span class="g-meta">${cmds.length} cmds &middot; ${fc} first-class</span>
      <span class="bar" title="${pct}% first-class"><i style="width:${pct}%"></i></span>
    </div>
    <div class="rows">${cmds.map(rowHtml).join("")}</div>
  </section>`;
}

document.getElementById("groups").innerHTML = GROUPS.map(groupHtml).join("");

// ---- Tools panel ----
(function(){
  const cats = [];
  const byCat = new Map();
  for(const t of DATA.tools){
    if(!byCat.has(t.cat)){ byCat.set(t.cat, []); cats.push(t.cat); }
    byCat.get(t.cat).push(t);
  }
  document.getElementById("tools").innerHTML = cats.map(cat=>`
    <div class="toolcat"><h3>${esc(cat)}</h3>
      ${byCat.get(cat).map(t=>`<div class="tool">
        <div class="tn">${esc(t.name)}
          ${t.mutates?'<span class="mini write">writes</span>':''}
          ${t.mode==="both"?'<span class="mini both">both modes</span>':''}
        </div>
        <div class="td">${esc(t.desc)}</div>
      </div>`).join("")}
    </div>`).join("");
})();

// ---- Filtering ----
function apply(){
  const q = state.q.trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll(".group").forEach(g=>{
    let vis = 0;
    g.querySelectorAll(".row").forEach(r=>{
      const okFam = state.fam==="all" || r.dataset.kind===state.fam;
      const okCov = state.cov==="all"
        || (state.cov==="first" && r.dataset.fc==="1")
        || (state.cov==="gateway" && r.dataset.fc==="0");
      const okQ = !q || r.dataset.name.includes(q) || r.dataset.desc.includes(q);
      const show = okFam && okCov && okQ;
      r.classList.toggle("hide", !show);
      if(show) vis++;
    });
    g.classList.toggle("hide", vis===0);
    shown += vis;
  });
  document.getElementById("shown").textContent = shown+" / "+DATA.counts.total+" shown";
  document.getElementById("empty").classList.toggle("show", shown===0);
}

document.getElementById("q").addEventListener("input", e=>{ state.q=e.target.value; apply(); });
for(const seg of [["fam","fam"],["cov","cov"]]){
  document.getElementById(seg[0]).addEventListener("click", e=>{
    const b = e.target.closest("button"); if(!b) return;
    [...e.currentTarget.children].forEach(x=>x.setAttribute("aria-pressed", x===b));
    state[seg[1]] = b.dataset.v; apply();
  });
}

// ---- Row expand + group collapse + copy ----
document.getElementById("groups").addEventListener("click", e=>{
  if(e.target.closest("[data-copy]")){
    const pre = e.target.closest(".detail").querySelector("pre");
    if(pre){ navigator.clipboard?.writeText(pre.textContent); const b=e.target; const o=b.textContent; b.textContent="copied"; setTimeout(()=>b.textContent=o,900); }
    return;
  }
  const head = e.target.closest(".r-head");
  if(head){ head.parentElement.classList.toggle("open"); return; }
  const gt = e.target.closest(".g-title");
  if(gt){ gt.parentElement.classList.toggle("collapsed"); }
});
document.getElementById("groups").addEventListener("keydown", e=>{
  if((e.key==="Enter"||e.key===" ") && e.target.classList.contains("g-title")){
    e.preventDefault(); e.target.parentElement.classList.toggle("collapsed");
  }
});

// ---- Theme ----
const root = document.documentElement;
const saved = localStorage.getItem("acmcp-theme");
if(saved) root.dataset.theme = saved;
document.getElementById("themebtn").addEventListener("click", ()=>{
  const next = root.dataset.theme==="dark" ? "light" : "dark";
  root.dataset.theme = next; localStorage.setItem("acmcp-theme", next);
});

apply();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
