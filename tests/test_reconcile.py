from surge_gw.reconcile import (
    append_update_interval,
    drop_unhosted_ruleset_lines,
    rewrite_ruleset_types,
)


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


def test_append_update_interval_on_hosted_rulesets_both_kinds():
    hosted = {"http://h/ruleset/cn"}
    lines = [
        "RULE-SET,http://h/ruleset/cn,Proxy,no-resolve",   # 自托管 RULE-SET → 追加
        "DOMAIN-SET,http://h/ruleset/cn,Proxy",            # 已改写为 DOMAIN-SET,同一托管 URL → 追加
        "DOMAIN-SUFFIX,x.com,Proxy",                       # 内联规则 → 不动
        "RULE-SET,http://other/ext,Proxy",                 # 非自托管 → 不动
    ]
    out = append_update_interval(lines, hosted, 1800)
    assert out[0] == "RULE-SET,http://h/ruleset/cn,Proxy,no-resolve,update-interval=1800"
    assert out[1] == "DOMAIN-SET,http://h/ruleset/cn,Proxy,update-interval=1800"
    assert out[2] == "DOMAIN-SUFFIX,x.com,Proxy"
    assert out[3] == "RULE-SET,http://other/ext,Proxy"


def test_append_update_interval_disabled_when_non_positive():
    lines = ["RULE-SET,http://h/ruleset/cn,Proxy"]
    assert append_update_interval(lines, {"http://h/ruleset/cn"}, 0) == lines


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
