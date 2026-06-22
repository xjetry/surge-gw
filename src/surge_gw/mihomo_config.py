from __future__ import annotations

import os

import yaml


def build_listeners(node_names: list[str], port_map: dict[str, int], *, udp: bool = True) -> list[dict]:
    """每个有端口的节点一个 socks listener;proxy 字段(SpecialProxy)把该端口流量
    钉死走该出站、绕过规则引擎,故 runtime 的 rules 可保持惰性占位。"""
    listeners: list[dict] = []
    for name in node_names:
        port = port_map.get(name)
        if port is None:
            continue
        listeners.append({
            "name": f"p{port}",
            "type": "socks",
            "port": port,
            "listen": "127.0.0.1",   # host 网络下仅对宿主回环可见;不向局域网暴露 SOCKS
            "udp": udp,
            "proxy": name,
        })
    return listeners


def collect_proxy_defs(upstream: dict, work_dir: str) -> dict[str, dict]:
    """名字 → 完整 proxy 定义,合并上游 inline `proxies` 与 proxy-provider 摊平结果。
    listener 的 `proxy:` 只能解析顶层 proxy,无法解析 proxy-provider 成员,故必须把成员摊平为顶层;
    成员的连接参数 REST API 不暴露,只能从 mihomo reload 后写下的 provider 缓存文件 <work_dir>/<path> 取。
    越界的 provider path(恶意/畸形订阅)直接跳过,避免任意文件读。"""
    defs: dict[str, dict] = {}
    for proxy in (upstream.get("proxies") or []):
        if isinstance(proxy, dict) and proxy.get("name"):
            defs[proxy["name"]] = proxy

    work_root = os.path.realpath(work_dir)
    for spec in (upstream.get("proxy-providers") or {}).values():
        path = (spec or {}).get("path")
        if not path:
            continue
        cache = os.path.realpath(os.path.join(work_dir, path))
        if cache != work_root and not cache.startswith(work_root + os.sep):
            continue                                     # path 逃出 work_dir → 跳过
        try:
            with open(cache, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        for proxy in (data.get("proxies") or []):
            if isinstance(proxy, dict) and proxy.get("name"):
                defs[proxy["name"]] = proxy
    return defs


def build_pinned_config(
    proxies: list[dict], listeners: list[dict], *, secret: str, controller: str = "127.0.0.1:9090"
) -> dict:
    """钉定阶段 runtime:每个出站都作为顶层 proxy(listener 的 `proxy:` 只能解析顶层 proxy,
    不能解析 proxy-provider 成员),listener 把端口流量钉死走对应出站;无 provider/组、规则惰性占位。"""
    return {
        "external-controller": controller,
        "secret": secret,
        "mode": "rule",
        "log-level": "warning",
        "dns": {"enable": False},
        "rules": ["MATCH,DIRECT"],
        "proxies": proxies,
        "listeners": listeners,
    }


def build_runtime_config(
    upstream: dict, listeners: list[dict], *, secret: str, controller: str = "127.0.0.1:9090"
) -> dict:
    """mihomo runtime:无全局入站、不劫持 DNS、规则惰性占位;只保留节点来源。
    listener 钉死出站 + 空惰 rules → mihomo 真的不分流,分流全部交给 Surge。"""
    cfg: dict = {
        "external-controller": controller,
        "secret": secret,
        "mode": "rule",
        "log-level": "warning",
        "dns": {"enable": False},
        "rules": ["MATCH,DIRECT"],
        "listeners": listeners,
    }
    for key in ("proxies", "proxy-providers", "proxy-groups"):
        if upstream.get(key) is not None:
            cfg[key] = upstream[key]
    return cfg
