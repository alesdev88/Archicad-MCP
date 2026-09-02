"""Deploy compiled .gsm library parts into a running Archicad.

Uses the same connection layer as the MCP server. Note: Tapir 1.5.3 cannot
overwrite an existing embedded-library file (it fails with a misleading
"outputPath is not a valid relative path" error), so iterating on an object
inside one project needs either fresh names or, better, a linked library
folder added once via Library Manager: then rebuilds just overwrite the .gsm
on disk (stable GUID) and reload_libraries updates placed instances in place.
"""

from __future__ import annotations

import base64
from pathlib import Path

from archicad_mcp.connection import ArchicadConnection


def embed_gsm(conn: ArchicadConnection, gsm_path: str | Path,
              output_name: str | None = None) -> dict:
    gsm_path = Path(gsm_path)
    return conn.tapir("AddFilesToEmbeddedLibrary", {
        "files": [{
            "inputPath": str(gsm_path.resolve()),
            "outputPath": output_name or gsm_path.name,
            "type": "Object",
        }]
    })


def embed_textures(conn: ArchicadConnection,
                   files: list[Path]) -> tuple[list[str], list[str]]:
    """Embed texture image files next to the objects that reference them.

    Texture file names carry a content hash, so a per-file failure (Tapir
    cannot overwrite an existing embedded file) means the identical file is
    already there and skipping is correct. Returns (added, skipped) names.
    """
    if not files:
        return [], []
    result = conn.tapir("AddFilesToEmbeddedLibrary", {
        "files": [{"inputPath": str(f.resolve()), "outputPath": f.name}
                  for f in files]
    })
    added, skipped = [], []
    for f, r in zip(files, result.get("executionResults", [])):
        (added if r.get("success") else skipped).append(f.name)
    return added, skipped


def reload_libraries(conn: ArchicadConnection) -> dict:
    return conn.tapir("ReloadLibraries")


def place_object(conn: ArchicadConnection, library_part_name: str,
                 x: float = 0.0, y: float = 0.0, z: float = 0.0) -> str:
    result = conn.tapir("CreateObjects", {
        "objectsData": [{
            "libraryPartName": library_part_name,
            "coordinates": {"x": x, "y": y, "z": z},
        }]
    })
    return result["elements"][0]["elementId"]["guid"]


def preview_png(conn: ArchicadConnection, element_guid: str,
                out_path: str | Path, size: int = 700) -> Path:
    """Render the placed element to a PNG.

    This is the only automated gate that catches defective 3D bodies:
    LP_XMLConverter's interpreter passes scripts whose geometry Archicad
    silently drops, so look at the picture after every deploy.
    """
    result = conn.tapir("GetElementPreviewImage", {
        "elementId": {"guid": element_guid},
        "imageType": "3D",
        "format": "png",
        "width": size,
        "height": size,
    })
    out_path = Path(out_path)
    out_path.write_bytes(base64.b64decode(result["previewImage"]))
    return out_path
