"""Planned operations waiting for apply_changeset.

In memory only: a server restart loses them, which costs one rerun of the
script. Single use, so an apply cannot be repeated by accident, and short-lived,
so a preview cannot be applied long after the model has moved on.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TTL_SECONDS = 30 * 60
CAPACITY = 20
SAMPLE = 20
# Skipped changes are shown per reason, not one by one: a preview repeating one
# long reason twenty times says nothing a count does not.
SKIP_REASONS = 10
SKIP_SAMPLE = 5


class ChangesetError(Exception):
    """str(exc) is a user-facing, actionable message."""


@dataclass
class Changeset:
    id: str
    port: int
    project: str | None
    operations: list[dict]
    skipped: list[dict]
    created: float
    expires_at: datetime
    applied: bool = False
    # {"name", "is_teamwork", "location"} from project_identity at plan time,
    # or None without Tapir, when apply can only compare names.
    identity: dict | None = None

    def property_writes(self) -> list[dict]:
        return [w for op in self.operations if op["kind"] == "props"
                for w in op["writes"]]

    def command_counts(self) -> dict[str, int]:
        return dict(Counter(op["name"] for op in self.operations
                            if op["kind"] == "command"))


class ChangesetStore:
    def __init__(self, ttl_s: float = TTL_SECONDS, capacity: int = CAPACITY,
                 clock=time.monotonic):
        self._ttl = ttl_s
        self._capacity = capacity
        self._clock = clock
        self._items: dict[str, Changeset] = {}  # insertion order is age
        # fastmcp runs sync tools on a threadpool, so two calls can reach the
        # store at once. Without the lock, expiry can iterate while another
        # call adds, and two applies can both see a changeset as unused.
        self._lock = threading.Lock()

    def add(self, port: int, project: str | None, operations: list[dict],
            skipped: list[dict], identity: dict | None = None) -> Changeset:
        with self._lock:
            self._expire()
            cs = Changeset(id=f"cs-{secrets.token_hex(6)}", port=port,
                           project=project, operations=operations, skipped=skipped,
                           created=self._clock(),
                           expires_at=datetime.now(timezone.utc)
                           + timedelta(seconds=self._ttl),
                           identity=identity)
            self._items[cs.id] = cs
            while len(self._items) > self._capacity:
                self._items.pop(next(iter(self._items)))
            return cs

    def lookup(self, changeset_id: str) -> Changeset:
        with self._lock:
            return self._get(changeset_id)

    def take(self, changeset_id: str) -> Changeset:
        """Look up and mark applied in one step, so only one apply can win."""
        with self._lock:
            cs = self._get(changeset_id)
            cs.applied = True
            return cs

    def _get(self, changeset_id: str) -> Changeset:
        self._expire()
        cs = self._items.get(changeset_id)
        if cs is None:
            raise ChangesetError(
                f"Unknown or expired changeset '{changeset_id}'. Changesets last "
                f"{self._ttl / 60:g} minutes and are lost when the server "
                "restarts; run the script again.")
        if cs.applied:
            raise ChangesetError(
                f"Changeset '{changeset_id}' was already applied. Run the script "
                "again to plan a new one.")
        return cs

    def _expire(self) -> None:
        now = self._clock()
        for key in [k for k, cs in self._items.items() if now - cs.created > self._ttl]:
            del self._items[key]


def group_skipped(skipped: list[dict]) -> tuple[list[dict], int]:
    """Skipped changes grouped by reason, largest first, and how many reasons
    were left out. Each group shows its count and up to SKIP_SAMPLE elements."""
    groups: dict[str, dict] = {}
    for s in skipped:
        group = groups.setdefault(s["reason"], {"reason": s["reason"], "count": 0,
                                                "sample": []})
        group["count"] += 1
        if len(group["sample"]) < SKIP_SAMPLE:
            group["sample"].append({"guid": s["guid"], "property": s["property"]})
    ordered = sorted(groups.values(), key=lambda g: -g["count"])
    return ordered[:SKIP_REASONS], max(len(ordered) - SKIP_REASONS, 0)


def skipped_report(skipped: list[dict]) -> dict:
    """The preview fields describing skipped changes."""
    groups, not_shown = group_skipped(skipped)
    report = {"skipped": len(skipped), "skipped_reasons": groups}
    if not_shown:
        report["skipped_reasons_not_shown"] = not_shown
    return report


def summarize(cs: Changeset) -> dict:
    writes = cs.property_writes()
    return {
        "id": cs.id,
        "expires_at": cs.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "port": cs.port,
        "project": cs.project,
        "teamwork": cs.identity["is_teamwork"] if cs.identity else None,
        "property_writes": len(writes),
        "commands": cs.command_counts(),
        "sample": [{k: w[k] for k in ("guid", "property", "current", "new")}
                   for w in writes[:SAMPLE]],
        **skipped_report(cs.skipped),
    }
