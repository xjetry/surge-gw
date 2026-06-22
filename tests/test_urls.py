from surge_gw.urls import RulesetUrls


def test_url_builders():
    u = RulesetUrls(host="127.0.0.1", port=8080)
    assert u.ruleset("mylist") == "http://127.0.0.1:8080/ruleset/mylist"
    assert u.geosite("google@cn") == "http://127.0.0.1:8080/ruleset/geosite-google@cn"
    assert u.managed() == "http://127.0.0.1:8080/surge"
