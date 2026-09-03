"""Deploy helpers against a fake connection: no Archicad required."""

import base64

from archicad_mcp.gdl import deploy as deploy_mod

PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeConn:
    port = 19723

    def __init__(self):
        self.calls = []

    def tapir(self, command, params=None):
        self.calls.append((command, params))
        if command == "GetElementPreviewImage":
            return {"previewImage": base64.b64encode(PNG).decode()}
        if command == "CreateObjects":
            return {"elements": [{"elementId": {"guid": "ABC-123"}}]}
        return {}


def test_preview_image_bytes_decodes_the_payload():
    conn = FakeConn()
    assert deploy_mod.preview_image_bytes(conn, "ABC-123") == PNG


def test_preview_image_bytes_asks_for_the_3d_view():
    conn = FakeConn()
    deploy_mod.preview_image_bytes(conn, "ABC-123", size=512)
    command, params = conn.calls[0]
    assert command == "GetElementPreviewImage"
    assert params["imageType"] == "3D"
    assert params["format"] == "png"
    assert params["width"] == params["height"] == 512
    assert params["elementId"] == {"guid": "ABC-123"}


def test_preview_png_still_writes_a_file(tmp_path):
    conn = FakeConn()
    out = deploy_mod.preview_png(conn, "ABC-123", tmp_path / "check.png")
    assert out.read_bytes() == PNG
