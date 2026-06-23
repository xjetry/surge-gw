import json
import threading
import urllib.request

from surge_gw.cache import Cache
from surge_gw.config import from_env
from surge_gw.http_server import build_server
from surge_gw.refresh_policy import Snapshot


class _FakeOrch:
    def __init__(self, live=None):
        self.refreshes = 0
        self.force_refreshes = 0
        self.nudges = 0
        self.live = live            # fetch_ruleset_live 返回值;None = 回退缓存
        self.live_keys = []
    def request_refresh(self):
        self.refreshes += 1
    def force_refresh(self):
        self.force_refreshes += 1
    def nudge(self):
        self.nudges += 1
    def health(self):
        return {"nodes": 1}
    def fetch_ruleset_live(self, key):
        self.live_keys.append(key)
        return self.live


def _serve(cache, orch):
    cfg = from_env({"SUBSCRIPTION_URL": "http://x/s", "HTTP_PORT": "0"})
    srv = build_server(cache, orch, cfg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_serves_cache_unauthenticated():
    cache = Cache(Snapshot(surge_text="SURGE-BODY", rulesets={"cnlist": ".cn\n"}))
    srv, port = _serve(cache, _FakeOrch())
    try:
        assert _get(port, "/surge") == (200, b"SURGE-BODY")           # 仅绑回环 → 无鉴权
        assert _get(port, "/ruleset/cnlist") == (200, b".cn\n")
        assert _get(port, "/ruleset/nope")[0] == 404
        assert _get(port, "/health")[0] == 200
    finally:
        srv.shutdown()


def test_refresh_triggers_orchestrator():
    orch = _FakeOrch()
    srv, port = _serve(Cache(Snapshot(surge_text="x")), orch)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/refresh", method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 202
        assert orch.refreshes == 1
    finally:
        srv.shutdown()


def test_server_binds_http_bind_not_advertise_host():
    cfg = from_env({"SUBSCRIPTION_URL": "http://x/s", "HTTP_PORT": "0",
                    "HTTP_BIND": "127.0.0.1", "ADVERTISE_HOST": "203.0.113.9"})
    srv = build_server(Cache(Snapshot(surge_text="x")), _FakeOrch(), cfg)
    try:
        assert srv.server_address[0] == "127.0.0.1"   # 绑定取 HTTP_BIND,而非 ADVERTISE_HOST
    finally:
        srv.server_close()


def test_ruleset_get_serves_live_when_available():
    orch = _FakeOrch(live="LIVE.cn\n")
    cache = Cache(Snapshot(surge_text="x", rulesets={"cnlist": ".cn\n"}))
    srv, port = _serve(cache, orch)
    try:
        assert _get(port, "/ruleset/cnlist") == (200, b"LIVE.cn\n")  # 同步按需拉取的最新内容优先于缓存
        assert orch.live_keys == ["cnlist"]
    finally:
        srv.shutdown()


def test_ruleset_get_falls_back_to_cache_when_live_none():
    orch = _FakeOrch(live=None)
    cache = Cache(Snapshot(surge_text="x", rulesets={"cnlist": ".cn\n"}))
    srv, port = _serve(cache, orch)
    try:
        assert _get(port, "/ruleset/cnlist") == (200, b".cn\n")      # live 不可用 → last-good 缓存
    finally:
        srv.shutdown()


def test_ruleset_get_404_when_neither_live_nor_cache():
    orch = _FakeOrch(live=None)
    srv, port = _serve(Cache(Snapshot(surge_text="x")), orch)
    try:
        assert _get(port, "/ruleset/missing")[0] == 404
    finally:
        srv.shutdown()


def test_surge_serves_cache_and_nudges():
    orch = _FakeOrch()
    cache = Cache(Snapshot(surge_text="SURGE-BODY"))
    srv, port = _serve(cache, orch)
    try:
        assert _get(port, "/surge") == (200, b"SURGE-BODY")   # 秒回缓存快照
        assert orch.nudges == 1                               # 异步唤醒一次刷新
        assert orch.force_refreshes == 0                      # 不在请求线程同步刷新
    finally:
        srv.shutdown()


def test_surge_sync_force_refreshes_then_serves():
    orch = _FakeOrch()
    cache = Cache(Snapshot(surge_text="SURGE-BODY"))
    srv, port = _serve(cache, orch)
    try:
        assert _get(port, "/surge/sync") == (200, b"SURGE-BODY")  # 同步刷新后返回最新构建
        assert orch.force_refreshes == 1                          # 每次访问都强制刷新一次
        assert orch.nudges == 0
    finally:
        srv.shutdown()
