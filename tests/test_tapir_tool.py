import json

from fastmcp import Client

import archicad_mcp.tapir_install.run as run_mod
from archicad_mcp.server import build_server
from archicad_mcp.tapir_install.release import load_pinned
from archicad_mcp.tapir_install.run import Report


async def _names(mode):
    async with Client(build_server(mode=mode)) as client:
        return {t.name for t in await client.list_tools()}


async def test_install_tapir_registered_only_in_full_mode():
    assert "install_tapir" in await _names("full")
    assert "install_tapir" not in await _names("verdicts")


def _capture(monkeypatch):
    seen = {}

    def fake_run(**kw):
        seen.update(kw)
        return Report(load_pinned(), [], [], kw["dry_run"])
    monkeypatch.setattr(run_mod, "run", fake_run)
    return seen


async def _call(args):
    async with Client(build_server(mode="full")) as client:
        result = await client.call_tool("install_tapir", args)
        return json.loads(result.content[0].text)


async def test_install_tapir_without_confirm_is_a_dry_run(monkeypatch):
    seen = _capture(monkeypatch)
    out = await _call({})
    assert seen["dry_run"] is True and seen["replace"] is False and seen["versions"] is None
    assert out["dry_run"] is True and out["tapir_version"] == "1.7.3"
    assert out["restart_required"] == [] and out["backups"] == []


async def test_install_tapir_confirm_passes_through(monkeypatch):
    seen = _capture(monkeypatch)
    await _call({"archicad_version": 30, "replace_existing": True, "confirm": True})
    assert seen["dry_run"] is False and seen["versions"] == {30} and seen["replace"] is True
