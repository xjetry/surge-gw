from surge_gw.refresh_policy import should_refresh, Snapshot, placeholder_surge


def test_should_refresh_single_flight_and_debounce():
    assert should_refresh(100.0, None, False, 300) is True        # 从未跑过
    assert should_refresh(100.0, 100.0, True, 300) is False       # 在途 → 单飞拒绝
    assert should_refresh(200.0, 100.0, False, 300) is False      # 距上次 100s < 300 防抖
    assert should_refresh(500.0, 100.0, False, 300) is True       # 距上次 400s ≥ 300


def test_snapshot_defaults():
    s = Snapshot(surge_text="x")
    assert s.rulesets == {} and s.node_port_map == {}
    assert s.skipped == [] and s.dropped == []


def test_placeholder_is_minimal_valid_config():
    text = placeholder_surge("http://h/surge?token=t", 3600)
    lines = text.splitlines()
    assert lines[0] == "#!MANAGED-CONFIG http://h/surge?token=t interval=3600 strict=false"
    assert "FINAL,DIRECT" in text
