from __future__ import annotations

import http.client
import socket
import ssl
import struct
import urllib.request
from urllib.parse import urlparse


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes; recv() may return short, so loop until satisfied."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("socks5 connection closed mid-handshake")
        buf.extend(chunk)
    return bytes(buf)


def _socks5_connect(socks_port: int, host: str, port: int, timeout: float) -> socket.socket:
    """对本地 socks5 完成无鉴权握手 + CONNECT,返回隧道 socket。"""
    s = socket.create_connection(("127.0.0.1", socks_port), timeout=timeout)
    try:
        s.sendall(b"\x05\x01\x00")
        if _recv_exact(s, 2) != b"\x05\x00":
            raise OSError("socks5 handshake rejected")
        host_b = host.encode("idna")
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + struct.pack("!H", port))
        reply = _recv_exact(s, 4)
        if reply[1] != 0x00:
            raise OSError("socks5 connect failed")
        atyp = reply[3]
        if atyp == 0x01:
            _recv_exact(s, 4)
        elif atyp == 0x03:
            addr_len = _recv_exact(s, 1)[0]
            _recv_exact(s, addr_len)
        elif atyp == 0x04:
            _recv_exact(s, 16)
        _recv_exact(s, 2)  # bound port
        return s
    except BaseException:
        s.close()
        raise


def fetch_via_socks(url: str, socks_port: int, *, timeout: float = 30.0) -> bytes:
    """经本地 socks5 拉取(rule-provider / geosite 走活节点出口)。支持 http 与 https
    (real-world rule-provider 普遍是 https,故 https 在 socks 隧道上再叠一层 TLS)。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("fetch_via_socks supports http/https only")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    tunnel = _socks5_connect(socks_port, host, port, timeout)
    try:
        if parsed.scheme == "https":
            tunnel = ssl.create_default_context().wrap_socket(tunnel, server_hostname=host)
    except BaseException:
        tunnel.close()
        raise
    # http.client.HTTPConnection only calls connect() when sock is None; assigning here hands socket
    # ownership to it (it closes the tunnel on conn.close()). For https the socket is already
    # TLS-wrapped, so plain HTTPConnection frames HTTP/1.1 over the encrypted tunnel.
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.sock = tunnel
    try:
        conn.request("GET", path, headers={"Host": host, "User-Agent": "surge-gw"})
        resp = conn.getresponse()
        return resp.read()
    finally:
        conn.close()


def fetch_text(url: str, *, timeout: float = 30.0) -> str:
    """直连拉取(订阅 URL 直连,不走 socks)。返回 utf-8 文本。"""
    req = urllib.request.Request(url, headers={"User-Agent": "surge-gw"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")
