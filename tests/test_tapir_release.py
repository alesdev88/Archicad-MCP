import hashlib
import json
import zipfile

import httpx
import pytest

from archicad_mcp.tapir_install.release import (
    Asset,
    InstallError,
    asset_name,
    check_mac_zip,
    download,
    fetch_latest,
    load_pinned,
    release_page_url,
    verify,
)


def test_pinned_release_is_1_7_2_with_all_twelve_addon_assets():
    rel = load_pinned()
    assert rel.version == "1.7.2" and rel.tag == "1.7.2" and rel.pinned
    assert set(rel.assets) == {f"TapirAddOn_AC{v}_{p}" for v in range(25, 31)
                               for p in ("Mac.zip", "Win.apx")}
    a = rel.assets["TapirAddOn_AC30_Mac.zip"]
    assert a.size == 2988141
    assert a.sha256 == "b70e53adffd24857d5822550b01ca25fbc783c17390f52cbfa88bf523b0faf91"
    assert a.url.endswith("/releases/download/1.7.2/TapirAddOn_AC30_Mac.zip")
    assert release_page_url(rel).endswith("/releases/tag/1.7.2")


def test_asset_name_per_platform():
    assert asset_name(30, "darwin") == "TapirAddOn_AC30_Mac.zip"
    assert asset_name(29, "win32") == "TapirAddOn_AC29_Win.apx"
    with pytest.raises(ValueError):
        asset_name(29, "linux")


def _latest_client(assets: list[dict]) -> httpx.Client:
    body = {"tag_name": "1.8.0", "assets": assets}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/releases/latest")
        return httpx.Response(200, text=json.dumps(body))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_latest_refuses_assets_without_digest():
    client = _latest_client([{"name": "TapirAddOn_AC30_Mac.zip", "size": 10,
                              "browser_download_url": "https://x/a.zip"}])
    with pytest.raises(InstallError) as exc:
        fetch_latest(client)
    assert "--latest" in str(exc.value) and "pinned" in str(exc.value)


def test_fetch_latest_reads_digests():
    client = _latest_client([
        {"name": "TapirAddOn_AC30_Mac.zip", "size": 10, "digest": "sha256:" + "ab" * 32,
         "browser_download_url": "https://x/TapirAddOn_AC30_Mac.zip"},
        {"name": "TapirInstaller_Mac.zip", "size": 99, "digest": "sha256:" + "cd" * 32,
         "browser_download_url": "https://x/TapirInstaller_Mac.zip"},
    ])
    rel = fetch_latest(client)
    assert rel.version == "1.8.0" and not rel.pinned
    assert set(rel.assets) == {"TapirAddOn_AC30_Mac.zip"}
    assert rel.assets["TapirAddOn_AC30_Mac.zip"].sha256 == "ab" * 32
    assert rel.assets["TapirAddOn_AC30_Mac.zip"].url == "https://x/TapirAddOn_AC30_Mac.zip"


# ---------- download and check ----------

def _asset_for(data: bytes, name: str = "TapirAddOn_AC30_Win.apx") -> Asset:
    return Asset(name, len(data), hashlib.sha256(data).hexdigest(), f"https://x/{name}")


def _serving(data: bytes, status: int = 200) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, content=data)))


def test_download_and_verify_accepts_matching_file(tmp_path):
    data = b"MZ" + b"\x00" * 100
    asset = _asset_for(data)
    path = download(asset, tmp_path, _serving(data))
    assert path == tmp_path / asset.name and path.read_bytes() == data
    verify(path, asset)


def test_verify_refuses_wrong_sha256(tmp_path):
    asset = _asset_for(b"MZ good")
    path = tmp_path / asset.name
    path.write_bytes(b"MZ evil")
    with pytest.raises(InstallError) as exc:
        verify(path, asset)
    assert asset.sha256 in str(exc.value)
    assert hashlib.sha256(b"MZ evil").hexdigest() in str(exc.value)


def test_download_refuses_more_bytes_than_expected(tmp_path):
    asset = _asset_for(b"MZ12")
    with pytest.raises(InstallError):
        download(asset, tmp_path, _serving(b"MZ12" + b"x" * 5000))


def test_download_refuses_html_page(tmp_path):
    # A proxy or login page answering 200 with HTML of a plausible size.
    real = b"MZ" + b"\x00" * 98
    page = b"<html>" + b" " * 87 + b"</html>"
    asset = _asset_for(real)
    path = download(asset, tmp_path, _serving(page))
    with pytest.raises(InstallError):
        verify(path, asset)


def test_download_refuses_http_error(tmp_path):
    with pytest.raises(InstallError) as exc:
        download(_asset_for(b"x"), tmp_path, _serving(b"nope", status=404))
    assert "404" in str(exc.value)


def _zip(tmp_path, members: dict[str, bytes]):
    path = tmp_path / "TapirAddOn_AC30_Mac.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_check_mac_zip_accepts_one_bundle(tmp_path):
    path = _zip(tmp_path, {"TapirAddOn_AC30_Mac.bundle/Contents/Info.plist": b"x",
                           "TapirAddOn_AC30_Mac.bundle/Contents/MacOS/TapirAddOn_AC30_Mac": b"y"})
    assert check_mac_zip(path, 30) == "TapirAddOn_AC30_Mac.bundle"


def test_check_mac_zip_refuses_dotdot(tmp_path):
    path = _zip(tmp_path, {"TapirAddOn_AC30_Mac.bundle/../../evil": b"x"})
    with pytest.raises(InstallError):
        check_mac_zip(path, 30)


def test_check_mac_zip_refuses_absolute_path(tmp_path):
    path = _zip(tmp_path, {"/tmp/TapirAddOn_AC30_Mac.bundle/x": b"x"})
    with pytest.raises(InstallError):
        check_mac_zip(path, 30)


def test_check_mac_zip_refuses_two_top_level_entries(tmp_path):
    path = _zip(tmp_path, {"TapirAddOn_AC30_Mac.bundle/a": b"x", "other/b": b"y"})
    with pytest.raises(InstallError):
        check_mac_zip(path, 30)


def test_check_mac_zip_refuses_wrong_version_bundle(tmp_path):
    path = _zip(tmp_path, {"TapirAddOn_AC29_Mac.bundle/a": b"x"})
    with pytest.raises(InstallError):
        check_mac_zip(path, 30)


def test_check_mac_zip_refuses_a_file_that_is_not_a_zip(tmp_path):
    path = tmp_path / "TapirAddOn_AC30_Mac.zip"
    path.write_bytes(b"<html></html>")
    with pytest.raises(InstallError):
        check_mac_zip(path, 30)
