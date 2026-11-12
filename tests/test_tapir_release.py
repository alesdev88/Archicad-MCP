import hashlib
import json
import ssl
import sys
import zipfile

import httpx
import pytest

from archicad_mcp.tapir_install import release
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


def test_pinned_release_is_2_0_0_with_all_twelve_addon_assets():
    rel = load_pinned()
    assert rel.version == "2.0.0" and rel.tag == "2.0.0" and rel.pinned
    assert set(rel.assets) == {f"TapirAddOn_AC{v}_{p}" for v in range(25, 31)
                               for p in ("Mac.zip", "Win.apx")}
    a = rel.assets["TapirAddOn_AC30_Mac.zip"]
    assert a.size == 7810606
    assert a.sha256 == "3048caa17fbb522d9a64e5f61c27605ad4935ea21a91d70ac7eed3716eb303bd"
    assert a.url.endswith("/releases/download/2.0.0/TapirAddOn_AC30_Mac.zip")
    assert release_page_url(rel).endswith("/releases/tag/2.0.0")


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


# What the client checks a server certificate against. certifi knows the public
# authorities only; an office network that inspects TLS signs with its own, which
# is in the operating system's store and nowhere else.

class _FakeContext:
    def __init__(self, protocol, fail_on_load=False):
        self.protocol, self.loaded, self._fail = protocol, [], fail_on_load

    def load_verify_locations(self, cafile=None, capath=None):
        if self._fail:
            raise FileNotFoundError(cafile)
        self.loaded.append((cafile, capath))


def _fake_truststore(monkeypatch, **kw):
    import types
    made = []

    def context(protocol):
        made.append(_FakeContext(protocol, **kw))
        return made[-1]
    monkeypatch.setitem(sys.modules, "truststore", types.SimpleNamespace(SSLContext=context))
    return made


@pytest.fixture
def no_cert_env(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


def test_certificates_are_checked_against_the_system_store(no_cert_env):
    import truststore
    assert isinstance(release.verify_context(), truststore.SSLContext)


def test_the_client_is_given_that_context(monkeypatch, no_cert_env):
    made = _fake_truststore(monkeypatch)
    seen = {}
    monkeypatch.setattr(release.httpx, "Client", lambda **kw: seen.update(kw) or "client")
    assert release.make_client() == "client"
    assert seen["verify"] is made[0] and made[0].protocol == ssl.PROTOCOL_TLS_CLIENT
    assert seen["follow_redirects"] is True and seen["timeout"] == 60.0


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_the_system_store_stands_alone_where_it_is_complete(monkeypatch, no_cert_env, platform):
    made = _fake_truststore(monkeypatch)
    monkeypatch.setattr(release.sys, "platform", platform)
    assert release.verify_context() is made[0] and made[0].loaded == []


def test_elsewhere_the_bundled_authorities_are_added_to_it(monkeypatch, no_cert_env):
    # On Linux the "system store" is whatever OpenSSL finds, which can be nothing.
    import certifi
    made = _fake_truststore(monkeypatch)
    monkeypatch.setattr(release.sys, "platform", "linux")
    assert release.verify_context() is made[0]
    assert made[0].loaded == [(certifi.where(), None)]


def test_a_certificate_file_named_in_the_environment_is_still_trusted(monkeypatch, no_cert_env):
    made = _fake_truststore(monkeypatch)
    monkeypatch.setattr(release.sys, "platform", "darwin")
    monkeypatch.setenv("SSL_CERT_FILE", "/office/ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/office/certs")
    release.verify_context()
    assert made[0].loaded == [("/office/ca.pem", "/office/certs")]


def test_a_certificate_file_that_cannot_be_read_does_not_cost_the_system_store(
        monkeypatch, no_cert_env):
    made = _fake_truststore(monkeypatch, fail_on_load=True)
    monkeypatch.setattr(release.sys, "platform", "darwin")
    monkeypatch.setenv("SSL_CERT_FILE", "/gone/ca.pem")
    assert release.verify_context() is made[0]


def test_without_a_system_store_the_client_checks_as_it_did(monkeypatch, no_cert_env):
    monkeypatch.setitem(sys.modules, "truststore", None)      # the import fails
    assert release.verify_context() is True

    import types

    def refuse(protocol):
        raise OSError("no store")
    monkeypatch.setitem(sys.modules, "truststore", types.SimpleNamespace(SSLContext=refuse))
    assert release.verify_context() is True


_UNTRUSTED = ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get "
              "local issuer certificate (_ssl.c:1010)")


def _refusing(message):
    def handler(request):
        raise httpx.ConnectError(message)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_an_untrusted_certificate_is_named_as_the_cause():
    with pytest.raises(InstallError) as exc:
        fetch_latest(_refusing(_UNTRUSTED))
    assert release.UNTRUSTED_CERTIFICATE in str(exc.value)


def test_a_download_stopped_by_an_untrusted_certificate_says_so_too(tmp_path):
    with pytest.raises(InstallError) as exc:
        download(_asset_for(b"x"), tmp_path, _refusing(_UNTRUSTED))
    assert release.UNTRUSTED_CERTIFICATE in str(exc.value)


def test_another_network_failure_gets_no_certificate_advice():
    with pytest.raises(InstallError) as exc:
        fetch_latest(_refusing("connection refused"))
    assert release.UNTRUSTED_CERTIFICATE not in str(exc.value)
    assert release.certificate_advice(httpx.ConnectError("connection refused")) == ""


def test_the_system_stores_own_wording_of_an_untrusted_certificate_is_recognised():
    # macOS says it in its own words, without OpenSSL's CERTIFICATE_VERIFY_FAILED;
    # what stays the same is the kind of error underneath.
    def handler(request):
        try:
            raise ssl.SSLCertVerificationError("“api.github.com” certificate is not trusted")
        except ssl.SSLError as cause:
            raise httpx.ConnectError(str(cause)) from cause
    with pytest.raises(InstallError) as exc:
        fetch_latest(httpx.Client(transport=httpx.MockTransport(handler)))
    assert release.UNTRUSTED_CERTIFICATE in str(exc.value)
