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


def domain_servers(servers: Iterable[str | None]) -> list[str]:
    """Node-server hostnames as Surge [General] always-real-ip patterns: deduped, and any parent
    domain shared by 2+ servers collapsed to *.parent (so a fake-ip/TUN setup resolves node servers
    to their real IP, letting the IP-CIDR DIRECT bypass match — a fake 198.18.x.x would dodge it).
    Grouping is by immediate parent (strip the leftmost label), so *.parent always matches its
    children with a single-label *. A parent that is a bare TLD (no dot) is never wildcarded, so
    distinct registrable domains like a.com / b.net stay separate. Sorted for byte-stable config."""
    hosts = {s for s in servers if s and _as_ip(s) is None}
    by_parent: dict[str, set[str]] = {}
    for host in hosts:
        _, _, parent = host.partition(".")
        by_parent.setdefault(parent, set()).add(host)
    out: set[str] = set()
    for parent, children in by_parent.items():
        if len(children) >= 2 and "." in parent:
            out.add(f"*.{parent}")
        else:
            out.update(children)
    return sorted(out)


def _as_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _ip_rule(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if ip.version == 6:
        return f"IP-CIDR6,{ip}/128,DIRECT,no-resolve"
    return f"IP-CIDR,{ip}/32,DIRECT,no-resolve"
