from __future__ import annotations

import re

from archicad_mcp.connection import ArchicadConnection

# On a Teamwork project Tapir's projectLocation is
#   teamwork://<user email>:<JWT refresh token>@<host>/<path>
# The token is a live credential. Returned verbatim it lands in the model's
# context and in the session transcript (observed on a live BIMcloud project
# through get_project_info, and on 19.10.2026 through
# execute_read_api_command), so it is stripped before any tool answers. The
# host and project path are kept, since those are what callers actually use.
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
# Everything between a URL's scheme and the last "@" before its path: the
# user and token, even when the user is an email address. Matched in running
# text as well as in a whole field, because a script's print output or a
# traceback carries the location inside a repr. The scheme length is capped
# so a long run of letters cannot make the search quadratic.
_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.-]{0,31}://)[^\s/?#\"'<>]*@")


def _scrub(value: object) -> object:
    # Both patterns need one of these, and the backstop runs over every string
    # in every tool's reply, so most strings are passed over without a search.
    if not isinstance(value, str) or ("@" not in value and "eyJ" not in value):
        return value
    # The userinfo strip handles the known shape; the JWT regex is the backstop
    # for any other place that turns out to carry a token.
    return _JWT.sub("[redacted-token]", _USERINFO.sub(r"\1", value))


def scrub_credentials(value):
    """value with _scrub applied to every string in it, dict keys included.

    The gateway runs it over each raw answer, and the tool wrapper over each
    reply, so a credential cannot reach the model through a path that forgot
    to shape its output.
    """
    if isinstance(value, dict):
        return {_scrub(key): scrub_credentials(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_credentials(item) for item in value]
    return _scrub(value)


def sanitize_project_info(info: dict) -> dict:
    return {key: _scrub(value) for key, value in info.items()}


def project_identity(conn: ArchicadConnection) -> dict | None:
    """What tells two open projects apart, or None without Tapir.

    The name alone does not: a scratch copy and the live Teamwork project can
    share it. The location is scrubbed like get_project_info's, and its query
    is dropped too, so no credential is kept in memory or compared.
    """
    if not conn.tapir_available():
        return None
    info = conn.tapir("GetProjectInfo")
    location = info.get("projectLocation") or info.get("projectPath")
    if isinstance(location, str):
        location = _scrub(location).split("?", 1)[0]
    return {"name": info.get("projectName"),
            "is_teamwork": bool(info.get("isTeamwork")),
            "location": location}


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
