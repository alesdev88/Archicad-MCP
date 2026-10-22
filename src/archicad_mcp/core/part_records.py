"""What the agent has learned about library parts, kept for good.

A bed's footprint and head direction, a door's hinge and swing: an agent works
them out once by placing samples and looking, and the record keeps them across
sessions and projects so the next placement costs nothing. The store is one JSON
file of records keyed by part name. A record also carries the part's main GUID,
so a library that reuses a name for another part does not inherit the old
record (see identity_problem).

The file is only ever replaced whole, from a temporary file in the same folder,
and never touched when it cannot be read: a hand edit gone wrong is reported
with the file's path instead of being overwritten with an empty store.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from archicad_mcp.data_dir import data_root

KINDS = ("object", "lamp", "door", "window")

# Door and window flags: the first letter is `reflected`, the second `oSide`
# (t for true, f for false).
COMBOS = ("ff", "tf", "ft", "tt")

# The direction a part's head points, in its own axes. The head is the end that goes
# against the wall: a bed's pillow end, a chair's or sofa's back.
HEADS = ("+x", "-x", "+y", "-y")

_VERSION = 1

# What a record must say before a part can be placed from it.
_NEEDED = {
    "object": ("footprint", "head", "one_sided"),
    "lamp": ("footprint", "head", "one_sided"),
    "door": ("combos", "leaves"),
    "window": ("combos", "leaves"),
}


class RecordsFileError(Exception):
    """The records file cannot be read or written; the message names it."""


@dataclass
class PartRecord:
    name: str
    guid: str
    kind: str
    learned: str                                  # ISO date
    tapir: str | None = None                      # Tapir version it was learned with
    # Objects and lamps. Each edge is [k, c], at k * size + c from the insertion
    # point in the part's own axes at angle 0 (x edges by A, y edges by B), so
    # the footprint follows whatever size the part is placed at.
    footprint: dict | None = None                 # xMin, xMax, yMin, yMax
    head: str | None = None                       # one of HEADS
    one_sided: bool | None = None
    height: float | None = None
    # Doors and windows: combo -> [hinge, swing], hinge "begin"/"end" and swing
    # "left"/"right" along the wall, seen with refSide false (every sample sends it).
    combos: dict[str, list[str]] | None = None
    leaves: int | None = None
    # State name -> {GDL parameter name: value}, for the sashes that open.
    leaf_states: dict[str, dict] | None = None
    # The `flipped` of the temporary wall the combos were seen in; None when it could
    # not be read, or for a record kept before it was.
    wall_flipped: bool | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}, not {self.kind!r}")

    def missing(self) -> list[str]:
        """The fields placement needs that the agent has not recorded yet."""
        return [field for field in _NEEDED[self.kind] if getattr(self, field) is None]

    def to_json(self) -> dict:
        return asdict(self)     # copies the nested dicts, so a record never aliases the store

    @classmethod
    def from_json(cls, data: dict) -> PartRecord:
        if not isinstance(data, dict):
            raise ValueError("a record is a JSON object")
        absent = [key for key in ("name", "guid", "kind", "learned") if key not in data]
        if absent:
            raise ValueError(f"it lacks {', '.join(absent)}")
        known = {field.name for field in fields(cls)}
        # Fields a newer version added are dropped here, not an error.
        return cls(**{key: value for key, value in data.items() if key in known})


class PartRecords:
    def __init__(self, path: Path | None = None):
        self.path = path or data_root() / "library-parts.json"

    def get(self, name: str) -> PartRecord | None:
        raw = self._load().get(name)
        return None if raw is None else self._record(name, raw)

    def save(self, record: PartRecord) -> None:
        parts = self._load()
        parts[record.name] = record.to_json()
        self._write(parts)

    def forget(self, name: str) -> bool:
        """True when a record was removed, False when none was kept under the name."""
        parts = self._load()
        if name not in parts:
            return False
        del parts[name]
        self._write(parts)
        return True

    def all(self) -> list[PartRecord]:
        return [self._record(name, raw) for name, raw in self._load().items()]

    def _load(self) -> dict:
        """The raw entries, so a save puts back every other part exactly as it was."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:     # ValueError: bad JSON or bad UTF-8
            raise RecordsFileError(f"The learned-parts file {self.path} cannot be read "
                                   f"({error}). It is left as it is.") from error
        if not isinstance(data, dict) or data.get("version") != _VERSION \
                or not isinstance(data.get("parts"), dict):
            raise RecordsFileError(f"The learned-parts file {self.path} is not a version "
                                   f"{_VERSION} records file. It is left as it is.")
        return data["parts"]

    def _record(self, name: str, raw: object) -> PartRecord:
        try:
            return PartRecord.from_json(raw)
        except (TypeError, ValueError) as error:
            raise RecordsFileError(f"The learned-parts file {self.path} holds a record for "
                                   f"{name!r} that cannot be read ({error}). "
                                   "It is left as it is.") from error

    def _write(self, parts: dict) -> None:
        text = json.dumps({"version": _VERSION, "parts": parts}, indent=2,
                          ensure_ascii=False) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=f"{self.path.name}.", suffix=".tmp",
                                                 dir=self.path.parent)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except OSError as error:
            raise RecordsFileError(f"The learned-parts file {self.path} cannot be written "
                                   f"({error}).") from error


def identity_problem(record: PartRecord, guid: str) -> str | None:
    """Why the record cannot be trusted for the part with this main GUID, or None."""
    if record.guid.lower() == guid.lower():
        return None
    return (f"The record for '{record.name}' was learned for another part (GUID "
            f"{record.guid}); this part's GUID is {guid}. Learn this part again to "
            "replace the record.")
