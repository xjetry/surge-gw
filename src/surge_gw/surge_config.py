from __future__ import annotations

_DEFAULT_GENERAL = {
    "loglevel": "notify",
    "skip-proxy": "127.0.0.1, localhost, *.local",
}


def build_surge_config(
    *,
    proxy_lines: list[str],
    group_lines: list[str],
    rule_lines: list[str],
    managed_url: str,
    update_interval: int = 3600,
    general: dict | None = None,
    always_real_ip: list[str] | None = None,
) -> str:
    """组装完整 Surge 配置文本。首行是 MANAGED-CONFIG 头,Surge 据此定期回取。
    always_real_ip(节点服务器域名/通配)非空时写进 [General] always-real-ip。"""
    general = {**_DEFAULT_GENERAL, **(general or {})}
    if always_real_ip:
        general = {**general, "always-real-ip": ", ".join(always_real_ip)}
    out: list[str] = []
    out.append(
        f"#!MANAGED-CONFIG {managed_url} interval={update_interval} strict=false"
    )
    out.append("")
    out.append("[General]")
    out.extend(f"{k} = {v}" for k, v in general.items())
    out.append("")
    out.append("[Proxy]")
    out.extend(proxy_lines)
    out.append("")
    out.append("[Proxy Group]")
    out.extend(group_lines)
    out.append("")
    out.append("[Rule]")
    out.extend(rule_lines)
    out.append("")
    return "\n".join(out)
