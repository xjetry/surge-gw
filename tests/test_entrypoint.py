import signal
import threading

from surge_gw.__main__ import random_secret


def test_random_secret_is_nonempty_and_varies():
    a, b = random_secret(), random_secret()
    assert a and b and a != b


def test_shutdown_quiesces_then_stops_server():
    from surge_gw.__main__ import shutdown
    order = []
    class S:
        def shutdown(self): order.append("server")
    class O:
        def stop(self): order.append("orch")
    class M:
        def stop(self): order.append("mgr")
    shutdown(S(), O(), M())
    assert order == ["orch", "mgr", "server"]   # 先停工再杀子进程,最后关 server


def test_serve_until_stopped_starts_server_then_tears_down():
    from surge_gw.__main__ import serve_until_stopped
    order = []
    started = threading.Event()
    class S:
        def serve_forever(self): started.set()
        def shutdown(self): order.append("server")
    class O:
        def stop(self): order.append("orch")
    class M:
        def stop(self): order.append("mgr")
    ev = threading.Event()
    ev.set()                                    # 已请求停止 → 立即走关停
    serve_until_stopped(S(), O(), M(), ev)
    assert started.wait(timeout=2)              # server 线程已起
    assert order == ["orch", "mgr", "server"]


def test_install_signal_handlers_sets_event_on_sigterm():
    from surge_gw.__main__ import install_signal_handlers
    orig_term, orig_int = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        ev = threading.Event()
        install_signal_handlers(ev)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert ev.is_set()
    finally:
        signal.signal(signal.SIGTERM, orig_term)
        signal.signal(signal.SIGINT, orig_int)
