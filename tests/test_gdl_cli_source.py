"""archicad-gdl build-source prints the build and each validation."""

from pathlib import Path

from archicad_mcp.gdl import cli, source


def test_build_source_prints_findings(tmp_path, monkeypatch, capsys):
    gsm = tmp_path / "Swept Beam.gsm"
    gsm.write_bytes(b"x" * 2048)
    seen = {}

    def fake_build(root, out_dir, archicad=None, validate_with=(27, 29), keep_hsf=False):
        seen.update(root=root, out=out_dir, archicad=archicad, validate_with=validate_with)
        return source.SourceBuild(gsm=gsm, name="Swept Beam", guid="G", version="0.1.0",
                                  archicad=27, findings={27: [], 29: ["warning: x"]}, skipped=[])

    monkeypatch.setattr(source, "build_source", fake_build)
    assert cli.main(["build-source", "gdl-src/swept-beam", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert seen["archicad"] is None and seen["validate_with"] == (27, 29)
    assert "built with the Archicad 27 converter" in out
    assert "validation (Archicad 27): scripts interpret cleanly" in out
    assert "warning: x" in out


def test_build_source_reports_source_errors(tmp_path, capsys):
    assert cli.main(["build-source", str(tmp_path / "missing")]) == 1
    assert "libpart.toml" in capsys.readouterr().err
