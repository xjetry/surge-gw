from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from surge_gw import geosite as geomod
from surge_gw import groups as groupmod
from surge_gw import naming
from surge_gw import providers as providermod
from surge_gw import proxies as proxymod
from surge_gw import reconcile
from surge_gw import rules as rulemod
from surge_gw import surge_config
from surge_gw.models import SkippedItem
from surge_gw.urls import RulesetUrls


@dataclass
class Bundle:
    surge_text: str
    rulesets: dict[str, str] = field(default_factory=dict)
    skipped: list[SkippedItem] = field(default_factory=list)


def _safe_ruleset_key(key: str) -> bool:
    """ruleset key 同时是 cache 文件名(cache.persist 的 os.path.join 段)与自托管 URL 路径段。
    含路径分隔符 / `.` / `..` / NUL 的 key 来自恶意或畸形订阅时是任意写原语,拒绝托管以保持
    key==文件名==URL 段的不变量(被拒的引用其规则行随后由 drop_unhosted_ruleset_lines 丢弃)。"""
    return key not in ("", ".", "..") and not any(c in key for c in ("/", "\\", "\x00"))


def _provider_artifact(entries: list[str], behavior: str | None):
    if behavior is None:
        return None
    if behavior == "domain":
        return providermod.convert_domain_provider(entries)
    if behavior == "ipcidr":
        return providermod.convert_ipcidr_provider(entries)
    if behavior == "classical":
        return providermod.convert_classical_provider(entries)
    return None


def _force_rule_set(art) -> None:
    """In-place: re-express a DOMAIN-SET artifact as an equivalent RULE-SET. Surge's DOMAIN-SET
    has no auto-update-interval channel, so a self-hosted DOMAIN-SET URL never refreshes; RULE-SET
    does. domain-set lines map 1:1 to DOMAIN-SUFFIX/DOMAIN (leading dot = suffix), no information
    lost, and modern Surge has no perf gap between the two."""
    if art.kind != "DOMAIN-SET":
        return
    art.lines = [f"DOMAIN-SUFFIX,{ln[1:]}" if ln.startswith(".") else f"DOMAIN,{ln}"
                 for ln in art.lines]
    art.kind = "RULE-SET"


def rebuild_ruleset_text(content: str, spec: dict, emit_domain_set: bool = True) -> str | None:
    """Re-derive one rule-provider's Surge ruleset text from freshly fetched content.
    Returns None when the provider can't be hosted (mrs binary / unknown behavior). Shares the
    same parse+convert (and the same emit_domain_set policy) as the full build, so the on-demand
    single-ruleset path and the full refresh always agree on a provider's converted form — a
    mismatch would hand Surge DOMAIN-SET-format lines under a RULE-SET reference (or vice versa)."""
    if spec.get("format") == "mrs":
        return None
    entries = providermod.extract_provider_entries(content, spec.get("format", "yaml"))
    art = _provider_artifact(entries, spec.get("behavior"))
    if art is None:
        return None
    if not emit_domain_set:
        _force_rule_set(art)
    return "\n".join(art.lines) + "\n"


def build_config_and_rulesets(
    *,
    upstream: dict,
    node_port_map: dict[str, int],
    provider_members: dict[str, list[str]],
    urls: RulesetUrls,
    host: str,
    fetch_ruleset_content: Callable[[str], str | None],
    geosite_dat: bytes | None,
    update_interval: int,
    general: dict | None = None,
    prepend_rule_lines: list[str] | None = None,
    ruleset_update_interval: int = 0,
    emit_domain_set: bool = True,
    always_real_ip_domains: list[str] | None = None,
) -> Bundle:
    """串起转换:节点/组/规则 → Surge,远程引用经注入回调拉取并转换。fetch 回调注入,本函数对其
    结果做纯转换。emit_domain_set=False 时纯域名表也输出 RULE-SET,使每个自托管引用都带
    update-interval、可被 Surge 远程自动更新(DOMAIN-SET 在 Surge 无此更新通道);运行时由
    EMIT_DOMAIN_SET 控制,默认 False。"""
    node_names = list(node_port_map.keys())
    groups = upstream.get("proxy-groups") or []
    name_map = naming.build_name_map([*node_names, *(g["name"] for g in groups)])
    available = set(node_names)

    proxy_lines = proxymod.build_proxy_section(node_names, name_map, node_port_map, host=host)
    group_lines, skipped = groupmod.convert_groups(groups, name_map, available, provider_members)

    result = rulemod.convert_rules(upstream.get("rules") or [], name_map, urls.ruleset, urls.geosite)
    skipped.extend(result.skipped)

    rulesets: dict[str, str] = {}
    domain_set_urls: set[str] = set()
    hosted_urls: set[str] = set()
    rp_defs = upstream.get("rule-providers") or {}

    for name in sorted(result.rule_providers):
        if not _safe_ruleset_key(name):
            skipped.append(SkippedItem("ruleset", name, "unsafe ruleset key"))
            continue
        spec = rp_defs.get(name)
        if spec is None:
            skipped.append(SkippedItem("ruleset", name, "rule-provider not defined upstream"))
            continue
        if spec.get("format") == "mrs":
            skipped.append(SkippedItem("ruleset", name, "mrs rule-provider unsupported"))
            continue
        content = fetch_ruleset_content(spec.get("url", ""))
        if content is None:
            skipped.append(SkippedItem("ruleset", name, "rule-provider fetch failed"))
            continue
        entries = providermod.extract_provider_entries(content, spec.get("format", "yaml"))
        art = _provider_artifact(entries, spec.get("behavior"))
        if art is None:
            skipped.append(SkippedItem("ruleset", name, f"unknown behavior {spec.get('behavior')}"))
            continue
        if not emit_domain_set:
            _force_rule_set(art)
        rulesets[name] = "\n".join(art.lines) + "\n"
        skipped.extend(art.skipped)
        hosted_urls.add(urls.ruleset(name))
        if art.kind == "DOMAIN-SET":
            domain_set_urls.add(urls.ruleset(name))

    if result.geosites:
        cats = geomod.decode_geosite_dat(geosite_dat) if geosite_dat is not None else {}
        for ref in sorted(result.geosites):
            if not _safe_ruleset_key(f"geosite-{ref}"):
                skipped.append(SkippedItem("geosite", ref, "unsafe ruleset key"))
                continue
            if geosite_dat is None:
                skipped.append(SkippedItem("geosite", ref, "geosite.dat unavailable"))
                continue
            category, attr = geomod.split_geosite_ref(ref)
            domains = cats.get(category)
            if domains is None:
                skipped.append(SkippedItem("geosite", ref, "geosite category not found"))
                continue
            art = geomod.build_geosite_artifact(domains, attr)
            if not emit_domain_set:
                _force_rule_set(art)
            rulesets[f"geosite-{ref}"] = "\n".join(art.lines) + "\n"
            skipped.extend(art.skipped)
            hosted_urls.add(urls.geosite(ref))
            if art.kind == "DOMAIN-SET":
                domain_set_urls.add(urls.geosite(ref))

    # 先丢弃指向未托管 ruleset 的规则行(避免 Surge 拉 404),再把纯域名表引用改写为 DOMAIN-SET,
    # 最后给自托管行追加 update-interval(须在改写之后,使 DOMAIN-SET 行也覆盖到)。
    rule_lines = reconcile.drop_unhosted_ruleset_lines(result.lines, hosted_urls)
    rule_lines = reconcile.rewrite_ruleset_types(rule_lines, domain_set_urls)
    rule_lines = reconcile.append_update_interval(rule_lines, hosted_urls, ruleset_update_interval)
    if prepend_rule_lines:
        rule_lines = [*prepend_rule_lines, *rule_lines]   # bypass 规则须早于 GEOIP/FINAL
    surge_text = surge_config.build_surge_config(
        proxy_lines=proxy_lines, group_lines=group_lines, rule_lines=rule_lines,
        managed_url=urls.managed(), update_interval=update_interval, general=general,
        always_real_ip=always_real_ip_domains,
    )
    return Bundle(surge_text=surge_text, rulesets=rulesets, skipped=skipped)
