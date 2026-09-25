"""Editing property and classification DEFINITIONS in place.

Definition reads and Tapir Update* commands only. Nothing here calls
GetPropertyValuesOfElements, so none of it can trigger the Archicad 29
property-read crash (docs/known-issues.md).

Every edit keeps GUIDs: the Tapir commands read the definition, patch only the
fields sent and call ACAPI_*_Change*. That is what keeps element values,
classifications and availability links attached through a rename.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from archicad_mcp.connection import ArchicadConnection

EDIT_MARKER = "UpdateClassificationItems"
FAILURE_SAMPLE = 5

NEEDS_BUILD = (
    "Editing definitions needs a Tapir add-on that has UpdateClassificationItems: "
    "the property-classification-editing build, or the upstream Tapir release that "
    "ships it. The add-on installed in this Archicad does not have it, so nothing "
    "was sent. Older Tapir versions accept UpdatePropertyDefinitions but ignore the "
    "new fields, which is why this is checked first.")


def editing_unavailable(conn: ArchicadConnection) -> dict | None:
    """An error result when the running Tapir lacks the editing commands."""
    if conn.tapir_command_available(EDIT_MARKER):
        return None
    return {"error": NEEDS_BUILD}


def group_by_error(failed: list[dict]) -> list[dict]:
    """Refusals grouped by message, largest group first, a few targets each."""
    groups: dict[str, dict] = {}
    for f in failed:
        g = groups.setdefault(f["message"], {"message": f["message"], "count": 0, "sample": []})
        g["count"] += 1
        if len(g["sample"]) < FAILURE_SAMPLE:
            g["sample"].append(f["target"])
    return sorted(groups.values(), key=lambda g: -g["count"])


def split_results(targets: list[str], response: dict) -> tuple[list[str], list[dict]]:
    """(applied targets, failed [{target, message}]) from an executionResults reply."""
    applied, failed = [], []
    for target, r in zip(targets, response.get("executionResults", [])):
        if r.get("success"):
            applied.append(target)
        else:
            failed.append({"target": target,
                           "message": r.get("error", {}).get("message", "refused")})
    return applied, failed


# ---------- classifications ----------

@dataclass
class ClassItem:
    guid: str
    code: str
    name: str
    description: str
    system: str
    parent: str | None
    children: list[str] = field(default_factory=list)


@dataclass
class ClassSystem:
    guid: str
    name: str
    description: str
    source: str
    version: str
    date: str
    roots: list[str] = field(default_factory=list)


class ClassificationIndex:
    """Every classification system and item of the open project, by name and code."""

    def __init__(self) -> None:
        self.systems: dict[str, ClassSystem] = {}
        self.items: dict[str, ClassItem] = {}

    @classmethod
    def load(cls, conn: ArchicadConnection) -> "ClassificationIndex":
        index = cls()
        response = conn.official("API.GetAllClassificationSystems")
        for s in response.get("classificationSystems", []):
            system = ClassSystem(
                guid=s["classificationSystemId"]["guid"], name=s.get("name", ""),
                description=s.get("description", ""), source=s.get("source", ""),
                version=s.get("version", ""), date=s.get("date", ""))
            index.systems[system.name] = system
            tree = conn.official("API.GetAllClassificationsInSystem",
                                 {"classificationSystemId": s["classificationSystemId"]})
            system.roots = index._walk(tree.get("classificationItems", []), system.name, None)
        return index

    def _walk(self, wrappers: list[dict], system: str, parent: str | None) -> list[str]:
        guids = []
        for wrapper in wrappers:
            item = wrapper.get("classificationItem", wrapper)
            guid = item.get("classificationItemId", {}).get("guid")
            if not guid:
                continue
            node = ClassItem(guid, item.get("id", ""), item.get("name", ""),
                             item.get("description", ""), system, parent)
            self.items[guid] = node
            node.children = self._walk(item.get("children", []), system, guid)
            guids.append(guid)
        return guids

    def system_of_guid(self, guid: str) -> ClassSystem | None:
        return next((s for s in self.systems.values() if s.guid == guid), None)

    def label(self, guid: str) -> str:
        item = self.items.get(guid)
        return f"{item.system}/{item.code}" if item else guid

    def descendants(self, guid: str) -> list[str]:
        out: list[str] = []
        for child in self.items[guid].children:
            out.append(child)
            out.extend(self.descendants(child))
        return out

    def siblings(self, guid: str) -> list[str]:
        item = self.items[guid]
        pool = (self.items[item.parent].children if item.parent
                else self.systems[item.system].roots)
        return [g for g in pool if g != guid]

    def _by_code(self, ref: str) -> tuple[str | None, str | None]:
        # Longest system name first: "ELEA 2/40" must not match system "ELEA".
        for name in sorted(self.systems, key=len, reverse=True):
            if ref.startswith(name + "/"):
                code = ref[len(name) + 1:]
                matches = [g for g, i in self.items.items()
                           if i.system == name and i.code == code]
                if len(matches) == 1:
                    return matches[0], None
                if matches:
                    return None, f"'{ref}' matches {len(matches)} items; address it by GUID"
                return None, f"no classification item '{ref}' (code '{code}' in system '{name}')"
        known = ", ".join(sorted(self.systems)) or "none"
        return None, f"no classification item '{ref}': write System/Code; systems here: {known}"

    def resolve(self, address: str) -> tuple[list[str], str | None]:
        """'System/Code' -> [guid]; 'System/Code/*' -> that item and all below it;
        an item GUID -> [guid]."""
        branch = address.endswith("/*")
        ref = address[:-2] if branch else address
        if ref in self.items:
            guid, err = ref, None
        else:
            guid, err = self._by_code(ref)
        if guid is None:
            return [], err
        return ([guid, *self.descendants(guid)] if branch else [guid]), None
