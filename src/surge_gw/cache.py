from __future__ import annotations

import json
import os
import tempfile
import threading

from surge_gw.refresh_policy import Snapshot

_SURGE = "surge.conf"
_META = "meta.json"
_RULESETS = "rulesets"


class Cache:
    """Current Snapshot atomic reference, thread-safe. Read-heavy, write-rare; lock only guards reference swap."""

    def __init__(self, snapshot: Snapshot) -> None:
        self._lock = threading.Lock()
        self._snapshot = snapshot

    def get(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def swap(self, snapshot: Snapshot) -> None:
        with self._lock:
            self._snapshot = snapshot


def _atomic_write(path: str, data: str) -> None:
    """Atomically write data to path; unlink temp file on failure."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def persist(snapshot: Snapshot, data_dir: str) -> None:
    """Persist last-good: surge text + each ruleset file + meta.json; restart can serve instantly."""
    cache_dir = os.path.join(data_dir, "cache")
    _atomic_write(os.path.join(cache_dir, _SURGE), snapshot.surge_text)
    rs_dir = os.path.join(cache_dir, _RULESETS)
    os.makedirs(rs_dir, exist_ok=True)
    keys = list(snapshot.rulesets)
    for key, text in snapshot.rulesets.items():
        _atomic_write(os.path.join(rs_dir, key), text)
    _atomic_write(os.path.join(cache_dir, _META),
                  json.dumps({"ruleset_keys": keys, "node_port_map": snapshot.node_port_map}))


def load_last_good(data_dir: str) -> Snapshot | None:
    """Load last-good on restart; return None when surge file or meta is missing."""
    cache_dir = os.path.join(data_dir, "cache")
    try:
        with open(os.path.join(cache_dir, _SURGE), encoding="utf-8") as f:
            surge_text = f.read()
        with open(os.path.join(cache_dir, _META), encoding="utf-8") as f:
            meta = json.load(f)
    except FileNotFoundError:
        return None
    rulesets: dict[str, str] = {}
    for key in meta.get("ruleset_keys", []):
        try:
            with open(os.path.join(cache_dir, _RULESETS, key), encoding="utf-8") as f:
                rulesets[key] = f.read()
        except FileNotFoundError:
            continue
    return Snapshot(surge_text=surge_text, rulesets=rulesets,
                    node_port_map={k: int(v) for k, v in meta.get("node_port_map", {}).items()})
