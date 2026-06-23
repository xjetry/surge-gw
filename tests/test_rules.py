from surge_gw.rules import convert_rules

PM = {"Proxy": "Proxy", "节点,A": "节点A"}


def url_rs(name): return f"http://h/ruleset/{name}"
def url_geo(cat): return f"http://h/ruleset/geosite-{cat}"


def conv(rules):
    return convert_rules(rules, PM, url_rs, url_geo)


def test_passthrough_and_rename_types():
    r = conv([
        "DOMAIN-SUFFIX,google.com,Proxy",
        "DST-PORT,443,Proxy",
        "SRC-IP-CIDR,192.168.0.0/16,Proxy",
        "NETWORK,udp,Proxy",
        "MATCH,Proxy",
    ])
    assert r.lines == [
        "DOMAIN-SUFFIX,google.com,Proxy",
        "DEST-PORT,443,Proxy",
        "SRC-IP,192.168.0.0/16,Proxy",
        "PROTOCOL,UDP,Proxy",
        "FINAL,Proxy",
    ]


def test_no_resolve_preserved():
    r = conv(["IP-CIDR,10.0.0.0/8,Proxy,no-resolve"])
    assert r.lines == ["IP-CIDR,10.0.0.0/8,Proxy,no-resolve"]


def test_policy_name_sanitized_and_builtins():
    r = conv(["DOMAIN,a.com,节点,A", "DOMAIN,b.com,REJECT"])
    assert r.lines == ["DOMAIN,a.com,节点A", "DOMAIN,b.com,REJECT"]


def test_rule_set_emits_reference_and_records():
    r = conv(["RULE-SET,mylist,Proxy"])
    assert r.lines == ["RULE-SET,http://h/ruleset/mylist,Proxy"]
    assert r.rule_providers == {"mylist"}


def test_geosite_emits_reference_and_records():
    r = conv(["GEOSITE,google@cn,Proxy"])
    assert r.lines == ["RULE-SET,http://h/ruleset/geosite-google@cn,Proxy"]
    assert r.geosites == {"google@cn"}


def test_logical_rule_converted_recursively():
    r = conv(["AND,((DOMAIN,a.com),(NETWORK,udp)),Proxy"])
    assert r.lines == ["AND,((DOMAIN,a.com),(PROTOCOL,UDP)),Proxy"]


def test_logical_rule_skipped_if_subrule_unsupported():
    r = conv(["AND,((DOMAIN,a.com),(DSCP,1)),Proxy"])
    assert r.lines == []
    assert any(s.kind == "rule" for s in r.skipped)


def test_unsupported_type_skipped():
    r = conv(["DOMAIN-REGEX,.*\\.cn,Proxy", "DSCP,4,Proxy"])
    assert r.lines == []
    assert len(r.skipped) == 2


def test_logical_subrule_preserves_options():
    r = conv(["AND,((IP-CIDR,1.2.3.0/24,no-resolve),(NETWORK,udp)),Proxy"])
    assert r.lines == ["AND,((IP-CIDR,1.2.3.0/24,no-resolve),(PROTOCOL,UDP)),Proxy"]


def test_convert_rule_body_maps_and_rejects():
    from surge_gw.rules import convert_rule_body
    assert convert_rule_body("DST-PORT,443") == "DEST-PORT,443"
    assert convert_rule_body("IP-CIDR,1.2.3.0/24,no-resolve") == "IP-CIDR,1.2.3.0/24,no-resolve"
    assert convert_rule_body("DOMAIN-REGEX,.*\\.cn") is None


def test_ip_cidr_gets_no_resolve_appended():
    # 顶层 IP 段规则未带 no-resolve → 自动补,避免为匹配 IP 规则而对域名触发 DNS 解析
    r = conv(["IP-CIDR,1.2.3.0/24,Proxy", "IP-CIDR6,2001:db8::/32,Proxy"])
    assert r.lines == [
        "IP-CIDR,1.2.3.0/24,Proxy,no-resolve",
        "IP-CIDR6,2001:db8::/32,Proxy,no-resolve",
    ]


def test_geoip_and_ipasn_not_auto_resolved():
    # 只补 IP-CIDR/IP-CIDR6;GEOIP/IP-ASN 不动
    r = conv(["GEOIP,cn,DIRECT", "IP-ASN,4538,DIRECT"])
    assert r.lines == ["GEOIP,cn,DIRECT", "IP-ASN,4538,DIRECT"]


def test_logical_subrule_ip_cidr_not_auto_resolved():
    # 逻辑子规则里的 IP-CIDR(未带 no-resolve)保持现状,不补
    r = conv(["AND,((IP-CIDR,1.2.3.0/24),(NETWORK,udp)),Proxy"])
    assert r.lines == ["AND,((IP-CIDR,1.2.3.0/24),(PROTOCOL,UDP)),Proxy"]


def test_convert_rule_body_ip_cidr_not_auto_resolved():
    # classical provider 体走 convert_rule_body(is_subrule 路径),不补
    from surge_gw.rules import convert_rule_body
    assert convert_rule_body("IP-CIDR,1.2.3.0/24") == "IP-CIDR,1.2.3.0/24"
