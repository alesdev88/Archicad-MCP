"""update_extension: find the newest release, check it, hand it to Claude Desktop.

Real download and hash code against a fake GitHub, and a real bundle-shaped
folder on disk. Only opening the file is replaced: that is what would put a
dialog on the screen of whoever runs the tests.
"""
import hashlib
import json
import subprocess

import httpx
import pytest
from fastmcp import Client

from archicad_mcp import extension_update
from archicad_mcp.extension_update import bundle_version, open_bundle, run
from archicad_mcp.server import build_server
from archicad_mcp.tapir_install.release import InstallError

BUNDLE = b"a real bundle would be a zip"
SHA256 = hashlib.sha256(BUNDLE).hexdigest()
MAC_FILE = "archicad-mcp-0.14.0-darwin-arm64.mcpb"
WIN_FILE = "archicad-mcp-0.14.0-win32.mcpb"
RELEASE_PAGE = "https://github.com/alesdev88/Archicad-MCP/releases/tag/v0.14.0"


def _asset(name, **changes):
    return {"name": name, "size": len(BUNDLE), "digest": f"sha256:{SHA256}",
            "browser_download_url": ("https://github.com/alesdev88/Archicad-MCP/releases/"
                                     f"download/v0.14.0/{name}"), **changes}


def _release(tag="v0.14.0", assets=None):
    if assets is None:
        assets = [_asset(MAC_FILE), _asset(WIN_FILE),
                  _asset("archicad_mcp_server-0.14.0-py3-none-any.whl")]
    return {"tag_name": tag, "html_url": RELEASE_PAGE, "prerelease": False, "assets": assets}


def _github(release=None, file=BUNDLE, status=200):
    """A client whose API answers with the release and whose downloads give file."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.host)
        if request.url.host == "api.github.com":
            assert request.url.path == "/repos/alesdev88/Archicad-MCP/releases/latest"
            return httpx.Response(status, json=release or _release())
        return httpx.Response(200, content=file)

    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def _runtime(tmp_path, version="0.13.1"):
    """The folder Desktop unpacks a bundle into, as it holds this server's sys.prefix."""
    root = tmp_path / "local.mcpb.ale-dolenec.archicad-mcp"
    (root / "runtime").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"name": "archicad-mcp", "author": {"name": "Aleš Dolenec"},
                    "version": version}), encoding="utf-8")
    return root / "runtime"


@pytest.fixture
def opened(monkeypatch):
    seen = []
    monkeypatch.setattr(extension_update, "open_bundle",
                        lambda path, platform: seen.append((path, platform)))
    return seen


def _run(tmp_path, client, *, confirm=False, platform="darwin", version="0.13.1"):
    return run(confirm=confirm, prefix=_runtime(tmp_path, version), client=client,
               platform=platform, folder=tmp_path / "update")


def test_bundle_version_reads_the_manifest_beside_the_runtime(tmp_path):
    assert bundle_version(_runtime(tmp_path, "0.12.3")) == "0.12.3"


def test_a_checkout_with_a_venv_is_not_a_bundle(tmp_path):
    # A dev checkout has a manifest.json beside its .venv, and it is not installed.
    (tmp_path / ".venv").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps({"version": "0.14.0"}))
    assert bundle_version(tmp_path / ".venv") is None


def test_a_server_that_is_not_a_bundle_gets_the_command_without_the_network(tmp_path, opened):
    client, requests = _github()
    (tmp_path / ".venv").mkdir()
    result = run(confirm=True, prefix=tmp_path / ".venv", client=client, platform="darwin",
                 folder=tmp_path / "update")
    assert result["installed_as_bundle"] is False
    assert "uv tool upgrade archicad-mcp-server" in result["next_step"]
    assert requests == [] and opened == []


def test_dry_run_reports_the_update_and_touches_nothing(tmp_path, opened):
    client, requests = _github()
    result = _run(tmp_path, client)
    assert result["installed_version"] == "0.13.1" and result["latest_version"] == "0.14.0"
    assert result["update_available"] is True and result["dry_run"] is True
    assert result["bundle_file"] == MAC_FILE and result["release_page"] == RELEASE_PAGE
    assert requests == ["api.github.com"]  # the bundle itself was not fetched
    assert opened == [] and not (tmp_path / "update").exists()


def test_confirm_opens_the_checked_file(tmp_path, opened):
    client, _ = _github()
    result = _run(tmp_path, client, confirm=True)
    path = tmp_path / "update" / MAC_FILE
    assert opened == [(path, "darwin")]
    assert path.read_bytes() == BUNDLE  # kept: Desktop reads it after this returns
    assert result["opened"] is True and result["sha256_checked"] is True
    assert result["dry_run"] is False and result["downloaded_to"] == str(path)


@pytest.mark.parametrize("platform,expected", [("darwin", MAC_FILE), ("win32", WIN_FILE)])
def test_each_platform_gets_its_own_bundle(tmp_path, opened, platform, expected):
    client, _ = _github()
    assert _run(tmp_path, client, confirm=True, platform=platform)["bundle_file"] == expected
    assert opened[0][0].name == expected


@pytest.mark.parametrize("installed,latest,available", [
    ("0.9.0", "v0.10.0", True),    # as text, "0.9.0" would sort after "0.10.0"
    ("0.10.0", "v0.9.0", False),
    ("0.14.0", "v0.14.0", False),
])
def test_versions_compare_as_numbers(tmp_path, opened, installed, latest, available):
    name = f"archicad-mcp-{latest[1:]}-darwin-arm64.mcpb"
    client, _ = _github(_release(latest, [_asset(name)]))
    result = _run(tmp_path, client, version=installed)
    assert result["update_available"] is available


@pytest.mark.parametrize("installed", ["0.14.0", "0.15.0"])
def test_confirm_with_nothing_newer_fetches_and_opens_nothing(tmp_path, opened, installed):
    # 0.15.0 is a build ahead of the latest release: confirm must not downgrade it.
    client, requests = _github()
    result = _run(tmp_path, client, confirm=True, version=installed)
    assert result["update_available"] is False
    assert requests == ["api.github.com"] and opened == []


def test_a_file_that_fails_the_hash_is_removed_and_never_opened(tmp_path, opened):
    tampered = b"X" * len(BUNDLE)
    client, _ = _github(file=tampered)
    with pytest.raises(InstallError, match="does not match"):
        _run(tmp_path, client, confirm=True)
    assert opened == [] and list((tmp_path / "update").iterdir()) == []


def test_a_download_larger_than_the_release_leaves_no_file(tmp_path, opened):
    client, _ = _github(file=BUNDLE * 2)
    with pytest.raises(InstallError, match="more than"):
        _run(tmp_path, client, confirm=True)
    assert opened == [] and list((tmp_path / "update").iterdir()) == []


def test_a_new_download_replaces_the_one_before_it(tmp_path, opened):
    # A bundle is 60 MB; repeated updates must not pile them up in the temp folder.
    old = tmp_path / "update" / "archicad-mcp-0.13.1-darwin-arm64.mcpb"
    old.parent.mkdir()
    old.write_bytes(b"old")
    client, _ = _github()
    _run(tmp_path, client, confirm=True)
    assert [p.name for p in (tmp_path / "update").iterdir()] == [MAC_FILE]


def test_an_unwritable_folder_is_an_install_error(tmp_path, opened):
    (tmp_path / "update").write_text("a file where the folder should go")
    client, _ = _github()
    with pytest.raises(InstallError, match="Nothing was installed"):
        _run(tmp_path, client, confirm=True)
    assert opened == []


def test_a_release_without_this_platforms_bundle_points_at_the_page(tmp_path, opened):
    client, _ = _github(_release(assets=[_asset(WIN_FILE)]))
    with pytest.raises(InstallError) as exc:
        _run(tmp_path, client, platform="darwin")
    assert MAC_FILE in str(exc.value) and RELEASE_PAGE in str(exc.value)


def test_an_asset_without_a_digest_is_refused_before_downloading(tmp_path, opened):
    client, requests = _github(_release(assets=[_asset(MAC_FILE, digest=None)]))
    with pytest.raises(InstallError, match="sha256"):
        _run(tmp_path, client, confirm=True)
    assert requests == ["api.github.com"] and opened == []


def test_a_github_failure_points_at_the_releases_page(tmp_path, opened):
    client, _ = _github(status=403)
    with pytest.raises(InstallError, match="github.com/alesdev88/Archicad-MCP/releases"):
        _run(tmp_path, client)


def test_a_tag_that_is_not_a_version_is_refused(tmp_path, opened):
    client, _ = _github(_release("nightly"))
    with pytest.raises(InstallError, match="nightly"):
        _run(tmp_path, client)


def test_failing_to_open_keeps_the_file_and_says_how_to_install_it(tmp_path, monkeypatch):
    def refuse(path, platform):
        raise subprocess.CalledProcessError(1, ["open", str(path)])
    monkeypatch.setattr(extension_update, "open_bundle", refuse)
    client, _ = _github()
    result = _run(tmp_path, client, confirm=True)
    path = tmp_path / "update" / MAC_FILE
    assert result["opened"] is False and path.is_file()
    assert str(path) in result["next_step"] and "Settings > Extensions" in result["next_step"]


def test_on_macos_the_file_goes_to_the_system_open_command(tmp_path, monkeypatch):
    seen = []

    def fake_run(args, **kw):
        seen.append((args, kw.get("check")))
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(extension_update.subprocess, "run", fake_run)
    open_bundle(tmp_path / MAC_FILE, "darwin")
    assert seen == [(["open", str(tmp_path / MAC_FILE)], True)]


def test_on_windows_the_file_goes_to_startfile(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(extension_update.os, "startfile", seen.append, raising=False)
    open_bundle(tmp_path / WIN_FILE, "win32")
    assert seen == [str(tmp_path / WIN_FILE)]


async def _names(mode):
    async with Client(build_server(mode=mode)) as client:
        return {t.name for t in await client.list_tools()}


async def test_the_tool_is_registered_only_in_full_mode():
    assert "update_extension" in await _names("full")
    assert "update_extension" not in await _names("verdicts")


async def _call(args):
    async with Client(build_server(mode="full")) as client:
        result = await client.call_tool("update_extension", args)
        return json.loads(result.content[0].text)


@pytest.mark.parametrize("args,confirm", [({}, False), ({"confirm": True}, True)])
async def test_the_tool_only_acts_when_confirmed(monkeypatch, args, confirm):
    seen = {}

    def fake_run(**kw):
        seen.update(kw)
        return {"dry_run": not kw["confirm"]}
    monkeypatch.setattr(extension_update, "run", fake_run)
    assert await _call(args) == {"dry_run": not confirm}
    assert seen == {"confirm": confirm}


async def test_the_tool_returns_a_failure_as_an_error(monkeypatch):
    def fail(**kw):
        raise InstallError("GitHub said no")
    monkeypatch.setattr(extension_update, "run", fail)
    assert await _call({}) == {"error": "GitHub said no"}
