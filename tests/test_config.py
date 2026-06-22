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


def test_http_bind_defaults_to_loopback():
    c = from_env({"SUBSCRIPTION_URL": "http://x/sub"})
    assert c.http_bind == "127.0.0.1"


def test_http_bind_override_and_empty_falls_back():
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub", "HTTP_BIND": "0.0.0.0"}).http_bind == "0.0.0.0"
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub", "HTTP_BIND": ""}).http_bind == "127.0.0.1"
