from surge_gw.bypass import build_bypass_rules


def test_literal_ipv4_server_becomes_host_direct_route():
    assert build_bypass_rules(["43.142.0.36"], resolve=lambda h: []) == [
        "IP-CIDR,43.142.0.36/32,DIRECT,no-resolve",
    ]


def test_literal_ipv6_server_becomes_host_direct_route():
    assert build_bypass_rules(["2606:4700::1111"], resolve=lambda h: []) == [
        "IP-CIDR6,2606:4700::1111/128,DIRECT,no-resolve",
    ]


def test_domain_server_emits_resolved_ip_rules_plus_domain_backup():
    # 网关实连的是解析后的 IP,host 侧代理按 IP 匹配,故 IP-CIDR 是断环主力;
    # 域名行作为 hostname 携带场景的兜底。
    out = build_bypass_rules(["node.example.com"], resolve=lambda h: ["1.2.3.4", "5.6.7.8"])
    assert out == [
        "IP-CIDR,1.2.3.4/32,DIRECT,no-resolve",
        "IP-CIDR,5.6.7.8/32,DIRECT,no-resolve",
        "DOMAIN,node.example.com,DIRECT",
    ]


def test_dedupes_shared_ip_across_servers_and_is_stable():
    out = build_bypass_rules(["a.example", "b.example"], resolve=lambda h: ["9.9.9.9"])
    assert out == [
        "IP-CIDR,9.9.9.9/32,DIRECT,no-resolve",
        "DOMAIN,a.example,DIRECT",
        "DOMAIN,b.example,DIRECT",
    ]


def test_skips_empty_servers_and_unresolvable_domains():
    out = build_bypass_rules(["", None, "bad.host"], resolve=lambda h: [])
    assert out == ["DOMAIN,bad.host,DIRECT"]
