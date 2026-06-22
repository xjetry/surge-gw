from surge_gw.naming import build_name_map, sanitize


def test_sanitize_removes_commas_keeps_emoji():
    assert sanitize("🇺🇸 US, Premium") == "🇺🇸 US Premium"


def test_sanitize_empty_falls_back():
    assert sanitize("   ") == "node"


def test_build_name_map_dedupes_after_sanitize():
    m = build_name_map(["A,1", "A 1", "B"])
    # "A,1" -> "A1"? 不,逗号剔除后是 "A1";"A 1" -> "A 1";两者不同,无需去重
    assert m["B"] == "B"
    assert len(set(m.values())) == 3


def test_build_name_map_collision_suffixes():
    m = build_name_map(["X,Y", "XY"])  # 都消毒成 "XY"
    assert m["X,Y"] != m["XY"]
    assert set(m.values()) == {"XY", "XY-2"}
