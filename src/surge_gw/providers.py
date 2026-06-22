from __future__ import annotations

import yaml

from surge_gw.models import RulesetArtifact, SkippedItem
from surge_gw.rules import convert_rule_body


def extract_provider_entries(raw: str, fmt: str) -> list[str]:
    """rule-provider 原文 → 条目列表。yaml 取 payload 列表;text 逐行去注释。
    抓取(经 socks)由后续 Plan 负责,本函数只做纯文本解析。"""
    if fmt == "yaml":
        data = yaml.safe_load(raw) or {}
        payload = data.get("payload") or []
        return [str(item) for item in payload]
    entries: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append(stripped)
    return entries


def _domain_entry_to_set_line(entry: str) -> str:
    """behavior=domain 条目 → Surge DOMAIN-SET 行。
    '+.x'(后缀)→ '.x';前导点原样;其余视作精确域名原样。"""
    if entry.startswith("+."):
        return "." + entry[2:]
    return entry


def convert_domain_provider(entries: list[str]) -> RulesetArtifact:
    """纯域名表 → Surge DOMAIN-SET(裸域名;前导点 = 后缀匹配)。"""
    art = RulesetArtifact(kind="DOMAIN-SET")
    for entry in entries:
        art.lines.append(_domain_entry_to_set_line(entry))
    return art


def convert_ipcidr_provider(entries: list[str]) -> RulesetArtifact:
    """IP 段表 → Surge RULE-SET(IP-CIDR / IP-CIDR6 行)。"""
    art = RulesetArtifact(kind="RULE-SET")
    for entry in entries:
        cidr = entry.strip()
        rtype = "IP-CIDR6" if ":" in cidr else "IP-CIDR"
        art.lines.append(f"{rtype},{cidr}")
    return art


def convert_classical_provider(entries: list[str]) -> RulesetArtifact:
    """classical 表 → Surge RULE-SET(逐条按规则类型映射去 policy;不可映射跳过)。"""
    art = RulesetArtifact(kind="RULE-SET")
    for entry in entries:
        body = convert_rule_body(entry)
        if body is None:
            art.skipped.append(SkippedItem("ruleset", entry, "unsupported classical rule"))
        else:
            art.lines.append(body)
    return art
