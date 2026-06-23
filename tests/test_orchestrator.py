import yaml

from surge_gw.cache import Cache
from surge_gw.config import from_env
from surge_gw.orchestrator import Orchestrator
from surge_gw.refresh_policy import Snapshot
from surge_gw.urls import RulesetUrls

SUB = yaml.safe_dump({
    "proxies": [{"name": "A"}],
    "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["A"]}],
    "rules": ["RULE-SET,cnlist,Proxy", "MATCH,Proxy"],
    "rule-providers": {"cnlist": {"behavior": "domain", "format": "text", "url": "http://h/cn.txt"}},
})


class FakeFetcher:
    def __init__(self, sub, fail=False, socks_error=None):
        self.sub = sub
        self.fail = fail
        self.socks_error = socks_error
    def fetch_text(self, url, *, timeout=30.0):
        if self.fail:
            raise OSError("subscription down")
        return self.sub
    def fetch_via_socks(self, url, socks_port, *, timeout=30.0):
        if self.socks_error is not None:
            raise self.socks_error
        return b"+.cn\n"


class FakeManager:
    def __init__(self):
        self.reloads = 0
        self.reload_configs = []
        self.written = []
        self.events = []
        self._alive = True
    def write_config(self, config):
        self.written.append(config)
        self.events.append("write")
    def alive(self):
        return self._alive
    def ensure_alive(self):
        self.events.append("start")
        self._alive = True
        return True
    def healthy(self):
        return True
    def reload(self, config):
        self.reloads += 1
        self.reload_configs.append(config)
    def get_proxies(self):
        return {"proxies": {"A": {"type": "Shadowsocks"}}}
    def get_providers_proxies(self):
        return {"providers": {}}


def _cfg(tmp_path):
    return from_env({"SUBSCRIPTION_URL": "http://h/sub",
                     "DATA_DIR": str(tmp_path), "MIN_REFRESH_INTERVAL": "300"})


def _orch(tmp_path, fetcher):
    cfg = _cfg(tmp_path)
    urls = RulesetUrls(host=cfg.advertise_host, port=cfg.http_port)
    return Orchestrator(config=cfg, fetcher=fetcher, manager=FakeManager(),
                        cache=Cache(Snapshot(surge_text="placeholder")),
                        urls=urls, secret="s", geosite_source=None, resolve_host=lambda h: [])


PROVIDER_SUB = yaml.safe_dump({
    "proxy-providers": {"merged": {"type": "http", "path": "./providers/merged.yaml"}},
    "rules": ["MATCH,DIRECT"],
})


class ProviderManager(FakeManager):
    def get_proxies(self):
        return {"proxies": {"N1": {"type": "Shadowsocks"}}}   # 节点全来自 provider


def test_provider_members_flattened_to_top_level_for_pinning(tmp_path):
    # mihomo phase-1 reload 后把 provider 成员摊平写到 <data_dir>/providers/merged.yaml
    (tmp_path / "providers").mkdir()
    (tmp_path / "providers" / "merged.yaml").write_text(
        yaml.safe_dump({"proxies": [{"name": "N1", "type": "ss", "server": "h", "port": 9}]}))
    cfg = _cfg(tmp_path)
    urls = RulesetUrls(host=cfg.advertise_host, port=cfg.http_port)
    o = Orchestrator(config=cfg, fetcher=FakeFetcher(PROVIDER_SUB), manager=ProviderManager(),
                     cache=Cache(Snapshot(surge_text="placeholder")),
                     urls=urls, secret="s", geosite_source=None, resolve_host=lambda h: [])
    snap = o.refresh_once()
    assert snap is not None
    assert snap.node_port_map == {"N1": 1200}                       # provider 成员成为可钉定节点
    phase2 = o.manager.reload_configs[-1]
    assert {"name": "N1", "type": "ss", "server": "h", "port": 9} in phase2["proxies"]  # 摊平为顶层 proxy
    assert phase2["listeners"][0]["proxy"] == "N1"                  # listener 钉定该顶层 proxy
    assert "proxy-providers" not in phase2                          # phase-2 不再需要 provider


TIMING_SUB = yaml.safe_dump({
    "proxy-providers": {"merged": {"type": "http", "path": "./providers/merged.yaml"}},
    "proxy-groups": [{"name": "hk", "type": "select", "use": ["merged"], "filter": "🇭🇰"}],
    "rules": ["MATCH,hk"],
})


class ProviderTimingManager(FakeManager):
    """mihomo 的 /providers/proxies 反映当前已加载的配置:`merged` 仅在 proxy-providers
    仍在配置里时存在(empty-listener reload),钉定 reload 把 provider 摊平剥离后就只剩
    合成的 `default`。用它复现"provider 成员必须在剥离前抓取"的时序。"""
    def get_proxies(self):
        return {"proxies": {"🇭🇰hk": {"type": "Shadowsocks"}, "🇯🇵jp": {"type": "Shadowsocks"}}}
    def get_providers_proxies(self):
        last = self.reload_configs[-1] if self.reload_configs else {}
        if "proxy-providers" in last:
            return {"providers": {"merged": {"proxies": [{"name": "🇭🇰hk"}, {"name": "🇯🇵jp"}]}}}
        return {"providers": {"default": {"proxies": [{"name": "🇭🇰hk"}, {"name": "🇯🇵jp"}]}}}


def test_provider_members_captured_before_strip_for_filtered_groups(tmp_path):
    (tmp_path / "providers").mkdir()
    (tmp_path / "providers" / "merged.yaml").write_text(yaml.safe_dump({"proxies": [
        {"name": "🇭🇰hk", "type": "ss", "server": "h", "port": 1},
        {"name": "🇯🇵jp", "type": "ss", "server": "h", "port": 2},
    ]}))
    cfg = _cfg(tmp_path)
    urls = RulesetUrls(host=cfg.advertise_host, port=cfg.http_port)
    o = Orchestrator(config=cfg, fetcher=FakeFetcher(TIMING_SUB), manager=ProviderTimingManager(),
                     cache=Cache(Snapshot(surge_text="placeholder")),
                     urls=urls, secret="s", geosite_source=None, resolve_host=lambda h: [])
    snap = o.refresh_once()
    assert snap is not None
    # 'hk' 组靠 use:[merged]+filter 选 🇭🇰 子集;只有在 provider 被剥离前抓取成员才解析得出
    assert "hk = select, 🇭🇰hk" in snap.surge_text


BYPASS_SUB = yaml.safe_dump({
    "proxies": [{"name": "A", "type": "ss", "server": "203.0.113.7", "port": 8388}],
    "rules": ["MATCH,DIRECT"],
})


def test_refresh_emits_server_bypass_rules_at_top(tmp_path):
    # 节点服务器地址必须以 DIRECT 落在 [Rule] 顶部,断开 host 侧代理重捕 egress 造成的环
    cfg = _cfg(tmp_path)
    urls = RulesetUrls(host=cfg.advertise_host, port=cfg.http_port)
    o = Orchestrator(config=cfg, fetcher=FakeFetcher(BYPASS_SUB), manager=FakeManager(),
                     cache=Cache(Snapshot(surge_text="placeholder")),
                     urls=urls, secret="s", geosite_source=None, resolve_host=lambda h: [])
    snap = o.refresh_once()
    assert snap is not None
    rule_body = snap.surge_text.split("[Rule]\n", 1)[1]
    assert rule_body.startswith("IP-CIDR,203.0.113.7/32,DIRECT,no-resolve\n")


def test_refresh_once_builds_and_swaps_cache(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    snap = o.refresh_once()
    assert snap is not None
    assert "A = socks5, 127.0.0.1, 1200, udp-relay=true" in snap.surge_text
    assert snap.node_port_map == {"A": 1200}
    assert snap.rulesets["cnlist"] == ".cn\n"
    assert o.cache.get().surge_text == snap.surge_text       # 换了缓存
    assert (tmp_path / "cache" / "surge.conf").exists()      # 持久化 last-good
    assert o.health()["last_success"] is not None


def test_refresh_rebootstraps_when_mihomo_dead(tmp_path):
    # mihomo 中途崩溃后,下一次刷新必须先重新播种配置并重启进程(自愈),否则 reload 永久失败
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.manager._alive = False
    snap = o.refresh_once()
    assert snap is not None
    assert o.manager.events[:2] == ["write", "start"]   # 重启发生在刷新流水线之前


def test_refresh_failure_keeps_last_good(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB, fail=True))
    o.cache.swap(Snapshot(surge_text="GOOD"))
    assert o.refresh_once() is None
    assert o.cache.get().surge_text == "GOOD"                # 未被污染
    assert o.health()["last_error"] is not None


def test_refresh_skips_ruleset_when_fetch_raises_non_oserror(tmp_path):
    # 一个 rule-provider 拉取抛非 OSError(如 https 之外的 scheme → ValueError)不得连累整次刷新
    err = ValueError("fetch_via_socks supports http/https only")
    o = _orch(tmp_path, FakeFetcher(SUB, socks_error=err))
    snap = o.refresh_once()
    assert snap is not None                                  # 整次刷新仍成功
    assert snap.node_port_map == {"A": 1200}                 # 节点保留
    assert "cnlist" not in snap.rulesets                     # 拉取失败的 ruleset 不托管
    assert any(s.kind == "ruleset" and s.detail == "cnlist" for s in snap.skipped)


def test_single_flight_rejects_reentrant(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    o._lock.acquire()                                        # 模拟在途
    try:
        assert o.refresh_once() is None
    finally:
        o._lock.release()


def test_lock_released_after_successful_refresh(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    snap1 = o.refresh_once()
    assert snap1 is not None
    assert snap1.node_port_map == {"A": 1200}
    snap2 = o.refresh_once()
    assert snap2 is not None
    assert snap2.node_port_map == {"A": 1200}


def test_request_refresh_runs_when_not_debounced(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.request_refresh()                      # 同步执行一次(测试用直驱)
    assert o.cache.get().node_port_map == {"A": 1200}


def test_bootstrap_mihomo_seeds_controller_config_before_start(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.bootstrap_mihomo()
    mgr = o.manager
    assert mgr.events == ["write", "start"]      # 控制器配置先写盘,mihomo 再起
    seed = mgr.written[0]
    assert seed.get("external-controller")        # 首次启动就带控制器,后续 reload 的 REST 才能打通
    assert seed.get("secret") == "s"              # 与 manager 同一 secret


def test_request_refresh_debounced(tmp_path):
    ticks = [1000.0]
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.clock = lambda: ticks[0]
    o.request_refresh()                      # 第一次跑
    first = o.last_success
    ticks[0] = 1100.0                        # 距上次 100s < 300 防抖
    o.request_refresh()
    assert o.last_success == first           # 没再跑


class UrlAwareFetcher:
    """fetch_text 区分订阅 URL 与 ruleset URL,以便覆盖无节点(socks_port=None)的直连拉取路径。"""
    def __init__(self, sub_url, sub_body, ruleset_body):
        self.sub_url = sub_url
        self.sub_body = sub_body
        self.ruleset_body = ruleset_body
        self.socks_calls = 0
    def fetch_text(self, url, *, timeout=30.0):
        return self.sub_body if url == self.sub_url else self.ruleset_body
    def fetch_via_socks(self, url, socks_port, *, timeout=30.0):
        self.socks_calls += 1
        return self.ruleset_body.encode()


class NoNodeManager(FakeManager):
    def get_proxies(self):
        return {"proxies": {}}                 # 无任何节点 → 无端口 → socks_port=None


def test_no_nodes_fetches_rulesets_directly(tmp_path):
    sub = yaml.safe_dump({
        "proxies": [],
        "rules": ["RULE-SET,cnlist,DIRECT", "MATCH,DIRECT"],
        "rule-providers": {"cnlist": {"behavior": "domain", "format": "text", "url": "http://h/cn.txt"}},
    })
    fetcher = UrlAwareFetcher("http://h/sub", sub, "+.cn\n")
    cfg = _cfg(tmp_path)
    urls = RulesetUrls(host=cfg.advertise_host, port=cfg.http_port)
    o = Orchestrator(config=cfg, fetcher=fetcher, manager=NoNodeManager(),
                     cache=Cache(Snapshot(surge_text="placeholder")),
                     urls=urls, secret="s", geosite_source=None, resolve_host=lambda h: [])
    snap = o.refresh_once()
    assert snap is not None
    assert snap.node_port_map == {}            # 无可钉定节点
    assert snap.rulesets["cnlist"] == ".cn\n"  # ruleset 经直连(fetch_text)托管;"+." 前缀被规范化为前导点
    assert fetcher.socks_calls == 0            # 无 socks 端口 → 未走 socks 路径


def test_nudge_sets_wake_on_first_call(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.nudge()
    assert o._wake.is_set()                              # 唤醒后台 _loop
    assert o.cache.get().surge_text == "placeholder"     # 未在调用线程跑 refresh_once
    assert o.last_success is None                        # refresh 从未执行


def test_nudge_debounced_within_min_interval(tmp_path):
    ticks = [1000.0]
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.clock = lambda: ticks[0]
    o._last_started = 1000.0
    ticks[0] = 1100.0                # 距上次起跑 100s < 300 防抖窗口
    o.nudge()
    assert not o._wake.is_set()      # 防抖窗口内不唤醒,避免高频 /surge 打爆上游


def test_nudge_wakes_after_min_interval(tmp_path):
    ticks = [1000.0]
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.clock = lambda: ticks[0]
    o._last_started = 1000.0
    ticks[0] = 1400.0                # 距上次起跑 400s >= 300
    o.nudge()
    assert o._wake.is_set()


def test_nudge_skips_when_in_flight(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    o._lock.acquire()                # 模拟刷新在途
    try:
        o.nudge()
        assert not o._wake.is_set()  # 在途时不唤醒,不叠加重操作
    finally:
        o._lock.release()
