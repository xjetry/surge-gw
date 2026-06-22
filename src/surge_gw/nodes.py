from __future__ import annotations

# mihomo /proxies 里非"具体出站"的 type:策略组 + 内置项。用排除法,
# 避免穷举所有出站协议(协议是开放集,新增协议不应漏掉)。
_NON_OUTBOUND_TYPES = {
    "Selector", "URLTest", "Fallback", "LoadBalance", "Relay",
    "Direct", "Reject", "RejectDrop", "Compatible", "Pass", "PassRule",
}


def select_outbound_nodes(proxies_resp: dict) -> list[str]:
    """只保留具体出站节点(可建 listener 的);策略组与内置项排除。保持响应顺序。"""
    out: list[str] = []
    for name, info in (proxies_resp.get("proxies") or {}).items():
        if info.get("type") in _NON_OUTBOUND_TYPES:
            continue
        out.append(name)
    return out


def provider_members(providers_resp: dict) -> dict[str, list[str]]:
    """provider 名 → 成员节点名,供策略组 use: 展开。"""
    result: dict[str, list[str]] = {}
    for name, info in (providers_resp.get("providers") or {}).items():
        result[name] = [p["name"] for p in (info.get("proxies") or [])]
    return result
