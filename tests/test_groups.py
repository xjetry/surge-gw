from surge_gw.groups import convert_groups


def test_select_group_maps_members():
    groups = [{"name": "Proxy", "type": "select", "proxies": ["A", "B", "DIRECT"]}]
    name_map = {"A": "A", "B": "B", "Proxy": "Proxy"}
    lines, skipped = convert_groups(groups, name_map, {"A", "B"}, {})
    assert lines == ["Proxy = select, A, B, DIRECT"]
    assert skipped == []


def test_url_test_group_carries_params():
    groups = [{
        "name": "Auto", "type": "url-test", "proxies": ["A"],
        "url": "http://x/generate_204", "interval": 300, "tolerance": 50,
    }]
    name_map = {"A": "A", "Auto": "Auto"}
    lines, _ = convert_groups(groups, name_map, {"A"}, {})
    assert lines == [
        "Auto = url-test, A, url=http://x/generate_204, interval=300, tolerance=50"
    ]


def test_use_provider_expands_members():
    groups = [{"name": "G", "type": "select", "use": ["prov1"]}]
    name_map = {"P1": "P1", "P2": "P2", "G": "G"}
    lines, _ = convert_groups(groups, name_map, {"P1", "P2"}, {"prov1": ["P1", "P2"]})
    assert lines == ["G = select, P1, P2"]


def test_load_balance_degrades_to_select():
    groups = [{"name": "LB", "type": "load-balance", "proxies": ["A"]}]
    name_map = {"A": "A", "LB": "LB"}
    lines, skipped = convert_groups(groups, name_map, {"A"}, {})
    assert lines == ["LB = select, A"]
    assert any(s.kind == "group" and "load-balance" in s.reason for s in skipped)


def test_empty_group_gets_direct_fallback():
    groups = [{"name": "G", "type": "select", "proxies": ["GHOST"]}]
    name_map = {"G": "G"}
    lines, _ = convert_groups(groups, name_map, set(), {})
    assert lines == ["G = select, DIRECT"]


def test_filter_keeps_only_matching_use_members():
    groups = [{"name": "HK", "type": "select", "use": ["p"], "filter": "🇭🇰"}]
    name_map = {"🇭🇰a": "🇭🇰a", "🇯🇵b": "🇯🇵b", "HK": "HK"}
    lines, _ = convert_groups(groups, name_map, {"🇭🇰a", "🇯🇵b"}, {"p": ["🇭🇰a", "🇯🇵b"]})
    assert lines == ["HK = select, 🇭🇰a"]


def test_exclude_filter_drops_matching_use_members():
    groups = [{"name": "NS", "type": "select", "use": ["p"], "exclude-filter": "gomami-hk"}]
    name_map = {"gomami-hk": "gomami-hk", "nuro": "nuro", "NS": "NS"}
    lines, _ = convert_groups(groups, name_map, {"gomami-hk", "nuro"}, {"p": ["gomami-hk", "nuro"]})
    assert lines == ["NS = select, nuro"]


def test_filter_does_not_touch_explicit_proxies():
    # 显式 proxies(如 REJECT)始终保留;filter 只筛 use:/include-all 收集来的成员
    groups = [{"name": "claude", "type": "select", "proxies": ["REJECT"],
               "use": ["p"], "filter": "claude"}]
    name_map = {"claude-x": "claude-x", "other": "other", "claude": "claude"}
    lines, _ = convert_groups(groups, name_map, {"claude-x", "other"}, {"p": ["claude-x", "other"]})
    assert lines == ["claude = select, REJECT, claude-x"]


def test_filter_applies_to_include_all():
    groups = [{"name": "JP", "type": "select", "include-all-proxies": True, "filter": "^jp-"}]
    name_map = {"jp-1": "jp-1", "us-1": "us-1", "JP": "JP"}
    lines, _ = convert_groups(groups, name_map, {"jp-1", "us-1"}, {})
    assert lines == ["JP = select, jp-1"]


def test_anchored_filter_matches_exact_name_only():
    # ^a$ 精确;与 mihomo Go MatchString 的非锚定语义一致(裸串=子串匹配)
    groups = [{"name": "X", "type": "select", "use": ["p"], "filter": "^a$"}]
    name_map = {"a": "a", "ab": "ab", "X": "X"}
    lines, _ = convert_groups(groups, name_map, {"a", "ab"}, {"p": ["a", "ab"]})
    assert lines == ["X = select, a"]


def test_invalid_filter_regex_degrades_to_direct_and_skips():
    # 畸形订阅的非法正则不得崩整次转换:该组降级 DIRECT + 计入 skipped
    groups = [{"name": "Bad", "type": "select", "use": ["p"], "filter": "("}]
    name_map = {"n": "n", "Bad": "Bad"}
    lines, skipped = convert_groups(groups, name_map, {"n"}, {"p": ["n"]})
    assert lines == ["Bad = select, DIRECT"]
    assert any(s.kind == "group" and s.detail == "Bad" and "regex" in s.reason for s in skipped)


def test_use_and_include_all_shared_node_not_duplicated_under_filter():
    # use: 与 include-all 都覆盖同一节点;去重后 filter 只保留一份
    groups = [{"name": "HK", "type": "select", "use": ["p"],
               "include-all-proxies": True, "filter": "🇭🇰"}]
    name_map = {"🇭🇰a": "🇭🇰a", "🇯🇵b": "🇯🇵b", "HK": "HK"}
    lines, _ = convert_groups(groups, name_map, {"🇭🇰a", "🇯🇵b"}, {"p": ["🇭🇰a", "🇯🇵b"]})
    assert lines == ["HK = select, 🇭🇰a"]


def test_filter_then_exclude_filter_combined():
    # filter 先留 🇭🇰,exclude-filter 再剔 iplc
    groups = [{"name": "G", "type": "select", "use": ["p"],
               "filter": "🇭🇰", "exclude-filter": "iplc"}]
    name_map = {"🇭🇰a": "🇭🇰a", "🇭🇰iplc": "🇭🇰iplc", "🇯🇵b": "🇯🇵b", "G": "G"}
    lines, _ = convert_groups(groups, name_map, {"🇭🇰a", "🇭🇰iplc", "🇯🇵b"},
                              {"p": ["🇭🇰a", "🇭🇰iplc", "🇯🇵b"]})
    assert lines == ["G = select, 🇭🇰a"]
