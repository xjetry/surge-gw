from __future__ import annotations

import socket
import threading
import time

import yaml

from surge_gw import assemble, bypass, cache as cachemod, mihomo_config, nodes, port_store, ports
from surge_gw.refresh_policy import should_refresh, Snapshot

_BOOTSTRAP_HEALTH_ATTEMPTS = 50
_BOOTSTRAP_HEALTH_DELAY = 0.1


def _default_resolve(host: str) -> list[str]:
    """Resolve a node server hostname to its IPs via this process's resolver — the same one
    mihomo uses (shared container), so the emitted IP-CIDR rules match what mihomo actually
    dials. Failure degrades to no IP rule (the DOMAIN backup still emits)."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return [info[4][0] for info in infos]


class Orchestrator:
    """Wires the refresh pipeline; single-flight + atomic cache swap + failure leaves last-good intact."""

    def __init__(self, *, config, fetcher, manager, cache, urls, secret,
                 geosite_source, clock=time.time, resolve_host=_default_resolve):
        self.config = config
        self.fetcher = fetcher
        self.manager = manager
        self.cache = cache
        self.urls = urls
        self.secret = secret
        self.geosite_source = geosite_source     # geosite.dat URL (None = skip)
        self.clock = clock
        self.resolve_host = resolve_host          # hostname -> [ip]; injected for tests
        self._lock = threading.Lock()
        self._port_store_path = f"{config.data_dir}/port-map.json"
        self.last_success: float | None = None
        self.last_error: str | None = None
        self._last_snapshot = cache.get()
        self._last_started: float | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        # On-demand single-ruleset rebuild reads these last-good inputs without touching _lock:
        # the rule-provider specs (url/format/behavior) and the live mihomo SOCKS egress port.
        self._last_rp_defs: dict = {}
        self._last_socks_port: int | None = None
        # Per-key single-flight: a Surge fetch storm on one ruleset must not fan out into
        # concurrent upstream pulls; contenders fall back to cache instead of piling on.
        self._ruleset_locks: dict[str, threading.Lock] = {}
        self._ruleset_locks_guard = threading.Lock()

    def bootstrap_mihomo(self) -> None:
        """Seed a controller-enabled minimal config, start mihomo, then wait for its REST controller.
        Started without a config, mihomo writes its own default (no external-controller), so every
        later reload's PUT would be refused; and even with the config, the first reload could outrun
        the controller's startup. Seeding first + waiting for /version makes the first reload reliable."""
        self.manager.write_config(mihomo_config.build_runtime_config({}, [], secret=self.secret))
        self.manager.ensure_alive()
        for _ in range(_BOOTSTRAP_HEALTH_ATTEMPTS):
            if self.manager.healthy():
                return
            time.sleep(_BOOTSTRAP_HEALTH_DELAY)

    def refresh_once(self) -> Snapshot | None:
        if not self._lock.acquire(blocking=False):
            return None                          # in-flight: caller must retry later
        try:
            # Self-heal a crashed mihomo: without this the loop would reload (PUT) forever against a
            # dead controller. bootstrap re-seeds a controller-enabled config, restarts, and waits.
            if not self.manager.alive():
                self.bootstrap_mihomo()

            upstream = yaml.safe_load(self.fetcher.fetch_text(self.config.subscription_url)) or {}

            # Empty-listener reload lets mihomo resolve proxy-providers (and write their flattened
            # cache files) before we query the node list.
            self.manager.reload(mihomo_config.build_runtime_config(upstream, [], secret=self.secret))
            node_names = nodes.select_outbound_nodes(self.manager.get_proxies())

            # Capture provider->member ownership while proxy-providers are still loaded: the pinned
            # reload below strips them (their members are inlined as top-level proxies), after which
            # /providers/proxies no longer reports them and proxy-groups selecting members via
            # `use:`/filter would resolve to nothing.
            pmembers = nodes.provider_members(self.manager.get_providers_proxies())

            # A listener's `proxy:` resolves only against top-level proxies, never proxy-provider
            # members, so every pinnable node needs its full def inlined; a node whose def cannot be
            # recovered cannot be pinned and is dropped from this refresh.
            defs = mihomo_config.collect_proxy_defs(upstream, self.config.data_dir)
            pinned = [name for name in node_names if name in defs]

            prev = port_store.load(self._port_store_path)
            alloc = ports.allocate(pinned, prev,
                                   port_base=self.config.port_base, max_nodes=self.config.max_nodes)
            port_store.save(self._port_store_path, alloc.mapping)

            listeners = mihomo_config.build_listeners(pinned, alloc.mapping)
            self.manager.reload(mihomo_config.build_pinned_config(
                [defs[name] for name in pinned], listeners, secret=self.secret))

            # Pin every node server to DIRECT so a host-side proxy capturing this gateway's
            # egress (e.g. Surge in TUN mode) cannot route a node-server connection back
            # through a node — an infinite loop.
            node_servers = [defs[name].get("server") for name in pinned]
            bypass_rules = bypass.build_bypass_rules(node_servers, self.resolve_host)
            # Domain node servers also go into [General] always-real-ip so a fake-ip/TUN host
            # resolves them to real IPs, letting the IP-CIDR DIRECT bypass above actually match.
            always_real_ip = bypass.domain_servers(node_servers)

            socks_port = min(alloc.mapping.values()) if alloc.mapping else None
            geosite_dat = self._load_geosite(socks_port) if upstream.get("rules") else None

            bundle = assemble.build_config_and_rulesets(
                upstream=upstream, node_port_map=alloc.mapping, provider_members=pmembers,
                urls=self.urls, host=self.config.advertise_host,
                fetch_ruleset_content=lambda url: self._fetch_ruleset(url, socks_port),
                geosite_dat=geosite_dat, update_interval=self.config.surge_update_interval,
                prepend_rule_lines=bypass_rules,
                ruleset_update_interval=self.config.ruleset_update_interval,
                emit_domain_set=self.config.emit_domain_set,
                always_real_ip_domains=always_real_ip,
            )
            snap = Snapshot(surge_text=bundle.surge_text, rulesets=bundle.rulesets,
                            node_port_map=alloc.mapping, skipped=bundle.skipped, dropped=alloc.dropped)
            self.cache.swap(snap)
            cachemod.persist(snap, self.config.data_dir)
            self._last_snapshot = snap
            # Stale-free inputs for on-demand rebuilds, published only on a fully successful refresh.
            self._last_rp_defs = upstream.get("rule-providers") or {}
            self._last_socks_port = socks_port
            self.last_success = self.clock()
            return snap
        except Exception as exc:               # noqa: BLE001 — any failure must not corrupt last-good
            self.last_error = repr(exc)
            return None
        finally:
            self._lock.release()

    def _fetch_ruleset(self, url: str, socks_port: int | None) -> str | None:
        try:
            if socks_port is not None:
                return self.fetcher.fetch_via_socks(url, socks_port).decode("utf-8")
            return self.fetcher.fetch_text(url)
        except Exception:   # noqa: BLE001 — one ruleset's fetch/TLS/decode failure must degrade to skip, never abort the whole refresh
            return None

    def _load_geosite(self, socks_port: int | None) -> bytes | None:
        if not self.geosite_source:
            return None
        try:
            if socks_port is not None:
                return self.fetcher.fetch_via_socks(self.geosite_source, socks_port)
            return self.fetcher.fetch_text(self.geosite_source).encode("utf-8")
        except OSError:
            return None

    def _ruleset_lock(self, key: str) -> threading.Lock:
        with self._ruleset_locks_guard:
            lock = self._ruleset_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._ruleset_locks[key] = lock
            return lock

    def fetch_ruleset_live(self, key: str) -> str | None:
        """On-demand re-fetch+convert of a single upstream rule-provider for GET /ruleset/<key>.
        Returns fresh Surge ruleset text, or None to fall back to last-good cache: out-of-scope key
        (geosite/undefined), single-flight contention, or any fetch/convert failure. Never blocks on
        the full-refresh lock and never raises, so a slow or dead upstream degrades to cache, not a
        hang or a 500 back to Surge. Serves the body only; the surge_text RULE-SET/DOMAIN-SET kind is
        left to the next full refresh, so a behavior flip self-corrects there rather than here."""
        spec = self._last_rp_defs.get(key)
        if spec is None:                          # geosite-* and undefined providers serve cache
            return None
        lock = self._ruleset_lock(key)
        if not lock.acquire(blocking=False):      # a fetch for this key is already in flight
            return None
        try:
            url = spec.get("url")
            if not url:
                return None
            content = self._fetch_live(url)
            if content is None:
                return None
            return assemble.rebuild_ruleset_text(content, spec, self.config.emit_domain_set)
        except Exception:   # noqa: BLE001 — any parse/convert failure degrades to cache, never reaches Surge
            return None
        finally:
            lock.release()

    def _fetch_live(self, url: str) -> str | None:
        timeout = self.config.ruleset_live_timeout
        port = self._last_socks_port
        try:
            if port is not None:
                return self.fetcher.fetch_via_socks(url, port, timeout=timeout).decode("utf-8")
            return self.fetcher.fetch_text(url, timeout=timeout)
        except Exception:   # noqa: BLE001 — fetch/TLS/decode failure degrades to cache
            return None

    def request_refresh(self) -> None:
        """Debounce-gated synchronous refresh kick; concurrent in-flight calls are merged by refresh_once's single-flight lock."""
        now = self.clock()
        if should_refresh(now, self._last_started,
                          self._lock.locked(), self.config.min_refresh_interval):
            self._last_started = now
            self.refresh_once()
            self._wake.set()  # nudge the background loop to re-evaluate now rather than after the full interval

    def force_refresh(self) -> None:
        """Synchronous refresh ignoring the debounce window so GET /surge/sync returns freshly-built
        config (not last-good). Single-flight: refresh_once takes the refresh lock non-blocking, so a
        concurrent in-flight refresh is not stacked — the caller then serves the current cache. Runs
        on the request thread (ThreadingHTTPServer serves each request on its own thread), so it
        blocks only this one /surge/sync response, never the whole server."""
        self._last_started = self.clock()
        self.refresh_once()

    def nudge(self) -> None:
        """Wake the background refresh loop (debounced) without running refresh_once on the caller's
        thread. GET /surge calls this so Surge's frequent managed-config polls return cache instantly
        while still triggering an upstream refresh; the fresh config lands on a later poll. Distinct
        from force_refresh, which refreshes synchronously on the request thread."""
        if should_refresh(self.clock(), self._last_started,
                          self._lock.locked(), self.config.min_refresh_interval):
            self._wake.set()

    def start_background(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        self._last_started = self.clock()
        self.refresh_once()
        while not self._stop.is_set():
            self._wake.wait(timeout=self.config.refresh_interval)
            self._wake.clear()
            if self._stop.is_set():
                return
            self._last_started = self.clock()
            self.refresh_once()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def health(self) -> dict:
        snap = self._last_snapshot
        return {
            "nodes": len(snap.node_port_map),
            "dropped": snap.dropped,
            "skipped": len(snap.skipped),
            "last_success": self.last_success,
            "last_error": self.last_error,
        }
