from surge_gw.assemble import build_config_and_rulesets, Bundle
from surge_gw.urls import RulesetUrls

# Minimal protobuf geosite.dat encoder for test fixtures
def _v(n):
    o = bytearray()
    while True:
        b = n & 0x7F; n >>= 7; o.append(b | (0x80 if n else 0))
        if not n: return bytes(o)
def _tag(f, w): return _v((f << 3) | w)
def _ld(f, d): return _tag(f, 2) + _v(len(d)) + d
def _vi(f, n): return _tag(f, 0) + _v(n)
def _s(f, s): return _ld(f, s.encode())
def _dom(t, val): return _ld(2, _vi(1, t) + _s(2, val))
def _geo(code, doms): return _ld(1, _s(1, code) + b"".join(doms))

UP = {
    "proxies": [{"name": "A"}, {"name": "B"}],
    "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["A", "B"]}],
    "rules": [
        "DOMAIN-SUFFIX,google.com,Proxy",
        "RULE-SET,cnlist,Proxy",
        "GEOSITE,cn,Proxy",
        "MATCH,Proxy",
    ],
    "rule-providers": {
        "cnlist": {"behavior": "domain", "format": "text", "url": "http://h/cn.txt"},
    },
}
URLS = RulesetUrls(host="127.0.0.1", port=8080)


def test_assemble_wires_conversion_and_promotes_domain_set():
    geo = _geo("cn", [_dom(2, "qq.com")])           # 纯域名 → DOMAIN-SET
    def fetch_rs(url):
        assert url == "http://h/cn.txt"
        return "+.cn\nexample.cn\n"
    b = build_config_and_rulesets(
        upstream=UP, node_port_map={"A": 1200, "B": 1201}, provider_members={},
        urls=URLS, host="127.0.0.1", fetch_ruleset_content=fetch_rs,
        geosite_dat=geo, update_interval=3600,
    )
    assert isinstance(b, Bundle)
    # 节点 socks 行
    assert "A = socks5, 127.0.0.1, 1200, udp-relay=true" in b.surge_text
    # 组
    assert "Proxy = select, A, B" in b.surge_text
    # rule-provider 是纯域名 → 反填成 DOMAIN-SET
    assert "DOMAIN-SET,http://127.0.0.1:8080/ruleset/cnlist,Proxy" in b.surge_text
    # geosite 纯域名 → 反填成 DOMAIN-SET
    assert "DOMAIN-SET,http://127.0.0.1:8080/ruleset/geosite-cn,Proxy" in b.surge_text
    # ruleset 内容已托管(端点键)
    assert b.rulesets["cnlist"] == ".cn\nexample.cn\n"
    assert b.rulesets["geosite-cn"] == ".qq.com\n"


def test_prepend_rule_lines_land_at_top_of_rule_section():
    # bypass(节点服务器 DIRECT)规则必须早于 GEOIP/FINAL,故拼在 [Rule] 段最前
    b = build_config_and_rulesets(
        upstream={**UP, "rules": ["MATCH,Proxy"], "rule-providers": {}},
        node_port_map={"A": 1200}, provider_members={}, urls=URLS, host="127.0.0.1",
        fetch_ruleset_content=lambda u: None, geosite_dat=None, update_interval=3600,
        prepend_rule_lines=["IP-CIDR,1.2.3.4/32,DIRECT,no-resolve"])
    rule_body = b.surge_text.split("[Rule]\n", 1)[1]
    assert rule_body.startswith("IP-CIDR,1.2.3.4/32,DIRECT,no-resolve\n")
    assert rule_body.index("IP-CIDR,1.2.3.4/32") < rule_body.index("FINAL,Proxy")


def test_assemble_skips_missing_and_mrs_providers():
    up = {**UP, "rules": ["RULE-SET,missing,Proxy", "RULE-SET,bin,Proxy", "MATCH,Proxy"],
          "rule-providers": {"bin": {"behavior": "domain", "format": "mrs", "url": "http://h/b.mrs"}}}
    b = build_config_and_rulesets(
        upstream=up, node_port_map={"A": 1200}, provider_members={}, urls=URLS,
        host="127.0.0.1", fetch_ruleset_content=lambda u: None, geosite_dat=None, update_interval=3600)
    reasons = " ".join(s.reason for s in b.skipped)
    assert "not defined" in reasons and "mrs" in reasons
    assert "cnlist" not in b.rulesets and "bin" not in b.rulesets


def test_assemble_skips_provider_on_fetch_failure():
    up = {**UP, "rules": ["RULE-SET,cnlist,Proxy", "MATCH,Proxy"]}
    b = build_config_and_rulesets(
        upstream=up, node_port_map={"A": 1200}, provider_members={}, urls=URLS,
        host="127.0.0.1", fetch_ruleset_content=lambda u: None, geosite_dat=None, update_interval=3600)
    skipped_items = [s for s in b.skipped if s.detail == "cnlist"]
    assert len(skipped_items) == 1
    assert "fetch failed" in skipped_items[0].reason
    assert "cnlist" not in b.rulesets


def test_assemble_drops_dangling_lines_for_skipped_refs():
    # 未定义的 rule-provider + 无 geosite.dat 的 GEOSITE:两条自托管引用都会 404,应从 surge 配置丢弃
    up = {**UP, "rules": ["RULE-SET,missing,Proxy", "GEOSITE,cn,Proxy", "MATCH,Proxy"],
          "rule-providers": {}}
    b = build_config_and_rulesets(
        upstream=up, node_port_map={"A": 1200}, provider_members={}, urls=URLS,
        host="127.0.0.1", fetch_ruleset_content=lambda u: None, geosite_dat=None, update_interval=3600)
    assert "ruleset/missing" not in b.surge_text       # dangling rule-provider 行已丢弃
    assert "ruleset/geosite-cn" not in b.surge_text    # dangling geosite 行已丢弃
    assert "FINAL,Proxy" in b.surge_text               # 其余规则不受影响


def test_assemble_skips_unsafe_ruleset_key():
    # rule-provider 名含路径分隔符 → key 会逃出 cache/rulesets/(任意写原语),必须跳过且丢弃其规则行
    up = {**UP, "rules": ["RULE-SET,../evil,Proxy", "MATCH,Proxy"],
          "rule-providers": {"../evil": {"behavior": "domain", "format": "text", "url": "http://h/e.txt"}}}
    b = build_config_and_rulesets(
        upstream=up, node_port_map={"A": 1200}, provider_members={}, urls=URLS,
        host="127.0.0.1", fetch_ruleset_content=lambda u: "+.x\n", geosite_dat=None, update_interval=3600)
    assert "../evil" not in b.rulesets                 # 不进 rulesets dict → cache.persist 不会越界写
    assert "ruleset/../evil" not in b.surge_text       # 其规则行已丢弃
    assert any(s.detail == "../evil" and "unsafe" in s.reason for s in b.skipped)


def test_assemble_skips_provider_on_unknown_behavior():
    up = {**UP, "rules": ["RULE-SET,cnlist,Proxy", "MATCH,Proxy"],
          "rule-providers": {"cnlist": {"behavior": "wat", "format": "text", "url": "http://h/cn.txt"}}}
    def fetch_rs(url):
        return "+.cn\nexample.cn\n"
    b = build_config_and_rulesets(
        upstream=up, node_port_map={"A": 1200}, provider_members={}, urls=URLS,
        host="127.0.0.1", fetch_ruleset_content=fetch_rs, geosite_dat=None, update_interval=3600)
    skipped_items = [s for s in b.skipped if s.detail == "cnlist"]
    assert len(skipped_items) == 1
    assert "unknown behavior" in skipped_items[0].reason
    assert "wat" in skipped_items[0].reason
    assert "cnlist" not in b.rulesets
