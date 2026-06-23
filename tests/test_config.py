import pytest
from surge_gw.config import from_env, Config


def test_required_url_missing_raises():
    with pytest.raises(ValueError):
        from_env({})


def test_defaults_applied():
    c = from_env({"SUBSCRIPTION_URL": "http://x/sub"})
    assert c.subscription_url == "http://x/sub"
    assert c.advertise_host == "127.0.0.1"
    assert c.http_port == 8080
    assert c.port_base == 1200
    assert c.max_nodes == 100
    assert c.refresh_interval == 21600
    assert c.min_refresh_interval == 300
    assert c.geosite_ttl == 86400
    assert c.geosite_url is None
    assert c.surge_update_interval == 3600
    assert c.mihomo_bin == "mihomo"
    assert c.data_dir == "./data"


def test_overrides_and_int_parsing():
    c = from_env({
        "SUBSCRIPTION_URL": "http://x/sub",
        "HTTP_PORT": "9000", "PORT_BASE": "2000", "MAX_NODES": "10",
        "GEOSITE_URL": "http://g/geosite.dat",
        "MIHOMO_BIN": "/opt/mihomo", "DATA_DIR": "/data",
    })
    assert (c.http_port, c.port_base, c.max_nodes) == (9000, 2000, 10)
    assert c.geosite_url == "http://g/geosite.dat"
    assert c.mihomo_bin == "/opt/mihomo"
    assert c.data_dir == "/data"


def test_ruleset_live_timeout_default_and_override():
    # 同步按需拉取必须有界:默认短超时给 Surge 的资源请求留余量,失败回退缓存。
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub"}).ruleset_live_timeout == 6
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub",
                     "RULESET_LIVE_TIMEOUT": "3"}).ruleset_live_timeout == 3


def test_emit_domain_set_default_false_and_override():
    # DOMAIN-SET 在 Surge 无「自动更新间隔」更新通道,默认改用可远程自动更新的 RULE-SET 输出。
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub"}).emit_domain_set is False
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub",
                     "EMIT_DOMAIN_SET": "true"}).emit_domain_set is True


def test_ruleset_update_interval_default_and_override():
    # 写进自托管 ruleset 行的 update-interval:决定 Surge 多久回拉一次(即多久触发一次按需重抓)。
    # 默认 86400 与 Surge 外部资源默认一致(不改变现状),调小即提高新鲜度。
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub"}).ruleset_update_interval == 86400
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub",
                     "RULESET_UPDATE_INTERVAL": "1800"}).ruleset_update_interval == 1800


def test_http_bind_defaults_to_loopback():
    c = from_env({"SUBSCRIPTION_URL": "http://x/sub"})
    assert c.http_bind == "127.0.0.1"


def test_http_bind_override_and_empty_falls_back():
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub", "HTTP_BIND": "0.0.0.0"}).http_bind == "0.0.0.0"
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub", "HTTP_BIND": ""}).http_bind == "127.0.0.1"
