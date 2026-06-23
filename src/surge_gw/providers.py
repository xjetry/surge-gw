from __future__ import annotations

import re

import yaml

from surge_gw.models import RulesetArtifact, SkippedItem
from surge_gw.rules import convert_rule_body

# Surge DOMAIN-SET / RULE-SET 均为严格校验:任一非法行会让整份资源失效。故据此校验,
# 不可表达的条目剔除并计入 skipped,以保资源整体有效。
# 裸域名(可选前导点 = 后缀),用于 DOMAIN-SET 行。
_DOMAIN_SET_LINE = re.compile(r"^\.?[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
# 裸域名(无前导点),用作 DOMAIN / DOMAIN-SUFFIX 的值。
_BARE_DOMAIN = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
# 通配符域名:裸域名字符集 + '*' / '?'(Surge DOMAIN-WILDCARD 的通配符)。
# 禁止逗号/空格/控制字符 —— 逗号会破坏 RULE-SET 行的字段切分。
_WILDCARD_DOMAIN = re.compile(r"^[A-Za-z0-9_*?-]+(?:\.[A-Za-z0-9_*?-]+)*$")


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


def _domain_entry_to_set_line(entry: str) -> str | None:
    """behavior=domain 条目 → Surge DOMAIN-SET 行,无法表达则 None。
    '+.x'(后缀)→ '.x';前导点/精确域名原样。含通配符 '*' 等 Surge DOMAIN-SET 不接受的形式
    返回 None —— 否则单行非法会让 Surge 判定整份资源无效、其余域名全部失配。"""
    line = "." + entry[2:] if entry.startswith("+.") else entry
    return line if _DOMAIN_SET_LINE.match(line) else None


def _has_wildcard(entry: str) -> bool:
    return "*" in entry or "?" in entry


def _domain_entry_to_rule_line(entry: str) -> str | None:
    """RULE-SET 模式:单个 domain 条目 → 一行 Surge 规则体(无 policy),不可表达返回 None。
    含通配符 → DOMAIN-WILDCARD(剥离前导 '+.' / '.';其'子域'语义被 '*' 跨段匹配覆盖);
    '+.x' / '.x' 后缀 → DOMAIN-SUFFIX;精确域名 → DOMAIN。"""
    if _has_wildcard(entry):
        pattern = entry[2:] if entry.startswith("+.") else entry.lstrip(".")
        # 至少含一个字面标签字符:否则裸 '*' / '?' 会生成匹配所有域名的 catch-all,
        # 把该 provider 的 policy 误施于全部流量(单条脏数据即可触发)。
        if _WILDCARD_DOMAIN.match(pattern) and re.search(r"[A-Za-z0-9_-]", pattern):
            return f"DOMAIN-WILDCARD,{pattern}"
        return None
    if entry.startswith("+."):
        body = entry[2:]
        return f"DOMAIN-SUFFIX,{body}" if _BARE_DOMAIN.match(body) else None
    if entry.startswith("."):
        body = entry[1:]
        return f"DOMAIN-SUFFIX,{body}" if _BARE_DOMAIN.match(body) else None
    return f"DOMAIN,{entry}" if _BARE_DOMAIN.match(entry) else None


def convert_domain_provider(entries: list[str]) -> RulesetArtifact:
    """纯域名表 → Surge DOMAIN-SET(裸域名;前导点 = 后缀)。
    含通配符(* / ?)的条目无法在 DOMAIN-SET 表达,则整表降级为 RULE-SET,逐条映射为
    DOMAIN / DOMAIN-SUFFIX / DOMAIN-WILDCARD,使通配符域名仍可命中。
    两种模式写出的每行都过严格校验,非法条目计入 skipped —— DOMAIN-SET / RULE-SET 均为
    严格校验,单行非法会让整份资源失效。"""
    if any(_has_wildcard(e) for e in entries):
        art = RulesetArtifact(kind="RULE-SET")
        to_line = _domain_entry_to_rule_line
    else:
        art = RulesetArtifact(kind="DOMAIN-SET")
        to_line = _domain_entry_to_set_line
    for entry in entries:
        line = to_line(entry)
        if line is None:
            art.skipped.append(SkippedItem("ruleset", entry, "domain entry not representable"))
        else:
            art.lines.append(line)
    return art


def convert_ipcidr_provider(entries: list[str]) -> RulesetArtifact:
    """IP 段表 → Surge RULE-SET(IP-CIDR / IP-CIDR6 行)。
    每行补 no-resolve:IP 段规则只需匹配连接目标 IP,无需为域名触发 DNS 解析。"""
    art = RulesetArtifact(kind="RULE-SET")
    for entry in entries:
        cidr = entry.strip()
        rtype = "IP-CIDR6" if ":" in cidr else "IP-CIDR"
        art.lines.append(f"{rtype},{cidr},no-resolve")
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
