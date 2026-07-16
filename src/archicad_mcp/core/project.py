from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection


def get_project_info(conn: ArchicadConnection) -> dict:
    product = conn.official("API.GetProductInfo")
    out: dict = {"archicad_version": product.get("version"),
                 "build": product.get("buildNumber")}
    if not conn.tapir_available():
        out["note"] = ("Install the Tapir add-on for project name, stories, "
                       "hotlinks and geolocation.")
        return out
    out["project"] = conn.tapir("GetProjectInfo")
    out["stories"] = conn.tapir("GetStories").get("stories", [])
    out["hotlinks"] = conn.tapir("GetHotlinks").get("hotlinks", [])
    geo = conn.tapir("GetGeoLocation")
    out["geolocation_present"] = bool(geo.get("projectLocation"))
    return out
