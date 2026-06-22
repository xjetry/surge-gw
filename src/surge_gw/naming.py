from __future__ import annotations

from collections.abc import Iterable


def sanitize(name: str) -> str:
    """Surge 以逗号分隔字段,名字里不能含逗号;空名给个回退。"""
    cleaned = name.replace(",", "").strip()
    return cleaned if cleaned else "node"


def build_name_map(names: Iterable[str]) -> dict[str, str]:
    """原始名 → 消毒后唯一名。消毒后撞名的追加 -2/-3… 以保持引用可区分。"""
    result: dict[str, str] = {}
    used: set[str] = set()
    for original in names:
        base = sanitize(original)
        candidate = base
        n = 1
        while candidate in used:
            n += 1
            candidate = f"{base}-{n}"
        used.add(candidate)
        result[original] = candidate
    return result
