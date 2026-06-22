from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkippedItem:
    """转换中被跳过的内容,用于汇总报告。"""
    kind: str      # "rule" / "geosite-regexp" / "group" / "proxy" ...
    detail: str    # 原始片段
    reason: str


@dataclass
class RuleResult:
    """规则转换的累积结果。"""
    lines: list[str] = field(default_factory=list)
    rule_providers: set[str] = field(default_factory=set)  # 被引用的 rule-provider 名
    geosites: set[str] = field(default_factory=set)        # 被引用的 geosite 分类(可含 @attr)
    skipped: list[SkippedItem] = field(default_factory=list)


@dataclass
class RulesetArtifact:
    """一个转换后的 ruleset/domainset 产物;kind 决定 Surge 引用关键字。"""
    lines: list[str] = field(default_factory=list)
    kind: str = "RULE-SET"          # "DOMAIN-SET" | "RULE-SET"
    skipped: list[SkippedItem] = field(default_factory=list)
