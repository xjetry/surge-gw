from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    subscription_url: str
    advertise_host: str
    http_bind: str
    http_port: int
    port_base: int
    max_nodes: int
    refresh_interval: int
    min_refresh_interval: int
    geosite_ttl: int
    geosite_url: str | None
    surge_update_interval: int
    ruleset_update_interval: int
    ruleset_live_timeout: int
    emit_domain_set: bool
    mihomo_bin: str
    data_dir: str


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    return int(raw) if raw not in (None, "") else default


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def from_env(env: Mapping[str, str]) -> Config:
    """Parse config from environment. SUBSCRIPTION_URL is required; geosite_url may be empty."""
    url = env.get("SUBSCRIPTION_URL")
    if not url:
        raise ValueError("SUBSCRIPTION_URL is required")
    return Config(
        subscription_url=url,
        advertise_host=env.get("ADVERTISE_HOST") or "127.0.0.1",
        http_bind=env.get("HTTP_BIND") or "127.0.0.1",
        http_port=_int(env, "HTTP_PORT", 8080),
        port_base=_int(env, "PORT_BASE", 1200),
        max_nodes=_int(env, "MAX_NODES", 100),
        refresh_interval=_int(env, "REFRESH_INTERVAL", 21600),
        min_refresh_interval=_int(env, "MIN_REFRESH_INTERVAL", 300),
        geosite_ttl=_int(env, "GEOSITE_TTL", 86400),
        geosite_url=env.get("GEOSITE_URL") or None,
        surge_update_interval=_int(env, "SURGE_UPDATE_INTERVAL", 3600),
        ruleset_update_interval=_int(env, "RULESET_UPDATE_INTERVAL", 86400),
        ruleset_live_timeout=_int(env, "RULESET_LIVE_TIMEOUT", 6),
        emit_domain_set=_bool(env, "EMIT_DOMAIN_SET", False),
        mihomo_bin=env.get("MIHOMO_BIN") or "mihomo",
        data_dir=env.get("DATA_DIR") or "./data",
    )
