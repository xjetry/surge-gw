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
