from surge_gw.bypass import build_bypass_rules, domain_servers


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


def test_domain_servers_keeps_singletons_and_drops_ips():
    # 单一子域名保留完整域名;字面 IP / 空 / None 跳过
    got = domain_servers(["node.example.com", "43.142.0.36", "2606:4700::1111", "", None])
    assert got == ["node.example.com"]


def test_domain_servers_collapses_shared_parent_to_wildcard():
    # 同一父域名下的多个子域名 → *.parent(单标签 * 必然命中各子域名)
    got = domain_servers(["hk1.example.com", "hk2.example.com", "jp.example.com"])
    assert got == ["*.example.com"]


def test_domain_servers_does_not_wildcard_bare_tld_parent():
    # 父为裸 TLD 的不同注册域名不能塌成 *.com / *.net
    assert domain_servers(["a.com", "b.net", "c.com"]) == ["a.com", "b.net", "c.com"]


def test_domain_servers_mixes_wildcard_and_singletons():
    got = domain_servers(["a.grp.example.com", "b.grp.example.com", "solo.other.org"])
    assert got == ["*.grp.example.com", "solo.other.org"]


def test_domain_servers_empty_when_all_literal_ips():
    assert domain_servers(["1.2.3.4", "2606:4700::1111"]) == []
