import json
import threading
import urllib.request

from surge_gw.cache import Cache
from surge_gw.config import from_env
from surge_gw.http_server import build_server
from surge_gw.refresh_policy import Snapshot


class _FakeOrch:
    def __init__(self):
        self.refreshes = 0
    def request_refresh(self):
        self.refreshes += 1
    def health(self):
        return {"nodes": 1}


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
