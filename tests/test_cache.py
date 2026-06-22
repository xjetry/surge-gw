from surge_gw.cache import Cache, persist, load_last_good
from surge_gw.refresh_policy import Snapshot


def test_swap_and_get():
    c = Cache(Snapshot(surge_text="old"))
    assert c.get().surge_text == "old"
    c.swap(Snapshot(surge_text="new"))
    assert c.get().surge_text == "new"


def test_persist_and_load_last_good(tmp_path):
    snap = Snapshot(surge_text="SURGE", rulesets={"cnlist": ".cn\n"},
                    node_port_map={"A": 1200})
    persist(snap, str(tmp_path))
    loaded = load_last_good(str(tmp_path))
    assert loaded is not None
    assert loaded.surge_text == "SURGE"
    assert loaded.rulesets == {"cnlist": ".cn\n"}
    assert loaded.node_port_map == {"A": 1200}


def test_load_last_good_missing_returns_none(tmp_path):
    assert load_last_good(str(tmp_path)) is None
