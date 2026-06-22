"""极简 SOCKS5 服务器:打印每个 CONNECT 请求的目标地址后立即关闭连接。
仅用于验证上游(Surge)交来的目标是域名还是 IP(fake-ip)。"""
import socket
import struct
import threading

HOST, PORT = "0.0.0.0", 11800


def handle(conn: socket.socket) -> None:
    try:
        # greeting: VER, NMETHODS, METHODS...
        ver, nmethods = conn.recv(2)
        conn.recv(nmethods)
        conn.sendall(b"\x05\x00")  # no-auth
        # request: VER, CMD, RSV, ATYP, ADDR, PORT
        header = conn.recv(4)
        if len(header) < 4:
            return
        atyp = header[3]
        if atyp == 0x01:  # IPv4
            addr = socket.inet_ntoa(conn.recv(4))
        elif atyp == 0x03:  # domain
            length = conn.recv(1)[0]
            # SOCKS5 domain field carries the hostname bytes; decode tolerantly.
            # The idna codec rejects error handlers, so use utf-8 to never crash —
            # the only thing this smoke check needs is "domain vs 198.18.x.x".
            addr = conn.recv(length).decode("utf-8", "replace")
        elif atyp == 0x04:  # IPv6
            addr = socket.inet_ntop(socket.AF_INET6, conn.recv(16))
        else:
            addr = "?"
        dport = struct.unpack("!H", conn.recv(2))[0]
        kind = "DOMAIN" if atyp == 0x03 else "IP"
        print(f"CONNECT {kind} -> {addr}:{dport}", flush=True)
    finally:
        conn.close()


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(64)
    print(f"socks logger on {HOST}:{PORT}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
