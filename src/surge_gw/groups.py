from __future__ import annotations

import re

from surge_gw.models import SkippedItem

_BUILTINS = {"DIRECT", "REJECT", "REJECT-DROP"}
_TYPE_MAP = {"select": "select", "url-test": "url-test", "fallback": "fallback"}


def _apply_filters(names: list[str], filt: str | None, excl: str | None) -> list[str]:
    """mihomo filter/exclude-filter:正则筛 use:/include-all 收集来的成员名。
    用 re.search(非锚定)对齐 mihomo 的 Go regexp.MatchString:裸串=子串匹配,
    需精确则上游自带 ^...$。非法正则交由调用方按降级处理(此处直接抛 re.error)。"""
    if filt:
        pat = re.compile(filt)
        names = [n for n in names if pat.search(n)]
    if excl:
        pat = re.compile(excl)
        names = [n for n in names if not pat.search(n)]
    return names


def _members(group: dict, name_map: dict[str, str], available: set[str],
             provider_members: dict[str, list[str]], known_groups: set[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(node: str) -> None:
        if node not in seen:
            seen.add(node)
            out.append(node)

    # 显式 proxies 始终保留;filter/exclude-filter 只作用于 use:/include-all 收集来的成员,
    # 与 mihomo 一致(filter 不影响显式列出的成员,如用作默认的 REJECT/DIRECT)。
    explicit: list[str] = list(group.get("proxies") or [])
    collected: list[str] = []
    for prov in group.get("use") or []:
        collected.extend(provider_members.get(prov, []))
    if group.get("include-all") or group.get("include-all-proxies"):
        collected.extend(sorted(available))
    # use: 与 include-all 可能重复收集同一节点;去重后再筛,filter 作用于真实成员集合一次。
    collected = _apply_filters(list(dict.fromkeys(collected)),
                               group.get("filter"), group.get("exclude-filter"))

    for member in (*explicit, *collected):
        if member in _BUILTINS:
            add(member)
        elif member in available or member in known_groups:
            add(name_map[member])
    return out


def convert_groups(
    groups: list[dict],
    name_map: dict[str, str],
    available_nodes: set[str],
    provider_members: dict[str, list[str]],
) -> tuple[list[str], list[SkippedItem]]:
    """mihomo 策略组 → Surge [Proxy Group]。LB 降级 select,relay 跳过。"""
    known_groups = {g["name"] for g in groups}
    lines: list[str] = []
    skipped: list[SkippedItem] = []

    for group in groups:
        gtype = group["type"]
        if gtype == "relay":
            skipped.append(SkippedItem("group", group["name"], "relay (chaining) unsupported"))
            continue

        surge_type = _TYPE_MAP.get(gtype)
        if surge_type is None:
            if gtype == "load-balance":
                surge_type = "select"
                skipped.append(SkippedItem("group", group["name"], "load-balance degraded to select"))
            else:
                skipped.append(SkippedItem("group", group["name"], f"unknown type {gtype}"))
                continue

        try:
            members = _members(group, name_map, available_nodes, provider_members, known_groups)
        except re.error:
            skipped.append(SkippedItem("group", group["name"], "invalid filter regex"))
            members = []
        if not members:
            members = ["DIRECT"]

        line = f"{name_map[group['name']]} = {surge_type}, " + ", ".join(members)

        if surge_type in ("url-test", "fallback"):
            opts = []
            if group.get("url"):
                opts.append(f"url={group['url']}")
            if group.get("interval") is not None:
                opts.append(f"interval={group['interval']}")
            if surge_type == "url-test" and group.get("tolerance") is not None:
                opts.append(f"tolerance={group['tolerance']}")
            if opts:
                line += ", " + ", ".join(opts)

        lines.append(line)

    return lines, skipped
