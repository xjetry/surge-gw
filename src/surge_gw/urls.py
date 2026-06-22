from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RulesetUrls:
    """自托管 URL 构造。三处必须同源:规则行里的 URL、反填的 domain_set_urls、
    以及 http_server 的端点路径段。仅绑回环、无鉴权,故 URL 不带 token。"""
    host: str
    port: int

    def _base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def ruleset(self, name: str) -> str:
        return f"{self._base()}/ruleset/{name}"

    def geosite(self, cat: str) -> str:
        return f"{self._base()}/ruleset/geosite-{cat}"

    def managed(self) -> str:
        return f"{self._base()}/surge"
