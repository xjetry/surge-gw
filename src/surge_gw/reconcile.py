from __future__ import annotations


def drop_unhosted_ruleset_lines(rule_lines: list[str], hosted_urls: set[str]) -> list[str]:
    """丢弃指向未成功托管的自托管 ruleset 的规则行。所有 RULE-SET 行的第二段都是自托管
    /ruleset/<key> URL;若该 URL 不在 hosted_urls(对应 ruleset 被跳过、未托管),保留它只会让
    Surge 拉到 404,故整行丢弃。非 RULE-SET 行不受影响。"""
    out: list[str] = []
    for line in rule_lines:
        if line.startswith("RULE-SET,"):
            fields = line.split(",")
            if len(fields) >= 2 and fields[1] not in hosted_urls:
                continue
        out.append(line)
    return out


def append_update_interval(rule_lines: list[str], hosted_urls: set[str], interval: int) -> list[str]:
    """给指向自托管 ruleset 的 RULE-SET / DOMAIN-SET 行追加 update-interval。Surge 据此按该间隔
    回拉 /ruleset(其外部资源默认 24h),而每次回拉正是触发网关按需重抓上游的时机;不设则只能等
    Surge 默认间隔。按 url(第二段)匹配自托管集,内联规则与外部 URL 不动。interval<=0 表示禁用
    (保留 Surge 默认)。须在 rewrite_ruleset_types 之后运行,使改写后的 DOMAIN-SET 行也覆盖到。"""
    if interval <= 0:
        return rule_lines
    out: list[str] = []
    for line in rule_lines:
        if line.startswith(("RULE-SET,", "DOMAIN-SET,")):
            fields = line.split(",")
            if len(fields) >= 2 and fields[1] in hosted_urls:
                out.append(f"{line},update-interval={interval}")
                continue
        out.append(line)
    return out


def rewrite_ruleset_types(rule_lines: list[str], domain_set_urls: set[str]) -> list[str]:
    """把内容确认为纯域名表的引用从 RULE-SET 改写成 DOMAIN-SET。
    按 url(第二段)精确匹配;其余行不动。"""
    out: list[str] = []
    for line in rule_lines:
        if line.startswith("RULE-SET,"):
            fields = line.split(",")
            if len(fields) >= 2 and fields[1] in domain_set_urls:
                fields[0] = "DOMAIN-SET"
                out.append(",".join(fields))
                continue
        out.append(line)
    return out
