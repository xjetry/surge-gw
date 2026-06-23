from __future__ import annotations

from collections.abc import Callable

from surge_gw.models import RuleResult, SkippedItem

# 直接同名透传(不带值变换)
_PASSTHROUGH = {
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD",
    "IP-CIDR", "IP-CIDR6", "GEOIP", "IP-ASN",
    "SRC-PORT", "PROCESS-NAME",
}
# 改名透传(payload 不变)
_RENAME = {"DST-PORT": "DEST-PORT", "SRC-IP-CIDR": "SRC-IP"}
# 规则尾部的内联选项关键字。需显式枚举,否则无法把它们与"含逗号的 policy 名"区分:
# 二者都跟在 PAYLOAD 之后,而 Surge/mihomo 都以逗号分隔字段。
_RULE_OPTIONS = {"no-resolve"}
# IP 段规则补 no-resolve 的类型:这两类匹配连接目标 IP,加 no-resolve 可跳过为域名触发的 DNS 解析。
_NO_RESOLVE_TYPES = {"IP-CIDR", "IP-CIDR6"}
_BUILTIN_POLICIES = {"DIRECT", "REJECT", "REJECT-DROP"}
_SKIP_TYPES = {"DOMAIN-REGEX", "PROCESS-PATH", "DSCP", "IN-PORT", "IN-TYPE", "IN-USER", "IN-NAME"}
_LOGICAL = {"AND", "OR", "NOT"}


def _map_policy(policy: str, policy_map: dict[str, str]) -> str | None:
    if policy in _BUILTIN_POLICIES:
        return policy
    if policy == "PASS":
        return None
    return policy_map.get(policy)


def _split_logical_payload(payload: str) -> list[str]:
    """把 ((a),(b),(c)) 拆成 ['(a)','(b)','(c)'],尊重括号深度。"""
    inner = payload[1:-1]  # 去掉最外层括号
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                parts.append(inner[start : i + 1])
                start = i + 1
        elif depth == 0 and ch == ",":
            start = i + 1
    return [p for p in parts if p]


def convert_rule_body(body: str) -> str | None:
    """把无 policy 的规则体 'TYPE,PAYLOAD[,opts]' 转成 Surge 规则体,不可映射返回 None。
    rule-provider(classical)与逻辑子规则共用,保证规则类型映射只有一处实现。"""
    line = _convert_one(f"{body},__NOPOLICY__", {}, lambda name: name, lambda cat: cat)
    if line is None or "__NOPOLICY__" not in line:
        return None
    return line.rsplit(",__NOPOLICY__", 1)[0]


def _convert_subrule(sub: str) -> str | None:
    """子规则形如 '(DOMAIN,a.com)';返回 Surge 子规则或 None(不可转)。"""
    body = convert_rule_body(sub[1:-1])
    return f"({body})" if body is not None else None


def _convert_one(rule: str, policy_map, ruleset_url, geosite_url) -> str | None:
    """转换单条(非逻辑)规则,policy 用占位 '__NOPOLICY__' 表示无 policy(逻辑子规则)。"""
    fields = rule.split(",")
    rtype = fields[0].strip()

    if rtype in _SKIP_TYPES:
        return None

    is_subrule = fields[-1] == "__NOPOLICY__"
    if is_subrule:
        policy_raw = "__NOPOLICY__"
        payload = fields[1].strip() if len(fields) > 2 else ""
        # 保留 payload 与占位 policy 之间的选项(如 no-resolve),供 classical
        # rule-provider 与逻辑子规则共用同一套规则类型映射时不丢信息。
        options = [o.strip() for o in fields[2:-1]]
    else:
        # TYPE, PAYLOAD, POLICY [, options...]
        if len(fields) < 3 and rtype not in ("MATCH",):
            return None
        if rtype == "MATCH":
            policy_raw, payload, options = fields[1].strip(), None, []
        else:
            payload = fields[1].strip()
            # policy 名可能含逗号(节点名未消毒前),会被 split 拆散;先从尾部
            # 剥离已知选项,剩下的 fields[2:] 拼回作为完整 policy,避免误判。
            rest = [f.strip() for f in fields[2:]]
            options = []
            while len(rest) > 1 and rest[-1] in _RULE_OPTIONS:
                options.insert(0, rest.pop())
            policy_raw = ",".join(rest)

    # policy 映射(子规则无 policy)
    if is_subrule:
        mapped_policy = None
    else:
        mapped_policy = _map_policy(policy_raw, policy_map)
        if mapped_policy is None:
            return None

    def emit(stype: str, spayload: str | None) -> str:
        parts = [stype]
        if spayload is not None:
            parts.append(spayload)
        if not is_subrule and mapped_policy is not None:
            parts.append(mapped_policy)
        rule_options = options
        # IP 段规则补 no-resolve:仅顶层规则(逻辑子规则/classical provider 体走 is_subrule,
        # 保持现状);幂等,已带的不重复。
        if stype in _NO_RESOLVE_TYPES and not is_subrule and "no-resolve" not in options:
            rule_options = [*options, "no-resolve"]
        parts.extend(rule_options)
        if is_subrule:
            parts.append("__NOPOLICY__")
        return ",".join(parts)

    if rtype in _PASSTHROUGH:
        return emit(rtype, payload)
    if rtype in _RENAME:
        return emit(_RENAME[rtype], payload)
    if rtype == "NETWORK":
        return emit("PROTOCOL", payload.upper())
    if rtype in ("MATCH", "FINAL"):
        return emit("FINAL", None)
    if rtype == "RULE-SET":
        return emit("RULE-SET", ruleset_url(payload))
    if rtype == "GEOSITE":
        return emit("RULE-SET", geosite_url(payload))
    return None


def convert_rules(
    rules: list[str],
    policy_map: dict[str, str],
    ruleset_url: Callable[[str], str],
    geosite_url: Callable[[str], str],
) -> RuleResult:
    result = RuleResult()
    for rule in rules:
        rtype = rule.split(",", 1)[0].strip()

        if rtype in _LOGICAL:
            line = _convert_logical(rule, policy_map, ruleset_url, geosite_url)
            if line is None:
                result.skipped.append(SkippedItem("rule", rule, "logical rule has unconvertible subrule"))
            else:
                result.lines.append(line)
            continue

        line = _convert_one(rule, policy_map, ruleset_url, geosite_url)
        if line is None:
            result.skipped.append(SkippedItem("rule", rule, f"unsupported or unmapped: {rtype}"))
            continue
        result.lines.append(line)

        if rtype == "RULE-SET":
            result.rule_providers.add(rule.split(",")[1].strip())
        elif rtype == "GEOSITE":
            result.geosites.add(rule.split(",")[1].strip())
    return result


def _convert_logical(rule, policy_map, ruleset_url, geosite_url) -> str | None:
    # AND,((sub),(sub)),POLICY
    head = rule.split(",", 1)[0].strip()
    rest = rule[len(head) + 1 :]
    depth = 0
    for i, ch in enumerate(rest):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                payload = rest[: i + 1]
                policy_raw = rest[i + 2 :].strip()
                break
    else:
        return None

    mapped_policy = _map_policy(policy_raw, policy_map)
    if mapped_policy is None:
        return None

    subs = _split_logical_payload(payload)
    converted_subs = []
    for sub in subs:
        if sub[1:].split(",", 1)[0] in _LOGICAL:  # 嵌套逻辑
            nested = _convert_logical(sub[1:-1], policy_map, ruleset_url, geosite_url)
            if nested is None:
                return None
            converted_subs.append("(" + nested.rsplit(",", 1)[0] + ")")
        else:
            c = _convert_subrule(sub)
            if c is None:
                return None
            converted_subs.append(c)

    # Surge 逻辑规则要求子规则间以逗号分隔:AND,((R1),(R2)),POLICY
    return f"{head},({','.join(converted_subs)}),{mapped_policy}"
