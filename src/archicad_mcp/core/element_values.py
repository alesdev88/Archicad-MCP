"""What a GDL, marker or IFC value must look like, and what reaches Tapir.

Values keep the units Tapir reads (metres, radians, attribute indexes), so a
value read with get_element_data can be written back as it is. Tapir's write
path is not symmetric for one case: SetGDLParametersOfElements hands a single
Angle to ACAPI_LibraryPart_ChangeAParameter, which takes degrees, while whole
arrays and marker values go straight into the parameter list, which holds
radians (docs/superpowers/specs/2026-09-29-swept-beam-findings.md). to_tapir()
hides that. From Tapir 1.7.3 (#1405) SetGDLParametersOfElements turns radians
into degrees itself, so every Angle goes as it reads.
"""
from __future__ import annotations

import math

# The first Tapir whose SetGDLParametersOfElements takes Angle values in radians.
RADIANS_TAPIR = (1, 7, 3)

# Attribute-typed GDL parameters hold an attribute index; a write may name the
# attribute instead. The values are Tapir GetAttributesByType's type names.
ATTRIBUTE_TYPES = {"LineType": "Line", "Material": "Surface", "FillPattern": "Fill",
                   "BuildingMaterial": "BuildingMaterial", "Profile": "Profile"}
_INTEGER_TYPES = frozenset({"Integer", "PenColor", *ATTRIBUTE_TYPES})
_NUMBER_TYPES = frozenset({"Length", "RealNumber", "Angle", "Intensity", "RGBColor"})
_STRING_TYPES = frozenset({"String", "Title"})

# Tapir copies a GDL string into a 512-character buffer and a marker string
# into the parameter's own (API_UAddParStrLen, 256 with the terminator), and
# cuts the rest silently.
GDL_STRING_LIMIT = 511
MARKER_STRING_LIMIT = 255

TOLERANCE = 1e-6


def is_array(entry: dict) -> bool:
    return "dimension1" in entry


def read_value(entry: dict):
    """The value as Tapir reads it, a two-dimensional array as rows.

    Tapir reads arrays flat with dimension1 and dimension2 and takes a list of
    rows back; a one-dimensional write is stored with dimension2 1, so that
    reads as a flat list.
    """
    value = entry.get("value")
    columns = entry.get("dimension2") or 1
    if is_array(entry) and isinstance(value, list) and columns > 1:
        return [value[i:i + columns] for i in range(0, len(value), columns)]
    return value


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fit_one(type_: str, value, string_limit: int, resolve_attribute):
    """(value to send, None) or (value, reason) for one non-array item."""
    if type_ in ATTRIBUTE_TYPES and isinstance(value, str) and resolve_attribute:
        try:
            return resolve_attribute(ATTRIBUTE_TYPES[type_], value), None
        except ValueError as exc:
            return value, str(exc)
    if type_ in _INTEGER_TYPES:
        if _is_number(value) and float(value).is_integer():
            return int(value), None
        what = (f"an index or a {ATTRIBUTE_TYPES[type_]} name" if type_ in ATTRIBUTE_TYPES
                else "a whole number")
        return value, f"a {type_} parameter takes {what}, got {value!r}"
    if type_ in _NUMBER_TYPES:
        if _is_number(value):
            return float(value), None
        unit = {"Length": " in metres", "Angle": " in radians"}.get(type_, "")
        return value, f"a {type_} parameter takes a number{unit}, got {value!r}"
    if type_ == "Boolean":
        if isinstance(value, bool):
            return value, None
        return value, f"a Boolean parameter takes true or false, got {value!r}"
    if type_ == "LightSwitch":
        if isinstance(value, bool):
            return ("On" if value else "Off"), None
        if value in ("On", "Off"):
            return value, None
        return value, f"a LightSwitch parameter takes 'On' or 'Off', got {value!r}"
    if type_ in _STRING_TYPES:
        if not isinstance(value, str):
            return value, f"a {type_} parameter takes text, got {value!r}"
        if len(value) > string_limit:
            return value, (f"the text is {len(value)} characters; Archicad keeps at most "
                           f"{string_limit} here and would cut the rest")
        return value, None
    return value, f"a {type_} parameter cannot be written through the API"


def fit(entry: dict, value, *, string_limit: int, arrays: bool,
        resolve_attribute) -> tuple[object, str | None]:
    """The value to send in tool units, or the reason it does not fit.

    resolve_attribute(attribute_type, name) -> index raises ValueError(reason).
    An array takes the whole array in its own shape: Tapir 1.7.2 writes it
    item by item and cannot resize it. A one-column array is a flat list
    (rows of one are accepted and flattened, as it reads back flat).
    """
    type_ = entry.get("type", "")
    if not is_array(entry):
        if isinstance(value, list):
            return value, f"'{entry.get('name')}' is not an array; send one value"
        return _fit_one(type_, value, string_limit, resolve_attribute)
    if not arrays:
        return value, "array parameters cannot be written here"
    rows, columns = entry.get("dimension1"), entry.get("dimension2") or 1
    shape = (f"a list of {rows} items" if columns == 1
             else f"{rows} rows of {columns} items ({rows} x {columns})")
    if not isinstance(value, list):
        return value, (f"'{entry.get('name')}' is an array; send the whole array as "
                       f"{shape} (read it with get_element_data first)")
    if columns == 1 and value and all(isinstance(r, list) and len(r) == 1 for r in value):
        value = [r[0] for r in value]
    grid = [value] if columns == 1 else value
    if (columns == 1 and len(value) != rows) or (columns > 1 and not (
            len(value) == rows and all(isinstance(r, list) and len(r) == columns
                                       for r in value))):
        return value, (f"'{entry.get('name')}' takes {shape}; arrays cannot be resized "
                       "through the API")
    out = []
    for row in grid:
        fitted = []
        for item in row:
            sent, reason = _fit_one(type_, item, string_limit, None)
            if reason is not None:
                return value, f"array item {item!r}: {reason}"
            fitted.append(sent)
        out.append(fitted)
    return (out[0] if columns == 1 else out), None


def to_tapir(entry: dict, value, kind: str, radians: bool = False):
    """The value as Tapir must receive it: a single GDL Angle in degrees, unless
    this Tapir takes radians (RADIANS_TAPIR)."""
    if kind == "gdl" and entry.get("type") == "Angle" and not is_array(entry) and not radians:
        return math.degrees(value)
    return value


def _listed(entry: dict) -> list:
    """The parameter's plain value list; ranges and steps are not one. Tapir
    reads a text list as plain strings and a number list as {value} items."""
    out = []
    for p in entry.get("possibleValues") or []:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict) and "value" in p and "flag" not in p:
            out.append(p["value"])
    return out


def warnings(entry: dict, value) -> list[str]:
    """What may stop the value from landing without making the write wrong."""
    out = []
    if entry.get("isLocked"):
        out.append("locked in the object's settings by its Parameter Script; Archicad "
                   "may keep the current value")
    listed = _listed(entry)
    if listed and entry.get("canHaveCustomValue") is False and not is_array(entry) \
            and not any(same(v, value) for v in listed):
        shown = ", ".join(str(v) for v in listed[:5]) + (", ..." if len(listed) > 5 else "")
        out.append(f"not one of the parameter's listed values ({shown}); Archicad may "
                   "replace it")
    return out


def same(expected, read) -> bool:
    """Equal, numbers within TOLERANCE; a bool never equals a number."""
    if isinstance(expected, list) or isinstance(read, list):
        return (isinstance(expected, list) and isinstance(read, list)
                and len(expected) == len(read)
                and all(same(e, r) for e, r in zip(expected, read)))
    if _is_number(expected) and _is_number(read):
        return math.isclose(float(expected), float(read), rel_tol=0, abs_tol=TOLERANCE)
    if isinstance(expected, bool) != isinstance(read, bool):
        return False
    return expected == read


# ---------- IFC ----------

_IFC_INTEGER = frozenset({"IfcInteger", "IfcCountMeasure", "IfcTimeStamp"})


def _ifc_real(ifc_type: str) -> bool:
    return ifc_type == "IfcReal" or (ifc_type.endswith("Measure")
                                     and ifc_type != "IfcDescriptiveMeasure")


def ifc_type_for(value) -> str | None:
    """The IFC type a value is sent as when the change names none."""
    if isinstance(value, bool):
        return "IfcBoolean"
    if isinstance(value, int):
        return "IfcInteger"
    if isinstance(value, float):
        return "IfcReal"
    if isinstance(value, str):
        return "IfcLabel"
    return None


def ifc_fit(value, ifc_type: str) -> str | None:
    """Why Tapir would refuse the value for the type, following its own parse
    (IFCCommands.cpp ParseIFCPropertyValue), or None."""
    if ifc_type in ("IfcBoolean", "IfcLogical"):
        if isinstance(value, bool) or (ifc_type == "IfcLogical" and value == "Unknown"):
            return None
        extra = " or 'Unknown'" if ifc_type == "IfcLogical" else ""
        return f"{ifc_type} takes true or false{extra}, got {value!r}"
    if ifc_type in _IFC_INTEGER:
        if isinstance(value, int) and not isinstance(value, bool):
            return None
        return f"{ifc_type} takes a whole number, got {value!r}"
    if _ifc_real(ifc_type):
        if _is_number(value):
            return None
        return f"{ifc_type} takes a number, got {value!r}"
    if isinstance(value, str):
        return None
    return f"{ifc_type} takes text, got {value!r}"


def ifc_same(value, ifc_type: str, read_text) -> bool:
    """Whether Tapir's text read of an IFC property shows the value sent."""
    if not isinstance(read_text, str):
        return False
    if isinstance(value, bool):
        return read_text == ("True" if value else "False")
    if _is_number(value):
        try:
            return math.isclose(float(read_text), float(value), rel_tol=1e-9,
                                abs_tol=TOLERANCE)
        except ValueError:
            return False
    return read_text == value
