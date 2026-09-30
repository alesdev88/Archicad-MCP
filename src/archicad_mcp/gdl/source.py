"""Hand-written library parts: GDL script files plus TOML metadata to HSF.

The mesh route (generate.py) derives a part from a model. This route is for
parts written as GDL, like Swept Beam (gdl-src/swept-beam): the scripts live as
.gdl files, the parameters in params.toml, the identity in libpart.toml. The
parameter XML below was proven on 2026-09-29 by compiling with the Archicad 27
and 29 converters and converting back: arrays, Profile, BuildingMaterial,
Material, PenColor, LineType and hidden flags all survive unchanged.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from archicad_mcp.gdl import toolchain
from archicad_mcp.gdl.generate import ANCESTRY_GUIDS

PARAM_TAGS = {
    "length": "Length", "angle": "Angle", "real": "RealNum",
    "integer": "Integer", "boolean": "Boolean", "string": "String",
    "surface": "Material", "pen": "PenColor", "linetype": "LineType",
    "bmat": "BuildingMaterial", "profile": "Profile", "fill": "FillPattern",
}
NUMERIC_TYPES = {"length", "angle", "real"}
INDEX_TYPES = {"integer", "surface", "pen", "linetype", "bmat", "profile", "fill"}
# the Author in the part's details when libpart.toml names none
DEFAULT_AUTHOR = "archicad-gdl"

# (source script, HSF script file, libpartdata.xml section), in section order
SCRIPTS = (
    ("master.gdl", "1d.gdl", "Script_1D"),
    ("2d.gdl", "2d.gdl", "Script_2D"),
    ("3d.gdl", "3d.gdl", "Script_3D"),
    ("param.gdl", "vl.gdl", "Script_VL"),
    ("ui.gdl", "ui.gdl", "Script_UI"),
)
REQUIRED_FIRST = ("A", "B", "ZZYZX")
PARAM_KEYS = {"name", "type", "default", "description", "hidden", "child"}
HSF_VERSION = "46"  # both the 27 and 29 converters accept it and write their own format

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
_GUID = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$")

_PARAMLIST_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<ParamSection>
\t<ParamSectHeader>
\t\t<AutoHotspots>false</AutoHotspots>
\t\t<StatBits>
\t\t\t<STBit_UIDefault/>
\t\t</StatBits>
\t\t<WDLeftFrame>0</WDLeftFrame>
\t\t<WDRightFrame>0</WDRightFrame>
\t\t<WDTopFrame>0</WDTopFrame>
\t\t<WDBotFrame>0</WDBotFrame>
\t\t<LayFlags>65535</LayFlags>
\t\t<WDMirrorThickness>0</WDMirrorThickness>
\t\t<WDWallInset>0</WDWallInset>
\t</ParamSectHeader>
\t<Parameters SectVersion="27" SectionFlags="0" SubIdent="0">
"""


class SourceError(ValueError):
    """The library part source folder is incomplete or inconsistent."""


@dataclass
class Param:
    name: str
    type: str
    default: object
    description: str = ""
    hidden: bool = False
    child: bool = False

    @property
    def is_array(self) -> bool:
        return isinstance(self.default, list)


@dataclass
class LibpartSource:
    root: Path
    name: str
    guid: str
    version: str
    keywords: str
    target_archicad: int | None
    params: list[Param]
    scripts: dict[str, str] = field(default_factory=dict)
    author: str = DEFAULT_AUTHOR


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        raise SourceError(f"Missing {path.name} in {path.parent}.")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SourceError(f"{path.name} is not valid TOML: {exc}") from exc


def _check_value(name: str, kind: str, value) -> None:
    if kind in NUMERIC_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SourceError(f"Parameter {name}: {kind} values must be numbers, got {value!r}.")
    elif kind in INDEX_TYPES:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SourceError(f"Parameter {name}: {kind} values must be whole numbers >= 0, got {value!r}.")
    elif kind == "boolean":
        if not (isinstance(value, bool) or value in (0, 1)):
            raise SourceError(f"Parameter {name}: boolean values must be true or false, got {value!r}.")
    elif kind == "string":
        if not isinstance(value, str):
            raise SourceError(f"Parameter {name}: string values must be text, got {value!r}.")
        if '"' in value or "]]>" in value:
            raise SourceError(f'Parameter {name}: string values cannot contain " or ]]>.')


def _param(entry: dict) -> Param:
    unknown = set(entry) - PARAM_KEYS
    if unknown:
        raise SourceError(f"Unknown keys in a [[param]] entry: {sorted(unknown)}.")
    name = str(entry.get("name", ""))
    if not _NAME.match(name):
        raise SourceError(f"{name!r} is not a valid GDL name (letters, digits, _; up to 32).")
    kind = str(entry.get("type", "")).lower()
    if kind not in PARAM_TAGS:
        raise SourceError(f"Parameter {name}: unknown type {kind!r}. Known: {sorted(PARAM_TAGS)}.")
    if "default" not in entry:
        raise SourceError(f"Parameter {name}: a default is required.")
    default = entry["default"]
    if isinstance(default, list):
        if not default:
            raise SourceError(f"Parameter {name}: an empty array needs at least one item.")
        for item in default:
            _check_value(name, kind, item)
    else:
        _check_value(name, kind, default)
    description = str(entry.get("description", ""))
    if '"' in description or "]]>" in description:
        raise SourceError(f'Parameter {name}: descriptions cannot contain " or ]]>.')
    return Param(name, kind, default, description,
                 bool(entry.get("hidden", False)), bool(entry.get("child", False)))


def _check_params(params: list[Param]) -> None:
    seen: set[str] = set()
    for p in params:
        key = p.name.casefold()  # GDL names are case-insensitive
        if key in seen:
            raise SourceError(f"Parameter {p.name}: duplicate name (GDL ignores case).")
        seen.add(key)
    first = [p.name for p in params[:3]]
    if first != list(REQUIRED_FIRST) or any(p.type != "length" or p.is_array for p in params[:3]):
        raise SourceError("The first three parameters must be A, B, ZZYZX, all single lengths.")


def load_source(root: str | Path) -> LibpartSource:
    root = Path(root)
    meta = _read_toml(root / "libpart.toml")
    raw = _read_toml(root / "params.toml")
    for key in ("name", "guid", "version"):
        if not str(meta.get(key, "")).strip():
            raise SourceError(f"libpart.toml needs '{key}'.")
    guid = str(meta["guid"]).upper()
    if not _GUID.match(guid):
        raise SourceError(f"libpart.toml guid is not a GUID: {meta['guid']!r}.")
    params = [_param(entry) for entry in raw.get("param", [])]
    _check_params(params)
    version_param = meta.get("version_param")
    if version_param:
        for p in params:
            if p.name == version_param:
                p.default = str(meta["version"])
    scripts = {}
    for src_name, _hsf_name, _section in SCRIPTS:
        path = root / "scripts" / src_name
        if path.is_file():
            scripts[src_name] = path.read_text(encoding="utf-8")
    if "2d.gdl" not in scripts or "3d.gdl" not in scripts:
        raise SourceError(f"scripts/2d.gdl and scripts/3d.gdl are required in {root}.")
    target = meta.get("target_archicad")
    author = str(meta.get("author", "")).strip() or DEFAULT_AUTHOR
    return LibpartSource(root, str(meta["name"]), guid, str(meta["version"]),
                         str(meta.get("keywords", "")),
                         int(target) if target is not None else None, params, scripts,
                         author)


def _number(value) -> str:
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _value_text(p: Param, value) -> str:
    if p.type == "string":
        return f'<![CDATA["{value}"]]>'
    if p.type == "boolean":
        return "1" if value else "0"
    if p.type in INDEX_TYPES:
        return str(int(value))
    return _number(value)


def paramlist_xml(params: list[Param]) -> str:
    rows: list[str] = []
    for p in params:
        tag = PARAM_TAGS[p.type]
        rows.append(f'\t\t<{tag} Name="{p.name}">')
        rows.append(f'\t\t\t<Description><![CDATA["{p.description}"]]></Description>')
        flags = [flag for flag, on in (("ParFlg_Child", p.child), ("ParFlg_Hidden", p.hidden)) if on]
        if flags:
            rows.append("\t\t\t<Flags>")
            rows += [f"\t\t\t\t<{flag}/>" for flag in flags]
            rows.append("\t\t\t</Flags>")
        if p.is_array:
            rows.append(f'\t\t\t<ArrayValues FirstDimension="{len(p.default)}" SecondDimension="0">')
            rows += [f'\t\t\t\t<AVal Row="{i}">{_value_text(p, v)}</AVal>'
                     for i, v in enumerate(p.default, 1)]
            rows.append("\t\t\t</ArrayValues>")
        else:
            rows.append(f"\t\t\t<Value>{_value_text(p, p.default)}</Value>")
        rows.append(f"\t\t</{tag}>")
    return _PARAMLIST_HEAD + "\n".join(rows) + "\n\t</Parameters>\n</ParamSection>\n"


def write_hsf(src: LibpartSource, hsf_dir: str | Path) -> Path:
    hsf_dir = Path(hsf_dir)
    scripts_dir = hsf_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    sections = []
    for src_name, hsf_name, section in SCRIPTS:
        if src_name in src.scripts:
            (scripts_dir / hsf_name).write_text(src.scripts[src_name], encoding="utf-8")
            sections.append(f'\t<{section} SectVersion="20" SectionFlags="0" SubIdent="0"/>')
    (hsf_dir / "libpartdata.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<LibpartData Owner="0" Signature="1196644685" Version="{HSF_VERSION}">
\t<Identification>
\t\t<MainGUID>{src.guid}</MainGUID>
\t\t<IsPlaceable>true</IsPlaceable>
\t\t<IsArchivable>false</IsArchivable>
\t\t<MigrationValue>Normal</MigrationValue>
\t\t<IsTemplate>false</IsTemplate>
\t</Identification>
\t<Copyright SectVersion="1" SectionFlags="0" SubIdent="0"/>
\t<Ancestry SectVersion="1" SectionFlags="0" SubIdent="0"/>
\t<ParamSection SectVersion="27" SectionFlags="0" SubIdent="0"/>
{chr(10).join(sections)}
</LibpartData>
""", encoding="utf-8")
    ancestry = "\n".join(f"\t<MainGUID>{g}</MainGUID>" for g in ANCESTRY_GUIDS)
    (hsf_dir / "ancestry.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<Ancestry>\n{ancestry}\n</Ancestry>\n',
        encoding="utf-8")
    (hsf_dir / "libpartdocs.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<libpartdocs>
\t<Copyright>
\t\t<Author>{escape(src.author)}</Author>
\t\t<License>
\t\t\t<Type>CC BY</Type>
\t\t\t<Version>4.0</Version>
\t\t</License>
\t</Copyright>
\t<Keywords SectVersion="1" SectionFlags="0" SubIdent="0">
\t\t<![CDATA[{src.keywords}]]>
\t</Keywords>
\t<CommentSection>
\t\t<![CDATA[{src.name} {src.version}, built by archicad-gdl build-source]]>
\t</CommentSection>
</libpartdocs>
""", encoding="utf-8")
    (hsf_dir / "paramlist.xml").write_text(paramlist_xml(src.params), encoding="utf-8")
    return hsf_dir


@dataclass
class SourceBuild:
    gsm: Path
    name: str
    guid: str
    version: str
    archicad: int | None
    findings: dict[int, list[str]]
    skipped: list[int]


def build_source(root: str | Path, out_dir: str | Path, archicad: int | None = None,
                 validate_with: tuple[int, ...] = (27, 29),
                 keep_hsf: bool = False) -> SourceBuild:
    """Compile a source folder to <out_dir>/<name>.gsm and check its scripts.

    The converter is `archicad`, else libpart.toml's target_archicad, else the
    newest install. Validation runs once per listed release whose converter is
    installed; the others are reported as skipped, not failed.
    """
    src = load_source(root)
    target = archicad if archicad is not None else src.target_archicad
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hsf_dir = out_dir / src.name
    if hsf_dir.exists():
        shutil.rmtree(hsf_dir)
    gsm_path = out_dir / f"{src.name}.gsm"
    write_hsf(src, hsf_dir)
    # Compile beside, then replace: out_dir is usually a linked library folder,
    # and a build that fails after deleting the old part would leave every
    # placed instance missing at the next library reload.
    staging = Path(tempfile.mkdtemp(prefix="gdl-build-"))
    try:
        built = toolchain.compile_hsf(hsf_dir, staging / gsm_path.name, version=target)
        shutil.move(str(built), str(gsm_path))
        gsm = gsm_path
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not keep_hsf:
            shutil.rmtree(hsf_dir, ignore_errors=True)
    installed = set(toolchain.installed_converter_versions())
    findings: dict[int, list[str]] = {}
    skipped: list[int] = []
    for v in validate_with:
        if v in installed:
            findings[v] = [ln.strip() for ln in toolchain.validate_gsm(gsm, version=v)]
        else:
            skipped.append(v)
    return SourceBuild(gsm, src.name, src.guid, src.version, target, findings, skipped)
