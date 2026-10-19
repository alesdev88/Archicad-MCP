"""Per attribute type: the Tapir commands, keys and fields that list_attributes
and edit_attributes use. Pure tables, no Archicad calls.

What the tables encode was read from Tapir 1.7.1's AttributeCommands.cpp on
2026-10-19 (docs/superpowers/specs/2026-10-19-edit-attributes-design.md, "What
the Tapir source says"): an overwrite keeps every field it is not sent, except
the ones the MODIFY_* tables below put back or refuse.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AttrType:
    name: str                   # Tapir's AttributeType string
    create: str                 # Create* command
    array_key: str              # its data array
    get: str                    # the detailed Get* command
    get_out: str                # Get*'s answer key
    teamwork_set: str           # ACAPI_Teamwork_FindLockableObjectSet name
    official_detail: str        # official API command that answers names without Tapir
    page: int = 50              # list_attributes(details=true) default page
    get_in: str = "attributeIds"
    # Fields a modify cannot send: they replace or drop what the caller did not ask for.
    refused_in_modify: dict[str, str] = field(default_factory=dict)
    # Fields a create must carry beyond `name`.
    create_requires: tuple[str, ...] = ()


_PROFILE_GEOMETRY = ("would replace or drop the profile's geometry; create a new "
                     "profile instead")

TYPES: dict[str, AttrType] = {t.name: t for t in (
    AttrType("Layer", "CreateLayers", "layerDataArray", "GetLayers", "layers",
             "LayerSettingsDialog", "API.GetLayerAttributes"),
    AttrType("LayerCombination", "CreateLayerCombinations", "layerCombinationDataArray",
             "GetLayerCombinations", "layerCombinations", "LayerSettingsDialog",
             "API.GetLayerCombinationAttributes", get_in="attributes"),
    AttrType("Line", "CreateLines", "lineDataArray", "GetLines", "lines", "LineTypes",
             "API.GetLineAttributes"),
    AttrType("Fill", "CreateFills", "fillDataArray", "GetFills", "fills", "FillTypes",
             "API.GetFillAttributes",
             refused_in_modify={"subType": "a fill's type cannot change in place (the "
                                           "old type's geometry would stay); create a "
                                           "new fill instead"}),
    AttrType("ZoneCategory", "CreateZoneCategories", "zoneCategoryDataArray",
             "GetZoneCategories", "zoneCategories", "ZoneCategories",
             "API.GetZoneCategoryAttributes"),
    AttrType("PenTable", "CreatePenTables", "penTableDataArray", "GetPenTables",
             "penTables", "PenTables", "API.GetPenTableAttributes", page=2,
             refused_in_modify={"sourceAttributeId": "would replace all 255 pens with the "
                                                     "source's; list the pens to change "
                                                     "instead"}),
    AttrType("Profile", "CreateProfiles", "profileDataArray", "GetProfiles", "profiles",
             "Profiles", "API.GetProfileAttributes", page=10,
             refused_in_modify={"sourceAttributeId": _PROFILE_GEOMETRY,
                                "newSkins": _PROFILE_GEOMETRY,
                                "replaceSkins": _PROFILE_GEOMETRY}),
    AttrType("BuildingMaterial", "CreateBuildingMaterials", "buildingMaterialDataArray",
             "GetBuildingMaterials", "buildingMaterials", "BuildingMaterials",
             "API.GetBuildingMaterialAttributes"),
    AttrType("Composite", "CreateComposites", "compositeDataArray", "GetComposites",
             "composites", "Composites", "API.GetCompositeAttributes",
             create_requires=("useWith", "skins", "separators")),
    AttrType("Surface", "CreateSurfaces", "surfaceDataArray", "GetSurfaces", "surfaces",
             "Surfaces", "API.GetSurfaceAttributes"),
)}

# Order of creates and modifies on commit: an attribute is written before the
# types that refer to it (materials name fills and surfaces, composites name
# materials and lines, surfaces name fills).
WRITE_ORDER = ("Fill", "Line", "Surface", "BuildingMaterial", "Layer", "LayerCombination",
               "ZoneCategory", "PenTable", "Profile", "Composite")

_ALIASES = {
    **{t.lower(): t for t in TYPES},
    **{t.lower() + "s": t for t in TYPES},
    "zonecategories": "ZoneCategory",
    "linetype": "Line", "linetypes": "Line", "filltype": "Fill", "filltypes": "Fill",
}


def lookup_type(name: str) -> AttrType | None:
    key = "".join(ch for ch in str(name).strip().lower() if ch not in " -_")
    canonical = _ALIASES.get(key)
    return TYPES[canonical] if canonical else None


# Fields holding another attribute: field -> (attribute type or None for "the
# same type as the record", "guid" for {"attributeId": {"guid"}} or "index").
REFERENCE_FIELDS: dict[str, tuple[str | None, str]] = {
    "buildingMaterialId": ("BuildingMaterial", "guid"),
    "lineTypeId": ("Line", "guid"),
    "contourLineTypeId": ("Line", "guid"),
    "cutEndLineTypeId": ("Line", "guid"),
    "fillId": ("Fill", "guid"),
    "surfaceId": ("Surface", "guid"),
    "cutFillIndex": ("Fill", "index"),
    "cutSurfaceIndex": ("Surface", "index"),
    "sourceAttributeId": (None, "guid"),
}
# A layer combination's layers[] name their layer with a one-level {"guid"}.
LAYER_LIST = "layers"

# Values Tapir silently replaces with a default when it does not know them
# (Tapir 1.7.1 source): checked here instead. Path segments: a list's items
# are "[]".
ENUMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("Line", "lineType"): ("Solid", "Dashed", "Symbol"),
    ("Fill", "subType"): ("Vector", "Solid", "Empty", "Symbol", "LinearGradient",
                          "RadialGradient", "Image"),
    ("BuildingMaterial", "cutFillOrientation"): ("ProjectOrigin", "ElementOrigin",
                                                 "FitToSkin"),
    ("Composite", "useWith[]"): ("Wall", "Slab", "Roof", "Shell"),
    ("Composite", "skins[].type"): ("Core", "Finish", "Other"),
}

# Inputs no Get* answer carries, so a read-back cannot confirm them.
WRITE_ONLY: dict[str, frozenset[str]] = {
    "Profile": frozenset({"sourceAttributeId", "newSkins", "replaceSkins"}),
    "PenTable": frozenset({"sourceAttributeId"}),
}
WRITE_ONLY_IN_SKIN_OVERRIDES = frozenset({"edgeOverrides"})
# The texture keys GetFills answers (Tapir 1.7.1).
FILL_TEXTURE_READ = frozenset({"name", "rotationAngle", "xSize", "ySize", "mirrorX", "mirrorY"})
