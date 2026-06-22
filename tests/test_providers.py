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
