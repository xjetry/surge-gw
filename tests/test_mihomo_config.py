import yaml

from surge_gw.mihomo_config import (
    build_listeners, build_pinned_config, build_runtime_config, collect_proxy_defs,
)


def test_build_listeners_shape_and_skip():
    ls = build_listeners(["A", "B"], {"A": 1200})  # B 无端口
    assert ls == [
        {"name": "p1200", "type": "socks", "port": 1200,
         "listen": "127.0.0.1", "udp": True, "proxy": "A"},
    ]


def test_runtime_has_no_global_inbound_and_lazy_rules():
    upstream = {"proxies": [{"name": "A"}], "proxy-groups": [{"name": "G"}],
                "proxy-providers": {"p": {}}, "rules": ["DOMAIN,x,G"]}
    cfg = build_runtime_config(upstream, [{"name": "p1200"}], secret="s")
    assert cfg["external-controller"] == "127.0.0.1:9090"
    assert cfg["secret"] == "s"
    assert cfg["mode"] == "rule"
    assert cfg["dns"] == {"enable": False}
    assert cfg["rules"] == ["MATCH,DIRECT"]            # upstream rules 不带入
    assert cfg["listeners"] == [{"name": "p1200"}]
    assert cfg["proxies"] == [{"name": "A"}]
    assert cfg["proxy-groups"] == [{"name": "G"}]
    assert cfg["proxy-providers"] == {"p": {}}
    for inbound in ("mixed-port", "port", "socks-port", "redir-port"):
        assert inbound not in cfg


def test_runtime_omits_absent_upstream_sections():
    cfg = build_runtime_config({"proxies": [{"name": "A"}]}, [], secret="s")
    assert "proxies" in cfg
    assert "proxy-groups" not in cfg
    assert "proxy-providers" not in cfg


def test_collect_proxy_defs_merges_inline_and_provider_cache(tmp_path):
    # mihomo reload 后把 proxy-provider 摊平写到 <work_dir>/<path>
    (tmp_path / "providers").mkdir()
    (tmp_path / "providers" / "merged.yaml").write_text(
        yaml.safe_dump({"proxies": [{"name": "N1", "type": "ss", "server": "h", "port": 1}]}))
    upstream = {
        "proxies": [{"name": "I1", "type": "vless", "server": "x", "port": 2}],
        "proxy-providers": {"merged": {"type": "http", "path": "./providers/merged.yaml"}},
    }
    defs = collect_proxy_defs(upstream, str(tmp_path))
    assert defs["I1"]["type"] == "vless"        # 顶层 inline proxy
    assert defs["N1"]["server"] == "h"          # provider 缓存摊平出的成员


def test_collect_proxy_defs_skips_path_escape(tmp_path):
    # 越界的 provider path(恶意/畸形订阅)不得被读取
    (tmp_path / "outside.yaml").write_text(
        yaml.safe_dump({"proxies": [{"name": "LEAK", "type": "ss"}]}))
    work = tmp_path / "work"
    work.mkdir()
    upstream = {"proxy-providers": {"evil": {"type": "http", "path": "../outside.yaml"}}}
    assert collect_proxy_defs(upstream, str(work)) == {}


def test_build_pinned_config_top_level_proxies_no_providers():
    proxies = [{"name": "N1", "type": "ss", "server": "h", "port": 1}]
    listeners = [{"name": "p1200", "type": "socks", "port": 1200, "proxy": "N1"}]
    cfg = build_pinned_config(proxies, listeners, secret="s")
    assert cfg["external-controller"] == "127.0.0.1:9090"
    assert cfg["secret"] == "s"
    assert cfg["mode"] == "rule"
    assert cfg["dns"] == {"enable": False}
    assert cfg["rules"] == ["MATCH,DIRECT"]
    assert cfg["proxies"] == proxies            # 每个出站都是顶层 proxy,listener 才能钉定
    assert cfg["listeners"] == listeners
    assert "proxy-providers" not in cfg         # 摊平后不再需要 provider
    assert "proxy-groups" not in cfg
    for inbound in ("mixed-port", "port", "socks-port", "redir-port"):
        assert inbound not in cfg
