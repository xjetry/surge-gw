import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from surge_gw.mihomo_manager import MihomoManager


def _fake_mihomo():
    state = {"reloaded": [], "auth_headers": []}

    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            state["auth_headers"].append(self.headers.get("Authorization"))
            if self.path == "/version":
                self._json(200, {"version": "fake"})
            elif self.path == "/proxies":
                self._json(200, {"proxies": {"A": {"type": "Shadowsocks"}}})
            elif self.path == "/providers/proxies":
                self._json(200, {"providers": {}})
            else:
                self._json(404, {})
        def do_PUT(self):
            state["auth_headers"].append(self.headers.get("Authorization"))
            length = int(self.headers.get("Content-Length", 0))
            state["reloaded"].append(self.rfile.read(length).decode())
            self.send_response(204); self.end_headers()
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


def test_rest_client(tmp_path):
    srv, state = _fake_mihomo()
    try:
        port = srv.server_address[1]
        m = MihomoManager("mihomo", str(tmp_path / "runtime.yaml"), str(tmp_path),
                          controller=f"127.0.0.1:{port}", secret="")
        assert m.healthy() is True
        assert m.get_proxies() == {"proxies": {"A": {"type": "Shadowsocks"}}}
        assert m.get_providers_proxies() == {"providers": {}}
        m.reload({"mode": "rule"})
        assert (tmp_path / "runtime.yaml").exists()   # 配置已写盘
        assert len(state["reloaded"]) == 1            # PUT /configs 被调用
    finally:
        srv.shutdown()


def test_healthy_false_when_unreachable(tmp_path):
    m = MihomoManager("mihomo", str(tmp_path / "r.yaml"), str(tmp_path),
                      controller="127.0.0.1:1", secret="")
    assert m.healthy() is False


def test_rest_client_sends_bearer_when_secret_set(tmp_path):
    srv, state = _fake_mihomo()
    try:
        port = srv.server_address[1]
        secret = "s3cr3t"
        m = MihomoManager("mihomo", str(tmp_path / "runtime.yaml"), str(tmp_path),
                          controller=f"127.0.0.1:{port}", secret=secret)
        # Call methods that trigger GET and PUT
        assert m.get_proxies() == {"proxies": {"A": {"type": "Shadowsocks"}}}
        m.reload({"mode": "rule"})
        # Verify Authorization header was sent with Bearer token
        assert any(auth == f"Bearer {secret}" for auth in state["auth_headers"]), \
            f"Expected Bearer {secret} in auth_headers, got {state['auth_headers']}"
    finally:
        srv.shutdown()


def test_rest_client_no_bearer_when_secret_empty(tmp_path):
    srv, state = _fake_mihomo()
    try:
        port = srv.server_address[1]
        m = MihomoManager("mihomo", str(tmp_path / "runtime.yaml"), str(tmp_path),
                          controller=f"127.0.0.1:{port}", secret="")
        # Call methods that trigger GET and PUT
        assert m.get_proxies() == {"proxies": {"A": {"type": "Shadowsocks"}}}
        m.reload({"mode": "rule"})
        # Verify no Authorization header was sent (all should be None)
        assert all(auth is None for auth in state["auth_headers"]), \
            f"Expected no Authorization headers, got {state['auth_headers']}"
    finally:
        srv.shutdown()


import sys


def test_subprocess_start_supervise_restart(tmp_path):
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    m = MihomoManager("mihomo", str(tmp_path / "r.yaml"), str(tmp_path), command=cmd)
    m.start()
    try:
        assert m._proc is not None and m._proc.poll() is None   # 活着
        assert m.ensure_alive() is False                         # 没死,不重启
        m._proc.kill(); m._proc.wait(timeout=5)
        assert m.ensure_alive() is True                          # 检测到死,重启
        assert m._proc.poll() is None
    finally:
        m.stop()
        assert m._proc.poll() is not None                        # 已停


def test_alive_reflects_process_state(tmp_path):
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    m = MihomoManager("mihomo", str(tmp_path / "r.yaml"), str(tmp_path), command=cmd)
    assert m.alive() is False           # 未启动
    m.start()
    try:
        assert m.alive() is True
    finally:
        m.stop()
    assert m.alive() is False           # 已停 → 供后台循环检测崩溃后自愈


def test_write_config_writes_file_without_reload(tmp_path):
    srv, state = _fake_mihomo()
    try:
        port = srv.server_address[1]
        m = MihomoManager("mihomo", str(tmp_path / "runtime.yaml"), str(tmp_path),
                          controller=f"127.0.0.1:{port}", secret="")
        m.write_config({"external-controller": f"127.0.0.1:{port}", "secret": "x"})
        loaded = yaml.safe_load((tmp_path / "runtime.yaml").read_text())
        assert loaded["external-controller"] == f"127.0.0.1:{port}"  # 写到配置路径
        assert state["reloaded"] == []                              # 仅写盘,不触发 PUT /configs
    finally:
        srv.shutdown()
