"""Records of learned library parts, for the tests that place parts by `placement`.

create_elements and modify_elements read the same records; the GUID a record carries
must be the part's main GUID as the fake under test reports it (create's library
listing, or an element's libPart.ownUnID for modify).
"""
from __future__ import annotations

from archicad_mcp.core.part_records import PartRecord, PartRecords

# Centred across, the foot end at the insertion point, the head along local +y.
FOOT_AT_INSERTION = {"xMin": [-0.5, 0], "xMax": [0.5, 0], "yMin": [0, 0], "yMax": [1, 0]}

# The door table measured live for the part "Door" (hinge, swing along the wall).
DOOR_COMBOS = {"ff": ["begin", "right"], "tf": ["end", "right"],
               "ft": ["end", "left"], "tt": ["begin", "left"]}

LEAVES = {"left open": {"gs_leaf_1_open": 1, "gs_leaf_2_open": 0}}


def bed_record(**overrides) -> PartRecord:
    """Centred across, the foot end at the insertion point, the head along local +y."""
    fields = dict(name="Bed Double 01", guid="lib-1", kind="object", learned="2026-10-22",
                  footprint=FOOT_AT_INSERTION, head="+y", one_sided=True)
    fields.update(overrides)
    return PartRecord(**fields)


def door_record(**overrides) -> PartRecord:
    fields = dict(name="Door", guid="lib-1", kind="door", learned="2026-10-22",
                  combos=DOOR_COMBOS, leaves=1)
    fields.update(overrides)
    return PartRecord(**fields)


def store(tmp_path, *records: PartRecord) -> PartRecords:
    kept = PartRecords(tmp_path / "library-parts.json")
    for record in records:
        kept.save(record)
    return kept
