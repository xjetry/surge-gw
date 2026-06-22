from surge_gw.ports import allocate


def test_first_allocation_is_sequential():
    res = allocate(["a", "b", "c"], previous={}, port_base=1200, max_nodes=100)
    assert res.mapping == {"a": 1200, "b": 1201, "c": 1202}
    assert res.dropped == []


def test_existing_nodes_keep_their_port():
    prev = {"a": 1205, "b": 1200}
    res = allocate(["a", "b", "c"], previous=prev, port_base=1200, max_nodes=100)
    assert res.mapping["a"] == 1205
    assert res.mapping["b"] == 1200
    # 新节点取最小空闲端口(1200/1205 已占)
    assert res.mapping["c"] == 1201


def test_removed_node_frees_its_port():
    prev = {"a": 1200, "b": 1201}
    res = allocate(["b", "x"], previous=prev, port_base=1200, max_nodes=100)
    assert res.mapping["b"] == 1201
    assert res.mapping["x"] == 1200  # a 走了,1200 空出


def test_overflow_drops_extra_nodes():
    names = [f"n{i}" for i in range(102)]
    res = allocate(names, previous={}, port_base=1200, max_nodes=100)
    assert len(res.mapping) == 100
    assert res.dropped == ["n100", "n101"]
    assert max(res.mapping.values()) == 1299
