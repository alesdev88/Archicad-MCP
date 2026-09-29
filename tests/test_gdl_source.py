"""Hand-written library parts: TOML plus GDL scripts to HSF."""

import subprocess
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from archicad_mcp.gdl import source, toolchain

LIBPART = textwrap.dedent("""\
    name = "Probe Part"
    guid = "5a1e7b3c-0d2f-4c6b-9e8a-7f1d2c3b4a50"
    version = "0.0.1"
    keywords = "probe"
    target_archicad = 27
    version_param = "sb_version"
""")

PARAMS = textwrap.dedent("""\
    [[param]]
    name = "A"
    type = "length"
    default = 1.0
    [[param]]
    name = "B"
    type = "length"
    default = 1.0
    [[param]]
    name = "ZZYZX"
    type = "length"
    default = 1.0
    [[param]]
    name = "nodeX"
    type = "length"
    default = [0.0, 3.0]
    description = "Node X"
    [[param]]
    name = "segArc"
    type = "angle"
    default = [15.0, 0.0]
    hidden = true
    [[param]]
    name = "beamProfile"
    type = "profile"
    default = 1
    [[param]]
    name = "rectBMat"
    type = "bmat"
    default = 1
    [[param]]
    name = "profileMode"
    type = "string"
    default = "Rectangle"
    [[param]]
    name = "flipProfile"
    type = "boolean"
    default = false
    [[param]]
    name = "planFill"
    type = "fill"
    default = 0
    [[param]]
    name = "pathTolerance"
    type = "length"
    default = 0.00001
    [[param]]
    name = "sb_version"
    type = "string"
    default = ""
    hidden = true
""")


def _write_source(root, libpart=LIBPART, params=PARAMS, scripts=None):
    scripts = scripts if scripts is not None else {
        "master.gdl": "EPS = 0.0001\n", "2d.gdl": "project2 3, 270, 2\n",
        "3d.gdl": "block 1, 1, 1\n", "param.gdl": "PARAMETERS A = A\n",
        "ui.gdl": 'UI_DIALOG "t"\n'}
    (root / "scripts").mkdir(parents=True)
    (root / "libpart.toml").write_text(libpart)
    (root / "params.toml").write_text(params)
    for name, text in scripts.items():
        (root / "scripts" / name).write_text(text)
    return root


def test_load_reads_metadata_params_and_scripts(tmp_path):
    src = source.load_source(_write_source(tmp_path))
    assert src.name == "Probe Part"
    assert src.guid == "5A1E7B3C-0D2F-4C6B-9E8A-7F1D2C3B4A50"
    assert src.target_archicad == 27
    assert [p.name for p in src.params][:4] == ["A", "B", "ZZYZX", "nodeX"]
    assert set(src.scripts) == {"master.gdl", "2d.gdl", "3d.gdl", "param.gdl", "ui.gdl"}


def test_version_param_takes_libpart_version(tmp_path):
    src = source.load_source(_write_source(tmp_path))
    assert next(p for p in src.params if p.name == "sb_version").default == "0.0.1"


def test_paramlist_scalar_array_and_flags(tmp_path):
    xml = source.paramlist_xml(source.load_source(_write_source(tmp_path)).params)
    assert '<Length Name="nodeX">' in xml
    assert '<ArrayValues FirstDimension="2" SecondDimension="0">' in xml
    assert '<AVal Row="1">0</AVal>' in xml and '<AVal Row="2">3</AVal>' in xml
    assert '<Angle Name="segArc">' in xml and "<ParFlg_Hidden/>" in xml
    assert '<Profile Name="beamProfile">' in xml
    assert '<BuildingMaterial Name="rectBMat">' in xml
    assert '<Value><![CDATA["Rectangle"]]></Value>' in xml
    assert '<Boolean Name="flipProfile">' in xml
    assert '<FillPattern Name="planFill">' in xml
    assert "<Value>0.00001</Value>" in xml


@pytest.mark.parametrize("entry, message", [
    ('[[param]]\nname = "extra"\ntype = "colour"\ndefault = 1.0\n', "unknown type"),
    ('[[param]]\nname = "2bad"\ntype = "length"\ndefault = 1.0\n', "not a valid GDL name"),
    ('[[param]]\nname = "mode"\ntype = "string"\ndefault = 3\n', "must be text"),
    ('[[param]]\nname = "flag"\ntype = "boolean"\ndefault = 2\n', "must be true or false"),
])
def test_bad_param_rejected(tmp_path, entry, message):
    with pytest.raises(source.SourceError, match=message):
        source.load_source(_write_source(tmp_path, params=PARAMS + entry))


def test_duplicate_name_rejected_case_insensitively(tmp_path):
    params = PARAMS + '[[param]]\nname = "NODEX"\ntype = "length"\ndefault = 1.0\n'
    with pytest.raises(source.SourceError, match="duplicate"):
        source.load_source(_write_source(tmp_path, params=params))


def test_empty_array_rejected(tmp_path):
    params = PARAMS + '[[param]]\nname = "empty"\ntype = "length"\ndefault = []\n'
    with pytest.raises(source.SourceError, match="empty array"):
        source.load_source(_write_source(tmp_path, params=params))


def test_a_b_zzyzx_must_come_first(tmp_path):
    params = PARAMS.replace('name = "A"', 'name = "Alpha"', 1)
    with pytest.raises(source.SourceError, match="A, B, ZZYZX"):
        source.load_source(_write_source(tmp_path, params=params))


def test_missing_3d_script_rejected(tmp_path):
    with pytest.raises(source.SourceError, match="3d.gdl"):
        source.load_source(_write_source(tmp_path, scripts={"2d.gdl": "x = 1\n"}))


def test_write_hsf_layout(tmp_path):
    src = source.load_source(_write_source(tmp_path / "src",
                                           scripts={"2d.gdl": "a = 1\n", "3d.gdl": "b = 1\n"}))
    hsf = source.write_hsf(src, tmp_path / "hsf")
    data = (hsf / "libpartdata.xml").read_text()
    assert "<MainGUID>5A1E7B3C-0D2F-4C6B-9E8A-7F1D2C3B4A50</MainGUID>" in data
    assert "Script_2D" in data and "Script_3D" in data
    assert "Script_1D" not in data and "Script_VL" not in data
    assert (hsf / "scripts" / "3d.gdl").read_text() == "b = 1\n"
    assert (hsf / "ancestry.xml").is_file() and (hsf / "paramlist.xml").is_file()


def test_param_script_goes_to_vl(tmp_path):
    src = source.load_source(_write_source(tmp_path / "src"))
    hsf = source.write_hsf(src, tmp_path / "hsf")
    assert (hsf / "scripts" / "vl.gdl").read_text() == "PARAMETERS A = A\n"
    assert (hsf / "scripts" / "1d.gdl").read_text() == "EPS = 0.0001\n"


@pytest.mark.skipif(27 not in toolchain.installed_converter_versions(),
                    reason="needs the Archicad 27 LP_XMLConverter")
def test_round_trip_through_archicad_27_converter(tmp_path):
    src = source.load_source(_write_source(tmp_path / "src"))
    hsf = source.write_hsf(src, tmp_path / "hsf")
    gsm = toolchain.compile_hsf(hsf, tmp_path / "part.gsm", version=27)
    back = tmp_path / "back"
    subprocess.run([str(toolchain.find_lp_xmlconverter(27)), "libpart2hsf", str(gsm), str(back)],
                   check=True, capture_output=True)

    # the converter rewrites numbers in its own notation (0.00001 comes back as
    # 1.e-5), so compare parsed parameters, not text
    def params(path):
        root = ET.fromstring(path.read_text(encoding="utf-8-sig"))
        out = []
        for el in root.find("Parameters"):
            flags = el.find("Flags")
            values = []
            for v in el.iter():
                if v.tag in ("Value", "AVal"):
                    try:
                        values.append(float(v.text))
                    except (TypeError, ValueError):
                        values.append(v.text)
            out.append((el.tag, el.get("Name"),
                        sorted(f.tag for f in flags) if flags is not None else [], values))
        return out

    assert params(back / "paramlist.xml") == params(hsf / "paramlist.xml")


def test_build_source_uses_target_and_validates_installed(tmp_path, monkeypatch):
    root = _write_source(tmp_path / "src")
    seen = {}

    def fake_compile(hsf_dir, gsm_path, version=None):
        seen["version"] = version
        seen["hsf_had_paramlist"] = (Path(hsf_dir) / "paramlist.xml").is_file()
        Path(gsm_path).write_bytes(b"gsm")
        return Path(gsm_path)

    monkeypatch.setattr(toolchain, "compile_hsf", fake_compile)
    monkeypatch.setattr(toolchain, "installed_converter_versions", lambda: [29])
    monkeypatch.setattr(toolchain, "validate_gsm", lambda gsm, extra_libs=None, version=None: [])
    result = source.build_source(root, tmp_path / "out")
    assert seen == {"version": 27, "hsf_had_paramlist": True}
    assert result.gsm == tmp_path / "out" / "Probe Part.gsm"
    assert result.findings == {29: []} and result.skipped == [27]
    assert not (tmp_path / "out" / "Probe Part").exists()  # HSF removed


def test_build_source_archicad_argument_wins(tmp_path, monkeypatch):
    root = _write_source(tmp_path / "src")
    seen = {}

    def fake_compile(hsf_dir, gsm_path, version=None):
        seen["v"] = version
        Path(gsm_path).write_bytes(b"")
        return Path(gsm_path)

    monkeypatch.setattr(toolchain, "compile_hsf", fake_compile)
    monkeypatch.setattr(toolchain, "installed_converter_versions", lambda: [])
    source.build_source(root, tmp_path / "out", archicad=29, validate_with=())
    assert seen["v"] == 29


def test_a_failed_build_keeps_the_previous_part(tmp_path, monkeypatch):
    # --out is usually the linked library folder: deleting the old part first
    # would leave every placed instance missing after the next reload
    root = _write_source(tmp_path / "src")
    out = tmp_path / "out"
    out.mkdir()
    (out / "Probe Part.gsm").write_bytes(b"previous build")

    def failing_compile(hsf_dir, gsm_path, version=None):
        Path(gsm_path).write_bytes(b"half written")
        raise toolchain.ToolchainError("hsf2libpart failed")

    monkeypatch.setattr(toolchain, "compile_hsf", failing_compile)
    with pytest.raises(toolchain.ToolchainError):
        source.build_source(root, out, validate_with=())
    assert (out / "Probe Part.gsm").read_bytes() == b"previous build"


def test_a_good_build_replaces_the_previous_part(tmp_path, monkeypatch):
    root = _write_source(tmp_path / "src")
    out = tmp_path / "out"
    out.mkdir()
    (out / "Probe Part.gsm").write_bytes(b"previous build")

    def fake_compile(hsf_dir, gsm_path, version=None):
        Path(gsm_path).write_bytes(b"new build")
        return Path(gsm_path)

    monkeypatch.setattr(toolchain, "compile_hsf", fake_compile)
    result = source.build_source(root, out, validate_with=())
    assert result.gsm == out / "Probe Part.gsm"
    assert result.gsm.read_bytes() == b"new build"
    assert sorted(p.name for p in out.iterdir()) == ["Probe Part.gsm"]


def test_gdl_sources_use_no_dash_stand_ins():
    root = Path(__file__).resolve().parent.parent / "gdl-src"
    bad = [f"{p.relative_to(root)}:{n}" for p in sorted(root.rglob("*")) if p.is_file()
           for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
           if "—" in line or "–" in line or " -- " in line]
    assert bad == []


def test_swept_beam_size_defaults_are_one_metre():
    # Tapir's SetGDLParametersOfElements (1.5.0 to 1.5.9) writes A and B in
    # metres into the placed size ratios, which Archicad reads as multiples of
    # these defaults: any default other than 1 m rescales the beam on every
    # parameter write (gate E, a 4 m beam came out 48 m long)
    src = source.load_source(Path(__file__).resolve().parent.parent / "gdl-src" / "swept-beam")
    defaults = {p.name: p.default for p in src.params}
    assert defaults["A"] == defaults["B"] == defaults["aDone"] == 1.0
