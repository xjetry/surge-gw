from __future__ import annotations


def build_proxy_section(
    names: list[str],
    name_map: dict[str, str],
    port_map: dict[str, int],
    host: str = "127.0.0.1",
) -> list[str]:
    """每个有端口的节点写成一条 socks5 行;udp-relay 显式开启以支持 UDP。"""
    lines: list[str] = []
    for name in names:
        port = port_map.get(name)
        if port is None:
            continue  # 被端口分配丢弃的节点不进配置
        surge_name = name_map[name]
        lines.append(f"{surge_name} = socks5, {host}, {port}, udp-relay=true")
    return lines
