from __future__ import annotations

import os
import secrets
import signal
import threading

from surge_gw import cache as cachemod
from surge_gw.config import Config, from_env
from surge_gw.http_server import build_server
from surge_gw.mihomo_manager import MihomoManager
from surge_gw.orchestrator import Orchestrator
from surge_gw.refresh_policy import Snapshot, placeholder_surge
from surge_gw.urls import RulesetUrls


def random_secret() -> str:
    return secrets.token_urlsafe(24)


def build_app(config: Config):
    """Assemble without starting; returns (server, orchestrator, manager)."""
    secret = random_secret()
    urls = RulesetUrls(host=config.advertise_host, port=config.http_port)

    last_good = cachemod.load_last_good(config.data_dir)
    initial = last_good or Snapshot(
        surge_text=placeholder_surge(urls.managed(), config.surge_update_interval))
    cache = cachemod.Cache(initial)

    manager = MihomoManager(
        config.mihomo_bin, os.path.join(config.data_dir, "runtime.yaml"), config.data_dir,
        secret=secret)
    orchestrator = Orchestrator(
        config=config, fetcher=_fetcher_module(), manager=manager, cache=cache,
        urls=urls, secret=secret, geosite_source=config.geosite_url)
    server = build_server(cache, orchestrator, config)
    return server, orchestrator, manager


def _fetcher_module():
    from surge_gw import fetcher
    return fetcher


def shutdown(server, orchestrator, manager) -> None:
    """停后台刷新、终止 mihomo 子进程、关停 HTTP server。
    次序:先让刷新线程停手、再杀子进程,避免在途 reload 比子进程活得久;server 最后关。"""
    orchestrator.stop()
    manager.stop()
    server.shutdown()


def serve_until_stopped(server, orchestrator, manager, stop_event) -> None:
    """server 跑后台线程,主线程阻塞到 stop_event 置位再优雅关停。
    作为容器 PID 1,显式关停让 docker stop 立即返回(而非等 SIGKILL),并回收 mihomo 子进程。"""
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop_event.wait()
    shutdown(server, orchestrator, manager)


def install_signal_handlers(stop_event) -> None:
    """SIGTERM/SIGINT 均置位 stop_event,主线程从 serve_until_stopped 阻塞中退出并优雅关停。"""
    def _handler(signum, frame):
        stop_event.set()
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main() -> None:
    config = from_env(os.environ)
    server, orchestrator, manager = build_app(config)
    orchestrator.bootstrap_mihomo()
    orchestrator.start_background()
    print(f"surge-gw serving on {config.advertise_host}:{config.http_port}", flush=True)
    stop_event = threading.Event()
    install_signal_handlers(stop_event)
    serve_until_stopped(server, orchestrator, manager, stop_event)


if __name__ == "__main__":
    main()
