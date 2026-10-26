from __future__ import annotations

import argparse
import functools
import os
import sys
import threading
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from multiconn_archicad.errors import APIErrorBase

from archicad_mcp import actions
from archicad_mcp.connection import (
    NO_OPEN_PROJECT_CODE,
    PORT_RANGE,
    ArchicadUnavailableError,
    InstanceInfo,
    discover_instances,
    get_connection,
)
from archicad_mcp.core import save as _save
from archicad_mcp.core.project import scrub_credentials
from archicad_mcp.core.query_schema import GroupSpec, groups_to_dicts
from archicad_mcp.extract import build_snapshot, coverage_of, owned_objects
from archicad_mcp.rules.engine import (
    collision_queries,
    data_needs,
    element_type_scope,
    filter_by_tag,
    property_needs,
    run_rules,
)
from archicad_mcp.rules.loader import load_rules
from archicad_mcp.rules.types import Verdict
from archicad_mcp.tapir_requirements import tapir_refusal

# Errors the tool layer converts into an actionable {"error": ...} envelope
# instead of leaking a protocol-level ToolError. ArchicadUnavailableError is
# raised by the connection layer (nothing answering); APIErrorBase is the shared
# base of every multiconn error raised *during* a command (StandardAPIError,
# TapirCommandError, timeouts, connection drops, ...).
_HANDLED_ERRORS = (ArchicadUnavailableError, APIErrorBase)


def _tool_error(exc: Exception) -> dict:
    if isinstance(exc, APIErrorBase):
        # Code 4001 ("Invalid program status") means Archicad cannot execute
        # commands right now: no project open, or a modal dialog blocking the
        # API while one is open. Only that code gets the hint; appending it to
        # every API error (e.g. a schema rejection) misleads.
        message = f"Archicad API error: {exc.message}"
        if getattr(exc, "code", None) == NO_OPEN_PROJECT_CODE:
            message += (". Open a project in Archicad if none is open; if one "
                        "is open, close any modal dialog (e.g. Object "
                        "Settings) blocking the API, and retry.")
        return {"error": message}
    return {"error": str(exc)}


def require_tapir(conn, tool: str):
    """conn, or ArchicadUnavailableError naming the Tapir the tool needs."""
    refusal = tapir_refusal(conn, tool)
    if refusal is not None:
        raise ArchicadUnavailableError(refusal["error"])
    return conn


def _tool_meta(title: str, *, read_only: bool, destructive: bool = False) -> dict:
    """Decorator kwargs carrying the tool's title and its safety hints.

    The Connectors Directory review requires every tool to declare a title and
    the applicable readOnlyHint or destructiveHint, and clients act on them:
    a read-only tool may run without asking the user, a destructive one always
    prompts. So the hints are a safety contract, not listing metadata.

    Title twice on purpose. The MCP schema carries one at the top level and one
    inside annotations, different clients read different ones, and there is no
    version of this worth debugging later to save a line.

    Where the two ideas disagree, this codebase reads "destructive" as "changes
    the project or writes a file", which is wider than the MCP spec's "not
    purely additive". Creating an issue is additive and still marked
    destructive, because it lands in someone's .pln and a confirmation prompt
    is the right cost for that. Only transient view state (selection,
    highlighting) is a non-destructive write.
    """
    return {
        "title": title,
        "annotations": {"title": title, "readOnlyHint": read_only,
                        "destructiveHint": destructive},
    }


def _guarded(func):
    """Wrap a tool handler so connection/API errors become {"error": ...}.

    Every reply, error or not, is also scrubbed of Teamwork credentials: the
    backstop for a tool that passes a raw answer through, or a script that
    returns, prints or raises one.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return scrub_credentials(func(*args, **kwargs))
        except _HANDLED_ERRORS as exc:
            return scrub_credentials(_tool_error(exc))
    return wrapper


def build_server(
    mode: str = "full",
    rules_dir: Path | None = None,
    port: int | None = None,
    gdl_workspace: Path | None = None,
    enable_scripts: bool = False,
) -> FastMCP:
    if mode not in ("verdicts", "full"):
        raise ValueError(f"mode must be 'verdicts' or 'full', got {mode!r}")
    mcp = FastMCP("archicad-mcp")
    loaded = load_rules(rules_dir)
    default_port = port
    # Carried on the server so main() can report it without loading rules twice.
    mcp.archicad_rule_count = len(loaded.rules)
    mcp.archicad_rule_errors = len(loaded.errors)
    mcp.archicad_rule_source = None if rules_dir is None else str(rules_dir)

    def _rules_subset(ruleset: str | None = None, rule_id: str | None = None):
        rules = loaded.rules
        if rule_id is not None:
            rules = [r for r in rules if r.rule_id == rule_id]
            if not rules:
                known = ", ".join(sorted(r.rule_id for r in loaded.rules)) or "none loaded"
                raise ArchicadUnavailableError(
                    f"Unknown rule '{rule_id}'. Loaded rules: {known}.")
            return rules
        matched = filter_by_tag(rules, ruleset)
        if ruleset is not None and not matched:
            tags = sorted({t for r in loaded.rules for t in r.tags})
            known = ", ".join(tags) or "no tags defined"
            raise ArchicadUnavailableError(
                f"Unknown ruleset tag '{ruleset}'. Known tags: {known}.")
        return matched

    def _verdict_for(rules, request_port: int | None) -> Verdict:
        conn = get_connection(request_port if request_port is not None else default_port)
        snapshot = build_snapshot(conn, data_needs(rules), property_needs(rules),
                                  element_types=element_type_scope(rules),
                                  collision_queries=collision_queries(rules))
        return run_rules(rules, snapshot)

    # ---------- Tier 1: verdict tools (both modes) ----------

    @mcp.tool(description="List running Archicad instances: port, version, open project, "
                          "Tapir add-on availability. Call this first.",
              **_tool_meta("List Archicad instances", read_only=True, destructive=False))
    @_guarded
    def list_instances() -> dict:
        instances = [i.to_dict() for i in discover_instances()]
        if mode == "verdicts":
            # Privacy: verdicts mode must never leak project info to the AI.
            for inst in instances:
                inst["project_name"] = None
        return {"instances": instances}

    @mcp.tool(description="Aggregate element counts. by_type is always returned "
                          "(cheap and safe). Set include_layer_story=true to also "
                          "break down by layer and story, which reads a property "
                          "across every element and is refused on very large models "
                          "(can crash Archicad). Counts only, never element data. "
                          "'coverage' says what element_count spans: 'whole-plan' "
                          "with the Tapir add-on, 'model-elements-only' without it "
                          "(then it is NOT a project total). 'owned_parts' counts apart "
                          "the GDL parts of stairs and other elements that Archicad "
                          "types Object.",
              **_tool_meta("Summarize model contents", read_only=True, destructive=False))
    @_guarded
    def get_model_summary(include_layer_story: bool = False,
                          port: int | None = None) -> dict:
        conn = get_connection(port if port is not None else default_port)
        needs = frozenset({"elements", "properties", "story"}) if include_layer_story \
            else frozenset({"elements"})
        snapshot = build_snapshot(conn, needs=needs)
        owned = owned_objects(conn, [e.guid for e in snapshot.elements
                                     if e.element_type == "Object"])
        by_type = Counter(e.element_type for e in snapshot.elements if e.guid not in owned)
        result = {"element_count": len(snapshot.elements),
                  "by_type": dict(by_type), **coverage_of(conn)}
        if owned:
            # GDL parts of a stair or another element, which the official API types
            # Object: counted apart so by_type's Object is what was placed as one.
            result["owned_parts"] = {"Object": len(owned)}
        if include_layer_story:
            by_layer = Counter(e.layer for e in snapshot.elements if e.layer)
            by_story = Counter(str(e.story) for e in snapshot.elements
                               if e.story is not None)
            result["by_layer"] = dict(by_layer)
            result["by_story"] = dict(by_story)
        return result

    @mcp.tool(description="List loaded QA rules (id, type, severity, tags) and any "
                          "rule-file load errors.",
              **_tool_meta("List QA rules", read_only=True, destructive=False))
    def list_rules() -> dict:
        return {
            "source": loaded.source,
            "rules": [{"id": r.rule_id, "type": type(r).__name__,
                       "severity": r.severity, "tags": sorted(r.tags)}
                      for r in loaded.rules],
            "errors": loaded.errors,
        }

    @mcp.tool(description="Run one QA rule by id. Returns a verdict: pass/fail, "
                          "failure count, failing element GUIDs.",
              **_tool_meta("Run one QA rule", read_only=True, destructive=False))
    @_guarded
    def run_rule(rule_id: str, port: int | None = None) -> dict:
        rules = _rules_subset(rule_id=rule_id)
        return _verdict_for(rules, port).to_dict()

    @mcp.tool(description="Run all loaded QA rules (optionally only those tagged with "
                          "'ruleset') against the open model. Returns a scored verdict.",
              **_tool_meta("Audit delivery readiness", read_only=True, destructive=False))
    @_guarded
    def audit_delivery_readiness(ruleset: str | None = None, port: int | None = None) -> dict:
        return _verdict_for(_rules_subset(ruleset=ruleset), port).to_dict()

    @mcp.tool(description="Run only the IFC-related QA rules to check IFC export "
                          "readiness. Requires the Tapir add-on for IFC data.",
              **_tool_meta("Verify IFC export readiness", read_only=True, destructive=False))
    @_guarded
    def verify_ifc_export_readiness(port: int | None = None) -> dict:
        ifc_rules = [r for r in loaded.rules if "ifc" in r.needs]
        if not ifc_rules:
            return {"error": "No IFC rules configured. Add 'ifc-property-required' "
                             "rules to your rules directory."}
        return _verdict_for(ifc_rules, port).to_dict()

    highlight_text = ("Highlight the elements failing a rule in the Archicad window and "
                      "zoom to them (zoom=false to leave the view). Highlights every story "
                      "at once. A rule with no failures removes the highlight.")
    if mode == "full":
        highlight_text += (" show_elements opens the right story or view and selects; "
                           "show_elements with guids=[] removes the highlight.")

    @mcp.tool(description=highlight_text + " Requires the Tapir add-on.",
              **_tool_meta("Highlight failing elements", read_only=False, destructive=False))
    @_guarded
    def highlight_failures(rule_id: str, zoom: bool = True, port: int | None = None) -> dict:
        require_tapir(get_connection(port if port is not None else default_port),
                      "highlight_failures")
        rules = _rules_subset(rule_id=rule_id)
        verdict = _verdict_for(rules, port)
        guids = [g for r in verdict.results for g in r.failing_guids]
        conn = get_connection(port if port is not None else default_port)
        if not guids:
            # Nothing fails: put the view back rather than leave an old highlight.
            actions.clear_highlight(conn)
        result = actions.highlight_elements(conn, guids)
        result["zoomed"] = bool(zoom and guids) and actions.fit_in_window(conn, guids)
        if zoom and guids and not result["zoomed"]:
            result["note"] = ("Not zoomed: this Tapir has no FitInWindow (Tapir 1.3.1), "
                              "or Archicad refused it.")
        return result

    @mcp.tool(description=(
        "Save the open project to its own file, as File > Save (Tapir). CONFIRM-GATED: "
        "without confirm=true it only reports what it would save. Call it before the "
        "reads that can crash Archicad 29 and lose unsaved work: property reads in "
        "get_element_data, set_element_data, find_elements with property comparisons, "
        "audit_delivery_readiness, run_rule, verify_ifc_export_readiness, "
        "get_model_summary with include_layer_story, modify_elements, and moving or "
        "deleting a locked element. A project never saved, or made in "
        "an older Archicad, needs File > Save As in Archicad; this tool does no Save As "
        "and says why a save was refused. On Teamwork it saves your local copy and never "
        "sends. file_updated says whether the file on disk changed."),
              **_tool_meta("Save the project", read_only=False, destructive=True))
    @_guarded
    def save_project(confirm: bool = False, port: int | None = None) -> dict:
        conn = require_tapir(get_connection(port if port is not None else default_port),
                             "save_project")
        return _save.save_project(conn, confirm, show_identity=(mode == "full"))

    @mcp.tool(description="Create an Archicad issue from a rule's failures and attach "
                          "the failing elements (requires Tapir add-on).",
              **_tool_meta("Create issues from rule failures", read_only=False, destructive=True))
    @_guarded
    def create_issues_from_failures(rule_id: str, port: int | None = None) -> dict:
        require_tapir(get_connection(port if port is not None else default_port),
                      "create_issues_from_failures")
        rules = _rules_subset(rule_id=rule_id)
        verdict = _verdict_for(rules, port)
        result = verdict.results[0]
        conn = get_connection(port if port is not None else default_port)
        return actions.create_issues(conn, rule_id, result.message,
                                     list(result.failing_guids))

    if mode == "full":
        _register_full_mode_tools(mcp, default_port)
        if gdl_workspace is not None:
            from archicad_mcp.gdl import tools as gdl_tools
            from archicad_mcp.gdl.workspace import Workspace
            gdl_tools.register(mcp, default_port, Workspace(gdl_workspace),
                               _tool_meta, _guarded)
        if enable_scripts:
            from archicad_mcp.scripting import tools as script_tools
            script_tools.register(mcp, default_port, _tool_meta, _guarded)

    return mcp


def _register_full_mode_tools(mcp: FastMCP, default_port: int | None) -> None:
    from archicad_mcp.core import element_data as _element_data
    from archicad_mcp.core import query as _query
    from archicad_mcp.core import schemes as core_schemes

    def _conn(port: int | None):
        return get_connection(port if port is not None else default_port)

    def _tapir_conn(port: int | None, tool: str):
        return require_tapir(_conn(port), tool)

    @mcp.tool(description=(
        "Find elements matching criteria groups. Groups combine with OR; inside "
        "a group the comparisons combine with logical_operator 'and' (default) "
        "or 'or'. Each group may restrict element types (is / is_not) and lists "
        "comparisons of {property, operator, value}; the schema enumerates the "
        "element types and the 22 operators, and each field documents its "
        "values and units. Call search_definitions to find a property's exact "
        "address. An element with no usable value matches no binary operator. "
        "Returns GUIDs, counts, how many elements had properties read, and "
        "'coverage' ('whole-plan' with Tapir, 'model-elements-only' without: "
        "then 2D elements are invisible and 0 is not proof of absence). "
        "Property comparisons read values in the server (no API filters by "
        "property); a read spanning more than the element ceiling is refused, "
        "so narrow with element_types, story or classification first."),
              **_tool_meta("Find elements by criteria", read_only=True, destructive=False))
    @_guarded
    def find_elements(groups: list[GroupSpec], selection_only: bool = False,
                      port: int | None = None) -> dict:
        return _query.find_elements(_conn(port), groups_to_dicts(groups), selection_only)

    from archicad_mcp.core import definitions as _definitions

    @mcp.tool(description=(
        "Fuzzy search over property and attribute definitions, so a caller "
        "does not need to know the exact 'Group/Name'. Matches names, groups "
        "and enum values; case- and accent-insensitive. kind: 'property', "
        "'attribute' (layers, fills, surfaces, composites, profiles, pen "
        "tables, ...) or 'any'. alternatives: up to 6 synonyms or translations "
        "searched too (useful on non-English projects). editable_only: keep "
        "only properties whose value can be written on at least one element "
        "type; check it before set_element_data. Each property match carries "
        "'property', the exact address find_elements, get_element_data, "
        "set_element_data and rules accept, plus value_type, measure_type "
        "(Length/Area/Volume/Angle values are in m, m2, m3, radian), "
        "collection, editable, expression_based and enum_values. Results are "
        "ranked: whole-word matches first, then word starts, then substrings; "
        "a query word under 4 letters must start a word. total_matches counts "
        "everything; pass next_offset as offset to page past limit. Reads "
        "definitions only, never property values."),
              **_tool_meta("Search property and attribute definitions",
                           read_only=True, destructive=False))
    @_guarded
    def search_definitions(query: str, kind: str = "any",
                           alternatives: list[str] | None = None,
                           editable_only: bool = False, limit: int = 25,
                           offset: int = 0, port: int | None = None) -> dict:
        return _definitions.search_definitions(_conn(port), query, kind, alternatives,
                                               editable_only, limit, offset)

    @mcp.tool(description=(
        "Read type, layer and requested values of the given elements. properties "
        "takes addresses: a property ('Group/Name', a built-in name or a property GUID, "
        "as search_definitions gives it); 'classification:<System>' (the item ID); "
        "'gdl:<name>' (a GDL parameter of an object, lamp, door, window or other "
        "library part element); 'marker:<name>' (a door's or window's marker "
        "parameter, Tapir 1.7.1); 'ifc:<Pset>.<Name>' (an IFC property). Lengths in "
        "metres, angles in radians, attribute-typed GDL parameters as attribute "
        "indexes. 'gdl:*', 'marker:*', 'ifc:*' and 'classification:*' read every "
        "value, with display names in 'labels'; at most 20 elements per wildcard "
        "call. A kind that cannot be read for an element is named in its "
        "'unreadable'. include_classifications adds every system's item GUID."),
              **_tool_meta("Read element data", read_only=True, destructive=False))
    @_guarded
    def get_element_data(guids: list[str], properties: list[str] | None = None,
                         include_classifications: bool = False,
                         port: int | None = None) -> dict:
        return _element_data.get_element_data(_conn(port), guids, properties,
                                              include_classifications)

    @mcp.tool(description=(
        "Write element data. DRY-RUN BY DEFAULT: returns planned changes (current "
        "-> new) without touching the model; pass dry_run=false to commit. A change "
        "is {guid, property, value}; property takes the addresses get_element_data "
        "reads (a property, 'classification:<System>', 'gdl:<name>', 'marker:<name>', "
        "'ifc:<Pset>.<Name>'), so one call can mix kinds. Classification values are "
        "an item ID or GUID, null unclassifies. GDL and marker values in metres and "
        "radians, an array as the whole list in its own shape (rows for two dimensions; "
        "array writes need Tapir 1.7.2), an attribute parameter by index or name. IFC values are text, a number or true/false, "
        "with an optional ifc_type (IfcLabel for text by default); Tapir writes IFC "
        "properties only in Archicad 27 and older. A commit sends classifications "
        "first and reads every change back: 'applied' counts what reads as sent; "
        "'not_applied', changes Archicad answered success for but kept or replaced "
        "(a GDL Parameter Script can); 'failed', Archicad's refusals grouped by code "
        "and message with a count and sample GUIDs; 'skipped', changes not sent, "
        "with the reason (value type, enum, unknown parameter, or an element "
        "Archicad would refuse: in another view's database than the active window's, "
        "on a hidden or locked layer, inside a hotlinked module, locked with Edit > "
        "Locking, or not reserved in Teamwork); 'not_sent' and 'stopped' if a batch "
        "was refused outright after earlier batches landed; 'unconfirmed' if the "
        "read after the write failed. run_parameter_script=true (Tapir 1.7.3) runs "
        "the Parameter Script of every element whose GDL write went through, before "
        "the read back, so parameters that depend on the written ones update as they "
        "would after an edit in the settings dialog; 'parameter_script' reports the "
        "run. Off by default: a part's script can reset what was just written."),
              **_tool_meta("Write element data", read_only=False, destructive=True))
    @_guarded
    def set_element_data(changes: list[dict], dry_run: bool = True,
                         run_parameter_script: bool = False,
                         port: int | None = None) -> dict:
        return _element_data.set_element_data(_conn(port), changes, dry_run,
                                              run_parameter_script)

    from archicad_mcp.core import classification_edit as _classification_edit
    from archicad_mcp.core import definition_edit as _definition_edit

    @mcp.tool(description=(
        "Edit custom property DEFINITIONS in place (not element values): name, "
        "description, group, default value or expressions, availability, enum "
        "options. DRY-RUN BY DEFAULT: returns each property's before/after and "
        "warnings; pass dry_run=false to commit. Address properties as 'Group/Name' "
        "(search_definitions) or GUID; availability entries as 'System/Code', with "
        "'/*' for the item and everything below it; enum options by their text (or "
        "GUID when two options share a text). Example change: {\"property\": "
        "\"Office/Status\", \"name\": \"Approval\", \"availability\": {\"add\": "
        "[\"Uniclass/Ss_25/*\"]}, \"enum\": {\"rename\": {\"Old\": \"New\"}, "
        "\"remove\": [\"X\"], \"add\": [\"Y\"], \"order\": [...]}, \"default\": "
        "\"New\"}. GUIDs are kept, so element values survive everything except "
        "removing an option (those elements show <Undefined>) or availability. In a "
        "Teamwork project the Properties set must be reserved first (reserve_elements "
        "object_sets). Needs the Tapir build with UpdateClassificationItems."),
        **_tool_meta("Edit property definitions", read_only=False, destructive=True))
    @_guarded
    def edit_property_definitions(changes: list[dict], dry_run: bool = True,
                                  port: int | None = None) -> dict:
        return _definition_edit.edit_property_definitions(_conn(port), changes, dry_run)

    @mcp.tool(description=(
        "Edit classification systems and items in place. DRY-RUN BY DEFAULT; pass "
        "dry_run=false to commit. Items: {\"item\": \"System/Code\", \"code\"?, "
        "\"name\"?, \"description\"?}. Systems: {\"system\": \"Name\", \"name\"?, "
        "\"description\"?, \"source\"?, \"version\"?, \"date\"? (YYYY-MM-DD)}. Items "
        "keep their GUID, so classified elements and property availability stay "
        "attached. Moving an item to another parent is not supported. In a Teamwork "
        "project the Classifications set must be reserved first (reserve_elements "
        "object_sets)."),
        **_tool_meta("Edit classifications", read_only=False, destructive=True))
    @_guarded
    def edit_classifications(changes: list[dict], dry_run: bool = True,
                             port: int | None = None) -> dict:
        return _classification_edit.edit_classifications(_conn(port), changes, dry_run)

    @mcp.tool(description=(
        "Import a Property Manager (kind='property') or Classification Manager "
        "(kind='classification') XML file. DRY-RUN BY DEFAULT: lists what is new and "
        "what collides with existing names, and what the conflict policy does. "
        "conflict: property append|replace|skip; classification merge|replace|skip "
        "(item_conflict replace|skip). Commit returns what was created and removed."),
        **_tool_meta("Import definitions XML", read_only=False, destructive=True))
    @_guarded
    def import_definitions(kind: str, xml_path: str, conflict: str,
                           item_conflict: str = "skip", dry_run: bool = True,
                           port: int | None = None) -> dict:
        return _classification_edit.import_definitions(
            _conn(port), kind, xml_path, conflict, item_conflict, dry_run)

    from archicad_mcp.core.part_records import PartRecords

    # The one store of learned library parts. The file is read when a tool first
    # needs it, never here, so an unreadable file stops no server start: it comes
    # back as the tool's error. create_elements, modify_elements and the learning
    # tools share it.
    records = PartRecords()

    from archicad_mcp.core import create as _create
    from archicad_mcp.core import mutate as _mutate
    from archicad_mcp.core import selection as _selection

    @mcp.tool(description=(
        "Create elements of any type Tapir can create. element_type: model wall, "
        "beam, column, slab, roof, morph, mesh, stair, railing, zone, curtain_wall, "
        "grid_element, object, lamp; hosted door, window (in a wall), opening; MEP "
        "mep_route, mep_element (Archicad 28+), electrical_panel, electrical_device, "
        "electrical_circuit (Archicad 30); markers section, interior_elevation; "
        "hotlink_instance; 2D "
        "line, arc, circle, polyline, spline, hatch, hotspot, text, label, "
        "keynote_label; dimension, level_dimension, wall_thickness_dimension, "
        "section_dimension; "
        "drawing (on a layout). Plurals and Tapir command names work too. Items use "
        "Tapir's field names and units (metres, radians): call with items=[] to get "
        "the item schema for a type, with notes on what the schema leaves out (which "
        "heights are absolute, which side a wall's body, a door's swing and a stair's "
        "flight lie on). DRY-RUN BY DEFAULT: every item is validated "
        "against the schema (any problem: nothing is sent, problems listed by item "
        "index and field), a favoriteName and a libraryPartName must exist (closest "
        "names suggested), and hosts (a door's wall, the elements a dimension "
        "measures, a label's parent) must exist, be the right type and be "
        "editable; items failing those are skipped with the reason and the rest go "
        "ahead. Most types take the Element ID as 'id' from Tapir 1.7.3 (doors, windows, "
        "openings, roofs and morphs do not); on an older Tapir such an item is skipped. "
        "Warns when the active window is wrong for the type, when an object "
        "or lamp has no dimensions (it would take the tool's current size, not the "
        "library part's), when it sends offset or useFixedAngle (Archicad "
        "discards them), when a door or window has no sillHeight (it would "
        "take the tool's sill), when one placed by libraryPartName leaves the Door "
        "or Window tool holding that part, when an absolute height lies off the story "
        "floorIndex names, when a stair's flights overlap, and, after the create, when "
        "a stair runs past its baseline. Learn a part once with learn_library_part "
        "before placing it by placement; a learned part is then placed with no "
        "samples. An object, lamp, door or window of such a part may give placement "
        "instead: an object's footprint centre, size (length along the head, width "
        "across it) and head direction (the head is the end that goes against the "
        "wall: a bed's pillow end, a chair's or sofa's back), for coordinates, angle, "
        "reflected and dimensions; a door's or window's hinge_near and opens_toward "
        "points, for reflected and oSide, and leaves (a recorded leaf state, written "
        "right after the create). items=[] shows its placement_schema; 'placements' "
        "lists what each one derived, and for a door or window the hinge and swing it "
        "read. Reading a sketch: choose the part from what it draws, the "
        "lines dividing a window give its sashes and the arcs give which sash or leaf "
        "opens and where it is hinged; prefer the plain generic part (\"Window\", "
        "\"Door\", \"Double Window\") when the sketch says no more. Width is a check: "
        "a window about 1.2 m wide is a single or a double window, never a multi-sash "
        "one; when the count and the width disagree, ask. A bed's head is the end with "
        "the pillows, or with the duvet's turned-back edge; when the sketch shows "
        "neither, ask. After placing, look (capture_window) and correct with "
        "modify_elements before reporting done. Pass dry_run=false to create: returns "
        "created GUIDs, plus "
        "Tapir's per-item errors grouped with item indices and by_index (item "
        "index -> GUID) when not every item was created, and 'placed_not_set' for an "
        "element placed whose leaves Archicad refused."),
              **_tool_meta("Create elements", read_only=False, destructive=True))
    @_guarded
    def create_elements(element_type: str, items: list[dict], dry_run: bool = True,
                        port: int | None = None) -> dict:
        return _create.create_elements(_conn(port), element_type, items, dry_run,
                                       records=records)

    from archicad_mcp.core import swept_beam as _swept

    @mcp.tool(description=(
        "Place a Swept Beam: a sloped, curved beam with a Profile Manager profile "
        "or a rectangle, as the 'Swept Beam' library part. The path comes from "
        "source_guid (a Morph line, Polyline, Line or Arc) or from points "
        "[{x, y, z}] in metres. Flat sources take start_height (metres above the "
        "source) and slope_percent; morph lines and points keep their own heights. "
        "profile is {\"attribute\": \"<Profile name>\"} or {\"rectangle\": {\"width\", "
        "\"height\", \"building_material\"}} (default 0.2 x 0.2 rectangle). "
        "update_guid rewrites the path of an existing Swept Beam, keeping its GUID; "
        "its profile, offsets, flip and end cuts stay unless given, and node roll "
        "resets to 0. End cuts in degrees: cut_start_plan and cut_end_plan turn a cut "
        "counter-clockwise from square in plan (-80 to 80); cut_start_tilt and "
        "cut_end_tilt lean the top of the face out past the end (-85 to 85, 0 is "
        "square to the beam, the beam's slope there gives a vertical face). "
        "ref_line ('left face', 'centre', 'right face', looking from the first "
        "point to the last) puts that face of the beam on the path; ref_offset "
        "(metres) moves the beam that far away from it (with centre, to the right). "
        "DRY-RUN BY DEFAULT: reports nodes, arcs, the maximum deviation from the "
        "source and warnings. Pass dry_run=false to place or update."),
        **_tool_meta("Create Swept Beam", read_only=False, destructive=True))
    @_guarded
    def create_swept_beam(source_guid: str | None = None,
                          points: list[dict] | str | None = None,
                          start_height: float | None = None,
                          slope_percent: float | None = None,
                          profile: dict | str | None = None,
                          offset_u: float | None = None, offset_w: float | None = None,
                          flip: bool | None = None, path_tolerance: float = 0.002,
                          update_guid: str | None = None, dry_run: bool = True,
                          cut_start_plan: float | None = None,
                          cut_start_tilt: float | None = None,
                          cut_end_plan: float | None = None,
                          cut_end_tilt: float | None = None,
                          ref_line: str | None = None, ref_offset: float | None = None,
                          port: int | None = None) -> dict:
        conn = _tapir_conn(port, "create_swept_beam")
        return _swept.create_swept_beam(conn, source_guid, points, start_height,
                                        slope_percent, profile, offset_u, offset_w, flip,
                                        path_tolerance, update_guid, dry_run,
                                        cut_start_plan=cut_start_plan,
                                        cut_start_tilt=cut_start_tilt,
                                        cut_end_plan=cut_end_plan,
                                        cut_end_tilt=cut_end_tilt,
                                        ref_line=ref_line, ref_offset=ref_offset)

    from archicad_mcp.core import road_marking as _road

    @mcp.tool(description=(
        "Place a Road Marking (lane lines, zebra crossings, stop and give way lines, "
        "parking rows, disabled, taxi and EV bays, bus and taxi stops, BUS and TAXI "
        "lettering) as the 'Road Marking' library part, and drape it onto Meshes and "
        "Slabs. The path comes from source_guid (a Morph line, Polyline, Line or Arc) "
        "or points [{x, y, z}] in metres. marking is a preset name, for example "
        "'5121 Broken divider, in settlements', '5352-1 Disabled bays, shared zone' or "
        "'5333-2 Bus stop on the carriageway', or 'Custom'. stripes changes stripes: "
        "{\"1\": {on, offset, width, dash, gap, angle, shape}}; bays sets {layout, "
        "width, depth, zone_width, zone_first, bar_width, symbol}; lettering sets "
        "{text, length, width, gap, offset, align, shift, repeat}. Offsets, bays and "
        "the word lie to the right looking from the first point to the last. drape_on "
        "lists Mesh and Slab GUIDs: the cross fall follows the surface, the paint never "
        "sinks below it, and nodes are added until the marking is within "
        "drape_tolerance (metres) of it along the path; lift raises it. update_guid "
        "rewrites a placed marking, keeping its settings unless given; with drape_on and "
        "no source it drapes the marking's own path. DRY-RUN BY DEFAULT: reports nodes, "
        "the marking's reach, drape deviations, stretches off the surface and warnings. "
        "Pass dry_run=false to place or update."),
        **_tool_meta("Create Road Marking", read_only=False, destructive=True))
    @_guarded
    def create_road_marking(source_guid: str | None = None,
                            points: list[dict] | str | None = None,
                            start_height: float | None = None,
                            slope_percent: float | None = None,
                            marking: str | None = None,
                            stripes: dict | str | None = None,
                            bays: dict | str | None = None,
                            lettering: dict | str | None = None,
                            flip_sides: bool | None = None,
                            pattern_align: str | None = None,
                            pattern_shift: float | None = None,
                            whole_dashes: bool | None = None,
                            thickness: float | None = None,
                            paint_surface: str | int | None = None,
                            drape_on: list[str] | str | None = None,
                            lift: float = 0.0, drape_tolerance: float = 0.005,
                            path_tolerance: float = 0.002,
                            update_guid: str | None = None, dry_run: bool = True,
                            port: int | None = None) -> dict:
        return _road.create_road_marking(
            _tapir_conn(port, "create_road_marking"), source_guid=source_guid,
            points=points, start_height=start_height,
            slope_percent=slope_percent, marking=marking, stripes=stripes, bays=bays,
            lettering=lettering, flip_sides=flip_sides, pattern_align=pattern_align,
            pattern_shift=pattern_shift, whole_dashes=whole_dashes, thickness=thickness,
            paint_surface=paint_surface, drape_on=drape_on, lift=lift,
            drape_tolerance=drape_tolerance, path_tolerance=path_tolerance,
            update_guid=update_guid, dry_run=dry_run)

    @mcp.tool(description="Move elements by a vector {x,y,z} in meters. Refuses without "
                          "confirm=true. Archicad changes only elements in the "
                          "database of the active window, so elements it would "
                          "refuse (another view's, on a hidden or locked layer, "
                          "not reserved in Teamwork, inside a hotlinked module, "
                          "locked, or not found) are not "
                          "sent. Returns requested; moved, counted from "
                          "Archicad's per-element results; not_moved grouped by "
                          "reason with the GUIDs; and active_window when "
                          "anything was refused up front.",
              **_tool_meta("Move elements", read_only=False, destructive=True))
    @_guarded
    def move_elements(guids: list[str], vector: dict, confirm: bool = False,
                      port: int | None = None) -> dict:
        return _mutate.move_elements(_tapir_conn(port, "move_elements"), guids, vector, confirm)

    from archicad_mcp.core import modify as _modify

    @mcp.tool(description=(
        "Change existing elements. DRY-RUN BY DEFAULT: returns per element the "
        "current and new value of every field, plus skipped elements with the reason "
        "and warnings; pass dry_run=false to commit. Each change is {\"guid\", "
        "...fields}. Any element: story (index, ground floor 0, or a story name; the "
        "element keeps its height above the story and moves with it unless "
        "keep_elevation=true), layer (name), draw_order (1 to 14, 0 = the type's "
        "default), id (the Element ID; Tapir 1.7.3 and newer, otherwise set_element_data "
        "General_ElementID). Walls, beams, slabs, columns, windows, doors, morphs, multi-plane "
        "roofs, meshes, objects, lamps, texts and labels also take the fields of "
        "Tapir's Modify<Type> command by its own names (describe_api_command "
        "ModifyWalls lists them), e.g. height, thickness, sillHeight, lightIsOn. "
        "Attribute fields (buildingMaterialId, compositeId, profileId, surfaceId, "
        "lineTypeId, sectionFillId, ...) take a name. Lengths in metres, angles in "
        "radians, text sizes in mm. Partial coordinates, dimensions and visibility "
        "are completed from the current values. Elements Archicad would refuse "
        "(another view's, hidden or locked layer, not reserved in Teamwork, inside a "
        "hotlinked module, locked) are skipped with the reason, never unlocked. A "
        "placed object, lamp, door or window of a part learned with learn_library_part "
        "may take placement, as create_elements does (its items=[] shows the "
        "placement_schema), in place of the fields it works out: an object or lamp "
        "keeps its story and elevation (center takes x and y only), a door or window its "
        "wall and centerOffset; leaves are written after the commit, and 'placed_not_set' "
        "names an element whose leaves were not set. A placement that cannot be worked "
        "out makes the whole call an error naming the change. After a placement, look "
        "(capture_window) and correct with this tool before reporting done; "
        "create_elements has the rules for reading a sketch. A commit reads the "
        "elements back: 'modified' counts what landed, 'failed' "
        "groups Archicad's refusals, 'not_applied' lists fields Archicad answered "
        "success for but did not change."),
        **_tool_meta("Modify elements", read_only=False, destructive=True))
    @_guarded
    def modify_elements(changes: list[dict], dry_run: bool = True,
                        port: int | None = None) -> dict:
        return _modify.modify_elements(_conn(port), changes, dry_run, records=records)

    from archicad_mcp.core import favorites as _favorites

    @mcp.tool(description=(
        "Make existing elements match a favourite (Tapir), the office standard in one "
        "step. Without favorite it changes nothing and lists the favourites that fit "
        "the given elements' types (all types when no guids either). DRY-RUN BY "
        "DEFAULT: lists the elements it would change, skips with the reason those of "
        "another type than the favourite's or that Archicad would refuse (another "
        "view's, hidden or locked layer, not reserved in Teamwork, inside a hotlinked "
        "module, locked), and warns what the favourite takes over besides materials "
        "and pens: a wall's height, top link, reference line and structure, a "
        "column's height and top link, a door's or window's library part, size and "
        "sill, and every element's layer; for other types it says what was not "
        "checked live. Plan positions stay. Stairs, railings and "
        "curtain walls get only their own settings, not their parts'. keep (default "
        "[\"id\"]) writes each element's own Element ID back after the favourite, "
        "and \"layer\" its own layer. From Tapir 1.7.3 Tapir keeps them itself, and "
        "keep also takes \"width\", \"height\" and \"sillHeight\" (doors, windows), "
        "\"referenceLineLocation\" (walls) and \"height\", \"topOffset\" and "
        "\"relativeTopStory\" (walls, columns; keep the three together), so e.g. only "
        "the library part or the wall type changes; on older Tapir use modify_elements "
        "for heights and sizes. On Tapir 1.7.3 the dry run also names the favourite's "
        "library part, marker and folder (favorite_settings). "
        "settings, classifications, categories and properties choose what the "
        "favourite applies; classifications are added per system and property values "
        "overwrite. Pass dry_run=false to apply: returns applied, changed (per element "
        "current and new of every field the favourite changed, read back), unchanged, "
        "failed grouped by reason, and restored for keep. Classifications, categories "
        "and properties are not read back. to_defaults=true with no guids sets the "
        "favourite's tool defaults instead."),
        **_tool_meta("Apply favorites", read_only=False, destructive=True))
    @_guarded
    def apply_favorites(guids: list[str] | None = None, favorite: str | None = None,
                        settings: bool = True, classifications: bool = True,
                        categories: bool = True, properties: bool = True,
                        keep: list[str] | None = None, to_defaults: bool = False,
                        dry_run: bool = True, port: int | None = None) -> dict:
        return _favorites.apply_favorites(
            _tapir_conn(port, "apply_favorites"), guids, favorite, settings,
            classifications, categories, properties, keep, to_defaults, dry_run)

    @mcp.tool(description="Delete elements. IRREVERSIBLE. Refuses without confirm=true. "
                          "Archicad deletes only elements in the database of the "
                          "active window and still answers success for the rest "
                          "(a floor-plan label cannot be deleted while a Layout "
                          "is active), so elements it would refuse (another "
                          "view's, on a hidden or locked layer, not reserved in "
                          "Teamwork, inside a hotlinked module, locked, or not "
                          "found) are not sent, and the "
                          "rest are read back afterwards. Returns requested; "
                          "deleted, the count that is really gone; not_deleted "
                          "grouped by reason with the GUIDs; active_window when "
                          "anything was refused up front; and stopped if the "
                          "delete command itself errored.",
              **_tool_meta("Delete elements", read_only=False, destructive=True))
    @_guarded
    def delete_elements(guids: list[str], confirm: bool = False,
                        port: int | None = None) -> dict:
        return _mutate.delete_elements(_tapir_conn(port, "delete_elements"), guids, confirm)

    @mcp.tool(description="Return the GUIDs of the elements currently selected in "
                          "Archicad, and 'coverage': 'whole-plan' when read "
                          "through the Tapir add-on, 'model-elements-only' "
                          "without it (then selected markers, 2D elements and "
                          "native MEP routes are left out, and an empty list is "
                          "not proof that nothing is selected).",
              **_tool_meta("Read current selection", read_only=True, destructive=False))
    @_guarded
    def get_selection(port: int | None = None) -> dict:
        return _selection.get_selection(_conn(port))

    @mcp.tool(description="Replace the current selection with the given element GUIDs. "
                          "Whatever the user had selected by hand is deselected. "
                          "The selection is read back afterwards. Returns "
                          "requested; selected, the count Archicad really "
                          "selected (it answers success and still leaves some "
                          "unselected: section Objects, railing sub-elements, "
                          "elevation marker parts, or GUIDs not in the plan); "
                          "not_selected with those GUIDs when any; and the "
                          "coverage of the read, as get_selection.",
              **_tool_meta("Replace current selection", read_only=False, destructive=False))
    @_guarded
    def set_selection(guids: list[str], port: int | None = None) -> dict:
        return _selection.set_selection(_conn(port), guids)

    @mcp.tool(description="Deselect everything in the Archicad window.",
              **_tool_meta("Clear current selection", read_only=False, destructive=False))
    @_guarded
    def clear_selection(port: int | None = None) -> dict:
        return _selection.clear_selection(_conn(port))

    from archicad_mcp.core import views as _views

    @mcp.tool(description=(
        "Show elements to the user in Archicad: open the story or view that holds them "
        "(a section, elevation, detail, worksheet or layout when they are not on the "
        "floor plan), select them, highlight them and zoom to them. When they span "
        "several stories or views, the one holding the most is shown and the rest are "
        "listed (on_other_stories, in_other_views) for another call; with the 3D window "
        "in front, floor plan elements are shown there across every story. selected "
        "counts what Archicad really selected (not_selectable lists the rest). "
        "highlight=false removes any earlier highlight; guids=[] removes the highlight "
        "and, with select, the selection, and changes nothing else. Changes only what "
        "is on screen, never the project."),
              **_tool_meta("Show elements in Archicad", read_only=False, destructive=False))
    @_guarded
    def show_elements(guids: list[str], select: bool = True, highlight: bool = True,
                      zoom: bool = True, port: int | None = None) -> dict:
        return _views.show_elements(_tapir_conn(port, "show_elements"), guids, select,
                                    highlight, zoom)

    from archicad_mcp.core import window_image as _window_image

    @mcp.tool(description=(
        "See what the current Archicad window shows (floor plan, section, 3D, layout "
        "...) as an image, to check a result by eye: which way a placed object faces, "
        "which way a door opens, whether walls meet. Call show_elements first to bring "
        "the elements into view. entire_drawing=true covers the whole drawing of a 2D "
        "window instead of the current zoom. Images larger than 1568 pixels are scaled "
        "down (scaled_from gives the original size). Needs Tapir 1.7.2. Reads only."),
              **_tool_meta("Capture the Archicad window", read_only=True))
    @_guarded
    def capture_window(entire_drawing: bool = False, port: int | None = None):
        result = _window_image.capture_window(_tapir_conn(port, "capture_window"),
                                              entire_drawing)
        png = result.pop("png", None)
        if png is None:
            return result
        return [Image(data=png, format="png"), result]

    from archicad_mcp.core import part_learning as _part_learning

    @mcp.tool(description=(
        "Learn a library part once, before placing it by placement; a learned part is "
        "then placed with no samples. Places samples of the part named name (kind: "
        "object, lamp, door or window) in a row just past the right edge of the model, "
        "on the story the floor plan shows, measures their footprints, shows them in "
        "one floor plan image, deletes every sample again in the same call and stores "
        "the measurement. The reply lists 'samples' left to right as the image shows "
        "them, the first sample's GDL parameters, warnings, and 'next', the fields "
        "still to give record_library_part. variants: optional sets of GDL parameter "
        "values (parameter name to value), each placed as one more sample, such as a "
        "leaf or one of a window's sashes opened. On Tapir 1.7.2 a door or window placed "
        "by part name, as its samples are, has GDL parameters that can be neither read "
        "nor written, and the Door or Window tool keeps that fault for later openings, "
        "so a door's or window's samples show no parameters and a placement's leaves "
        "end in placed_not_set; the same part placed from a favourite has them. When a window's capture shows no opening lines, the view "
        "settings hide them: ask the user to show them, then learn the window again, "
        "before recording hinge and swing. A part whose plan size does not follow its "
        "own A and B gets no footprint (the reply's note says so) and is placed by "
        "coordinates. Lengths are metres. The floor plan must be the active window, "
        "with its view not rotated. It zooms the plan to the samples and leaves it "
        "zoomed, since Tapir cannot set the zoom back; a door or window learned may "
        "leave the Door or Window tool holding the part. Needs Tapir 1.7.2."),
              **_tool_meta("Learn a library part", read_only=False, destructive=True))
    @_guarded
    def learn_library_part(name: str, kind: str, variants: list[dict] | None = None,
                           port: int | None = None):
        result = _part_learning.learn_library_part(
            _tapir_conn(port, "learn_library_part"), records, name, kind, variants)
        png = result.pop("png", None)
        if png is None:
            return result
        return [Image(data=png, format="png"), result]

    @mcp.tool(description=(
        "Record what you judged from learn_library_part's image, in the part's stored "
        "record. Objects and lamps: head ('+x', '-x', '+y' or '-y', the part's own "
        "axis its head points along), one_sided (true or false) and height (metres). "
        "The head is the end that goes against the wall: a bed's pillow end, a chair's "
        "or sofa's back, not its front. A bed's head is the end with the pillows, or "
        "with the duvet's turned-back edge; when the image shows neither, ask. Doors "
        "and windows (learned with refSide false): combos, which "
        "maps a flag pair (reflected then oSide, t for true and f for false: 'ff', "
        "'tf', 'ft' or 'tt') to [hinge, swing], hinge 'begin' or 'end' of the wall's "
        "own direction and swing 'left' or 'right' of it; leaves (a whole number, at "
        "least 1); and leaf_states, which maps a state name to GDL parameter values. "
        "The lines dividing a window give its sashes, the arcs give which sash or "
        "leaf opens and where it is hinged. combos and leaf_states merge by key: "
        "recording a key again replaces it, and removing one takes forget and a new "
        "learn; every other field replaces its value. One refused value saves "
        "nothing. Returns the record and 'missing', the fields still needed before "
        "the part can be placed from it. forget=true removes the part's record, which "
        "can be learned again, and takes no other field. The part must have been "
        "learned first. Writes only the MCP's own learned-parts file, never the "
        "model, and needs no Archicad."),
              **_tool_meta("Record a learned library part", read_only=False,
                           destructive=False))
    @_guarded
    def record_library_part(name: str, head: str | None = None,
                            one_sided: bool | None = None, height: float | None = None,
                            combos: dict | None = None, leaves: int | None = None,
                            leaf_states: dict | None = None, forget: bool = False) -> dict:
        return _part_learning.record_library_part(records, name, head, one_sided, height,
                                                  combos, leaves, leaf_states, forget)

    @mcp.tool(description=(
        "List the learned library parts, all or only those in names, each with its "
        "stored fields and 'missing', the fields still needed before it can be placed "
        "from. 'not_recorded' names any part asked for that has no record. Also "
        "returns the path of the records file. Reads only that local file and needs "
        "no Archicad."),
              **_tool_meta("List learned library parts", read_only=True, destructive=False))
    @_guarded
    def list_library_part_records(names: list[str] | None = None) -> dict:
        return _part_learning.list_library_part_records(records, names)

    from archicad_mcp.core import collisions as _collisions

    @mcp.tool(description=(
        "Find clashes: which elements of one group collide with which elements of "
        "another (Tapir). The first group is guids plus every element of "
        "element_types; the second is against_guids plus against_types, and without "
        "it the first group is checked against itself. Element types are Archicad's "
        "names (Wall, Beam, Column, Slab, Object, Morph, ...); for property criteria "
        "call find_elements first and pass its GUIDs. volume_tolerance (m3, default "
        "0.001) is the overlap volume below which a clash is ignored; "
        "surface_tolerance (m2) also reports touching faces over that area. Returns "
        "each pair with both elements' type and ID, the first one's story, and kind "
        "(body, clearance for MEP clearance zones, or both), counts by type pair, and "
        "'elements', every GUID in a pair, to pass to show_elements. Archicad does not "
        "count elements its own junctions clean up (crossing walls); the volume check "
        "sees only closed bodies, so an open surface (a Morph surface) clashes only "
        "with surface_tolerance set; building materials set not to take part in "
        "collision detection are left out. No pairs is not proof that nothing "
        "overlaps. A whole-model check can take a while. Reads only."),
              **_tool_meta("Find collisions", read_only=True, destructive=False))
    @_guarded
    def find_collisions(guids: list[str] | None = None,
                        element_types: list[str] | None = None,
                        against_guids: list[str] | None = None,
                        against_types: list[str] | None = None,
                        volume_tolerance: float = _collisions.DEFAULT_VOLUME_TOLERANCE,
                        surface_tolerance: float | None = None,
                        port: int | None = None) -> dict:
        return _collisions.find_collisions(
            _tapir_conn(port, "find_collisions"), guids, element_types, against_guids,
            against_types, volume_tolerance, surface_tolerance)

    from archicad_mcp.core import teamwork as _teamwork

    @mcp.tool(description=(
        "Reserve elements, and non-element object sets, in a Teamwork project so "
        "you can edit them (Tapir). CONFIRM-GATED: without confirm=true it only "
        "reports what it can learn without touching the server: not_found, "
        "already_mine, and would_attempt. On Tapir 1.7.3 it also reads who holds an "
        "element (held_by_others, with the user's name); on older Tapir that is only "
        "learned by attempting, because Archicad exposes no read for it. With "
        "confirm=true returns reserved, reserved_by_others (with the user's name), "
        "already_mine, not_found, and indirectly_reserved: elements Archicad pulled "
        "into your workspace that you did not ask for, such as a door's wall. "
        "object_sets reserves what attribute, property and classification edits "
        "need: an attribute type (Layers, Composites, Fills, Lines, PenTables, "
        "Profiles, BuildingMaterials, Surfaces, ZoneCategories), Properties or "
        "Classifications, or any Archicad set name (Favorites, ProjectInfo, ...); "
        "needs Tapir 1.7.1. Each set's entry says status, held_by and outcome "
        "(would_reserve, already_mine, held_by_others, reserved, failed); for sets "
        "the dry run does know who holds them. A reservation is visible to every "
        "teammate and blocks their edits until released."),
              **_tool_meta("Reserve Teamwork elements and sets", read_only=False,
                           destructive=False))
    @_guarded
    def reserve_elements(guids: list[str] | None = None,
                         object_sets: list[str] | None = None, confirm: bool = False,
                         port: int | None = None) -> dict:
        return _teamwork.reserve_elements(_tapir_conn(port, "reserve_elements"), guids,
                                          confirm, object_sets)

    @mcp.tool(description=(
        "Release elements, and non-element object sets, from your Teamwork "
        "workspace (Tapir). CONFIRM-GATED: without confirm=true it reports "
        "would_release (the ones actually in your workspace), not_mine and "
        "not_found. With confirm=true releases them and reports released and "
        "still_mine. Unsent changes on a released element are not lost by this "
        "call; TeamworkSend is the gateway's job. object_sets takes the names "
        "reserve_elements takes; releasing a set sends that set's changes to the "
        "server first (Archicad does that as part of the release)."),
              **_tool_meta("Release Teamwork elements and sets", read_only=False,
                           destructive=False))
    @_guarded
    def release_elements(guids: list[str] | None = None,
                         object_sets: list[str] | None = None, confirm: bool = False,
                         port: int | None = None) -> dict:
        return _teamwork.release_elements(_tapir_conn(port, "release_elements"), guids,
                                          confirm, object_sets)

    from archicad_mcp.core import attributes as _attributes
    from archicad_mcp.core import issues as _issues
    from archicad_mcp.core import project as _project
    from archicad_mcp.core import publish as _publish

    @mcp.tool(description="Project info: Archicad version, project name, stories, "
                          "hotlinks, geolocation presence (Tapir enriches).",
              **_tool_meta("Get project info", read_only=True, destructive=False))
    @_guarded
    def get_project_info(port: int | None = None) -> dict:
        return _project.get_project_info(_conn(port))

    @mcp.tool(description=(
        "List attributes by type: Layer, LayerCombination, Line, Fill, ZoneCategory, "
        "PenTable, Profile, BuildingMaterial, Composite, Surface (plurals work). "
        "Names only by default. details=true returns each attribute's settings "
        "(Tapir): a composite's skins and separators, a building material's fills "
        "and physical values, a layer combination's layer states, a pen table's "
        "pens, ... with references to other attributes shown by name, in the "
        "vocabulary edit_attributes takes. names= picks attributes; otherwise "
        "details are paged (limit, offset; next_offset when more remain)."),
              **_tool_meta("List attributes", read_only=True, destructive=False))
    @_guarded
    def list_attributes(attribute_type: str, details: bool = False,
                        names: list[str] | None = None, limit: int | None = None,
                        offset: int = 0, port: int | None = None) -> dict:
        return _attributes.list_attributes(_conn(port), attribute_type, details, names,
                                           limit, offset)

    from archicad_mcp.core import attribute_edit as _attribute_edit

    @mcp.tool(description=(
        "Create, modify and delete attributes (layers, layer combinations, lines, "
        "fills, zone categories, pen tables, profiles, building materials, composites, "
        "surfaces) and set attribute property values (Tapir). DRY-RUN BY DEFAULT; pass "
        "dry_run=false to commit. Each change has one action key whose value is the "
        "type: {\"create\": \"Layer\", \"name\": \"X\", <fields>} makes a new one and "
        "NEVER overwrites (an existing name is skipped, with what would change and the "
        "modify to send instead); {\"modify\": \"Composite\", \"attribute\": \"<name or "
        "GUID>\", <fields>} changes an existing one (name renames; fields not given keep "
        "their value, including what Tapir's own overwrite would drop: a composite's "
        "useWith, a building material's texts); {\"modify\": ..., \"properties\": "
        "{\"Group/Name\": \"value\"}} sets property values; {\"delete\": \"Fill\", "
        "\"attribute\": ...} deletes. Fields use Tapir's names and units (metres, "
        "radians, pen numbers); a field holding another attribute (buildingMaterialId, "
        "lineTypeId, fillId, cutFillIndex, a layer combination's layers[].attributeId) "
        "takes its name. changes=[] lists every type's fields; list_attributes"
        "(details=true) shows current values in the same form. The dry run shows "
        "current -> new and skips with the reason anything Archicad or Tapir would "
        "refuse or silently get wrong: misspelt enums, unknown references, Archicad's "
        "own index 1 attributes, a layer delete while elements are on the layer (it "
        "would delete them) or without the floor plan active. In Teamwork the "
        "attribute type's set must be reserved (reserve_elements object_sets). "
        "Attribute writes cannot be undone. A commit returns applied (read back), "
        "not_applied, failed (grouped) and stopped."),
              **_tool_meta("Edit attributes", read_only=False, destructive=True))
    @_guarded
    def edit_attributes(changes: list[dict], dry_run: bool = True,
                        port: int | None = None) -> dict:
        return _attribute_edit.edit_attributes(_tapir_conn(port, "edit_attributes"),
                                               changes, dry_run)

    @mcp.tool(description="List the issues in the open project, with their ids "
                          "(requires the Tapir add-on).",
              **_tool_meta("List issues", read_only=True, destructive=False))
    @_guarded
    def list_issues(port: int | None = None) -> dict:
        return _issues.list_issues(_tapir_conn(port, "list_issues"))

    @mcp.tool(description="Create a new issue in the open project and return its id "
                          "(requires the Tapir add-on).",
              **_tool_meta("Create an issue", read_only=False, destructive=True))
    @_guarded
    def create_issue(name: str, port: int | None = None) -> dict:
        return _issues.create_issue(_tapir_conn(port, "create_issue"), name)

    @mcp.tool(description="Add a text comment to an existing issue, addressed by its id "
                          "(requires the Tapir add-on).",
              **_tool_meta("Comment on an issue", read_only=False, destructive=True))
    @_guarded
    def add_issue_comment(issue_id: str, comment: str, port: int | None = None) -> dict:
        conn = _tapir_conn(port, "add_issue_comment")
        return _issues.add_issue_comment(conn, issue_id, comment)

    @mcp.tool(description="Attach elements to an existing issue as highlights "
                          "(requires the Tapir add-on).",
              **_tool_meta("Attach elements to an issue", read_only=False, destructive=True))
    @_guarded
    def attach_elements_to_issue(issue_id: str, guids: list[str],
                                 port: int | None = None) -> dict:
        conn = _tapir_conn(port, "attach_elements_to_issue")
        return _issues.attach_elements_to_issue(conn, issue_id, guids)

    @mcp.tool(description="Export every issue in the project to a BCF file at the given "
                          "path, aligned to the survey point. Overwrites the file if it "
                          "exists (requires the Tapir add-on).",
              **_tool_meta("Export issues to BCF", read_only=False, destructive=True))
    @_guarded
    def export_issues_bcf(bcf_path: str, port: int | None = None) -> dict:
        return _issues.export_issues_bcf(_tapir_conn(port, "export_issues_bcf"), bcf_path)

    @mcp.tool(description="Import issues into the project from a BCF file, aligned to "
                          "the survey point (requires the Tapir add-on).",
              **_tool_meta("Import issues from BCF", read_only=False, destructive=True))
    @_guarded
    def import_issues_bcf(bcf_path: str, port: int | None = None) -> dict:
        return _issues.import_issues_bcf(_tapir_conn(port, "import_issues_bcf"), bcf_path)

    from archicad_mcp.core import ifc_export as _ifc_export

    @mcp.tool(description=(
        "Save the project as an IFC file, the step after verify_ifc_export_readiness. "
        "DRY-RUN BY DEFAULT: returns the translator, path, file type and scope, and "
        "lists every translator; pass dry_run=false to write. path: absolute, ending in "
        ".ifc or .ifczip. translator: a name from the project's IFC translators "
        "(default: the preview translator). scope: EntireProject, "
        "VisibleElementsOnAllStories (default), AllElementsOnCurrentStory, "
        "VisibleElementsOnCurrentStory or SelectedElementsOnly. Refuses to replace an "
        "existing file unless overwrite=true. Archicad exports from the floor plan or "
        "the 3D window, so from any other window it first brings the floor plan to "
        "the front and leaves it there. The commit checks the file and returns its "
        "size. Never opens or merges IFC. Needs Tapir 1.6.0."),
              **_tool_meta("Export IFC", read_only=False, destructive=True))
    @_guarded
    def export_ifc(path: str, translator: str | None = None,
                   scope: str = "VisibleElementsOnAllStories", overwrite: bool = False,
                   dry_run: bool = True, port: int | None = None) -> dict:
        return _ifc_export.export_ifc(_tapir_conn(port, "export_ifc"), path, translator,
                                      scope, overwrite, dry_run)

    @mcp.tool(description="Fire an Archicad publisher set by name (Tapir).",
              **_tool_meta("Run a publisher set", read_only=False, destructive=True))
    @_guarded
    def publish(publisher_set_name: str, port: int | None = None) -> dict:
        return _publish.publish(_tapir_conn(port, "publish"), publisher_set_name)

    @mcp.tool(description=(
        "Install the pinned Tapir add-on (1.7.2) into Archicad's Add-Ons folder. "
        "Dry run by default: it finds every Archicad on this computer, downloads and "
        "checks the file (sha256) and says what it would do. confirm=true writes. "
        "Never replaces an existing Tapir unless replace_existing=true, and then "
        "moves the old copy to a backup folder; a custom Tapir build loses its own "
        "commands when replaced. archicad_version limits it to one version. On macOS "
        "a protected Add-Ons folder brings up the system password dialog; on "
        "Windows it returns the command to run from an administrator terminal. "
        "Archicad must be restarted afterwards. Needs no running Archicad."),
        **_tool_meta("Install the Tapir add-on", read_only=False, destructive=True))
    def install_tapir(archicad_version: int | None = None, replace_existing: bool = False,
                      confirm: bool = False) -> dict:
        from archicad_mcp.tapir_install import InstallError
        from archicad_mcp.tapir_install import run as _tapir_run
        try:
            report = _tapir_run.run(
                versions={archicad_version} if archicad_version else None,
                replace=replace_existing, dry_run=not confirm)
        except InstallError as exc:
            return {"error": str(exc)}
        result = report.to_dict()
        result["restart_required"] = [o.version for o in report.outcomes if o.installed]
        result["backups"] = [str(o.backup) for o in report.outcomes if o.backup]
        return result

    @mcp.tool(description="Describe an exported Archicad schedule scheme XML: its "
                          "criteria and its ordered columns, with what each column "
                          "binds to. Schedules have no API, so export the scheme "
                          "first via Document > Schedules > Scheme Settings > Export "
                          "and pass the file path. Reads the file only, never "
                          "Archicad.",
              **_tool_meta("Read a schedule scheme", read_only=True, destructive=False))
    def read_schedule_scheme(path: str) -> dict:
        return core_schemes.read_schedule_scheme(path)

    @mcp.tool(description="Apply a YAML scheme spec to an exported schedule scheme "
                          "XML: set the columns and their order, retarget bindings, "
                          "rename the scheme. DRY-RUN BY DEFAULT: returns the before "
                          "and after column lists and writes nothing until "
                          "dry_run=false. Never overwrites the input; writes to "
                          "'output' or to <name>.edited.xml beside it. Import the "
                          "result via Document > Schedules > Scheme Settings > "
                          "Import. Criteria are preserved, not yet editable. A spec "
                          "that binds every property by GUID needs no Archicad "
                          "connection and runs fully offline; a spec that binds a "
                          "property by a 'Group/Name' string needs Archicad open "
                          "so the name can be resolved.",
              **_tool_meta("Edit a schedule scheme", read_only=False, destructive=True))
    @_guarded
    def edit_schedule_scheme(path: str, spec_path: str, spec_id: str | None = None,
                             output: str | None = None, dry_run: bool = True,
                             port: int | None = None) -> dict:
        return core_schemes.edit_schedule_scheme(
            path, spec_path, spec_id, output, dry_run,
            port if port is not None else default_port)

    @mcp.tool(description="Check an exported schedule scheme against the open "
                          "project: do its property bindings still exist, and does "
                          "any column caption disagree with what it is bound to. "
                          "Reads property definitions only, not values, so it does "
                          "not risk the property-read crash.",
              **_tool_meta("Validate a schedule scheme", read_only=True, destructive=False))
    @_guarded
    def validate_schedule_scheme(path: str, port: int | None = None) -> dict:
        return core_schemes.validate_schedule_scheme(
            path, port if port is not None else default_port)

    from archicad_mcp.gateway import execute as _gateway
    from archicad_mcp.gateway.registry import build_registry

    registry = build_registry()

    @mcp.tool(description="Catalog of ALL available Archicad API commands (official "
                          "JSON API + Tapir). Filter by group, or by access='read' / "
                          "'write' to see which of the two execute tools runs a "
                          "given command.",
              **_tool_meta("List API commands", read_only=True, destructive=False))
    def list_api_commands(group: str | None = None, access: str | None = None) -> dict:
        return _gateway.list_api_commands(registry, group, access)

    @mcp.tool(description="Full description and input schema for one API command. "
                          "Call before execute_read_api_command or "
                          "execute_write_api_command.",
              **_tool_meta("Describe an API command", read_only=True, destructive=False))
    def describe_api_command(name: str) -> dict:
        return _gateway.describe_api_command(registry, name)

    @mcp.tool(description="Run one read-only Archicad API command by name and return "
                          "its result. Covers the official Archicad JSON API "
                          "(https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/) "
                          "and the Tapir add-on "
                          "(https://github.com/ENZYME-APD/tapir-archicad-automation). "
                          "Reads only: a command that changes the project is refused "
                          "here and belongs to execute_write_api_command. Params are "
                          "validated against the bundled schema where available. "
                          "Prefer the dedicated tools when one exists.",
              **_tool_meta("Run a read-only API command", read_only=True, destructive=False))
    @_guarded
    def execute_read_api_command(name: str, params: dict | str | None = None,
                                 port: int | None = None) -> dict:
        return _gateway.execute_read_api_command(
            registry, lambda: _conn(port), name, params)

    @mcp.tool(description="Run one Archicad API command that changes the project. "
                          "Covers the official Archicad JSON API "
                          "(https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/) "
                          "and the Tapir add-on "
                          "(https://github.com/ENZYME-APD/tapir-archicad-automation). "
                          "IRREVERSIBLE for many commands, and reaches DeleteElements "
                          "and QuitArchicad among others. Refuses without confirm=true; "
                          "the refusal echoes the command and params it would have run. "
                          "Params are validated against the bundled schema where "
                          "available. Prefer the dedicated tools when one exists.",
              **_tool_meta("Run a write API command", read_only=False, destructive=True))
    @_guarded
    def execute_write_api_command(name: str, params: dict | str | None = None,
                                  confirm: bool = False,
                                  port: int | None = None) -> dict:
        return _gateway.execute_write_api_command(
            registry, lambda: _conn(port), name, params, confirm)


_BANNER_PREFIX = "archicad-mcp:"

# What the user loses when the add-on is absent, named rather than implied, so
# the log line answers "why did create_elements fail" without a docs round trip.
_NO_TAPIR_NOTE = ("element creation, issues, IFC checks, highlighting and "
                  "publishing are unavailable, and element counts cover model "
                  "elements only")


def _instance_line(info: InstanceInfo, mode: str) -> str:
    if not info.project_open:
        detail = f" ({info.status_error})" if info.status_error else ""
        return (f"{_BANNER_PREFIX} Archicad on port {info.port} refused API "
                f"commands{detail}: it has no project open, or a modal dialog "
                "is blocking the API. Close any open dialog or open a project")
    parts = [f"Archicad {info.version} (build {info.build}) on port {info.port}"]
    # verdicts mode exists to keep project identity out of what this server
    # surfaces; the log is no exception.
    if info.project_name and mode != "verdicts":
        parts.append(f"project {info.project_name!r}")
    if info.tapir_available:
        parts.append(f"Tapir {info.tapir_version or 'version unknown'}")
    else:
        install = ("archicad-mcp install-tapir"
                   + (f" --archicad {info.version}" if info.version else ""))
        parts.append(f"Tapir add-on not installed ({_NO_TAPIR_NOTE}); "
                     f"install with: {install}")
    return f"{_BANNER_PREFIX} " + ", ".join(parts)


def _rules_phrase(rule_count: int, rules_source: str | None) -> str:
    """'1 rule loaded from /x' or '3 bundled example rules loaded (no rules
    directory set)'. A bare count was read as "scoring against nothing" when
    it was the office folder holding one uncommented rule, so the count has
    to carry where it came from."""
    noun = "rule" if rule_count == 1 else "rules"
    if rules_source:
        return f"{rule_count} {noun} loaded from {rules_source}"
    return f"{rule_count} bundled example {noun} loaded (no rules directory set)"


def _tool_gates(mode: str, gdl_workspace: Path | None, scripts_enabled: bool) -> str:
    """The ', GDL ...' and ', scripts ...' tail of the banner's config line."""
    # GDL tools register only in full mode with a workspace folder set
    if mode == "full" and gdl_workspace is not None:
        tail = f", GDL workspace {gdl_workspace}"
    else:
        tail = ", GDL tools off"
    if not scripts_enabled:
        return tail + ", scripts off"
    if mode != "full":
        return tail + ", scripts ignored in verdicts mode"
    return tail + ", scripts on"


def format_startup_banner(mode: str, rule_count: int,
                          instances: Sequence[InstanceInfo],
                          rule_errors: int = 0,
                          gdl_workspace: Path | None = None,
                          rules_source: str | None = None,
                          scripts_enabled: bool = False) -> str:
    """The diagnostic lines written to stderr at startup.

    This is what someone reads in mcp-server-archicad.log when the tools are
    not behaving, so every line has to be actionable on its own.
    """
    head = f"{_BANNER_PREFIX} mode={mode}, {_rules_phrase(rule_count, rules_source)}"
    if rule_errors:
        head += f", {rule_errors} rule file(s) rejected (call list_rules for details)"
    head += _tool_gates(mode, gdl_workspace, scripts_enabled)
    lines = [head]
    if not instances:
        first, last = PORT_RANGE[0], PORT_RANGE[-1]
        lines.append(
            f"{_BANNER_PREFIX} no Archicad answering on ports {first}-{last}. "
            "Start Archicad 29 and open a project. Tools connect on demand, so "
            "this server does not need restarting once it is up.")
    else:
        lines.extend(_instance_line(info, mode) for info in instances)
    return "\n".join(lines)


def emit_startup_banner(mode: str, rule_count: int, rule_errors: int = 0,
                        gdl_workspace: Path | None = None,
                        rules_source: str | None = None,
                        scripts_enabled: bool = False) -> None:
    """Write the banner to stderr. Never raises, never touches stdout.

    Under stdio transport stdout is the JSON-RPC channel: one stray byte there
    corrupts the stream and the client drops the server. And a diagnostic that
    can itself abort startup is worse than no diagnostic, so discovery failure
    degrades to the config line alone.
    """
    try:
        instances = discover_instances()
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break startup
        prefix_config = (f"{_BANNER_PREFIX} mode={mode}, "
                         f"{_rules_phrase(rule_count, rules_source)}")
        prefix_config += _tool_gates(mode, gdl_workspace, scripts_enabled)
        print(f"{prefix_config} (instance discovery failed: {exc})",
              file=sys.stderr, flush=True)
        return
    print(format_startup_banner(mode, rule_count, instances, rule_errors, gdl_workspace,
                                rules_source, scripts_enabled=scripts_enabled),
          file=sys.stderr, flush=True)


def start_startup_banner(mode: str, rule_count: int, rule_errors: int = 0,
                         gdl_workspace: Path | None = None,
                         rules_source: str | None = None,
                         scripts_enabled: bool = False) -> threading.Thread:
    """Emit the banner from a daemon thread so it never delays the handshake.

    The banner is a diagnostic, not a precondition for answering initialize:
    tools connect on demand, so nothing downstream waits on discovery. Every
    probe is also bounded by PROBE_TIMEOUT, but 21 ports times that bound is
    still longer than the desktop app's startup deadline, so the scan has to
    leave the startup path entirely. Daemon, so a stuck probe cannot keep the
    process alive after the client closes stdin.
    """
    worker = threading.Thread(
        target=emit_startup_banner,
        args=(mode, rule_count, rule_errors, gdl_workspace, rules_source, scripts_enabled),
        name="archicad-mcp-startup-banner", daemon=True)
    worker.start()
    return worker


def resolve_mode(raw: str | None) -> str:
    """Fall back to 'full' for an unset or blank mode.

    An .mcpb bundle substitutes an unfilled user_config field as an empty
    string, so the env var arrives set-but-empty. Passing that straight to
    argparse turns an optional setting into a startup crash.
    """
    return raw.strip() if raw and raw.strip() else "full"


def resolve_rules_dir(raw: str | Path | None) -> Path | None:
    """None for an unset or blank rules directory, so the bundled rules load.

    Path("") is Path("."), which is truthy. A blank env var therefore used to
    slip past an `if rules_dir` guard and scan the working directory, loading
    zero rules instead of falling back to the bundled examples.
    """
    text = str(raw).strip() if raw is not None else ""
    return Path(text) if text else None


def resolve_gdl_workspace(raw: str | Path | None) -> Path | None:
    """None for an unset or blank workspace, so the GDL tools stay unregistered.

    Same trap as resolve_rules_dir: an unfilled .mcpb field arrives as an empty
    string, and Path("") is Path("."), which is truthy. Registering the GDL
    tools against the working directory would be worse than not registering
    them, because builds would write .gsm files into it.
    """
    text = str(raw).strip() if raw is not None else ""
    return Path(text) if text else None


def resolve_transport(raw: str | None) -> str:
    """Fall back to 'stdio' for an unset or blank transport.

    Same reasoning as resolve_mode: a set-but-empty env var must select the
    default, not crash argparse at startup.
    """
    return raw.strip() if raw and raw.strip() else "stdio"


def resolve_flag(raw: str | None) -> bool:
    """True for 1, true or yes, case-insensitive. Unset or blank is False.

    An .mcpb bundle passes a boolean user_config field as the text "true" or
    "false", and an unfilled one as an empty string.
    """
    return (raw or "").strip().lower() in {"1", "true", "yes"}


def check_script_transport(enable_scripts: bool, mode: str, transport: str,
                           allow_over_http: bool) -> str | None:
    """Why this configuration must not start, or None.

    run_script executes arbitrary Python on this machine. Over stdio only the
    local client that launched the server can reach it; over http, anything that
    reaches the listening port can. That second case has to be asked for twice.
    """
    if enable_scripts and mode == "full" and transport == "http" and not allow_over_http:
        return ("--enable-scripts runs arbitrary Python on this machine, and "
                "--transport http lets any client that reaches the port call it. "
                "Pass --allow-scripts-over-http as well if that is intended, or "
                "drop --enable-scripts.")
    return None


def main() -> None:
    if sys.argv[1:2] == ["install-tapir"]:
        # A subcommand, not a server flag: hand off before the server's own
        # parse_known_args would swallow its options.
        from archicad_mcp.tapir_install import cli as tapir_cli
        raise SystemExit(tapir_cli.main(sys.argv[2:]))
    parser = argparse.ArgumentParser(prog="archicad-mcp")
    parser.add_argument("--mode", choices=["verdicts", "full"],
                        default=resolve_mode(os.environ.get("ARCHICAD_MCP_MODE")))
    parser.add_argument("--rules-dir", type=Path,
                        default=resolve_rules_dir(
                            os.environ.get("ARCHICAD_MCP_RULES_DIR")))
    parser.add_argument("--port", type=int, default=None,
                        help="Archicad API port (19723-19743); auto-detected if omitted")
    parser.add_argument("--gdl-workspace", type=Path,
                        default=resolve_gdl_workspace(
                            os.environ.get("ARCHICAD_MCP_GDL_WORKSPACE")))
    parser.add_argument("--transport", choices=["stdio", "http"],
                        default=resolve_transport(
                            os.environ.get("ARCHICAD_MCP_TRANSPORT")),
                        help="stdio for Claude Desktop, Claude Code and Codex; "
                             "http (Streamable HTTP at /mcp) for ChatGPT and "
                             "other remote clients")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address for --transport http")
    parser.add_argument("--http-port", type=int, default=8000,
                        help="listen port for --transport http "
                             "(not the Archicad port; see --port)")
    parser.add_argument("--enable-scripts", action="store_true",
                        default=resolve_flag(os.environ.get("ARCHICAD_MCP_SCRIPTS")),
                        help="register run_script and apply_changeset (full mode "
                             "only); they run arbitrary Python on this machine")
    parser.add_argument("--allow-scripts-over-http", action="store_true",
                        help="permit --enable-scripts together with --transport http")
    args, _ = parser.parse_known_args()
    refusal = check_script_transport(args.enable_scripts, args.mode, args.transport,
                                     args.allow_scripts_over_http)
    if refusal is not None:
        parser.error(refusal)
    rules_dir = resolve_rules_dir(args.rules_dir)
    gdl_workspace = resolve_gdl_workspace(args.gdl_workspace)
    server = build_server(mode=args.mode, rules_dir=rules_dir, port=args.port,
                          gdl_workspace=gdl_workspace, enable_scripts=args.enable_scripts)
    start_startup_banner(args.mode, server.archicad_rule_count,
                         server.archicad_rule_errors, gdl_workspace,
                         server.archicad_rule_source, scripts_enabled=args.enable_scripts)
    if args.transport == "http":
        server.run(transport="http", host=args.host, port=args.http_port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
