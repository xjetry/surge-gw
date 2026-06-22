from surge_gw.surge_config import build_surge_config


def test_managed_header_first_and_sections_present():
    text = build_surge_config(
        proxy_lines=["A = socks5, 127.0.0.1, 1200, udp-relay=true"],
        group_lines=["Proxy = select, A, DIRECT"],
        rule_lines=["DOMAIN-SUFFIX,google.com,Proxy", "FINAL,DIRECT"],
        managed_url="http://127.0.0.1:8080/surge?token=abc",
        update_interval=3600,
    )
    lines = text.splitlines()
    assert lines[0] == "#!MANAGED-CONFIG http://127.0.0.1:8080/surge?token=abc interval=3600 strict=false"
    assert "[Proxy]" in text
    assert "[Proxy Group]" in text
    assert "[Rule]" in text
    assert "A = socks5, 127.0.0.1, 1200, udp-relay=true" in text
    assert text.index("[Proxy]") < text.index("[Proxy Group]") < text.index("[Rule]")
