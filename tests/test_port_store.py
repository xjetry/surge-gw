from surge_gw.port_store import load, save


def test_save_then_load_roundtrip(tmp_path):
    p = str(tmp_path / "sub" / "port-map.json")  # 不存在的子目录也应创建
    save(p, {"A": 1200, "B": 1201})
    assert load(p) == {"A": 1200, "B": 1201}


def test_load_missing_returns_empty(tmp_path):
    assert load(str(tmp_path / "nope.json")) == {}
