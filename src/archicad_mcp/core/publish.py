from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection


def publish(conn: ArchicadConnection, publisher_set_name: str) -> dict:
    conn.tapir("PublishPublisherSet", {"publisherSetName": publisher_set_name})
    return {"published": publisher_set_name}
