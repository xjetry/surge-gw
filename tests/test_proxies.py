from surge_gw.proxies import build_proxy_section


def test_build_proxy_lines():
    names = ["US-1", "JP,2"]
    name_map = {"US-1": "US-1", "JP,2": "JP2"}
    port_map = {"US-1": 1200, "JP,2": 1201}
    lines = build_proxy_section(names, name_map, port_map, host="127.0.0.1")
    assert lines == [
        "US-1 = socks5, 127.0.0.1, 1200, udp-relay=true",
        "JP2 = socks5, 127.0.0.1, 1201, udp-relay=true",
    ]


def test_dropped_node_without_port_is_skipped():
    names = ["a", "b"]
    name_map = {"a": "a", "b": "b"}
    port_map = {"a": 1200}  # b 被丢弃,无端口
    lines = build_proxy_section(names, name_map, port_map)
    assert lines == ["a = socks5, 127.0.0.1, 1200, udp-relay=true"]
