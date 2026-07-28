# Schedule Scheme Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an MCP client read, edit, and validate Archicad Interactive Schedule schemes through the Scheme Settings Import/Export XML, which is the only programmatic seam Archicad exposes for schedules.

**Architecture:** A pure library package `archicad_mcp/schemes/` parses an exported scheme XML into a small typed model, edits only `Header_Items` and `Criteria_Settings`, and reserialises byte-for-byte so untouched sections cannot be corrupted. Three thin MCP tools in `core/schemes.py` wrap it, following the same layering as `rules/` and the existing `core/` modules.

**Tech Stack:** Python 3.12+, `xml.etree.ElementTree` (stdlib, no new dependency), PyYAML (already a dependency), pytest, FastMCP.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-28-schedule-scheme-editing-design.md`
- **Byte-exact round trip is the primary invariant.** Parse then serialise must reproduce the input byte for byte. A no-op edit must not change one byte.
- **Serializer rules, all three required:** declaration is `<?xml version="1.0" encoding="UTF-8" standalone="no" ?>` followed by a newline; self-closing tags are `/>` with no preceding space; file ends with exactly one trailing newline.
- **Preserve what we do not understand.** Only `Header_Items` and `Criteria_Settings` may be mutated. `View_Settings`, `W2D_Settings`, `DimensionSettings`, `FieldCustomDataStore` and every unrecognised element or attribute pass through untouched.
- **No new runtime dependencies.** stdlib plus what `pyproject.toml` already declares.
- **Test fixtures are anonymised.** Never commit the real office schemes from `~/Documents/ArchiCAD/AC templates/AC29/`. This repo is going public.
- **Offline tests only.** Everything except `validate_schedule_scheme` runs with no Archicad. Use `FakeCore` from `tests/conftest.py` for the one that does.
- **Tools register in `full` mode only**, inside `_register_full_mode_tools` in `src/archicad_mcp/server.py`.
- **Writes are dry-run by default**, matching `set_element_data` and the other mutating tools.
- **No em dashes or en dashes** in any code comment, docstring, tool description, commit message, or doc.
- **Run tests with:** `uv run pytest`

---

### Task 1: Byte-exact XML round trip

The foundation. Everything else depends on being able to load and save without drift.

**Files:**
- Create: `src/archicad_mcp/schemes/__init__.py`
- Create: `src/archicad_mcp/schemes/xml_io.py`
- Create: `tests/schemes/__init__.py`
- Create: `tests/fixtures/schemes/sample_scheme.xml`
- Test: `tests/schemes/test_xml_io.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `DECLARATION: str`
  - `load_scheme_tree(path: Path) -> ET.ElementTree`
  - `dumps_scheme_tree(tree: ET.ElementTree) -> str`
  - `save_scheme_tree(tree: ET.ElementTree, path: Path) -> None`

- [ ] **Step 1: Create the anonymised fixture**

Create `tests/fixtures/schemes/sample_scheme.xml`. This reproduces the structural shape of a real 29.0.0 scheme: tab indentation, self-closing tags, an empty self-closing element (`Parameter_Desc_Name`), text-content elements, an attribute-carrying non-value element (`Variant Type=`), both criterion `Param_Type` values, and all three column binding kinds. Captions and GUIDs are invented.

```xml
<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Scheme_Settings ID="9001" Last_Modified="1759741159" Name="Sample Door Scheme" Scheme_Type="Element_List" Version="29.0.0">
	<View_Settings>
		<Hide_Main_Header value="true"/>
		<Separator value=", "/>
	</View_Settings>
	<Criteria_Settings>
		<Complex_Criteria>
			<Criterion>
				<Param_Type value="88"/>
				<Relation_Index value="1"/>
				<ACPropertyGuid value="00000000-0000-0000-0000-000000000000"/>
				<UniValue>
					<HasVariant>true</HasVariant>
					<Variant Type="GuidVariant">
						<Value>D8F07689-9CFA-4FBE-AEB4-0A60B8E667EE</Value>
					</Variant>
				</UniValue>
				<AndNext value="1"/>
				<ExtendedElem_ElemClassId value="D8F07689-9CFA-4FBE-AEB4-0A60B8E667EE"/>
			</Criterion>
			<Criterion>
				<Param_Type value="232"/>
				<Relation_Index value="12"/>
				<ACPropertyGuid value="432FA53A-B71E-404B-A9D5-F1964237A3EB"/>
				<UniValue>
					<HasVariant>true</HasVariant>
				</UniValue>
				<AndNext value="0"/>
				<ExtendedElem_ElemClassId value="00000000-0000-0000-0000-000000000000"/>
			</Criterion>
		</Complex_Criteria>
	</Criteria_Settings>
	<Header_Items>
		<Header_Item>
			<Numbers_of_Columns value="3"/>
			<Index_of_Columns value="-1"/>
			<ID_of_Item value="1000"/>
			<ID_of_Parent value="0"/>
			<ID_of_firstChild value="1001"/>
			<ID_of_previous value="0"/>
			<ID_of_next value="0"/>
			<Caption>Sample Door Scheme</Caption>
			<Width_of_cell_portrait value="30"/>
			<Parameter_Type value="0"/>
			<Parameter_Index value="0"/>
			<Parameter_Name value=""/>
			<Parameter_Desc_Name/>
			<ACPropertyGuid value="00000000-0000-0000-0000-000000000000"/>
			<ACPropertyName value=""/>
			<UniqueID value="AAAAAAAA-0000-0000-0000-000000000000"/>
		</Header_Item>
		<Header_Item>
			<Numbers_of_Columns value="0"/>
			<Index_of_Columns value="0"/>
			<ID_of_Item value="1001"/>
			<ID_of_Parent value="1000"/>
			<ID_of_firstChild value="0"/>
			<ID_of_previous value="0"/>
			<ID_of_next value="1002"/>
			<Caption>Door ID</Caption>
			<Width_of_cell_portrait value="30"/>
			<Parameter_Type value="0"/>
			<Parameter_Index value="0"/>
			<Parameter_Name value=""/>
			<Parameter_Desc_Name/>
			<ACPropertyGuid value="69A58F6F-1111-4000-8000-000000000001"/>
			<ACPropertyName value="Door ID"/>
			<UniqueID value="BBBBBBBB-0000-0000-0000-000000000001"/>
		</Header_Item>
		<Header_Item>
			<Numbers_of_Columns value="0"/>
			<Index_of_Columns value="1"/>
			<ID_of_Item value="1002"/>
			<ID_of_Parent value="1000"/>
			<ID_of_firstChild value="0"/>
			<ID_of_previous value="1001"/>
			<ID_of_next value="1003"/>
			<Caption>Quantity</Caption>
			<Width_of_cell_portrait value="30"/>
			<Parameter_Type value="1"/>
			<Parameter_Index value="-1003"/>
			<Parameter_Name value=""/>
			<Parameter_Desc_Name/>
			<ACPropertyGuid value="00000000-0000-0000-0000-000000000000"/>
			<ACPropertyName value=""/>
			<UniqueID value="BBBBBBBB-0000-0000-0000-000000000002"/>
		</Header_Item>
		<Header_Item>
			<Numbers_of_Columns value="0"/>
			<Index_of_Columns value="2"/>
			<ID_of_Item value="1003"/>
			<ID_of_Parent value="1000"/>
			<ID_of_firstChild value="0"/>
			<ID_of_previous value="1002"/>
			<ID_of_next value="0"/>
			<Caption>Fire Resistance</Caption>
			<Width_of_cell_portrait value="30"/>
			<Parameter_Type value="180"/>
			<Parameter_Index value="-1604"/>
			<Parameter_Name value=""/>
			<Parameter_Desc_Name>Fire Resistance</Parameter_Desc_Name>
			<ACPropertyGuid value="00000000-0000-0000-0000-000000000000"/>
			<ACPropertyName value="Fire Rating Param"/>
			<UniqueID value="BBBBBBBB-0000-0000-0000-000000000003"/>
		</Header_Item>
	</Header_Items>
	<DimensionSettings>
		<DimensionSetting value="0"/>
	</DimensionSettings>
	<FieldCustomDataStore>
		<NumberOf value="0"/>
	</FieldCustomDataStore>
</Scheme_Settings>
```

Also create empty `src/archicad_mcp/schemes/__init__.py` and `tests/schemes/__init__.py`.

- [ ] **Step 2: Write the failing test**

Create `tests/schemes/test_xml_io.py`:

```python
from pathlib import Path

from archicad_mcp.schemes.xml_io import (
    dumps_scheme_tree,
    load_scheme_tree,
    save_scheme_tree,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def test_round_trip_is_byte_identical():
    original = FIXTURE.read_text(encoding="utf-8")
    tree = load_scheme_tree(FIXTURE)
    assert dumps_scheme_tree(tree) == original


def test_round_trip_keeps_the_declaration_verbatim():
    tree = load_scheme_tree(FIXTURE)
    first_line = dumps_scheme_tree(tree).splitlines()[0]
    assert first_line == '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>'


def test_self_closing_tags_have_no_leading_space():
    tree = load_scheme_tree(FIXTURE)
    assert " />" not in dumps_scheme_tree(tree)


def test_output_ends_with_exactly_one_newline():
    tree = load_scheme_tree(FIXTURE)
    text = dumps_scheme_tree(tree)
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_save_writes_utf8_bytes(tmp_path):
    tree = load_scheme_tree(FIXTURE)
    out = tmp_path / "out.xml"
    save_scheme_tree(tree, out)
    assert out.read_bytes() == FIXTURE.read_bytes()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/schemes/test_xml_io.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'archicad_mcp.schemes.xml_io'`

- [ ] **Step 4: Write minimal implementation**

Create `src/archicad_mcp/schemes/xml_io.py`:

```python
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

# Archicad writes this exact declaration. ElementTree's own declaration uses
# single quotes and drops standalone, so we emit ours and suppress its.
DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'

# ElementTree emits "<Foo />", Archicad writes "<Foo/>". Purely cosmetic to a
# parser, but we round-trip byte for byte so that a no-op edit provably changes
# nothing, which is what makes it safe to leave unmodelled sections alone.
_SELF_CLOSING = re.compile(r" />")


def load_scheme_tree(path: Path) -> ET.ElementTree:
    return ET.parse(path)


def dumps_scheme_tree(tree: ET.ElementTree) -> str:
    body = ET.tostring(tree.getroot(), encoding="unicode")
    return DECLARATION + _SELF_CLOSING.sub("/>", body) + "\n"


def save_scheme_tree(tree: ET.ElementTree, path: Path) -> None:
    path.write_text(dumps_scheme_tree(tree), encoding="utf-8")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/schemes/test_xml_io.py -v`
Expected: 5 passed

- [ ] **Step 6: Verify against a real scheme locally (not committed)**

Run this one-off check against a real export to confirm the round trip holds on a full-size file. It reads only, writes nothing:

```bash
uv run python -c "
from pathlib import Path
from archicad_mcp.schemes.xml_io import load_scheme_tree, dumps_scheme_tree
p = Path.home() / 'Documents/ArchiCAD/AC templates/AC29/2001 Shema Vrat _ Door Scheme.xml'
print('byte-identical:', dumps_scheme_tree(load_scheme_tree(p)) == p.read_text(encoding='utf-8'))
"
```

Expected: `byte-identical: True`. If False, stop and report; the serializer rules are wrong and every later task inherits the fault.

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/schemes tests/schemes tests/fixtures/schemes
git commit -m "feat(schemes): byte-exact scheme XML round trip"
```

---

### Task 2: Scheme model and column tree traversal

Turn the XML into a typed model. The column order comes from the linked list, not from document order.

**Files:**
- Create: `src/archicad_mcp/schemes/model.py`
- Test: `tests/schemes/test_model.py`

**Interfaces:**
- Consumes: `load_scheme_tree` from Task 1
- Produces:
  - `NULL_GUID: str`
  - `KIND_PROPERTY`, `KIND_GDL_PARAM`, `KIND_BUILTIN`: `str`
  - `Binding(kind, property_guid, property_name, param_type, param_index, desc_name)` frozen dataclass
  - `Column(item_id, caption, binding, unique_id, element)` dataclass
  - `Criterion(param_type, relation_index, property_guid, element_class_id, and_next, element)` dataclass
  - `Scheme(tree, root, scheme_id, name, scheme_type, version, root_item, columns, criteria, header_items_el)` dataclass
  - `parse_scheme(tree: ET.ElementTree) -> Scheme`
  - `binding_of(item_el: ET.Element) -> Binding`
  - `field_value(el: ET.Element, tag: str) -> str` reads a child's `value` attribute or its text
  - `set_field(el: ET.Element, tag: str, value: str) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/schemes/test_model.py`:

```python
from pathlib import Path

from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    parse_scheme,
)
from archicad_mcp.schemes.xml_io import load_scheme_tree

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def load():
    return parse_scheme(load_scheme_tree(FIXTURE))


def test_reads_scheme_header():
    s = load()
    assert s.scheme_id == "9001"
    assert s.name == "Sample Door Scheme"
    assert s.scheme_type == "Element_List"
    assert s.version == "29.0.0"


def test_columns_exclude_the_root_item():
    s = load()
    assert [c.caption for c in s.columns] == ["Door ID", "Quantity", "Fire Resistance"]
    assert s.root_item.caption == "Sample Door Scheme"


def test_column_order_follows_the_linked_list_not_document_order():
    # Reverse the XML children; the linked list still dictates the order.
    tree = load_scheme_tree(FIXTURE)
    items = tree.getroot().find("Header_Items")
    children = list(items)
    for c in children:
        items.remove(c)
    for c in reversed(children):
        items.append(c)
    s = parse_scheme(tree)
    assert [c.caption for c in s.columns] == ["Door ID", "Quantity", "Fire Resistance"]


def test_recognises_all_three_binding_kinds():
    s = load()
    by_caption = {c.caption: c.binding for c in s.columns}
    assert by_caption["Door ID"].kind == KIND_PROPERTY
    assert by_caption["Door ID"].property_guid == "69A58F6F-1111-4000-8000-000000000001"
    assert by_caption["Quantity"].kind == KIND_BUILTIN
    assert by_caption["Quantity"].param_type == 1
    assert by_caption["Quantity"].param_index == -1003
    assert by_caption["Fire Resistance"].kind == KIND_GDL_PARAM
    assert by_caption["Fire Resistance"].property_name == "Fire Rating Param"


def test_reads_criteria():
    s = load()
    assert len(s.criteria) == 2
    assert s.criteria[0].param_type == 88
    assert s.criteria[0].element_class_id == "D8F07689-9CFA-4FBE-AEB4-0A60B8E667EE"
    assert s.criteria[1].param_type == 232
    assert s.criteria[1].property_guid == "432FA53A-B71E-404B-A9D5-F1964237A3EB"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/schemes/test_model.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'archicad_mcp.schemes.model'`

- [ ] **Step 3: Write minimal implementation**

Create `src/archicad_mcp/schemes/model.py`:

```python
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NULL_GUID = "00000000-0000-0000-0000-000000000000"

# How a column gets its data. Verified against real 29.0.0 schemes: an Archicad
# property carries a real ACPropertyGuid; a GDL library parameter carries
# Parameter_Type 180 with the parameter's name in ACPropertyName; everything
# else is a built-in field addressed by Parameter_Type plus Parameter_Index
# (Quantity is type 1, index -1003).
KIND_PROPERTY = "property"
KIND_GDL_PARAM = "gdl_param"
KIND_BUILTIN = "builtin"

GDL_PARAM_TYPE = 180


def field_value(el: ET.Element, tag: str) -> str:
    """A child's payload. Most carry it in a 'value' attribute, some (Caption,
    Parameter_Desc_Name) carry it as text."""
    child = el.find(tag)
    if child is None:
        return ""
    if "value" in child.attrib:
        return child.attrib["value"]
    return (child.text or "").strip()


def set_field(el: ET.Element, tag: str, value: str) -> None:
    child = el.find(tag)
    if child is None:
        child = ET.SubElement(el, tag)
        child.set("value", value)
        return
    if "value" in child.attrib:
        child.set("value", value)
    else:
        child.text = value


def _int_field(el: ET.Element, tag: str) -> int:
    try:
        return int(field_value(el, tag))
    except ValueError:
        return 0


@dataclass(frozen=True)
class Binding:
    kind: str
    property_guid: str = NULL_GUID
    property_name: str = ""
    param_type: int = 0
    param_index: int = 0
    desc_name: str = ""


@dataclass
class Column:
    item_id: str
    caption: str
    binding: Binding
    unique_id: str
    element: ET.Element = field(repr=False)


@dataclass
class Criterion:
    param_type: int
    relation_index: int
    property_guid: str
    element_class_id: str
    and_next: int
    element: ET.Element = field(repr=False)


@dataclass
class Scheme:
    tree: ET.ElementTree
    root: ET.Element = field(repr=False)
    scheme_id: str = ""
    name: str = ""
    scheme_type: str = ""
    version: str = ""
    root_item: Column | None = None
    columns: list[Column] = field(default_factory=list)
    criteria: list[Criterion] = field(default_factory=list)
    header_items_el: ET.Element | None = field(default=None, repr=False)


def binding_of(item_el: ET.Element) -> Binding:
    guid = field_value(item_el, "ACPropertyGuid")
    param_type = _int_field(item_el, "Parameter_Type")
    param_index = _int_field(item_el, "Parameter_Index")
    name = field_value(item_el, "ACPropertyName")
    desc = field_value(item_el, "Parameter_Desc_Name")
    if guid and guid != NULL_GUID:
        kind = KIND_PROPERTY
    elif param_type == GDL_PARAM_TYPE:
        kind = KIND_GDL_PARAM
    else:
        kind = KIND_BUILTIN
    return Binding(kind=kind, property_guid=guid or NULL_GUID, property_name=name,
                   param_type=param_type, param_index=param_index, desc_name=desc)


def _column_of(item_el: ET.Element) -> Column:
    return Column(item_id=field_value(item_el, "ID_of_Item"),
                  caption=field_value(item_el, "Caption"),
                  binding=binding_of(item_el),
                  unique_id=field_value(item_el, "UniqueID"),
                  element=item_el)


def parse_scheme(tree: ET.ElementTree) -> Scheme:
    root = tree.getroot()
    items_el = root.find("Header_Items")
    item_els = list(items_el) if items_el is not None else []
    by_id = {field_value(e, "ID_of_Item"): e for e in item_els}

    root_els = [e for e in item_els if field_value(e, "ID_of_Parent") == "0"]
    root_item = _column_of(root_els[0]) if root_els else None

    # Order comes from the sibling chain, never document order. Guard against a
    # malformed file looping forever.
    columns: list[Column] = []
    seen: set[str] = set()
    current = field_value(root_els[0], "ID_of_firstChild") if root_els else "0"
    while current and current != "0" and current in by_id and current not in seen:
        seen.add(current)
        el = by_id[current]
        columns.append(_column_of(el))
        current = field_value(el, "ID_of_next")

    criteria = []
    for c in root.findall("Criteria_Settings/Complex_Criteria/Criterion"):
        criteria.append(Criterion(param_type=_int_field(c, "Param_Type"),
                                  relation_index=_int_field(c, "Relation_Index"),
                                  property_guid=field_value(c, "ACPropertyGuid"),
                                  element_class_id=field_value(c, "ExtendedElem_ElemClassId"),
                                  and_next=_int_field(c, "AndNext"),
                                  element=c))

    return Scheme(tree=tree, root=root, scheme_id=root.get("ID", ""),
                  name=root.get("Name", ""), scheme_type=root.get("Scheme_Type", ""),
                  version=root.get("Version", ""), root_item=root_item,
                  columns=columns, criteria=criteria, header_items_el=items_el)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/schemes/test_model.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/schemes/model.py tests/schemes/test_model.py
git commit -m "feat(schemes): typed scheme model with linked-list column order"
```

---

### Task 3: The read_schedule_scheme tool

**Files:**
- Create: `src/archicad_mcp/core/schemes.py`
- Modify: `src/archicad_mcp/server.py` (inside `_register_full_mode_tools`)
- Test: `tests/schemes/test_core_read.py`
- Test: `tests/test_server_smoke.py` (add the new tool name to whatever list it asserts on)

**Interfaces:**
- Consumes: `parse_scheme`, `load_scheme_tree`, `Binding` kind constants
- Produces: `read_schedule_scheme(path: str) -> dict` with keys `name`, `scheme_id`, `scheme_type`, `version`, `column_count`, `columns` (list of `{index, caption, binds_to, detail}`), `criteria` (list of `{param_type, relation_index, target, and_next}`)

- [ ] **Step 1: Write the failing test**

Create `tests/schemes/test_core_read.py`:

```python
from pathlib import Path

from archicad_mcp.core.schemes import read_schedule_scheme

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def test_reports_scheme_header():
    out = read_schedule_scheme(str(FIXTURE))
    assert out["name"] == "Sample Door Scheme"
    assert out["scheme_id"] == "9001"
    assert out["column_count"] == 3


def test_describes_each_column_binding_in_words():
    out = read_schedule_scheme(str(FIXTURE))
    rows = {c["caption"]: c for c in out["columns"]}
    assert rows["Door ID"]["binds_to"] == "property"
    assert rows["Quantity"]["binds_to"] == "builtin"
    assert rows["Fire Resistance"]["binds_to"] == "gdl_param"
    assert rows["Fire Resistance"]["detail"] == "Fire Rating Param"
    assert rows["Door ID"]["index"] == 0


def test_reports_criteria():
    out = read_schedule_scheme(str(FIXTURE))
    assert len(out["criteria"]) == 2
    assert out["criteria"][0]["target"] == "D8F07689-9CFA-4FBE-AEB4-0A60B8E667EE"


def test_missing_file_returns_an_error_envelope():
    out = read_schedule_scheme("/nonexistent/nope.xml")
    assert "error" in out
    assert "not found" in out["error"].lower()


def test_non_scheme_xml_returns_an_error_envelope(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text('<?xml version="1.0"?>\n<BuildingInformation/>\n', encoding="utf-8")
    out = read_schedule_scheme(str(bad))
    assert "error" in out
    assert "Scheme_Settings" in out["error"]


def test_scheme_without_a_root_header_item_is_rejected(tmp_path):
    bad = tmp_path / "rootless.xml"
    bad.write_text('<?xml version="1.0"?>\n<Scheme_Settings ID="1" Name="X">'
                   "<Header_Items/></Scheme_Settings>\n", encoding="utf-8")
    out = read_schedule_scheme(str(bad))
    assert "error" in out
    assert "root Header_Item" in out["error"]


def test_malformed_xml_returns_an_error_envelope(tmp_path):
    bad = tmp_path / "broken.xml"
    bad.write_text("<Scheme_Settings><unclosed>\n", encoding="utf-8")
    out = read_schedule_scheme(str(bad))
    assert "error" in out
    assert "not valid XML" in out["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/schemes/test_core_read.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'archicad_mcp.core.schemes'`

- [ ] **Step 3: Write minimal implementation**

Create `src/archicad_mcp/core/schemes.py`:

```python
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    NULL_GUID,
    Scheme,
    parse_scheme,
)
from archicad_mcp.schemes.xml_io import load_scheme_tree


def _load(path: str) -> Scheme | dict:
    """Returns a Scheme, or an {"error": ...} envelope the tool can return as-is."""
    p = Path(path).expanduser()
    if not p.is_file():
        return {"error": f"Scheme file not found: {p}. Export one from Archicad via "
                         "Document > Schedules > Scheme Settings > Export."}
    try:
        tree = load_scheme_tree(p)
    except ET.ParseError as exc:
        return {"error": f"{p} is not valid XML: {exc}"}
    if tree.getroot().tag != "Scheme_Settings":
        return {"error": f"{p} is not a schedule scheme. Expected a Scheme_Settings "
                         f"root, got {tree.getroot().tag}."}
    scheme = parse_scheme(tree)
    # Every later operation reads through root_item (relink rewrites the chain
    # from it), so a scheme without one is rejected here rather than crashing
    # somewhere less obvious.
    if scheme.root_item is None:
        return {"error": f"{p} has no root Header_Item, so its column tree has no "
                         "anchor. The export is incomplete or corrupt."}
    return scheme


def _binding_detail(binding) -> str:
    if binding.kind == KIND_PROPERTY:
        return binding.property_guid
    if binding.kind == KIND_GDL_PARAM:
        return binding.property_name
    return f"type {binding.param_type}, index {binding.param_index}"


def _criterion_target(criterion) -> str:
    if criterion.element_class_id and criterion.element_class_id != NULL_GUID:
        return criterion.element_class_id
    return criterion.property_guid


def read_schedule_scheme(path: str) -> dict:
    scheme = _load(path)
    if isinstance(scheme, dict):
        return scheme
    return {
        "name": scheme.name,
        "scheme_id": scheme.scheme_id,
        "scheme_type": scheme.scheme_type,
        "version": scheme.version,
        "column_count": len(scheme.columns),
        "columns": [{"index": i, "caption": c.caption, "binds_to": c.binding.kind,
                     "detail": _binding_detail(c.binding)}
                    for i, c in enumerate(scheme.columns)],
        "criteria": [{"param_type": c.param_type, "relation_index": c.relation_index,
                      "target": _criterion_target(c), "and_next": c.and_next}
                     for c in scheme.criteria],
    }
```

- [ ] **Step 4: Register the tool**

In `src/archicad_mcp/server.py`, add to the imports near the other core imports:

```python
from archicad_mcp.core import schemes as core_schemes
```

Then inside `_register_full_mode_tools`, after the `publish` tool registration and before the gateway tools, add:

```python
    @mcp.tool(description="Describe an exported Archicad schedule scheme XML: its "
                          "criteria and its ordered columns, with what each column "
                          "binds to. Schedules have no API, so export the scheme "
                          "first via Document > Schedules > Scheme Settings > Export "
                          "and pass the file path. Reads the file only, never "
                          "Archicad.")
    def read_schedule_scheme(path: str) -> dict:
        return core_schemes.read_schedule_scheme(path)
```

Note there is no `@_guarded`: this tool never touches Archicad, and `_load` already returns error envelopes.

- [ ] **Step 5: Assert the tool is registered**

`tests/test_server_smoke.py` currently only asserts `"list_rules" in names`. Add a second test to it:

```python
async def test_full_mode_registers_the_schedule_scheme_tools():
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "read_schedule_scheme" in names


async def test_verdicts_mode_omits_the_schedule_scheme_tools():
    mcp = build_server(mode="verdicts")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "read_schedule_scheme" not in names
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/schemes/ tests/test_server_smoke.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/core/schemes.py src/archicad_mcp/server.py tests/
git commit -m "feat(schemes): read_schedule_scheme tool"
```

---

### Task 4: Column operations

The linked-list work. Rather than splicing pointers incrementally, mutations reorder `scheme.columns` and a single `relink` pass rewrites every link field from that order. Far fewer ways to get it wrong, and directly testable.

**Files:**
- Create: `src/archicad_mcp/schemes/columns.py`
- Test: `tests/schemes/test_columns.py`

**Interfaces:**
- Consumes: `Scheme`, `Column`, `Binding`, `field_value`, `set_field`, kind constants, `NULL_GUID`, `GDL_PARAM_TYPE`
- Produces:
  - `relink(scheme: Scheme) -> None`
  - `add_column(scheme, caption, binding, index=None, template_caption=None) -> Column`
  - `remove_column(scheme, caption) -> None`
  - `move_column(scheme, caption, to_index) -> None`
  - `rename_column(scheme, caption, new_caption) -> None`
  - `retarget_column(scheme, caption, binding) -> None`
  - `ColumnNotFound(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/schemes/test_columns.py`:

```python
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from archicad_mcp.schemes.columns import (
    ColumnNotFound,
    add_column,
    move_column,
    relink,
    remove_column,
    rename_column,
    retarget_column,
)
from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_PROPERTY,
    Binding,
    field_value,
    parse_scheme,
)
from archicad_mcp.schemes.xml_io import dumps_scheme_tree, load_scheme_tree

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def load():
    return parse_scheme(load_scheme_tree(FIXTURE))


def reparse(scheme):
    """Serialise and parse again, so assertions test what a file would contain
    rather than the in-memory objects we just mutated."""
    return parse_scheme(ET.ElementTree(ET.fromstring(dumps_scheme_tree(scheme.tree))))


def assert_chain_is_intact(scheme):
    cols = scheme.columns
    root_id = scheme.root_item.item_id
    assert field_value(scheme.root_item.element, "Numbers_of_Columns") == str(len(cols))
    expected_first = cols[0].item_id if cols else "0"
    assert field_value(scheme.root_item.element, "ID_of_firstChild") == expected_first
    for i, c in enumerate(cols):
        prev = cols[i - 1].item_id if i > 0 else "0"
        nxt = cols[i + 1].item_id if i < len(cols) - 1 else "0"
        assert field_value(c.element, "ID_of_previous") == prev
        assert field_value(c.element, "ID_of_next") == nxt
        assert field_value(c.element, "ID_of_Parent") == root_id
        assert field_value(c.element, "Index_of_Columns") == str(i)
    ids = [c.item_id for c in cols]
    uniques = [field_value(c.element, "UniqueID") for c in cols]
    assert len(set(ids)) == len(ids)
    assert len(set(uniques)) == len(uniques)


def test_relink_alone_changes_nothing():
    original = FIXTURE.read_text(encoding="utf-8")
    scheme = load()
    relink(scheme)
    assert dumps_scheme_tree(scheme.tree) == original


def test_remove_column():
    scheme = load()
    remove_column(scheme, "Quantity")
    assert [c.caption for c in scheme.columns] == ["Door ID", "Fire Resistance"]
    assert_chain_is_intact(scheme)
    assert [c.caption for c in reparse(scheme).columns] == ["Door ID", "Fire Resistance"]


def test_remove_unknown_column_raises():
    with pytest.raises(ColumnNotFound):
        remove_column(load(), "Nope")


def test_add_column_appends_by_default():
    scheme = load()
    add_column(scheme, "Notes", Binding(kind=KIND_PROPERTY,
                                        property_guid="11111111-2222-3333-4444-555555555555"))
    assert [c.caption for c in scheme.columns][-1] == "Notes"
    assert_chain_is_intact(scheme)
    round_tripped = reparse(scheme)
    assert round_tripped.columns[-1].caption == "Notes"
    assert round_tripped.columns[-1].binding.kind == KIND_PROPERTY


def test_add_column_at_index():
    scheme = load()
    add_column(scheme, "Notes", Binding(kind=KIND_BUILTIN, param_type=1, param_index=-1003),
               index=0)
    assert [c.caption for c in scheme.columns][0] == "Notes"
    assert_chain_is_intact(scheme)


def test_added_column_gets_fresh_ids():
    scheme = load()
    existing = {c.item_id for c in scheme.columns} | {scheme.root_item.item_id}
    new = add_column(scheme, "Notes", Binding(kind=KIND_BUILTIN))
    assert new.item_id not in existing
    assert_chain_is_intact(scheme)


def test_move_column():
    scheme = load()
    move_column(scheme, "Fire Resistance", 0)
    assert [c.caption for c in scheme.columns] == ["Fire Resistance", "Door ID", "Quantity"]
    assert_chain_is_intact(scheme)


def test_rename_column():
    scheme = load()
    rename_column(scheme, "Quantity", "Count")
    assert [c.caption for c in scheme.columns] == ["Door ID", "Count", "Fire Resistance"]
    assert reparse(scheme).columns[1].caption == "Count"


def test_retarget_column():
    scheme = load()
    retarget_column(scheme, "Fire Resistance",
                    Binding(kind=KIND_PROPERTY,
                            property_guid="99999999-8888-7777-6666-555555555555"))
    col = reparse(scheme).columns[2]
    assert col.binding.kind == KIND_PROPERTY
    assert col.binding.property_guid == "99999999-8888-7777-6666-555555555555"
    # The old GDL parameter fields must be cleared, not left behind.
    assert col.binding.param_type == 0


def test_removing_every_column_leaves_a_valid_scheme():
    scheme = load()
    for caption in ["Door ID", "Quantity", "Fire Resistance"]:
        remove_column(scheme, caption)
    assert scheme.columns == []
    assert_chain_is_intact(scheme)
    assert field_value(scheme.root_item.element, "ID_of_firstChild") == "0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/schemes/test_columns.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'archicad_mcp.schemes.columns'`

- [ ] **Step 3: Write minimal implementation**

Create `src/archicad_mcp/schemes/columns.py`:

```python
from __future__ import annotations

import copy
import uuid
import xml.etree.ElementTree as ET

from archicad_mcp.schemes.model import (
    GDL_PARAM_TYPE,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    NULL_GUID,
    Binding,
    Column,
    Scheme,
    binding_of,
    field_value,
    set_field,
)


class ColumnNotFound(Exception):
    pass


def _find(scheme: Scheme, caption: str) -> Column:
    for c in scheme.columns:
        if c.caption == caption:
            return c
    known = ", ".join(c.caption for c in scheme.columns) or "none"
    raise ColumnNotFound(f"No column captioned {caption!r}. Columns: {known}.")


def _next_item_id(scheme: Scheme) -> str:
    used = [scheme.root_item.item_id] + [c.item_id for c in scheme.columns]
    highest = 0
    for value in used:
        try:
            highest = max(highest, int(value))
        except ValueError:
            continue
    return str(highest + 1)


def relink(scheme: Scheme) -> None:
    """Rewrite every link field from the order of scheme.columns.

    Rebuilding the whole chain from one ordered list is far harder to get wrong
    than splicing prev/next pointers per operation, and it means every mutation
    shares one tested code path. Fields we do not model are untouched.
    """
    items_el = scheme.header_items_el
    root_el = scheme.root_item.element

    for el in list(items_el):
        items_el.remove(el)
    items_el.append(root_el)
    for col in scheme.columns:
        items_el.append(col.element)

    set_field(root_el, "Numbers_of_Columns", str(len(scheme.columns)))
    set_field(root_el, "ID_of_firstChild",
              scheme.columns[0].item_id if scheme.columns else "0")

    for i, col in enumerate(scheme.columns):
        set_field(col.element, "ID_of_Parent", scheme.root_item.item_id)
        set_field(col.element, "Index_of_Columns", str(i))
        set_field(col.element, "ID_of_previous",
                  scheme.columns[i - 1].item_id if i > 0 else "0")
        set_field(col.element, "ID_of_next",
                  scheme.columns[i + 1].item_id if i < len(scheme.columns) - 1 else "0")


def _apply_binding(item_el: ET.Element, binding: Binding) -> None:
    """Write a binding, clearing the fields the other binding kinds use so a
    retarget cannot leave a stale GUID or parameter index behind."""
    if binding.kind == KIND_PROPERTY:
        set_field(item_el, "ACPropertyGuid", binding.property_guid)
        set_field(item_el, "ACPropertyName", binding.property_name)
        set_field(item_el, "Parameter_Type", "0")
        set_field(item_el, "Parameter_Index", "0")
        set_field(item_el, "Parameter_Desc_Name", "")
    elif binding.kind == KIND_GDL_PARAM:
        set_field(item_el, "ACPropertyGuid", NULL_GUID)
        set_field(item_el, "ACPropertyName", binding.property_name)
        set_field(item_el, "Parameter_Type", str(binding.param_type or GDL_PARAM_TYPE))
        set_field(item_el, "Parameter_Index", str(binding.param_index or -1604))
        set_field(item_el, "Parameter_Desc_Name", binding.desc_name or binding.property_name)
    else:
        set_field(item_el, "ACPropertyGuid", NULL_GUID)
        set_field(item_el, "ACPropertyName", "")
        set_field(item_el, "Parameter_Type", str(binding.param_type))
        set_field(item_el, "Parameter_Index", str(binding.param_index))
        set_field(item_el, "Parameter_Desc_Name", "")


def add_column(scheme: Scheme, caption: str, binding: Binding,
               index: int | None = None, template_caption: str | None = None) -> Column:
    """Insert a column. Formatting is inherited by deep-copying a template
    column, so widths, fonts, totals and colours match the scheme rather than
    being invented."""
    if template_caption is not None:
        template_el = _find(scheme, template_caption).element
    elif scheme.columns:
        template_el = scheme.columns[0].element
    else:
        template_el = scheme.root_item.element

    el = copy.deepcopy(template_el)
    item_id = _next_item_id(scheme)
    set_field(el, "ID_of_Item", item_id)
    set_field(el, "UniqueID", str(uuid.uuid4()).upper())
    set_field(el, "Caption", caption)
    set_field(el, "Numbers_of_Columns", "0")
    set_field(el, "ID_of_firstChild", "0")
    _apply_binding(el, binding)

    column = Column(item_id=item_id, caption=caption, binding=binding_of(el),
                    unique_id=field_value(el, "UniqueID"), element=el)
    at = len(scheme.columns) if index is None else index
    scheme.columns.insert(at, column)
    relink(scheme)
    return column


def remove_column(scheme: Scheme, caption: str) -> None:
    scheme.columns.remove(_find(scheme, caption))
    relink(scheme)


def move_column(scheme: Scheme, caption: str, to_index: int) -> None:
    column = _find(scheme, caption)
    scheme.columns.remove(column)
    scheme.columns.insert(to_index, column)
    relink(scheme)


def rename_column(scheme: Scheme, caption: str, new_caption: str) -> None:
    column = _find(scheme, caption)
    set_field(column.element, "Caption", new_caption)
    column.caption = new_caption


def retarget_column(scheme: Scheme, caption: str, binding: Binding) -> None:
    column = _find(scheme, caption)
    _apply_binding(column.element, binding)
    column.binding = binding_of(column.element)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/schemes/test_columns.py -v`
Expected: 10 passed

- [ ] **Step 5: Run the whole suite for regressions**

Run: `uv run pytest`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/schemes/columns.py tests/schemes/test_columns.py
git commit -m "feat(schemes): column add, remove, move, rename, retarget"
```

---

### Task 5: YAML spec loader and apply

**Files:**
- Create: `src/archicad_mcp/schemes/spec.py`
- Test: `tests/schemes/test_spec.py`

**Interfaces:**
- Consumes: `Scheme`, `Binding`, kind constants, `add_column`, `remove_column`, `move_column`, `parse_scheme`
- Produces:
  - `ColumnSpec(caption, bind, width)` dataclass
  - `SchemeSpec(spec_id, template, name, criteria, columns)` dataclass
  - `load_specs(path: Path) -> tuple[list[SchemeSpec], list[str]]` returns specs and load errors
  - `binding_from_bind(bind: dict, resolver: Callable[[str], str] | None) -> Binding`
  - `apply_spec(spec: SchemeSpec, scheme: Scheme, resolver=None) -> list[str]` mutates the scheme, returns a change log
  - `SpecError(Exception)`

The `resolver` maps a `"Group/Name"` property string to a GUID. It is `None` for offline use, in which case a `property:` bind must already be a GUID or `apply_spec` raises `SpecError`. Task 7 supplies a live resolver.

- [ ] **Step 1: Write the failing test**

Create `tests/schemes/test_spec.py`:

```python
from pathlib import Path

import pytest

from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    parse_scheme,
)
from archicad_mcp.schemes.spec import SpecError, apply_spec, load_specs
from archicad_mcp.schemes.xml_io import load_scheme_tree

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"

SPEC_YAML = """
- id: door-schedule
  template: door-scheme.xml
  name: "Rebuilt Door Scheme"
  columns:
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Door ID"
      bind: { property: "69A58F6F-1111-4000-8000-000000000001" }
    - caption: "Notes"
      bind: { gdl_param: "Notes Param" }
"""


def load_scheme():
    return parse_scheme(load_scheme_tree(FIXTURE))


def write_spec(tmp_path, text=SPEC_YAML):
    p = tmp_path / "schemes.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_a_spec(tmp_path):
    specs, errors = load_specs(write_spec(tmp_path))
    assert errors == []
    assert len(specs) == 1
    assert specs[0].spec_id == "door-schedule"
    assert specs[0].template == "door-scheme.xml"
    assert [c.caption for c in specs[0].columns] == ["Quantity", "Door ID", "Notes"]


def test_apply_sets_the_column_list_and_order(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    scheme = load_scheme()
    apply_spec(specs[0], scheme)
    assert [c.caption for c in scheme.columns] == ["Quantity", "Door ID", "Notes"]


def test_apply_sets_binding_kinds(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    scheme = load_scheme()
    apply_spec(specs[0], scheme)
    kinds = {c.caption: c.binding.kind for c in scheme.columns}
    assert kinds["Quantity"] == KIND_BUILTIN
    assert kinds["Door ID"] == KIND_PROPERTY
    assert kinds["Notes"] == KIND_GDL_PARAM


def test_apply_renames_the_scheme(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    scheme = load_scheme()
    apply_spec(specs[0], scheme)
    assert scheme.root.get("Name") == "Rebuilt Door Scheme"


def test_apply_returns_a_change_log(tmp_path):
    specs, _ = load_specs(write_spec(tmp_path))
    changes = apply_spec(specs[0], load_scheme())
    assert any("Notes" in c for c in changes)
    assert any("Fire Resistance" in c for c in changes)


def test_named_property_without_a_resolver_is_an_error(tmp_path):
    spec_text = """
- id: s
  template: t.xml
  columns:
    - caption: "Fire"
      bind: { property: "OFFICE/Fire Rating" }
"""
    specs, _ = load_specs(write_spec(tmp_path, spec_text))
    with pytest.raises(SpecError):
        apply_spec(specs[0], load_scheme())


def test_named_property_uses_the_resolver(tmp_path):
    spec_text = """
- id: s
  template: t.xml
  columns:
    - caption: "Fire"
      bind: { property: "OFFICE/Fire Rating" }
"""
    specs, _ = load_specs(write_spec(tmp_path, spec_text))
    scheme = load_scheme()
    apply_spec(specs[0], scheme, resolver=lambda n: "AAAA1111-0000-0000-0000-000000000000")
    assert scheme.columns[0].binding.property_guid == "AAAA1111-0000-0000-0000-000000000000"


def test_malformed_yaml_is_reported_not_raised(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- id: x\n  bind: [unclosed\n", encoding="utf-8")
    specs, errors = load_specs(p)
    assert specs == []
    assert errors and "bad.yaml" in errors[0]


def test_spec_missing_id_is_reported(tmp_path):
    specs, errors = load_specs(write_spec(tmp_path, "- template: t.xml\n  columns: []\n"))
    assert specs == []
    assert errors and "id" in errors[0]


def test_template_is_optional(tmp_path):
    specs, errors = load_specs(write_spec(tmp_path, "- id: s\n  columns: []\n"))
    assert errors == []
    assert specs[0].template is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/schemes/test_spec.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'archicad_mcp.schemes.spec'`

- [ ] **Step 3: Write minimal implementation**

Create `src/archicad_mcp/schemes/spec.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from archicad_mcp.schemes.columns import add_column, remove_column
from archicad_mcp.schemes.model import (
    GDL_PARAM_TYPE,
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    Binding,
    Scheme,
    set_field,
)

# Built-in fields addressable by name in a spec. Verified live: Quantity is
# Parameter_Type 1 with Parameter_Index -1003. Extend as more are confirmed;
# an unknown name is an error rather than a guess.
BUILTIN_FIELDS: dict[str, tuple[int, int]] = {
    "Quantity": (1, -1003),
}

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class SpecError(Exception):
    pass


@dataclass
class ColumnSpec:
    caption: str
    bind: dict
    width: str | None = None


@dataclass
class SchemeSpec:
    spec_id: str
    # Which export this spec was written against. Optional, and never used to
    # locate the file: the tool's own 'path' argument is the template. It exists
    # so applying the window spec to the door export can be caught and warned
    # about instead of silently producing nonsense.
    template: str | None = None
    name: str | None = None
    criteria: list[dict] = field(default_factory=list)
    columns: list[ColumnSpec] = field(default_factory=list)


def binding_from_bind(bind: dict, resolver: Callable[[str], str] | None = None) -> Binding:
    if not isinstance(bind, dict) or len(bind) != 1:
        raise SpecError(f"bind must name exactly one of property, gdl_param, builtin. "
                        f"Got: {bind!r}")
    kind, value = next(iter(bind.items()))
    if kind == "property":
        if _GUID.match(str(value)):
            return Binding(kind=KIND_PROPERTY, property_guid=str(value))
        if resolver is None:
            raise SpecError(
                f"Property {value!r} is a name, not a GUID, and no live model is "
                "available to resolve it. Pass a GUID, or run with Archicad open "
                "so the name can be looked up.")
        return Binding(kind=KIND_PROPERTY, property_guid=resolver(str(value)),
                       property_name=str(value))
    if kind == "gdl_param":
        return Binding(kind=KIND_GDL_PARAM, property_name=str(value),
                       desc_name=str(value), param_type=GDL_PARAM_TYPE,
                       param_index=-1604)
    if kind == "builtin":
        if value not in BUILTIN_FIELDS:
            known = ", ".join(sorted(BUILTIN_FIELDS)) or "none"
            raise SpecError(f"Unknown built-in field {value!r}. Known: {known}.")
        param_type, param_index = BUILTIN_FIELDS[value]
        return Binding(kind=KIND_BUILTIN, param_type=param_type, param_index=param_index)
    raise SpecError(f"Unknown bind kind {kind!r}. Use property, gdl_param, or builtin.")


def load_specs(path: Path) -> tuple[list[SchemeSpec], list[str]]:
    """Returns (specs, errors). A malformed file is reported, never raised, so a
    bad spec cannot take down the tool."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [], [f"{path}: {exc}"]
    if not isinstance(raw, list):
        return [], [f"{path}: expected a list of scheme specs, got {type(raw).__name__}"]

    specs, errors = [], []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            errors.append(f"{path}: entry {i} is not a mapping")
            continue
        if not entry.get("id"):
            errors.append(f"{path}: entry {i} is missing 'id'")
            continue
        columns = []
        for c in entry.get("columns") or []:
            if not isinstance(c, dict) or "caption" not in c or "bind" not in c:
                errors.append(f"{path}: {entry['id']} has a column without "
                              "'caption' and 'bind'")
                columns = None
                break
            columns.append(ColumnSpec(caption=str(c["caption"]), bind=c["bind"],
                                      width=c.get("width")))
        if columns is None:
            continue
        template = entry.get("template")
        specs.append(SchemeSpec(spec_id=str(entry["id"]),
                                template=str(template) if template else None,
                                name=entry.get("name"),
                                criteria=entry.get("criteria") or [], columns=columns))
    return specs, errors


def apply_spec(spec: SchemeSpec, scheme: Scheme,
               resolver: Callable[[str], str] | None = None) -> list[str]:
    """Make the scheme's columns match the spec. Returns a human-readable change
    log. Criteria are preserved as-is; editing them needs the Param_Type table
    that does not exist yet."""
    changes: list[str] = []
    if spec.name is not None and spec.name != scheme.root.get("Name"):
        changes.append(f"renamed scheme to {spec.name!r}")
        scheme.root.set("Name", spec.name)

    wanted = [c.caption for c in spec.columns]
    for existing in [c.caption for c in scheme.columns]:
        if existing not in wanted:
            remove_column(scheme, existing)
            changes.append(f"removed column {existing!r}")

    for target_index, col_spec in enumerate(spec.columns):
        binding = binding_from_bind(col_spec.bind, resolver)
        current = {c.caption: c for c in scheme.columns}
        if col_spec.caption in current:
            column = current[col_spec.caption]
            if column.binding != binding:
                from archicad_mcp.schemes.columns import retarget_column
                retarget_column(scheme, col_spec.caption, binding)
                changes.append(f"retargeted column {col_spec.caption!r}")
            if scheme.columns.index(column) != target_index:
                from archicad_mcp.schemes.columns import move_column
                move_column(scheme, col_spec.caption, target_index)
                changes.append(f"moved column {col_spec.caption!r} to {target_index}")
        else:
            add_column(scheme, col_spec.caption, binding, index=target_index)
            changes.append(f"added column {col_spec.caption!r}")
        if col_spec.width is not None:
            column = {c.caption: c for c in scheme.columns}[col_spec.caption]
            set_field(column.element, "Width_of_cell_portrait", str(col_spec.width))
            set_field(column.element, "Width_of_cell_landscape", str(col_spec.width))
    return changes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/schemes/test_spec.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/schemes/spec.py tests/schemes/test_spec.py
git commit -m "feat(schemes): YAML scheme spec loader and apply"
```

---

### Task 6: The edit_schedule_scheme tool

**Files:**
- Modify: `src/archicad_mcp/core/schemes.py`
- Modify: `src/archicad_mcp/server.py`
- Test: `tests/schemes/test_core_edit.py`

**Interfaces:**
- Consumes: `load_specs`, `apply_spec`, `_load`, `save_scheme_tree`, `read_schedule_scheme`
- Produces: `edit_schedule_scheme(path, spec_path, spec_id=None, output=None, dry_run=True) -> dict` with keys `spec_id`, `dry_run`, `changes`, `columns_before`, `columns_after`, and `written` (the output path, or `None` on a dry run)

- [ ] **Step 1: Write the failing test**

Create `tests/schemes/test_core_edit.py`:

```python
import shutil
from pathlib import Path

from archicad_mcp.core.schemes import edit_schedule_scheme, read_schedule_scheme

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"

SPEC_YAML = """
- id: door-schedule
  template: sample_scheme.xml
  columns:
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Door ID"
      bind: { property: "69A58F6F-1111-4000-8000-000000000001" }
"""


def setup_case(tmp_path):
    scheme = tmp_path / "sample_scheme.xml"
    shutil.copy(FIXTURE, scheme)
    spec = tmp_path / "schemes.yaml"
    spec.write_text(SPEC_YAML, encoding="utf-8")
    return scheme, spec


def test_dry_run_writes_nothing(tmp_path):
    scheme, spec = setup_case(tmp_path)
    before = scheme.read_bytes()
    out = edit_schedule_scheme(str(scheme), str(spec))
    assert out["dry_run"] is True
    assert out["written"] is None
    assert scheme.read_bytes() == before


def test_dry_run_reports_before_and_after(tmp_path):
    scheme, spec = setup_case(tmp_path)
    out = edit_schedule_scheme(str(scheme), str(spec))
    assert out["columns_before"] == ["Door ID", "Quantity", "Fire Resistance"]
    assert out["columns_after"] == ["Quantity", "Door ID"]
    assert any("Fire Resistance" in c for c in out["changes"])


def test_commit_writes_to_the_output_path(tmp_path):
    scheme, spec = setup_case(tmp_path)
    dest = tmp_path / "edited.xml"
    out = edit_schedule_scheme(str(scheme), str(spec), output=str(dest), dry_run=False)
    assert out["written"] == str(dest)
    assert dest.is_file()
    assert read_schedule_scheme(str(dest))["columns"][0]["caption"] == "Quantity"


def test_commit_never_overwrites_the_input(tmp_path):
    scheme, spec = setup_case(tmp_path)
    before = scheme.read_bytes()
    edit_schedule_scheme(str(scheme), str(spec), output=str(tmp_path / "e.xml"),
                         dry_run=False)
    assert scheme.read_bytes() == before


def test_commit_refuses_to_write_over_the_input(tmp_path):
    scheme, spec = setup_case(tmp_path)
    out = edit_schedule_scheme(str(scheme), str(spec), output=str(scheme), dry_run=False)
    assert "error" in out
    assert "overwrite" in out["error"].lower()


def test_commit_without_output_defaults_beside_the_input(tmp_path):
    scheme, spec = setup_case(tmp_path)
    out = edit_schedule_scheme(str(scheme), str(spec), dry_run=False)
    assert out["written"].endswith("sample_scheme.edited.xml")
    assert Path(out["written"]).is_file()


def test_unknown_spec_id_is_an_error(tmp_path):
    scheme, spec = setup_case(tmp_path)
    out = edit_schedule_scheme(str(scheme), str(spec), spec_id="nope")
    assert "error" in out
    assert "door-schedule" in out["error"]


def test_spec_load_errors_are_surfaced(tmp_path):
    scheme, _ = setup_case(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text("- template: t.xml\n", encoding="utf-8")
    out = edit_schedule_scheme(str(scheme), str(bad))
    assert "error" in out


def test_matching_template_produces_no_warning(tmp_path):
    scheme, spec = setup_case(tmp_path)
    assert edit_schedule_scheme(str(scheme), str(spec))["warnings"] == []


def test_template_mismatch_warns_but_still_applies(tmp_path):
    scheme, _ = setup_case(tmp_path)
    spec = tmp_path / "window.yaml"
    spec.write_text(SPEC_YAML.replace("sample_scheme.xml", "window_scheme.xml"),
                    encoding="utf-8")
    out = edit_schedule_scheme(str(scheme), str(spec))
    assert out["warnings"] and "window_scheme.xml" in out["warnings"][0]
    assert out["columns_after"] == ["Quantity", "Door ID"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/schemes/test_core_edit.py -v`
Expected: FAIL, `ImportError: cannot import name 'edit_schedule_scheme'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/archicad_mcp/core/schemes.py`:

```python
def edit_schedule_scheme(path: str, spec_path: str, spec_id: str | None = None,
                         output: str | None = None, dry_run: bool = True) -> dict:
    scheme = _load(path)
    if isinstance(scheme, dict):
        return scheme

    specs, errors = load_specs(Path(spec_path).expanduser())
    if errors:
        return {"error": "Spec file problems: " + "; ".join(errors)}
    if not specs:
        return {"error": f"No scheme specs found in {spec_path}."}
    if spec_id is None:
        spec = specs[0]
    else:
        matched = [s for s in specs if s.spec_id == spec_id]
        if not matched:
            known = ", ".join(s.spec_id for s in specs)
            return {"error": f"Unknown spec id {spec_id!r}. Available: {known}."}
        spec = matched[0]

    source = Path(path).expanduser()
    warnings = []
    if spec.template and spec.template != source.name:
        warnings.append(
            f"Spec {spec.spec_id!r} was written against {spec.template!r} but is "
            f"being applied to {source.name!r}. Check this is deliberate.")

    columns_before = [c.caption for c in scheme.columns]
    try:
        changes = apply_spec(spec, scheme)
    except SpecError as exc:
        return {"error": str(exc)}
    columns_after = [c.caption for c in scheme.columns]

    written = None
    if not dry_run:
        dest = Path(output).expanduser() if output else \
            source.with_suffix(".edited" + source.suffix)
        if dest.resolve() == source.resolve():
            return {"error": "Refusing to overwrite the input scheme. Pass a "
                             "different 'output' path so the export stays intact."}
        save_scheme_tree(scheme.tree, dest)
        written = str(dest)

    return {"spec_id": spec.spec_id, "dry_run": dry_run, "changes": changes,
            "warnings": warnings, "columns_before": columns_before,
            "columns_after": columns_after, "written": written}
```

Add to the imports at the top of the same file:

```python
from archicad_mcp.schemes.spec import SpecError, apply_spec, load_specs
from archicad_mcp.schemes.xml_io import load_scheme_tree, save_scheme_tree
```

(replacing the existing `from archicad_mcp.schemes.xml_io import load_scheme_tree` line)

- [ ] **Step 4: Register the tool**

In `src/archicad_mcp/server.py`, directly after the `read_schedule_scheme` registration:

```python
    @mcp.tool(description="Apply a YAML scheme spec to an exported schedule scheme "
                          "XML: set the columns and their order, retarget bindings, "
                          "rename the scheme. DRY-RUN BY DEFAULT: returns the before "
                          "and after column lists and writes nothing until "
                          "dry_run=false. Never overwrites the input; writes to "
                          "'output' or to <name>.edited.xml beside it. Import the "
                          "result via Document > Schedules > Scheme Settings > "
                          "Import. Criteria are preserved, not yet editable.")
    def edit_schedule_scheme(path: str, spec_path: str, spec_id: str | None = None,
                             output: str | None = None, dry_run: bool = True) -> dict:
        return core_schemes.edit_schedule_scheme(path, spec_path, spec_id, output, dry_run)
```

- [ ] **Step 5: Extend the smoke assertion**

In `tests/test_server_smoke.py`, add `edit_schedule_scheme` to both schedule-tool tests from Task 3:

```python
    assert "read_schedule_scheme" in names
    assert "edit_schedule_scheme" in names
```

and correspondingly `assert "edit_schedule_scheme" not in names` in the verdicts-mode test.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/schemes/ tests/test_server_smoke.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/core/schemes.py src/archicad_mcp/server.py tests/
git commit -m "feat(schemes): edit_schedule_scheme tool, dry-run by default"
```

---

### Task 7: The validate_schedule_scheme tool

The only piece that touches Archicad, and only through `GetAllProperties`, which reads property definitions. It does not call `GetPropertyValuesOfElements` and so does not sit on the crash path in `docs/known-issues.md`.

**Files:**
- Create: `src/archicad_mcp/schemes/validate.py`
- Modify: `src/archicad_mcp/core/schemes.py`
- Modify: `src/archicad_mcp/server.py`
- Test: `tests/schemes/test_validate.py`

**Interfaces:**
- Consumes: `ArchicadConnection`, `Scheme`, kind constants, `NULL_GUID`
- Produces:
  - `property_index(conn) -> dict[str, str]` mapping `"Group/Name"` to GUID
  - `validate_scheme(conn, scheme) -> list[dict]` findings, each `{severity, column, message}`
  - `validate_schedule_scheme(path, port=None) -> dict` in `core/schemes.py`

`GetAllProperties` is a Tapir command returning `{"properties": [{"propertyId": {"guid": ...}, "propertyGroupName": ..., "propertyName": ...}, ...]}`.

- [ ] **Step 1: Write the failing test**

Create `tests/schemes/test_validate.py`:

```python
from pathlib import Path

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.schemes.model import parse_scheme
from archicad_mcp.schemes.validate import property_index, validate_scheme
from archicad_mcp.schemes.xml_io import load_scheme_tree
from tests.conftest import FakeCore

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"

ALL_PROPERTIES = {
    "properties": [
        {"propertyId": {"guid": "69A58F6F-1111-4000-8000-000000000001"},
         "propertyGroupName": "OFFICE", "propertyName": "Door ID"},
        {"propertyId": {"guid": "432FA53A-B71E-404B-A9D5-F1964237A3EB"},
         "propertyGroupName": "OFFICE", "propertyName": "Fire Rating"},
    ]
}


def conn_with(properties=ALL_PROPERTIES):
    # conn.tapir() gates on tapir_available(), which probes via the OFFICIAL
    # table, so the fake has to answer that too or every call raises.
    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}},
                    tapir={"GetAllProperties": properties})
    return ArchicadConnection(19723, core=core)


def load():
    return parse_scheme(load_scheme_tree(FIXTURE))


def test_property_index_maps_group_slash_name_to_guid():
    index = property_index(conn_with())
    assert index["OFFICE/Door ID"] == "69A58F6F-1111-4000-8000-000000000001"


def test_resolvable_property_column_produces_no_finding():
    findings = validate_scheme(conn_with(), load())
    assert not [f for f in findings if f["column"] == "Door ID"]


def test_unresolvable_property_guid_is_reported():
    empty = {"properties": []}
    findings = validate_scheme(conn_with(empty), load())
    door = [f for f in findings if f["column"] == "Door ID"]
    assert door and door[0]["severity"] == "error"
    assert "does not exist" in door[0]["message"]


def test_caption_disagreeing_with_binding_is_reported():
    # The fixture's "Fire Resistance" column binds to "Fire Rating Param".
    findings = validate_scheme(conn_with(), load())
    mismatch = [f for f in findings if f["column"] == "Fire Resistance"]
    assert mismatch and mismatch[0]["severity"] == "warning"


def test_builtin_columns_are_not_flagged():
    findings = validate_scheme(conn_with(), load())
    assert not [f for f in findings if f["column"] == "Quantity"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/schemes/test_validate.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'archicad_mcp.schemes.validate'`

- [ ] **Step 3: Write minimal implementation**

Create `src/archicad_mcp/schemes/validate.py`:

```python
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.schemes.model import (
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    NULL_GUID,
    Scheme,
)


def property_index(conn: ArchicadConnection) -> dict[str, str]:
    """'Group/Name' to GUID for every property defined in the open project.

    Reads property DEFINITIONS via Tapir GetAllProperties. This is not
    GetPropertyValuesOfElements and does not sit on the crash path documented in
    docs/known-issues.md.
    """
    response = conn.tapir("GetAllProperties", None)
    index: dict[str, str] = {}
    for item in response.get("properties", []):
        guid = (item.get("propertyId") or {}).get("guid")
        if not guid:
            continue
        group = item.get("propertyGroupName", "")
        name = item.get("propertyName", "")
        index[f"{group}/{name}"] = guid
    return index


def validate_scheme(conn: ArchicadConnection, scheme: Scheme) -> list[dict]:
    index = property_index(conn)
    known_guids = set(index.values())
    guid_to_name = {guid: name for name, guid in index.items()}
    findings: list[dict] = []

    for column in scheme.columns:
        binding = column.binding
        if binding.kind == KIND_PROPERTY:
            guid = binding.property_guid
            if guid == NULL_GUID or guid not in known_guids:
                findings.append({
                    "severity": "error", "column": column.caption,
                    "message": f"Bound to property {guid}, which does not exist in "
                               "this project. The scheme came from a project with "
                               "different property definitions.",
                })
                continue
            resolved = guid_to_name[guid]
            short = resolved.split("/", 1)[-1]
            if short and short.lower() not in column.caption.lower():
                findings.append({
                    "severity": "warning", "column": column.caption,
                    "message": f"Caption does not mention the bound property "
                               f"{resolved!r}. Check the column shows what it claims.",
                })
        elif binding.kind == KIND_GDL_PARAM:
            bound = binding.property_name
            if bound and bound.lower() not in column.caption.lower():
                findings.append({
                    "severity": "warning", "column": column.caption,
                    "message": f"Caption does not mention the bound GDL parameter "
                               f"{bound!r}. Check the column shows what it claims.",
                })
    return findings
```

Append to `src/archicad_mcp/core/schemes.py`:

```python
def validate_schedule_scheme(path: str, port: int | None = None) -> dict:
    scheme = _load(path)
    if isinstance(scheme, dict):
        return scheme
    conn = get_connection(port)
    findings = validate_scheme(conn, scheme)
    return {
        "name": scheme.name,
        "column_count": len(scheme.columns),
        "ok": not any(f["severity"] == "error" for f in findings),
        "findings": findings,
    }
```

Add to that file's imports:

```python
from archicad_mcp.connection import get_connection
from archicad_mcp.schemes.validate import validate_scheme
```

- [ ] **Step 4: Register the tool**

In `src/archicad_mcp/server.py`, after `edit_schedule_scheme`:

```python
    @mcp.tool(description="Check an exported schedule scheme against the open "
                          "project: do its property bindings still exist, and does "
                          "any column caption disagree with what it is bound to. "
                          "Reads property definitions only, not values, so it does "
                          "not risk the property-read crash.")
    @_guarded
    def validate_schedule_scheme(path: str, port: int | None = None) -> dict:
        return core_schemes.validate_schedule_scheme(
            path, port if port is not None else default_port)
```

This one does carry `@_guarded`, because it talks to Archicad.

- [ ] **Step 5: Extend the smoke assertion**

In `tests/test_server_smoke.py`, add `validate_schedule_scheme` to both schedule-tool tests, matching the pattern from Tasks 3 and 6.

- [ ] **Step 5b: Run tests to verify they pass**

Run: `uv run pytest tests/schemes/ tests/test_server_smoke.py -v`
Expected: all pass

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/schemes/validate.py src/archicad_mcp/core/schemes.py src/archicad_mcp/server.py tests/
git commit -m "feat(schemes): validate_schedule_scheme against the live model"
```

---

### Task 8: Criteria research harness

Criteria editing needs the `Param_Type` and `Relation_Index` code tables, which are undocumented. This task builds the tool that derives them from paired exports. It does not implement criteria writing: that gets its own plan once the table has real entries.

**Files:**
- Create: `scripts/diff_scheme_criteria.py`
- Create: `docs/scheme-criteria-codes.md`
- Test: `tests/schemes/test_criteria_diff.py`

**Interfaces:**
- Consumes: `load_scheme_tree`, `parse_scheme`
- Produces: `diff_criteria(before_path, after_path) -> list[dict]`, each `{index, field, before, after}`

- [ ] **Step 1: Write the failing test**

Create `tests/schemes/test_criteria_diff.py`:

```python
import shutil
from pathlib import Path

from scripts.diff_scheme_criteria import diff_criteria

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def test_identical_files_diff_to_nothing(tmp_path):
    a = tmp_path / "a.xml"
    shutil.copy(FIXTURE, a)
    assert diff_criteria(a, a) == []


def test_reports_a_changed_relation_index(tmp_path):
    a = tmp_path / "a.xml"
    b = tmp_path / "b.xml"
    shutil.copy(FIXTURE, a)
    b.write_text(FIXTURE.read_text(encoding="utf-8").replace(
        '<Relation_Index value="12"/>', '<Relation_Index value="7"/>', 1),
        encoding="utf-8")
    changes = diff_criteria(a, b)
    assert {"index": 1, "field": "Relation_Index", "before": "12", "after": "7"} in changes


def test_reports_a_criterion_count_change(tmp_path):
    a = tmp_path / "a.xml"
    b = tmp_path / "b.xml"
    shutil.copy(FIXTURE, a)
    text = FIXTURE.read_text(encoding="utf-8")
    start = text.index("\t\t\t<Criterion>")
    end = text.index("</Criterion>", start) + len("</Criterion>\n")
    b.write_text(text[:start] + text[end:], encoding="utf-8")
    changes = diff_criteria(a, b)
    assert any(c["field"] == "criterion_count" for c in changes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/schemes/test_criteria_diff.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'scripts.diff_scheme_criteria'`

If `scripts/` is not importable, add an empty `scripts/__init__.py` in this step.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/diff_scheme_criteria.py`:

```python
"""Derive the undocumented criteria code tables from paired scheme exports.

Archicad's Scheme Settings encodes each criterion as a numeric Param_Type and
Relation_Index with no public documentation. The way to learn them is
empirical: export a scheme, change exactly one criterion in the GUI, export
again, and diff. Run this on each pair and record the result in
docs/scheme-criteria-codes.md.

Usage:
    uv run python scripts/diff_scheme_criteria.py before.xml after.xml
"""
from __future__ import annotations

import sys
from pathlib import Path

from archicad_mcp.schemes.model import field_value, parse_scheme
from archicad_mcp.schemes.xml_io import load_scheme_tree

# Every field of a Criterion worth watching. Anything that moves between two
# exports is a candidate for the code table.
WATCHED = [
    "Param_Type", "Relation_Index", "ACPropertyGuid", "ACPropertyName",
    "ACPropertyGroup", "ACPropertyType", "AndNext", "Before_Brackets",
    "After_Brackets", "ExtendedElem_ElemClassId", "ExtendedElem_SpecialType",
    "Variable_Type_ID", "Variable", "IFCType", "IFCAssignmentType",
]


def _criterion_values(criterion) -> dict[str, str]:
    values = {tag: field_value(criterion.element, tag) for tag in WATCHED}
    value_el = criterion.element.find("UniValue/Variant/Value")
    values["UniValue"] = (value_el.text or "").strip() if value_el is not None else ""
    return values


def diff_criteria(before_path: Path, after_path: Path) -> list[dict]:
    before = parse_scheme(load_scheme_tree(Path(before_path)))
    after = parse_scheme(load_scheme_tree(Path(after_path)))

    changes: list[dict] = []
    if len(before.criteria) != len(after.criteria):
        changes.append({"index": -1, "field": "criterion_count",
                        "before": str(len(before.criteria)),
                        "after": str(len(after.criteria))})

    for i in range(min(len(before.criteria), len(after.criteria))):
        b = _criterion_values(before.criteria[i])
        a = _criterion_values(after.criteria[i])
        for tag in b:
            if b[tag] != a[tag]:
                changes.append({"index": i, "field": tag,
                                "before": b[tag], "after": a[tag]})
    return changes


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    changes = diff_criteria(Path(sys.argv[1]), Path(sys.argv[2]))
    if not changes:
        print("No criteria differences.")
        return 0
    for c in changes:
        where = "count" if c["index"] < 0 else f"criterion {c['index']}"
        print(f"{where}: {c['field']}: {c['before']!r} -> {c['after']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/schemes/test_criteria_diff.py -v`
Expected: 3 passed

- [ ] **Step 5: Create the code table document**

Create `docs/scheme-criteria-codes.md`:

```markdown
# Schedule criteria codes

Archicad encodes each schedule criterion as a numeric `Param_Type` and
`Relation_Index`. Neither is publicly documented, so this table is built
empirically and is the prerequisite for editing criteria rather than only
reading them.

## How to add an entry

1. Open a scratch project. Never use a client model.
2. Document > Schedules > Scheme Settings, pick a scheme, Export it as `before.xml`.
3. Change **exactly one** criterion in the dialog. One change per pair.
4. Export again as `after.xml`.
5. Run:

   ```bash
   uv run python scripts/diff_scheme_criteria.py before.xml after.xml
   ```

6. Add a row below with what you changed in the GUI and what the script reported.

## Confirmed codes

| Param_Type | Relation_Index | GUI meaning | Value field | Source |
|---|---|---|---|---|
| 88 | 1 | Element type is <class> | `ExtendedElem_ElemClassId` and `UniValue` carry the classification GUID | Observed in a real 29.0.0 door schedule |
| 232 | 12 | Property comparison on `ACPropertyGuid` | `UniValue` | Observed in a real 29.0.0 door schedule; the exact relation 12 means is not yet confirmed |

## Still unknown

Everything else. Priority order for the next exports: layer equals, element
type variants beyond Door, property is empty vs is not empty, property equals a
string, classification is, and the OR chaining that `AndNext` and the bracket
fields encode.
```

- [ ] **Step 6: Commit**

```bash
git add scripts/diff_scheme_criteria.py docs/scheme-criteria-codes.md tests/schemes/test_criteria_diff.py
git commit -m "feat(schemes): criteria code-table research harness"
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md` (the Tools section and the Docs list)
- Modify: `docs/known-issues.md` (a schedules note)

- [ ] **Step 1: Update the README tool list**

In `README.md`, in the **Core (full mode)** paragraph, add the three tools to the list so it reads:

```
**Core (full mode):** `query_elements`, `get_element_data`, `set_element_data`,
`create_elements`, `move_elements`, `delete_elements`, `manage_selection`,
`get_project_info`, `list_attributes`, `manage_issues`, `publish`,
`read_schedule_scheme`, `edit_schedule_scheme`, `validate_schedule_scheme`.
Every write is dry-run by default; delete and move also require `confirm=true`.
```

- [ ] **Step 2: Add a Schedules section to the README**

Insert after the **Rules** section:

```markdown
## Schedules

Archicad exposes **no API for schedules at all**. Not the JSON API, not Tapir,
and per Graphisoft not the C++ API either. What it does support is the XML
round trip built into Scheme Settings, and that is what these tools work
through:

1. In Archicad: Document > Schedules > Scheme Settings, select a scheme, **Export**
2. Edit it: `read_schedule_scheme` to see what it does, `edit_schedule_scheme`
   to apply a YAML spec, `validate_schedule_scheme` to check its bindings
   against the open project
3. In Archicad: Scheme Settings > **Import**

A scheme spec looks like this:

```yaml
- id: door-schedule
  template: exports/door-scheme.xml
  name: "Door Schedule"
  columns:
    - caption: "Quantity"
      bind: { builtin: Quantity }
    - caption: "Fire Resistance"
      bind: { gdl_param: "Fire Rating" }
```

Columns bind three ways, matching the three the format uses: `property` (an
Archicad property, by GUID or by `Group/Name` when Archicad is open),
`gdl_param` (a library part parameter, by name), and `builtin` (a built-in
field such as Quantity).

Criteria are read and preserved but not yet editable: the numeric codes behind
them are undocumented and are being mapped in
[docs/scheme-criteria-codes.md](docs/scheme-criteria-codes.md).
```

- [ ] **Step 3: Add the docs link**

In the README's **Docs** list, add:

```markdown
- **[Schedule criteria codes](docs/scheme-criteria-codes.md)**: the empirical
  `Param_Type` and `Relation_Index` table, and how to extend it.
```

- [ ] **Step 4: Note schedules in known issues**

Append to `docs/known-issues.md`:

```markdown
## Schedules

Schedules have no programmatic interface. No command in the official JSON API
or Tapir reads or writes a schedule, and Graphisoft's developer forum states
the C++ API does not reach them either. The only supported route is the Scheme
Settings Import and Export XML, which is what the `*_schedule_scheme` tools
operate on. This means every schedule edit needs two manual clicks in Archicad,
before and after.

Whether re-importing an edited scheme updates it in place or creates a numbered
duplicate is **not yet confirmed**. Graphisoft's documentation says duplicate
names are auto-numbered, but exported schemes carry stable IDs that suggest an
in-place match may be possible. Test on a scratch project before relying on
either behaviour.
```

- [ ] **Step 5: Verify the suite still passes**

Run: `uv run pytest`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add README.md docs/known-issues.md
git commit -m "docs: schedule scheme editing workflow and the no-API constraint"
```

---

## Deferred to a follow-up plan

**Criteria editing.** Task 8 builds the harness and the empty table; writing
criteria needs real entries in `docs/scheme-criteria-codes.md` first. Once 10
to 15 export pairs have been diffed, a follow-up plan adds
`schemes/criteria.py` with `set_criteria(scheme, criteria_specs)` and wires the
spec's `criteria:` block (already parsed and carried by `SchemeSpec`, currently
ignored by `apply_spec`) through to it.

**The in-place import question.** Settle empirically before the workflow is
documented as final. If imports always duplicate, `edit_schedule_scheme` should
probably clear or rewrite the root `ID` attribute and say so in its output.
