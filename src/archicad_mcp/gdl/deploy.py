"""Deploy compiled .gsm library parts into a running Archicad.

Uses the same connection layer as the MCP server. Note: unless told to replace it,
Tapir refuses a name already in the embedded library (with a misleading "outputPath
is not a valid relative path" error), so iterating on an object inside one project
needs replacing, fresh names or, better, a linked library folder: then rebuilds just
overwrite the .gsm on disk (stable GUID) and reload_libraries updates placed instances
in place.
"""

from __future__ import annotations

import base64
from pathlib import Path

from multiconn_archicad.errors import TapirCommandError

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.gdl.toolchain import ToolchainError


def embed_gsm(conn: ArchicadConnection, gsm_path: str | Path,
              output_name: str | None = None, replace: bool = False) -> dict:
    """`replace` deletes an embedded file of that name first, which changes the part for
    every instance placed from it; otherwise Tapir refuses a name already there."""
    gsm_path = Path(gsm_path)
    params: dict = {
        "files": [{
            "inputPath": str(gsm_path.resolve()),
            "outputPath": output_name or gsm_path.name,
            "type": "Object",
        }]
    }
    if replace:
        params["overwriteExisting"] = True
    return conn.tapir("AddFilesToEmbeddedLibrary", params)


def embed_textures(conn: ArchicadConnection,
                   files: list[Path]) -> tuple[list[str], list[str]]:
    """Embed texture image files next to the objects that reference them.

    Texture file names carry a content hash, so a per-file failure (Tapir
    refuses a name already embedded unless told to replace it) means the identical
    file is already there and skipping is correct. Returns (added, skipped) names.
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


def link_library_folder(conn: ArchicadConnection, folder: Path) -> dict:
    """Adds `folder` to the project's library list, unless it is loaded already. A
    refusal raises: the part would not load, and the deploy would fail on a worse message.
    """
    for library in conn.tapir("GetLibraries").get("libraries", []):
        path = library.get("path")
        if path and Path(path).expanduser().resolve() == folder:
            return {"path": str(folder), "added": False}
    results = conn.tapir("ModifyLibraryList", {"add": [{"path": str(folder)}]}).get("addResults")
    result = results[0] if results else {}
    if not result.get("success"):
        detail = (result.get("error") or {}).get("message", "no reason given")
        raise ToolchainError(f"Archicad did not add {folder} to the library list: {detail}")
    return {"path": str(folder), "added": True}


def check_script(conn: ArchicadConnection, library_part_name: str) -> dict | None:
    """Runs the part's master and 3D scripts with its default parameters; None when this
    Tapir cannot (InterpretGDLScript3D came after 1.8.0). An interpreter that stops comes
    as an error, and Tapir gives no message of the GDL's own or line number."""
    if not conn.tapir_command_available("InterpretGDLScript3D"):
        return None
    try:
        answer = conn.tapir("InterpretGDLScript3D", {"libraryPartName": library_part_name})
    except TapirCommandError as exc:
        return {"ran": False, "message": exc.message}
    check = {"ran": True, "element_count": answer.get("elementCount", 0)}
    if "boundingBox3D" in answer:
        check["bounding_box"] = answer["boundingBox3D"]
    return check


def place_object(conn: ArchicadConnection, library_part_name: str,
                 x: float = 0.0, y: float = 0.0, z: float = 0.0) -> str:
    result = conn.tapir("CreateObjects", {
        "objectsData": [{
            "libraryPartName": library_part_name,
            "coordinates": {"x": x, "y": y, "z": z},
        }]
    })
    return result["elements"][0]["elementId"]["guid"]


def preview_image_bytes(conn: ArchicadConnection, element_guid: str,
                        size: int = 700) -> bytes:
    """Render the placed element and return the PNG bytes.

    This is the only automated gate that catches defective 3D bodies:
    LP_XMLConverter's interpreter passes scripts whose geometry Archicad
    silently drops, so something has to actually look at the picture.
    """
    result = conn.tapir("GetElementPreviewImage", {
        "elementId": {"guid": element_guid},
        "imageType": "3D",
        "format": "png",
        "width": size,
        "height": size,
    })
    return base64.b64decode(result["previewImage"])


def preview_png(conn: ArchicadConnection, element_guid: str,
                out_path: str | Path, size: int = 700) -> Path:
    """preview_image_bytes, written to a file. Used by the CLI."""
    out_path = Path(out_path)
    out_path.write_bytes(preview_image_bytes(conn, element_guid, size))
    return out_path
