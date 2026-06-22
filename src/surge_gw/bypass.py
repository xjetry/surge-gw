from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable


def build_bypass_rules(
    servers: Iterable[str | None],
    resolve: Callable[[str], Iterable[str]],
) -> list[str]:
    """DIRECT rules pinning every upstream node server to a direct route, so a host-side
    proxy that captures this gateway's egress (e.g. Surge in TUN mode) never routes the
    gateway's own connection to a node server back through a node — which would loop forever.

    A literal-IP server yields an IP-CIDR(6) host route. A domain server is resolved and each
    resolved IP also yields an IP-CIDR(6): the gateway dials the resolved IP, so a TUN-mode
    proxy matches on IP, not hostname — a DOMAIN rule alone would not break the loop. The
    hostname is still emitted as a DOMAIN backup for hostname-carrying captures. Output is
    deduped and sorted so an unchanged node set produces byte-identical config across refreshes
    (no needless MANAGED-CONFIG churn)."""
    ip_lines: set[str] = set()
    domain_lines: set[str] = set()
    for server in servers:
        if not server:
            continue
        literal = _as_ip(server)
        if literal is not None:
            ip_lines.add(_ip_rule(literal))
            continue
        domain_lines.add(f"DOMAIN,{server},DIRECT")
        for addr in resolve(server):
            resolved = _as_ip(addr)
            if resolved is not None:
                ip_lines.add(_ip_rule(resolved))
    return sorted(ip_lines) + sorted(domain_lines)


def _as_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _ip_rule(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if ip.version == 6:
        return f"IP-CIDR6,{ip}/128,DIRECT,no-resolve"
    return f"IP-CIDR,{ip}/32,DIRECT,no-resolve"
