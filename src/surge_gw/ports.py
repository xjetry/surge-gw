from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AllocationResult:
    mapping: dict[str, int]
    dropped: list[str]


def allocate(
    names: list[str],
    previous: dict[str, int],
    port_base: int = 1200,
    max_nodes: int = 100,
) -> AllocationResult:
    """同名节点跨刷新保持端口,避免 Surge 选中项漂移。超上限的丢弃。"""
    ports = range(port_base, port_base + max_nodes)
    mapping: dict[str, int] = {}
    taken: set[int] = set()

    # 保留仍存在节点的旧端口(必须在区间内且未被占)
    for name in names:
        port = previous.get(name)
        if port is not None and port in ports and port not in taken:
            mapping[name] = port
            taken.add(port)

    free = (p for p in ports if p not in taken)
    dropped: list[str] = []
    for name in names:
        if name in mapping:
            continue
        port = next(free, None)
        if port is None:
            dropped.append(name)
        else:
            mapping[name] = port
            taken.add(port)

    return AllocationResult(mapping=mapping, dropped=dropped)
