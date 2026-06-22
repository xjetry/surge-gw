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
) -> Bundle:
    """串起 Plan 1/2 转换:节点/组/规则 → Surge,远程引用经注入回调拉取并转换,
    纯域名引用反填为 DOMAIN-SET。fetch 回调注入,本函数对其结果做纯转换。"""
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
            rulesets[f"geosite-{ref}"] = "\n".join(art.lines) + "\n"
            skipped.extend(art.skipped)
            hosted_urls.add(urls.geosite(ref))
            if art.kind == "DOMAIN-SET":
                domain_set_urls.add(urls.geosite(ref))

    # 先丢弃指向未托管 ruleset 的规则行(避免 Surge 拉 404),再把纯域名表引用改写为 DOMAIN-SET。
    rule_lines = reconcile.drop_unhosted_ruleset_lines(result.lines, hosted_urls)
    rule_lines = reconcile.rewrite_ruleset_types(rule_lines, domain_set_urls)
    if prepend_rule_lines:
        rule_lines = [*prepend_rule_lines, *rule_lines]   # bypass 规则须早于 GEOIP/FINAL
    surge_text = surge_config.build_surge_config(
        proxy_lines=proxy_lines, group_lines=group_lines, rule_lines=rule_lines,
        managed_url=urls.managed(), update_interval=update_interval, general=general,
    )
    return Bundle(surge_text=surge_text, rulesets=rulesets, skipped=skipped)
