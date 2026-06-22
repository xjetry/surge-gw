from __future__ import annotations

import json
import os
import subprocess
import urllib.request

import yaml


class MihomoManager:
    """托管 mihomo:REST 联动(reload/读节点表/健康)+ 子进程生命周期。"""

    def __init__(self, bin_path, config_path, work_dir, *,
                 controller="127.0.0.1:9090", secret="", command=None):
        self.bin_path = bin_path
        self.config_path = config_path
        self.work_dir = work_dir
        self.controller = controller
        self.secret = secret
        self._command = command
        self._proc = None

    def _api(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://{self.controller}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.secret:
            req.add_header("Authorization", f"Bearer {self.secret}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}

    def healthy(self) -> bool:
        try:
            self._api("GET", "/version")
            return True
        except OSError:
            return False

    def get_proxies(self) -> dict:
        return self._api("GET", "/proxies")

    def get_providers_proxies(self) -> dict:
        return self._api("GET", "/providers/proxies")

    def write_config(self, config: dict) -> None:
        """只把 runtime 配置写盘,不触发 reload。用于首次启动前播下带 external-controller
        的配置,使 mihomo 起来时控制器就在听,后续 reload 的 REST 调用才能连得上。"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    def reload(self, config: dict) -> None:
        """写 runtime 配置后触发 reload(尽量不断开 Surge 已建连接)。"""
        self.write_config(config)
        self._api("PUT", "/configs?force=true", {"path": os.path.abspath(self.config_path)})

    def _build_command(self) -> list[str]:
        return self._command or [self.bin_path, "-f", self.config_path, "-d", self.work_dir]

    def start(self) -> None:
        self._proc = subprocess.Popen(self._build_command())

    def stop(self) -> None:
        """Terminate the process if running; SIGKILL fallback after a grace period. Safe to call when not running (guarded) — the post-kill wait blocks (no timeout) because SIGKILL is uncatchable, so it reaps promptly."""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

    def alive(self) -> bool:
        """进程在运行返回 True;供后台刷新循环检测崩溃后自愈,而非每轮盲目重启。"""
        return self._proc is not None and self._proc.poll() is None

    def ensure_alive(self) -> bool:
        """进程缺失或已退出则(重)启动;返回是否发生了(重)启动。"""
        if not self.alive():
            self.start()
            return True
        return False
