from surge_gw.providers import (
    extract_provider_entries, convert_domain_provider, convert_ipcidr_provider,
    convert_classical_provider,
)


def test_extract_text_skips_comments_and_blanks():
    raw = "# header\n+.a.com\n\n  b.com  \n# tail\n"
    assert extract_provider_entries(raw, "text") == ["+.a.com", "b.com"]


def test_extract_yaml_reads_payload_list():
    raw = "payload:\n  - '+.a.com'\n  - b.com\n  - 'IP-CIDR,1.2.3.0/24'\n"
    assert extract_provider_entries(raw, "yaml") == [
        "+.a.com", "b.com", "IP-CIDR,1.2.3.0/24",
    ]


def test_extract_yaml_empty_payload_is_empty_list():
    assert extract_provider_entries("payload:\n", "yaml") == []


def test_domain_provider_to_domain_set():
    art = convert_domain_provider(["+.google.com", "example.com", ".cdn.net"])
    assert art.kind == "DOMAIN-SET"
    assert art.lines == [".google.com", "example.com", ".cdn.net"]
    assert art.skipped == []


def test_domain_provider_with_wildcard_becomes_rule_set():
    # 含通配符 → 整表降级 RULE-SET;通配符用 DOMAIN-WILDCARD 表达(剥离前导 +. / .),
    # 纯域名/后缀按类型映射。通配符域名因此仍可命中,而非被丢弃。
    art = convert_domain_provider([
        "+.github.com", ".objectstorage.*.oraclecloud.com", "localhost.*.qq.com", "ok.com",
    ])
    assert art.kind == "RULE-SET"
    assert art.lines == [
        "DOMAIN-SUFFIX,github.com",
        "DOMAIN-WILDCARD,objectstorage.*.oraclecloud.com",
        "DOMAIN-WILDCARD,localhost.*.qq.com",
        "DOMAIN,ok.com",
    ]
    assert art.skipped == []


def test_domain_provider_rule_set_maps_suffix_and_exact():
    # 进入 RULE-SET 模式后,各形态条目的映射:通配符 / 前导点后缀 / 精确 / +. 后缀
    art = convert_domain_provider(["*.wild.com", ".cdn.net", "exact.com", "+.suf.org"])
    assert art.kind == "RULE-SET"
    assert art.lines == [
        "DOMAIN-WILDCARD,*.wild.com",
        "DOMAIN-SUFFIX,cdn.net",
        "DOMAIN,exact.com",
        "DOMAIN-SUFFIX,suf.org",
    ]
    assert art.skipped == []


def test_domain_provider_rule_set_skips_invalid_lines():
    # RULE-SET 同样严格校验:含逗号/空格的条目无法成行,计入 skipped 而非写入非法行
    art = convert_domain_provider(["*.ok.com", "bad,comma.com", "has space.com"])
    assert art.kind == "RULE-SET"
    assert art.lines == ["DOMAIN-WILDCARD,*.ok.com"]
    assert {s.detail for s in art.skipped} == {"bad,comma.com", "has space.com"}
    assert all(s.kind == "ruleset" for s in art.skipped)


def test_ipcidr_provider_to_rule_set():
    art = convert_ipcidr_provider(["1.2.3.0/24", "2001:db8::/32"])
    assert art.kind == "RULE-SET"
    assert art.lines == ["IP-CIDR,1.2.3.0/24", "IP-CIDR6,2001:db8::/32"]


def test_classical_provider_maps_bodies():
    art = convert_classical_provider([
        "DOMAIN-SUFFIX,example.com",
        "IP-CIDR,1.2.3.0/24,no-resolve",
        "DST-PORT,443",
    ])
    assert art.kind == "RULE-SET"
    assert art.lines == [
        "DOMAIN-SUFFIX,example.com",
        "IP-CIDR,1.2.3.0/24,no-resolve",
        "DEST-PORT,443",
    ]


def test_classical_provider_skips_unsupported():
    art = convert_classical_provider(["DOMAIN-REGEX,.*\\.cn", "DOMAIN,ok.com"])
    assert art.lines == ["DOMAIN,ok.com"]
    assert len(art.skipped) == 1
    assert art.skipped[0].kind == "ruleset"
