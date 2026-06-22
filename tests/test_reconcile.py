from surge_gw.reconcile import drop_unhosted_ruleset_lines, rewrite_ruleset_types


def test_rewrite_promotes_matching_urls():
    lines = [
        "RULE-SET,http://h/ruleset/cn?token=t,Proxy",
        "RULE-SET,http://h/ruleset/geosite-google?token=t,Proxy",
        "DOMAIN,x.com,Proxy",
    ]
    out = rewrite_ruleset_types(lines, {"http://h/ruleset/cn?token=t"})
    assert out == [
        "DOMAIN-SET,http://h/ruleset/cn?token=t,Proxy",
        "RULE-SET,http://h/ruleset/geosite-google?token=t,Proxy",
        "DOMAIN,x.com,Proxy",
    ]


def test_rewrite_noop_when_no_match():
    lines = ["RULE-SET,http://h/ruleset/cn,Proxy"]
    assert rewrite_ruleset_types(lines, set()) == lines


def test_drop_unhosted_ruleset_lines():
    lines = [
        "RULE-SET,http://h/ruleset/cn?token=t,Proxy",        # 已托管 → 保留
        "RULE-SET,http://h/ruleset/missing?token=t,Proxy",   # 未托管 → 丢弃(否则 Surge 拉 404)
        "DOMAIN,x.com,Proxy",                                # 非 ruleset 行 → 保留
    ]
    out = drop_unhosted_ruleset_lines(lines, {"http://h/ruleset/cn?token=t"})
    assert out == [
        "RULE-SET,http://h/ruleset/cn?token=t,Proxy",
        "DOMAIN,x.com,Proxy",
    ]
