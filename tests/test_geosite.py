from surge_gw.geosite import (
    GeoDomain, decode_geosite_dat, TYPE_FULL, TYPE_DOMAIN, TYPE_PLAIN,
    TYPE_REGEX, split_geosite_ref, build_geosite_artifact,
)


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _tag(field_no: int, wire: int) -> bytes:
    return _varint((field_no << 3) | wire)


def _ld(field_no: int, data: bytes) -> bytes:        # length-delimited
    return _tag(field_no, 2) + _varint(len(data)) + data


def _vint(field_no: int, n: int) -> bytes:           # varint
    return _tag(field_no, 0) + _varint(n)


def _str(field_no: int, s: str) -> bytes:
    return _ld(field_no, s.encode("utf-8"))


def _attr(key: str) -> bytes:                        # Domain.attribute(field 3)
    return _ld(3, _str(1, key))


def _domain(dtype: int, value: str, attr_keys=()) -> bytes:   # GeoSite.domain(field 2)
    body = _vint(1, dtype) + _str(2, value) + b"".join(_attr(k) for k in attr_keys)
    return _ld(2, body)


def _geosite(code: str, domains) -> bytes:           # GeoSiteList.entry(field 1)
    return _ld(1, _str(1, code) + b"".join(domains))


def test_split_geosite_ref():
    assert split_geosite_ref("google@cn") == ("GOOGLE", "cn")
    assert split_geosite_ref("google") == ("GOOGLE", None)


def test_geosite_pure_domain_is_domain_set():
    domains = [
        GeoDomain(TYPE_FULL, "a.com", frozenset()),
        GeoDomain(TYPE_DOMAIN, "b.com", frozenset()),
    ]
    art = build_geosite_artifact(domains, None)
    assert art.kind == "DOMAIN-SET"
    assert art.lines == ["a.com", ".b.com"]
    assert art.skipped == []


def test_geosite_with_keyword_is_rule_set():
    domains = [
        GeoDomain(TYPE_DOMAIN, "b.com", frozenset()),
        GeoDomain(TYPE_PLAIN, "ads", frozenset()),
    ]
    art = build_geosite_artifact(domains, None)
    assert art.kind == "RULE-SET"
    assert art.lines == ["DOMAIN-SUFFIX,b.com", "DOMAIN-KEYWORD,ads"]


def test_geosite_attr_filter_and_regex_skipped():
    domains = [
        GeoDomain(TYPE_DOMAIN, "keep.com", frozenset({"cn"})),
        GeoDomain(TYPE_DOMAIN, "drop.com", frozenset()),
        GeoDomain(TYPE_REGEX, ".*ads.*", frozenset({"cn"})),
    ]
    art = build_geosite_artifact(domains, "cn")
    assert art.kind == "DOMAIN-SET"
    assert art.lines == [".keep.com"]
    assert len(art.skipped) == 1
    assert art.skipped[0].kind == "geosite-regexp"


def test_decode_categories_types_and_attrs():
    dat = (
        _geosite("google", [
            _domain(TYPE_FULL, "google.com"),
            _domain(TYPE_DOMAIN, "google.com.hk"),
            _domain(TYPE_PLAIN, "googlevideo", ["ads"]),
            _domain(TYPE_REGEX, "ad[0-9]+\\.google\\.com"),
        ])
        + _geosite("cn", [_domain(TYPE_DOMAIN, "qq.com")])
    )
    cats = decode_geosite_dat(dat)
    assert set(cats) == {"GOOGLE", "CN"}
    g = cats["GOOGLE"]
    assert (g[0].type, g[0].value) == (TYPE_FULL, "google.com")
    assert (g[1].type, g[1].value) == (TYPE_DOMAIN, "google.com.hk")
    assert g[2].attrs == frozenset({"ads"})
    assert (g[3].type, g[3].value) == (TYPE_REGEX, "ad[0-9]+\\.google\\.com")
    assert cats["CN"] == [GeoDomain(TYPE_DOMAIN, "qq.com", frozenset())]


def test_decode_empty_bytes_is_empty_dict():
    assert decode_geosite_dat(b"") == {}
