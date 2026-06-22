from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse


def build_server(cache, orchestrator, config) -> ThreadingHTTPServer:
    """ThreadingHTTPServer:一律秒回缓存(对 Surge 异步)。仅绑 http_bind(回环),
    所有端点无鉴权;写进 Surge 配置的 host 用 advertise_host(宿主回环),二者不共用。"""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                body = json.dumps(orchestrator.health()).encode()
                self._send(200, body, "application/json")
                return
            if parsed.path == "/surge":
                self._send(200, cache.get().surge_text.encode())
                return
            if parsed.path.startswith("/ruleset/"):
                key = unquote(parsed.path[len("/ruleset/"):])
                text = cache.get().rulesets.get(key)
                if text is None:
                    self._send(404, b"not found\n")
                    return
                self._send(200, text.encode())
                return
            self._send(404, b"not found\n")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/refresh":
                orchestrator.request_refresh()
                self._send(202, b"accepted\n")
                return
            self._send(404, b"not found\n")

        def log_message(self, *args) -> None:  # 静默默认访问日志
            pass

    return ThreadingHTTPServer((config.http_bind, config.http_port), Handler)
