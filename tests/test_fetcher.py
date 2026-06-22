import shutil
import socket as _socket
import ssl as _ssl
import struct as _struct
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from surge_gw.fetcher import fetch_text, fetch_via_socks


def _origin(body: bytes):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_fetch_text_returns_body():
    srv = _origin(b"hello-sub")
    try:
        port = srv.server_address[1]
        assert fetch_text(f"http://127.0.0.1:{port}/sub") == "hello-sub"
    finally:
        srv.shutdown()


def _forwarding_socks(target_host: str, target_port: int):
    """极简 socks5:握手 + CONNECT,然后双向转发到固定 target。仅测试用。"""
    srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                greeting = conn.recv(2)
                ver, nmethods = greeting[0], greeting[1]
                assert ver == 0x05
                methods = conn.recv(nmethods)
            except OSError:
                conn.close(); continue
            _handle(conn)

    def _handle(conn):
        # greeting
        conn.sendall(b"\x05\x00")
        header = conn.recv(4)
        assert header[0] == 0x05 and header[1] == 0x01
        atyp = header[3]
        if atyp == 0x01:
            conn.recv(4)
        elif atyp == 0x03:
            conn.recv(conn.recv(1)[0])
        elif atyp == 0x04:
            conn.recv(16)
        conn.recv(2)
        conn.sendall(b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00" + _struct.pack("!H", 0))
        up = _socket.create_connection((target_host, target_port), timeout=5)
        try:
            req = conn.recv(65536)
            up.sendall(req)
            while True:
                data = up.recv(65536)
                if not data:
                    break
                conn.sendall(data)
        finally:
            up.close(); conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv


def test_fetch_via_socks_through_tunnel():
    origin = _origin(b"ruleset-body")
    try:
        oport = origin.server_address[1]
        socks = _forwarding_socks("127.0.0.1", oport)
        try:
            sport = socks.getsockname()[1]
            body = fetch_via_socks(f"http://example.test/data", sport)
            assert body == b"ruleset-body"
        finally:
            socks.close()
    finally:
        origin.shutdown()


def _gen_self_signed(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("openssl unavailable")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-keyout", str(key), "-out", str(cert), "-subj", "/CN=example.test",
         "-addext", "subjectAltName=DNS:example.test"],
        check=True, capture_output=True)
    return str(cert), str(key)


def _tls_origin(body: bytes, certfile: str, keyfile: str):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile, keyfile)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _bidi_forwarding_socks(target_host: str, target_port: int):
    """socks5(握手 + CONNECT)后双向转发到固定 target;TLS 握手需多轮往返,故必须全双工。"""
    srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def _pump(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            try:
                b.shutdown(_socket.SHUT_WR)
            except OSError:
                pass

    def _handle(conn):
        try:
            greeting = conn.recv(2)
            conn.recv(greeting[1])                       # methods
            conn.sendall(b"\x05\x00")
            header = conn.recv(4)
            atyp = header[3]
            if atyp == 0x01:
                conn.recv(4)
            elif atyp == 0x03:
                conn.recv(conn.recv(1)[0])
            elif atyp == 0x04:
                conn.recv(16)
            conn.recv(2)                                 # port
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00" + _struct.pack("!H", 0))
            up = _socket.create_connection((target_host, target_port), timeout=5)
        except OSError:
            conn.close()
            return
        t1 = threading.Thread(target=_pump, args=(conn, up), daemon=True)
        t2 = threading.Thread(target=_pump, args=(up, conn), daemon=True)
        t1.start(); t2.start(); t1.join(); t2.join()
        up.close(); conn.close()

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=_handle, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return srv


def test_fetch_via_socks_https_through_tunnel(tmp_path, monkeypatch):
    certfile, keyfile = _gen_self_signed(tmp_path)
    origin = _tls_origin(b"tls-ruleset-body", certfile, keyfile)
    try:
        oport = origin.server_address[1]
        socks = _bidi_forwarding_socks("127.0.0.1", oport)
        try:
            sport = socks.getsockname()[1]
            trust = _ssl.create_default_context(cafile=certfile)   # 验签:信任自签 CA、SAN 匹配 example.test
            monkeypatch.setattr(_ssl, "create_default_context", lambda: trust)
            body = fetch_via_socks("https://example.test/data", sport)
            assert body == b"tls-ruleset-body"
        finally:
            socks.close()
    finally:
        origin.shutdown()
