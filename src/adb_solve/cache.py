"""On-disk metrics cache keyed by the exact simulation input.

The key covers the netlist text, the rendered deck text and the image id, so a
cache hit can never come from a different PDK or a different corner.  Entries
are one JSON file each: cheap to write, trivially inspectable, and safe to
delete at any time.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, cast

DEFAULT_ROOT = Path(".adb-cache")


def hash_inputs(*parts: bytes | str) -> str:
    """Stable sha256 over a list of byte/text parts."""
    digest = hashlib.sha256()
    for part in parts:
        payload = part.encode() if isinstance(part, str) else part
        digest.update(len(payload).to_bytes(8, "little"))
        digest.update(payload)
    return digest.hexdigest()


class Cache:
    """Namespaced JSON cache; disabled instances still validate their key."""

    def __init__(self, root: Path | str = DEFAULT_ROOT, namespace: str = "", enabled: bool = True):
        self.root = Path(root)
        self.namespace = namespace
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        shard = key[:2]
        suffix = f"{self.namespace}-{key}" if self.namespace else key
        return self.root / shard / f"{suffix}.json"

    def get(self, key: str) -> dict[str, object] | None:
        if not self.enabled:
            return None
        path = self._path(key)
        try:
            payload = cast(dict[str, object], json.loads(path.read_text()))
        except (OSError, ValueError):
            self.misses += 1
            return None
        self.hits += 1
        return payload

    def put(self, key: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        )
        try:
            with handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
            os.replace(handle.name, path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

    def stats(self) -> dict[str, object]:
        total = self.hits + self.misses
        return {
            "enabled": self.enabled,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (self.hits / total) if total else None,
            "root": str(self.root),
        }
