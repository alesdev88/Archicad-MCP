import json

import httpx
import pytest

from archicad_mcp.tapir_install.release import (
    InstallError,
    asset_name,
    fetch_latest,
    load_pinned,
    release_page_url,
)


def test_pinned_release_is_1_7_1_with_all_twelve_addon_assets():
    rel = load_pinned()
    assert rel.version == "1.7.1" and rel.tag == "1.7.1" and rel.pinned
    assert set(rel.assets) == {f"TapirAddOn_AC{v}_{p}" for v in range(25, 31)
                               for p in ("Mac.zip", "Win.apx")}
    a = rel.assets["TapirAddOn_AC30_Mac.zip"]
    assert a.size == 2498912
    assert a.sha256 == "a08e63048c705c3c99a26b51dba1beb485f762c3f4d5f53d75423b29daee254f"
    assert a.url.endswith("/releases/download/1.7.1/TapirAddOn_AC30_Mac.zip")
    assert release_page_url(rel).endswith("/releases/tag/1.7.1")


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
