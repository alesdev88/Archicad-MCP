"""Per-object configuration for the GDL pipeline, loaded from a JSON file.

The config keeps asset-specific knowledge (texture files, group naming,
finish variants, stable GUIDs, decimation targets) out of the code. Paths are
resolved relative to the config file. Objects are matched by exact name first,
then by name prefix, so "Mosquito Chair v4" finds the "Mosquito Chair" entry.

Example:

    {
      "objects": {
        "Mosquito Barstool": {
          "guid": "4E501AE2-172D-4F03-B248-C9C2DE3E641E",
          "source": "Mosquito/.../Rex_Kralj_Mosquito_Barstool_Low_mat1.obj",
          "textures": {"logo": "maps/logo.jpg"},
          "variants": [
            {"label": "Natural oak",
             "roles": {"face": "maps/natural oak.jpg",
                       "side": "maps/natural oak side.jpg"}},
            {"label": "Fenix laminate black", "roles": {"face": [0.09, 0.09, 0.10]}}
          ],
          "frame_variants": [
            {"label": "Black steel", "rgb": [0.10, 0.10, 0.11]}
          ],
          "groups": {
            "m1_1": {"label": "Seat and legs", "texture": "face",
                     "rgb": [0.72, 0.58, 0.40]},
            "m1_3": {"label": "Metal braces", "texture": "@frame",
                     "rgb": [0.55, 0.55, 0.58]}
          },
          "decimate": {"Powder Co": 6000, "Laminate": 0}
        }
      }
    }

A variant role maps to either a texture file path (string) or a flat [r, g, b]
color. A group's "texture" is a variant role name, a shared texture key,
"@frame" for the frame-finish dropdown, or omitted for a plain colored group.
In "decimate", the value is the target face count for material names
containing the key; 0 means weld only, which is the right choice for visible
surfaces with gentle curvature (decimation smears their shading).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

GUID_NAMESPACE = uuid.UUID("8d34dc27-2c95-4f5a-9d6e-0b1f2a3c4d5e")

RGB = tuple[float, float, float]


@dataclass
class GroupSpec:
    label: str
    texture: str | None = None   # variant role, shared texture key, or "@frame"
    rgb: RGB = (0.6, 0.6, 0.6)


@dataclass
class ObjectConfig:
    name: str
    guid: str | None = None
    source: Path | None = None
    textures: dict[str, Path] = field(default_factory=dict)
    variants: list[tuple[str, dict[str, Path | RGB]]] = field(default_factory=list)
    frame_variants: list[tuple[str, RGB]] = field(default_factory=list)
    groups: dict[str, GroupSpec] = field(default_factory=dict)
    decimate: dict[str, int] = field(default_factory=dict)

    def resolve_guid(self, name: str) -> str:
        return self.guid or str(uuid.uuid5(GUID_NAMESPACE, name)).upper()

    def group_for(self, material: str, index: int) -> GroupSpec:
        for sub, spec in self.groups.items():
            if sub in material:
                return spec
        return GroupSpec(label=f"Surface {index} ({material})")


def _as_rgb(value) -> RGB:
    r, g, b = value
    return (float(r), float(g), float(b))


def _role_value(value, base: Path) -> Path | RGB:
    if isinstance(value, str):
        return base / value
    return _as_rgb(value)


def load_config(path: str | Path) -> dict[str, ObjectConfig]:
    path = Path(path)
    base = path.parent
    raw = json.loads(path.read_text())
    objects: dict[str, ObjectConfig] = {}
    for name, spec in raw.get("objects", {}).items():
        objects[name] = ObjectConfig(
            name=name,
            guid=spec.get("guid"),
            source=(base / spec["source"]) if spec.get("source") else None,
            textures={k: base / v for k, v in spec.get("textures", {}).items()},
            variants=[(v["label"],
                       {role: _role_value(val, base)
                        for role, val in v.get("roles", {}).items()})
                      for v in spec.get("variants", [])],
            frame_variants=[(v["label"], _as_rgb(v["rgb"]))
                            for v in spec.get("frame_variants", [])],
            groups={sub: GroupSpec(label=g["label"],
                                   texture=g.get("texture"),
                                   rgb=_as_rgb(g.get("rgb", (0.6, 0.6, 0.6))))
                    for sub, g in spec.get("groups", {}).items()},
            decimate={k: int(v) for k, v in spec.get("decimate", {}).items()},
        )
    return objects


def find_object(objects: dict[str, ObjectConfig], name: str) -> ObjectConfig:
    if name in objects:
        return objects[name]
    for key, cfg in objects.items():
        if name.startswith(key):
            return cfg
    return ObjectConfig(name=name)
