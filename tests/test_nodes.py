from surge_gw.nodes import select_outbound_nodes, provider_members


def test_select_excludes_groups_and_builtins():
    resp = {"proxies": {
        "DIRECT": {"type": "Direct"},
        "REJECT": {"type": "Reject"},
        "GLOBAL": {"type": "Selector"},
        "Auto": {"type": "URLTest"},
        "LB": {"type": "LoadBalance"},
        "Chain": {"type": "Relay"},
        "ss-1": {"type": "Shadowsocks"},
        "vless-1": {"type": "Vless"},
    }}
    assert select_outbound_nodes(resp) == ["ss-1", "vless-1"]


def test_select_empty_when_no_proxies_key():
    assert select_outbound_nodes({}) == []


def test_select_excludes_passrule():
    # PASS-RULE 的 type 是 "PassRule"(mihomo 特殊出站,非真实节点),不应被钉成 listener
    resp = {"proxies": {"PASS-RULE": {"type": "PassRule"}, "ss-1": {"type": "Shadowsocks"}}}
    assert select_outbound_nodes(resp) == ["ss-1"]


def test_provider_members_extraction():
    resp = {"providers": {
        "prov1": {"name": "prov1", "proxies": [{"name": "A"}, {"name": "B"}]},
        "default": {"name": "default", "proxies": []},
    }}
    assert provider_members(resp) == {"prov1": ["A", "B"], "default": []}
