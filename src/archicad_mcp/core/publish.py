from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection


def _publisher_sets(conn: ArchicadConnection) -> list[str] | None:
    """The project's publisher set names; None when this Tapir cannot list them
    (GetPublisherSets came after 1.8.0) or the read fails, so Archicad judges the name."""
    if not conn.tapir_command_available("GetPublisherSets"):
        return None
    try:
        sets = conn.tapir("GetPublisherSets").get("publisherSets")
    except APIErrorBase:
        return None
    return [s.get("name") for s in sets] if isinstance(sets, list) else None


def publish(conn: ArchicadConnection, publisher_set_name: str) -> dict:
    known = _publisher_sets(conn)
    if known is not None and publisher_set_name not in known:
        return {"error": f"This project has no publisher set named '{publisher_set_name}'. "
                         "Nothing was published.",
                "publisher_sets": known}
    conn.tapir("PublishPublisherSet", {"publisherSetName": publisher_set_name})
    return {"published": publisher_set_name}
