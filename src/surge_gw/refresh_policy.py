from __future__ import annotations

from dataclasses import dataclass, field

from surge_gw import surge_config
from surge_gw.models import SkippedItem


def should_refresh(now: float, last_started: float | None, in_flight: bool, min_interval: float) -> bool:
    """Single-flight (reject in-flight) + debounce (reject if less than min_interval since last_started)."""
    if in_flight:
        return False
    if last_started is None:
        return True
    return (now - last_started) >= min_interval


@dataclass
class Snapshot:
    """Complete atomic unit of a successful conversion; used for serve-from-cache."""
    surge_text: str
    rulesets: dict[str, str] = field(default_factory=dict)
    node_port_map: dict[str, int] = field(default_factory=dict)
    skipped: list[SkippedItem] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def placeholder_surge(managed_url: str, update_interval: int) -> str:
    """Minimal valid config for cold-start when not yet ready; Surge will re-fetch at update_interval."""
    return surge_config.build_surge_config(
        proxy_lines=[], group_lines=[], rule_lines=["FINAL,DIRECT"],
        managed_url=managed_url, update_interval=update_interval,
    )
