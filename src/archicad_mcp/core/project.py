from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from archicad_mcp.connection import ArchicadConnection

# On a Teamwork project Tapir's projectLocation is
#   teamwork://<user>:<JWT refresh token>@<host>/<path>
# The token is a live credential. Returned verbatim it lands in the model's
# context and in the session transcript (observed twice on a live BIMcloud
# project), so it is stripped before the tool answers. The host and project
# path are kept -- those are what callers actually use.
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")


def _strip_credentials(value: str) -> str:
    parts = urlsplit(value)
    if "@" not in parts.netloc:
        return value
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _scrub(value: object) -> object:
    if not isinstance(value, str):
        return value
    # The netloc strip handles the known shape; the regex is the backstop for
    # any other field that turns out to carry a token.
    return _JWT.sub("[redacted-token]", _strip_credentials(value))


def sanitize_project_info(info: dict) -> dict:
    return {key: _scrub(value) for key, value in info.items()}


def get_project_info(conn: ArchicadConnection) -> dict:
    product = conn.official("API.GetProductInfo")
    out: dict = {"archicad_version": product.get("version"),
                 "build": product.get("buildNumber")}
    if not conn.tapir_available():
        out["note"] = ("Install the Tapir add-on for project name, stories, "
                       "hotlinks and geolocation.")
        return out
    out["project"] = sanitize_project_info(conn.tapir("GetProjectInfo"))
    out["stories"] = conn.tapir("GetStories").get("stories", [])
    out["hotlinks"] = conn.tapir("GetHotlinks").get("hotlinks", [])
    geo = conn.tapir("GetGeoLocation")
    out["geolocation_present"] = bool(geo.get("projectLocation"))
    return out
