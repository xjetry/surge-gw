# surge-gw Plan 3 — 运行时层(节点流水线 + serve 流水线)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把远程 mihomo 订阅落地为本地 socks 端口,并通过 HTTP 暴露完整 Surge `#!MANAGED-CONFIG` 订阅与自托管 ruleset;`SUBSCRIPTION_URL` → mihomo 起 socks → `/surge` 秒回缓存。

**Architecture:** 纯函数(config/mihomo_config/nodes/urls/refresh_policy/assemble)做可 golden 测试的核心;impure 层(fetcher/port_store/cache/mihomo_manager/http_server/orchestrator)对**进程内 fake** 测;真实 mihomo 子进程用**手动 smoke 闸门**验证。orchestrator 用依赖注入串起刷新流水线(单飞 + 防抖 + 缓存原子替换),复用 Plan 1/2 全部转换纯函数。HTTP 用 stdlib `ThreadingHTTPServer` + threading,一律 serve-from-cache。

**Tech Stack:** Python 3.12+、PyYAML(运行期,已有)、stdlib(http.server / urllib / socket / subprocess / threading / json)、pytest。无额外运行期依赖。

依据设计:[`docs/superpowers/specs/2026-06-22-surge-gw-plan3-runtime-design.md`](../specs/2026-06-22-surge-gw-plan3-runtime-design.md)。

## Global Constraints

- Python 3.12+;运行期依赖仅 `pyyaml`,其余全 stdlib。HTTP 用 `http.server.ThreadingHTTPServer`,并发用 threading(后台刷新线程 + 单飞锁)。
- mihomo 为纯静态 socks provider:runtime 配置**无任何全局入站**(无 mixed-port/port/socks-port)、`dns.enable:false`、`mode:rule`、`rules:["MATCH,DIRECT"]` 惰性占位、listener `proxy:` 钉死出站、`listen:0.0.0.0`、`external-controller 127.0.0.1:9090` + 随机 secret。
- 端口区间 `PORT_BASE..PORT_BASE+MAX_NODES-1`,默认 `1200..1299`;同名节点跨刷新保端口(`port-map.json`)。
- 类型决策沿用 Plan 2:rule-provider `domain`→DOMAIN-SET、`ipcidr`/`classical`→RULE-SET;geosite 无 keyword→DOMAIN-SET、含 keyword→RULE-SET;反填用 `reconcile.rewrite_ruleset_types`。
- 一律 serve-from-cache;缓存原子换指针,绝不服务半成品;冷启动回 last-good 或最简占位(`FINAL,DIRECT`)。
- token:首次随机生成、持久化、打日志;`/surge`/`/ruleset`/`/refresh` 需 token,`/health` 不需;自托管 ruleset URL 带同一 token。
- impure 层对进程内 fake 测,**不在自动化测试里跑真实 mihomo 二进制**;真实 mihomo 用手动 smoke。
- 提交信息与代码注释**禁止过程污染**:不得出现任务/步骤编号、方案代号、审阅轮次、临时引用;只解释 WHY 与不变量。
- TDD:先写失败测试,再最小实现;频繁提交;DRY;YAGNI。

---

## File Structure

```
src/surge_gw/
├── config.py            # 纯:env → Config
├── mihomo_config.py     # 纯:节点+端口 → listeners;upstream → runtime.yaml dict
├── nodes.py             # 纯:/proxies 筛选出站节点;/providers/proxies → provider 成员
├── urls.py              # 纯:带 token 的自托管 URL 构造(喂 Plan 1 回调 + Plan 2 反填)
├── refresh_policy.py    # 纯:单飞/防抖判定;Snapshot;占位配置
├── assemble.py          # 纯(注入 fetch 回调):Plan 1/2 转换 + 反填 → Bundle
├── fetcher.py           # impure:直连 / 经 socks 拉取
├── port_store.py        # impure:port-map.json 原子读写
├── cache.py             # impure:Snapshot 原子引用 + last-good 持久化
├── mihomo_manager.py    # impure:子进程生命周期 + REST 客户端
├── http_server.py       # impure:ThreadingHTTPServer + 端点 + token
├── orchestrator.py      # impure:刷新流水线协调(DI)
└── __main__.py          # impure:入口装配
smoke/
└── plan3_runtime.md     # 手动 mihomo 闸门步骤
tests/
├── test_config.py  test_mihomo_config.py  test_nodes.py  test_urls.py
├── test_refresh_policy.py  test_assemble.py  test_fetcher.py
├── test_port_store.py  test_cache.py  test_mihomo_manager.py
├── test_http_server.py  test_orchestrator.py  test_entrypoint.py
```

依赖方向:`assemble`→Plan 1/2 纯函数;`orchestrator`→上述全部 + assemble;`__main__`→全部。无环。

---

### Task 1: `config.py` — env → Config

**Files:**
- Create: `src/surge_gw/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Config` frozen dataclass(字段见实现)。
  - `from_env(env: Mapping[str, str]) -> Config` — 缺 `SUBSCRIPTION_URL` 抛 `ValueError`;空字符串视作未设;token/geosite_url 空 → `None`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config.py
import pytest
from surge_gw.config import from_env, Config


def test_required_url_missing_raises():
    with pytest.raises(ValueError):
        from_env({})


def test_defaults_applied():
    c = from_env({"SUBSCRIPTION_URL": "http://x/sub"})
    assert c.subscription_url == "http://x/sub"
    assert c.advertise_host == "127.0.0.1"
    assert c.http_port == 8080
    assert c.port_base == 1200
    assert c.max_nodes == 100
    assert c.refresh_interval == 21600
    assert c.min_refresh_interval == 300
    assert c.geosite_ttl == 86400
    assert c.geosite_url is None
    assert c.subscription_token is None
    assert c.surge_update_interval == 3600
    assert c.mihomo_bin == "mihomo"
    assert c.data_dir == "./data"


def test_overrides_and_int_parsing():
    c = from_env({
        "SUBSCRIPTION_URL": "http://x/sub",
        "HTTP_PORT": "9000", "PORT_BASE": "2000", "MAX_NODES": "10",
        "SUBSCRIPTION_TOKEN": "tok", "GEOSITE_URL": "http://g/geosite.dat",
        "MIHOMO_BIN": "/opt/mihomo", "DATA_DIR": "/data",
    })
    assert (c.http_port, c.port_base, c.max_nodes) == (9000, 2000, 10)
    assert c.subscription_token == "tok"
    assert c.geosite_url == "http://g/geosite.dat"
    assert c.mihomo_bin == "/opt/mihomo"
    assert c.data_dir == "/data"


def test_empty_string_token_is_none():
    c = from_env({"SUBSCRIPTION_URL": "http://x/sub", "SUBSCRIPTION_TOKEN": ""})
    assert c.subscription_token is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL(ModuleNotFoundError: surge_gw.config)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/config.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    subscription_url: str
    advertise_host: str
    http_port: int
    port_base: int
    max_nodes: int
    refresh_interval: int
    min_refresh_interval: int
    geosite_ttl: int
    geosite_url: str | None
    subscription_token: str | None
    surge_update_interval: int
    mihomo_bin: str
    data_dir: str


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    return int(raw) if raw not in (None, "") else default


def from_env(env: Mapping[str, str]) -> Config:
    """从环境解析配置。SUBSCRIPTION_URL 必填;token 留空时由调用方自举并持久化。"""
    url = env.get("SUBSCRIPTION_URL")
    if not url:
        raise ValueError("SUBSCRIPTION_URL is required")
    return Config(
        subscription_url=url,
        advertise_host=env.get("ADVERTISE_HOST") or "127.0.0.1",
        http_port=_int(env, "HTTP_PORT", 8080),
        port_base=_int(env, "PORT_BASE", 1200),
        max_nodes=_int(env, "MAX_NODES", 100),
        refresh_interval=_int(env, "REFRESH_INTERVAL", 21600),
        min_refresh_interval=_int(env, "MIN_REFRESH_INTERVAL", 300),
        geosite_ttl=_int(env, "GEOSITE_TTL", 86400),
        geosite_url=env.get("GEOSITE_URL") or None,
        subscription_token=env.get("SUBSCRIPTION_TOKEN") or None,
        surge_update_interval=_int(env, "SURGE_UPDATE_INTERVAL", 3600),
        mihomo_bin=env.get("MIHOMO_BIN") or "mihomo",
        data_dir=env.get("DATA_DIR") or "./data",
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/config.py tests/test_config.py
git commit -m "feat: parse runtime config from environment"
```

---

### Task 2: `mihomo_config.py` — listeners + runtime 配置

**Files:**
- Create: `src/surge_gw/mihomo_config.py`
- Test: `tests/test_mihomo_config.py`

**Interfaces:**
- Produces:
  - `build_listeners(node_names: list[str], port_map: dict[str, int], *, udp: bool = True) -> list[dict]` — 每个有端口的节点一个 socks listener;无端口的跳过。
  - `build_runtime_config(upstream: dict, listeners: list[dict], *, secret: str, controller: str = "127.0.0.1:9090") -> dict`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mihomo_config.py
from surge_gw.mihomo_config import build_listeners, build_runtime_config


def test_build_listeners_shape_and_skip():
    ls = build_listeners(["A", "B"], {"A": 1200})  # B 无端口
    assert ls == [
        {"name": "p1200", "type": "socks", "port": 1200,
         "listen": "0.0.0.0", "udp": True, "proxy": "A"},
    ]


def test_runtime_has_no_global_inbound_and_lazy_rules():
    upstream = {"proxies": [{"name": "A"}], "proxy-groups": [{"name": "G"}],
                "proxy-providers": {"p": {}}, "rules": ["DOMAIN,x,G"]}
    cfg = build_runtime_config(upstream, [{"name": "p1200"}], secret="s")
    assert cfg["external-controller"] == "127.0.0.1:9090"
    assert cfg["secret"] == "s"
    assert cfg["mode"] == "rule"
    assert cfg["dns"] == {"enable": False}
    assert cfg["rules"] == ["MATCH,DIRECT"]            # upstream rules 不带入
    assert cfg["listeners"] == [{"name": "p1200"}]
    assert cfg["proxies"] == [{"name": "A"}]
    assert cfg["proxy-groups"] == [{"name": "G"}]
    assert cfg["proxy-providers"] == {"p": {}}
    for inbound in ("mixed-port", "port", "socks-port", "redir-port"):
        assert inbound not in cfg


def test_runtime_omits_absent_upstream_sections():
    cfg = build_runtime_config({"proxies": [{"name": "A"}]}, [], secret="s")
    assert "proxies" in cfg
    assert "proxy-groups" not in cfg
    assert "proxy-providers" not in cfg
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_mihomo_config.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/mihomo_config.py
from __future__ import annotations


def build_listeners(node_names: list[str], port_map: dict[str, int], *, udp: bool = True) -> list[dict]:
    """每个有端口的节点一个 socks listener;proxy 字段(SpecialProxy)把该端口流量
    钉死走该出站、绕过规则引擎,故 runtime 的 rules 可保持惰性占位。"""
    listeners: list[dict] = []
    for name in node_names:
        port = port_map.get(name)
        if port is None:
            continue
        listeners.append({
            "name": f"p{port}",
            "type": "socks",
            "port": port,
            "listen": "0.0.0.0",
            "udp": udp,
            "proxy": name,
        })
    return listeners


def build_runtime_config(
    upstream: dict, listeners: list[dict], *, secret: str, controller: str = "127.0.0.1:9090"
) -> dict:
    """mihomo runtime:无全局入站、不劫持 DNS、规则惰性占位;只保留节点来源。
    listener 钉死出站 + 空惰 rules → mihomo 真的不分流,分流全部交给 Surge。"""
    cfg: dict = {
        "external-controller": controller,
        "secret": secret,
        "mode": "rule",
        "log-level": "warning",
        "dns": {"enable": False},
        "rules": ["MATCH,DIRECT"],
        "listeners": listeners,
    }
    for key in ("proxies", "proxy-providers", "proxy-groups"):
        if upstream.get(key) is not None:
            cfg[key] = upstream[key]
    return cfg
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_mihomo_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/mihomo_config.py tests/test_mihomo_config.py
git commit -m "feat: build mihomo runtime config with pinned socks listeners"
```

---

### Task 3: `nodes.py` — /proxies 筛选 + provider 成员

**Files:**
- Create: `src/surge_gw/nodes.py`
- Test: `tests/test_nodes.py`

**Interfaces:**
- Produces:
  - `select_outbound_nodes(proxies_resp: dict) -> list[str]` — 从 mihomo `/proxies` 滤掉策略组类型与内置项,只留具体出站节点名(保持响应顺序)。
  - `provider_members(providers_resp: dict) -> dict[str, list[str]]` — 从 `/providers/proxies` 取 provider→成员名。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_nodes.py
from surge_gw.nodes import select_outbound_nodes, provider_members


def test_select_excludes_groups_and_builtins():
    resp = {"proxies": {
        "DIRECT": {"type": "Direct"},
        "REJECT": {"type": "Reject"},
        "GLOBAL": {"type": "Selector"},
        "Auto": {"type": "URLTest"},
        "LB": {"type": "LoadBalance"},
        "Chain": {"type": "Relay"},
        "ss-1": {"type": "Shadowsocks"},
        "vless-1": {"type": "Vless"},
    }}
    assert select_outbound_nodes(resp) == ["ss-1", "vless-1"]


def test_select_empty_when_no_proxies_key():
    assert select_outbound_nodes({}) == []


def test_provider_members_extraction():
    resp = {"providers": {
        "prov1": {"name": "prov1", "proxies": [{"name": "A"}, {"name": "B"}]},
        "default": {"name": "default", "proxies": []},
    }}
    assert provider_members(resp) == {"prov1": ["A", "B"], "default": []}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_nodes.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/nodes.py
from __future__ import annotations

# mihomo /proxies 里非"具体出站"的 type:策略组 + 内置项。用排除法,
# 避免穷举所有出站协议(协议是开放集,新增协议不应漏掉)。
_NON_OUTBOUND_TYPES = {
    "Selector", "URLTest", "Fallback", "LoadBalance", "Relay",
    "Direct", "Reject", "RejectDrop", "Compatible", "Pass",
}


def select_outbound_nodes(proxies_resp: dict) -> list[str]:
    """只保留具体出站节点(可建 listener 的);策略组与内置项排除。保持响应顺序。"""
    out: list[str] = []
    for name, info in (proxies_resp.get("proxies") or {}).items():
        if info.get("type") in _NON_OUTBOUND_TYPES:
            continue
        out.append(name)
    return out


def provider_members(providers_resp: dict) -> dict[str, list[str]]:
    """provider 名 → 成员节点名,供策略组 use: 展开。"""
    result: dict[str, list[str]] = {}
    for name, info in (providers_resp.get("providers") or {}).items():
        result[name] = [p["name"] for p in (info.get("proxies") or [])]
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_nodes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/nodes.py tests/test_nodes.py
git commit -m "feat: select concrete outbound nodes and provider membership"
```

---

### Task 4: `urls.py` — 自托管 URL 构造

**Files:**
- Create: `src/surge_gw/urls.py`
- Test: `tests/test_urls.py`

**Interfaces:**
- Produces:
  - `RulesetUrls(host: str, port: int, token: str)` frozen dataclass,方法 `ruleset(name) -> str`、`geosite(cat) -> str`、`managed() -> str`。
  - rule-provider 端点路径段 = `name`;geosite 端点路径段 = `geosite-<cat>`(http_server 据此查 cache.rulesets;reconcile 据完整 URL 反填,故三者必须同源)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_urls.py
from surge_gw.urls import RulesetUrls


def test_url_builders():
    u = RulesetUrls(host="127.0.0.1", port=8080, token="tok")
    assert u.ruleset("mylist") == "http://127.0.0.1:8080/ruleset/mylist?token=tok"
    assert u.geosite("google@cn") == "http://127.0.0.1:8080/ruleset/geosite-google@cn?token=tok"
    assert u.managed() == "http://127.0.0.1:8080/surge?token=tok"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_urls.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/urls.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RulesetUrls:
    """自托管 URL 构造。三处必须同源:Plan 1 规则行里的 URL、Plan 2 反填的
    domain_set_urls、以及 http_server 的端点路径段。"""
    host: str
    port: int
    token: str

    def _base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def ruleset(self, name: str) -> str:
        return f"{self._base()}/ruleset/{name}?token={self.token}"

    def geosite(self, cat: str) -> str:
        return f"{self._base()}/ruleset/geosite-{cat}?token={self.token}"

    def managed(self) -> str:
        return f"{self._base()}/surge?token={self.token}"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_urls.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/urls.py tests/test_urls.py
git commit -m "feat: build self-hosted ruleset and managed-config urls"
```

---

### Task 5: `refresh_policy.py` — 单飞/防抖 + Snapshot + 占位

**Files:**
- Create: `src/surge_gw/refresh_policy.py`
- Test: `tests/test_refresh_policy.py`

**Interfaces:**
- Consumes: `models.SkippedItem`、`surge_config.build_surge_config`。
- Produces:
  - `should_refresh(now: float, last_started: float | None, in_flight: bool, min_interval: float) -> bool`。
  - `Snapshot` dataclass:`surge_text: str`、`rulesets: dict[str, str]`、`node_port_map: dict[str, int]`、`skipped: list[SkippedItem]`、`dropped: list[str]`。
  - `placeholder_surge(managed_url: str, update_interval: int) -> str`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_refresh_policy.py
from surge_gw.refresh_policy import should_refresh, Snapshot, placeholder_surge


def test_should_refresh_single_flight_and_debounce():
    assert should_refresh(100.0, None, False, 300) is True        # 从未跑过
    assert should_refresh(100.0, 100.0, True, 300) is False       # 在途 → 单飞拒绝
    assert should_refresh(200.0, 100.0, False, 300) is False      # 距上次 100s < 300 防抖
    assert should_refresh(500.0, 100.0, False, 300) is True       # 距上次 400s ≥ 300


def test_snapshot_defaults():
    s = Snapshot(surge_text="x")
    assert s.rulesets == {} and s.node_port_map == {}
    assert s.skipped == [] and s.dropped == []


def test_placeholder_is_minimal_valid_config():
    text = placeholder_surge("http://h/surge?token=t", 3600)
    lines = text.splitlines()
    assert lines[0] == "#!MANAGED-CONFIG http://h/surge?token=t interval=3600 strict=false"
    assert "FINAL,DIRECT" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_refresh_policy.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/refresh_policy.py
from __future__ import annotations

from dataclasses import dataclass, field

from surge_gw import surge_config
from surge_gw.models import SkippedItem


def should_refresh(now: float, last_started: float | None, in_flight: bool, min_interval: float) -> bool:
    """单飞(在途拒绝)+ 防抖(距上次启动不足 min_interval 拒绝)。"""
    if in_flight:
        return False
    if last_started is None:
        return True
    return (now - last_started) >= min_interval


@dataclass
class Snapshot:
    """一次成功转换的完整产物;serve-from-cache 的原子单元。"""
    surge_text: str
    rulesets: dict[str, str] = field(default_factory=dict)   # 端点路径段 → 文件文本
    node_port_map: dict[str, int] = field(default_factory=dict)
    skipped: list[SkippedItem] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def placeholder_surge(managed_url: str, update_interval: int) -> str:
    """冷启动未就绪时的最简合法配置;Surge 按 interval 重取。"""
    return surge_config.build_surge_config(
        proxy_lines=[], group_lines=[], rule_lines=["FINAL,DIRECT"],
        managed_url=managed_url, update_interval=update_interval,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_refresh_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/refresh_policy.py tests/test_refresh_policy.py
git commit -m "feat: refresh debounce policy, snapshot model, placeholder config"
```

---

### Task 6: `assemble.py` — Plan 1/2 转换 + 反填 → Bundle

把上游配置 + 节点端口映射 + provider 成员,经 Plan 1/2 纯函数 + 注入的 fetch 回调,组装成完整 Surge 文本与各 ruleset 文本。fetch 回调注入 → 可用 fake 离线测。

**Files:**
- Create: `src/surge_gw/assemble.py`
- Test: `tests/test_assemble.py`

**Interfaces:**
- Consumes: `naming`、`proxies`、`groups`、`rules`、`providers`、`geosite`、`reconcile`、`surge_config`、`models.SkippedItem`、`urls.RulesetUrls`。
- Produces:
  - `Bundle` dataclass:`surge_text: str`、`rulesets: dict[str, str]`、`skipped: list[SkippedItem]`。
  - `build_config_and_rulesets(*, upstream: dict, node_port_map: dict[str, int], provider_members: dict[str, list[str]], urls: RulesetUrls, host: str, fetch_ruleset_content: Callable[[str], str | None], geosite_dat: bytes | None, update_interval: int, general: dict | None = None) -> Bundle`。
- 不变量:rule-provider 缺定义/`mrs`/拉取失败/未知 behavior → 记 `SkippedItem` 不产半条;DOMAIN-SET 的引用收集到 `domain_set_urls` 后用 `reconcile` 反填;ruleset 端点键与 `urls` 同源(rule-provider 用 `name`,geosite 用 `geosite-<ref>`)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_assemble.py
from surge_gw.assemble import build_config_and_rulesets, Bundle
from surge_gw.urls import RulesetUrls

# 复用 Plan 2 的 protobuf 编码器思路构造一个最小 geosite.dat
def _v(n):
    o = bytearray()
    while True:
        b = n & 0x7F; n >>= 7; o.append(b | (0x80 if n else 0))
        if not n: return bytes(o)
def _tag(f, w): return _v((f << 3) | w)
def _ld(f, d): return _tag(f, 2) + _v(len(d)) + d
def _vi(f, n): return _tag(f, 0) + _v(n)
def _s(f, s): return _ld(f, s.encode())
def _dom(t, val): return _ld(2, _vi(1, t) + _s(2, val))
def _geo(code, doms): return _ld(1, _s(1, code) + b"".join(doms))

UP = {
    "proxies": [{"name": "A"}, {"name": "B"}],
    "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["A", "B"]}],
    "rules": [
        "DOMAIN-SUFFIX,google.com,Proxy",
        "RULE-SET,cnlist,Proxy",
        "GEOSITE,cn,Proxy",
        "MATCH,Proxy",
    ],
    "rule-providers": {
        "cnlist": {"behavior": "domain", "format": "text", "url": "http://h/cn.txt"},
    },
}
URLS = RulesetUrls(host="127.0.0.1", port=8080, token="t")


def test_assemble_wires_conversion_and_promotes_domain_set():
    geo = _geo("cn", [_dom(2, "qq.com")])           # 纯域名 → DOMAIN-SET
    def fetch_rs(url):
        assert url == "http://h/cn.txt"
        return "+.cn\nexample.cn\n"
    b = build_config_and_rulesets(
        upstream=UP, node_port_map={"A": 1200, "B": 1201}, provider_members={},
        urls=URLS, host="127.0.0.1", fetch_ruleset_content=fetch_rs,
        geosite_dat=geo, update_interval=3600,
    )
    assert isinstance(b, Bundle)
    # 节点 socks 行
    assert "A = socks5, 127.0.0.1, 1200, udp-relay=true" in b.surge_text
    # 组
    assert "Proxy = select, A, B" in b.surge_text
    # rule-provider 是纯域名 → 反填成 DOMAIN-SET
    assert "DOMAIN-SET,http://127.0.0.1:8080/ruleset/cnlist?token=t,Proxy" in b.surge_text
    # geosite 纯域名 → 反填成 DOMAIN-SET
    assert "DOMAIN-SET,http://127.0.0.1:8080/ruleset/geosite-cn?token=t,Proxy" in b.surge_text
    # ruleset 内容已托管(端点键)
    assert b.rulesets["cnlist"] == ".cn\nexample.cn\n"
    assert b.rulesets["geosite-cn"] == ".qq.com\n"


def test_assemble_skips_missing_and_mrs_providers():
    up = {**UP, "rules": ["RULE-SET,missing,Proxy", "RULE-SET,bin,Proxy", "MATCH,Proxy"],
          "rule-providers": {"bin": {"behavior": "domain", "format": "mrs", "url": "http://h/b.mrs"}}}
    b = build_config_and_rulesets(
        upstream=up, node_port_map={"A": 1200}, provider_members={}, urls=URLS,
        host="127.0.0.1", fetch_ruleset_content=lambda u: None, geosite_dat=None, update_interval=3600)
    reasons = " ".join(s.reason for s in b.skipped)
    assert "not defined" in reasons and "mrs" in reasons
    assert "cnlist" not in b.rulesets and "bin" not in b.rulesets
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_assemble.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/assemble.py
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from surge_gw import geosite as geomod
from surge_gw import groups as groupmod
from surge_gw import naming
from surge_gw import providers as providermod
from surge_gw import proxies as proxymod
from surge_gw import reconcile
from surge_gw import rules as rulemod
from surge_gw import surge_config
from surge_gw.models import SkippedItem
from surge_gw.urls import RulesetUrls


@dataclass
class Bundle:
    surge_text: str
    rulesets: dict[str, str] = field(default_factory=dict)
    skipped: list[SkippedItem] = field(default_factory=list)


def _provider_artifact(entries: list[str], behavior: str):
    if behavior == "domain":
        return providermod.convert_domain_provider(entries)
    if behavior == "ipcidr":
        return providermod.convert_ipcidr_provider(entries)
    if behavior == "classical":
        return providermod.convert_classical_provider(entries)
    return None


def build_config_and_rulesets(
    *,
    upstream: dict,
    node_port_map: dict[str, int],
    provider_members: dict[str, list[str]],
    urls: RulesetUrls,
    host: str,
    fetch_ruleset_content: Callable[[str], str | None],
    geosite_dat: bytes | None,
    update_interval: int,
    general: dict | None = None,
) -> Bundle:
    """串起 Plan 1/2 转换:节点/组/规则 → Surge,远程引用经注入回调拉取并转换,
    纯域名引用反填为 DOMAIN-SET。fetch 回调注入,本函数对其结果做纯转换。"""
    node_names = list(node_port_map.keys())
    groups = upstream.get("proxy-groups") or []
    name_map = naming.build_name_map([*node_names, *(g["name"] for g in groups)])
    available = set(node_names)

    proxy_lines = proxymod.build_proxy_section(node_names, name_map, node_port_map, host=host)
    group_lines, skipped = groupmod.convert_groups(groups, name_map, available, provider_members)

    result = rulemod.convert_rules(upstream.get("rules") or [], name_map, urls.ruleset, urls.geosite)
    skipped.extend(result.skipped)

    rulesets: dict[str, str] = {}
    domain_set_urls: set[str] = set()
    rp_defs = upstream.get("rule-providers") or {}

    for name in sorted(result.rule_providers):
        spec = rp_defs.get(name)
        if spec is None:
            skipped.append(SkippedItem("ruleset", name, "rule-provider not defined upstream"))
            continue
        if spec.get("format") == "mrs":
            skipped.append(SkippedItem("ruleset", name, "mrs rule-provider unsupported"))
            continue
        content = fetch_ruleset_content(spec.get("url", ""))
        if content is None:
            skipped.append(SkippedItem("ruleset", name, "rule-provider fetch failed"))
            continue
        entries = providermod.extract_provider_entries(content, spec.get("format", "yaml"))
        art = _provider_artifact(entries, spec.get("behavior"))
        if art is None:
            skipped.append(SkippedItem("ruleset", name, f"unknown behavior {spec.get('behavior')}"))
            continue
        rulesets[name] = "\n".join(art.lines) + "\n"
        skipped.extend(art.skipped)
        if art.kind == "DOMAIN-SET":
            domain_set_urls.add(urls.ruleset(name))

    if result.geosites:
        cats = geomod.decode_geosite_dat(geosite_dat) if geosite_dat is not None else {}
        for ref in sorted(result.geosites):
            if geosite_dat is None:
                skipped.append(SkippedItem("geosite", ref, "geosite.dat unavailable"))
                continue
            category, attr = geomod.split_geosite_ref(ref)
            domains = cats.get(category)
            if domains is None:
                skipped.append(SkippedItem("geosite", ref, "geosite category not found"))
                continue
            art = geomod.build_geosite_artifact(domains, attr)
            rulesets[f"geosite-{ref}"] = "\n".join(art.lines) + "\n"
            skipped.extend(art.skipped)
            if art.kind == "DOMAIN-SET":
                domain_set_urls.add(urls.geosite(ref))

    rule_lines = reconcile.rewrite_ruleset_types(result.lines, domain_set_urls)
    surge_text = surge_config.build_surge_config(
        proxy_lines=proxy_lines, group_lines=group_lines, rule_lines=rule_lines,
        managed_url=urls.managed(), update_interval=update_interval, general=general,
    )
    return Bundle(surge_text=surge_text, rulesets=rulesets, skipped=skipped)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_assemble.py -v`
Expected: PASS。`name_map` 同时作为 `convert_rules` 的 `policy_map`(节点/组原名→消毒名);builtin policy(DIRECT/REJECT)由 `convert_rules` 内部透传。

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/assemble.py tests/test_assemble.py
git commit -m "feat: assemble full surge config and rulesets from upstream"
```

---

### Task 7: `fetcher.py` — 直连拉取

**Files:**
- Create: `src/surge_gw/fetcher.py`
- Test: `tests/test_fetcher.py`

**Interfaces:**
- Produces: `fetch_text(url: str, *, timeout: float = 30.0) -> str` — urllib 直连 GET,返回 utf-8 文本。

- [ ] **Step 1: 写失败测试(进程内假 HTTP origin)**

```python
# tests/test_fetcher.py
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from surge_gw.fetcher import fetch_text


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_fetcher.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/fetcher.py
from __future__ import annotations

import http.client
import socket
import struct
import urllib.request
from urllib.parse import urlparse


def fetch_text(url: str, *, timeout: float = 30.0) -> str:
    """直连拉取(订阅 URL 直连,不走 socks)。返回 utf-8 文本。"""
    req = urllib.request.Request(url, headers={"User-Agent": "surge-gw"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_fetcher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/fetcher.py tests/test_fetcher.py
git commit -m "feat: fetch remote text over direct connection"
```

---

### Task 8: `fetcher.fetch_via_socks` — 经 socks5 拉取

**Files:**
- Modify: `src/surge_gw/fetcher.py`
- Test: `tests/test_fetcher.py`

**Interfaces:**
- Produces: `fetch_via_socks(url: str, socks_port: int, *, timeout: float = 30.0) -> bytes` — 经本地 socks5 CONNECT 到目标后用 `http.client` 取 body(http only)。

- [ ] **Step 1: 写失败测试(进程内转发 socks5 → 假 origin)**

```python
# tests/test_fetcher.py(追加)
import socket as _socket
import struct as _struct

from surge_gw.fetcher import fetch_via_socks


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
                conn.recv(2); n = conn.recv(1)  # ver+nmethods already; this is simplified below
            except OSError:
                conn.close(); continue
            _handle(conn)

    def _handle(conn):
        # greeting
        conn.sendall(b"\x05\x00")
        header = conn.recv(4)
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
```

> 注:上面的 `_forwarding_socks` 的 greeting 读取写得过简,实现时按真实 socks5 握手(读 `VER,NMETHODS` 再读 methods)修正读法;关键是它对每个连接回 `\x05\x00`、解析 CONNECT、然后把后续字节双向转发到 `target`。先让本测试 RED,再在 Step 3 把客户端写对、必要时修正这个测试夹具的握手读取直到 GREEN。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_fetcher.py::test_fetch_via_socks_through_tunnel -v`
Expected: FAIL(ImportError: fetch_via_socks)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/fetcher.py(追加)
def _socks5_connect(socks_port: int, host: str, port: int, timeout: float) -> socket.socket:
    """对本地 socks5 完成无鉴权握手 + CONNECT,返回隧道 socket。"""
    s = socket.create_connection(("127.0.0.1", socks_port), timeout=timeout)
    try:
        s.sendall(b"\x05\x01\x00")
        if s.recv(2) != b"\x05\x00":
            raise OSError("socks5 handshake rejected")
        host_b = host.encode("idna")
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + struct.pack("!H", port))
        reply = s.recv(4)
        if len(reply) < 4 or reply[1] != 0x00:
            raise OSError("socks5 connect failed")
        atyp = reply[3]
        if atyp == 0x01:
            s.recv(4)
        elif atyp == 0x03:
            s.recv(s.recv(1)[0])
        elif atyp == 0x04:
            s.recv(16)
        s.recv(2)  # bound port
        return s
    except BaseException:
        s.close()
        raise


def fetch_via_socks(url: str, socks_port: int, *, timeout: float = 30.0) -> bytes:
    """经本地 socks5 拉取(rule-provider / geosite 走活节点出口)。仅支持 http。"""
    parsed = urlparse(url)
    if parsed.scheme != "http":
        raise ValueError("fetch_via_socks supports http only")
    host = parsed.hostname or ""
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    tunnel = _socks5_connect(socks_port, host, port, timeout)
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.sock = tunnel  # 让 http.client 在隧道上做 HTTP/1.1 解析(content-length/chunked)
    try:
        conn.request("GET", path, headers={"Host": host, "User-Agent": "surge-gw"})
        resp = conn.getresponse()
        return resp.read()
    finally:
        conn.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_fetcher.py -v`
Expected: PASS(若夹具握手读取不匹配,按 socks5 协议修正 `_forwarding_socks` 的 greeting 读取直至 GREEN)

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/fetcher.py tests/test_fetcher.py
git commit -m "feat: fetch remote content through a local socks5 tunnel"
```

---

### Task 9: `port_store.py` — port-map.json 原子读写

**Files:**
- Create: `src/surge_gw/port_store.py`
- Test: `tests/test_port_store.py`

**Interfaces:**
- Produces:
  - `load(path: str) -> dict[str, int]` — 文件不存在返回 `{}`。
  - `save(path: str, mapping: dict[str, int]) -> None` — 原子写(临时文件 + `os.replace`)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_port_store.py
from surge_gw.port_store import load, save


def test_save_then_load_roundtrip(tmp_path):
    p = str(tmp_path / "sub" / "port-map.json")  # 不存在的子目录也应创建
    save(p, {"A": 1200, "B": 1201})
    assert load(p) == {"A": 1200, "B": 1201}


def test_load_missing_returns_empty(tmp_path):
    assert load(str(tmp_path / "nope.json")) == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_port_store.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/port_store.py
from __future__ import annotations

import json
import os
import tempfile


def load(path: str) -> dict[str, int]:
    """读 port-map.json;不存在返回空映射(首次运行)。"""
    try:
        with open(path, encoding="utf-8") as f:
            return {k: int(v) for k, v in json.load(f).items()}
    except FileNotFoundError:
        return {}


def save(path: str, mapping: dict[str, int]) -> None:
    """原子写:同名节点跨刷新保端口依赖这份持久化,写一半会让端口漂移。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(mapping, f)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_port_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/port_store.py tests/test_port_store.py
git commit -m "feat: persist node-to-port map atomically"
```

---

### Task 10: `cache.py` — Snapshot 原子引用 + last-good 持久化

**Files:**
- Create: `src/surge_gw/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `refresh_policy.Snapshot`。
- Produces:
  - `Cache(snapshot: Snapshot)`:`get() -> Snapshot`、`swap(snapshot: Snapshot) -> None`(加锁原子换引用)。
  - `persist(snapshot: Snapshot, data_dir: str) -> None`、`load_last_good(data_dir: str) -> Snapshot | None`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cache.py
from surge_gw.cache import Cache, persist, load_last_good
from surge_gw.refresh_policy import Snapshot


def test_swap_and_get():
    c = Cache(Snapshot(surge_text="old"))
    assert c.get().surge_text == "old"
    c.swap(Snapshot(surge_text="new"))
    assert c.get().surge_text == "new"


def test_persist_and_load_last_good(tmp_path):
    snap = Snapshot(surge_text="SURGE", rulesets={"cnlist": ".cn\n"},
                    node_port_map={"A": 1200})
    persist(snap, str(tmp_path))
    loaded = load_last_good(str(tmp_path))
    assert loaded is not None
    assert loaded.surge_text == "SURGE"
    assert loaded.rulesets == {"cnlist": ".cn\n"}
    assert loaded.node_port_map == {"A": 1200}


def test_load_last_good_missing_returns_none(tmp_path):
    assert load_last_good(str(tmp_path)) is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_cache.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/cache.py
from __future__ import annotations

import json
import os
import tempfile
import threading

from surge_gw.refresh_policy import Snapshot

_SURGE = "surge.conf"
_META = "meta.json"
_RULESETS = "rulesets"


class Cache:
    """当前 Snapshot 的线程安全原子引用。读多写少,锁只护引用替换。"""

    def __init__(self, snapshot: Snapshot) -> None:
        self._lock = threading.Lock()
        self._snapshot = snapshot

    def get(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def swap(self, snapshot: Snapshot) -> None:
        with self._lock:
            self._snapshot = snapshot


def _atomic_write(path: str, data: str) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def persist(snapshot: Snapshot, data_dir: str) -> None:
    """落 last-good:surge 文本 + 各 ruleset + 端口映射;重启可秒回。"""
    cache_dir = os.path.join(data_dir, "cache")
    _atomic_write(os.path.join(cache_dir, _SURGE), snapshot.surge_text)
    rs_dir = os.path.join(cache_dir, _RULESETS)
    os.makedirs(rs_dir, exist_ok=True)
    keys = list(snapshot.rulesets)
    for key, text in snapshot.rulesets.items():
        _atomic_write(os.path.join(rs_dir, key), text)
    _atomic_write(os.path.join(cache_dir, _META),
                  json.dumps({"ruleset_keys": keys, "node_port_map": snapshot.node_port_map}))


def load_last_good(data_dir: str) -> Snapshot | None:
    """重启读 last-good;缺失返回 None(调用方退回占位)。"""
    cache_dir = os.path.join(data_dir, "cache")
    try:
        with open(os.path.join(cache_dir, _SURGE), encoding="utf-8") as f:
            surge_text = f.read()
        with open(os.path.join(cache_dir, _META), encoding="utf-8") as f:
            meta = json.load(f)
    except FileNotFoundError:
        return None
    rulesets: dict[str, str] = {}
    for key in meta.get("ruleset_keys", []):
        try:
            with open(os.path.join(cache_dir, _RULESETS, key), encoding="utf-8") as f:
                rulesets[key] = f.read()
        except FileNotFoundError:
            continue
    return Snapshot(surge_text=surge_text, rulesets=rulesets,
                    node_port_map={k: int(v) for k, v in meta.get("node_port_map", {}).items()})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_cache.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/cache.py tests/test_cache.py
git commit -m "feat: atomic in-memory cache with last-good persistence"
```

---

### Task 11: `mihomo_manager.py` — REST 客户端

**Files:**
- Create: `src/surge_gw/mihomo_manager.py`
- Test: `tests/test_mihomo_manager.py`

**Interfaces:**
- Produces:
  - `MihomoManager(bin_path, config_path, work_dir, *, controller="127.0.0.1:9090", secret="", command=None)`。
  - REST:`healthy() -> bool`、`get_proxies() -> dict`、`get_providers_proxies() -> dict`、`reload(config: dict) -> None`(写 config 文件后 `PUT /configs?force=true`)。

- [ ] **Step 1: 写失败测试(进程内假 mihomo REST)**

```python
# tests/test_mihomo_manager.py
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from surge_gw.mihomo_manager import MihomoManager


def _fake_mihomo():
    state = {"reloaded": []}

    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            if self.path == "/version":
                self._json(200, {"version": "fake"})
            elif self.path == "/proxies":
                self._json(200, {"proxies": {"A": {"type": "Shadowsocks"}}})
            elif self.path == "/providers/proxies":
                self._json(200, {"providers": {}})
            else:
                self._json(404, {})
        def do_PUT(self):
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_mihomo_manager.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/mihomo_manager.py
from __future__ import annotations

import json
import os
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

    def reload(self, config: dict) -> None:
        """写 runtime 配置后触发 reload(尽量不断开 Surge 已建连接)。"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
        self._api("PUT", "/configs?force=true", {"path": os.path.abspath(self.config_path)})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_mihomo_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/mihomo_manager.py tests/test_mihomo_manager.py
git commit -m "feat: mihomo rest client for reload and node tables"
```

---

### Task 12: `mihomo_manager` — 子进程生命周期

**Files:**
- Modify: `src/surge_gw/mihomo_manager.py`
- Test: `tests/test_mihomo_manager.py`

**Interfaces:**
- Produces:`start() -> None`、`stop() -> None`、`ensure_alive() -> bool`(死了则重启,返回是否(重)启动);命令由 `command` 注入覆盖,默认 `[bin_path, "-f", config_path, "-d", work_dir]`。

- [ ] **Step 1: 写失败测试(假长驻进程)**

```python
# tests/test_mihomo_manager.py(追加)
import sys
import time


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_mihomo_manager.py::test_subprocess_start_supervise_restart -v`
Expected: FAIL(AttributeError: 'MihomoManager' object has no attribute 'start')

- [ ] **Step 3: 实现**

```python
# src/surge_gw/mihomo_manager.py(在文件顶部 import 区追加)
import subprocess

# 在 MihomoManager 类内追加方法:
    def _build_command(self) -> list[str]:
        return self._command or [self.bin_path, "-f", self.config_path, "-d", self.work_dir]

    def start(self) -> None:
        self._proc = subprocess.Popen(self._build_command())

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)

    def ensure_alive(self) -> bool:
        """进程缺失或已退出则(重)启动;返回是否发生了(重)启动。"""
        if self._proc is None or self._proc.poll() is not None:
            self.start()
            return True
        return False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_mihomo_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/mihomo_manager.py tests/test_mihomo_manager.py
git commit -m "feat: supervise mihomo subprocess with restart on exit"
```

---

### Task 13: `http_server.py` — 端点 + token + serve-from-cache

**Files:**
- Create: `src/surge_gw/http_server.py`
- Test: `tests/test_http_server.py`

**Interfaces:**
- Consumes: `Cache`、orchestrator(只需 `request_refresh()`)、`Config`。
- Produces: `build_server(cache, orchestrator, config) -> ThreadingHTTPServer` — 绑 `config.advertise_host:config.http_port`(测试用 `http_port=0` 取临时端口);路由 `/surge` `/ruleset/<key>` `/health` `/refresh`;token 鉴权(`/health` 免);一律秒回缓存。

- [ ] **Step 1: 写失败测试(进程内客户端打真实端点)**

```python
# tests/test_http_server.py
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


def _serve(cache, orch, token):
    cfg = from_env({"SUBSCRIPTION_URL": "http://x/s", "HTTP_PORT": "0",
                    "SUBSCRIPTION_TOKEN": token})
    srv = build_server(cache, orch, cfg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_surge_requires_token_and_serves_cache():
    cache = Cache(Snapshot(surge_text="SURGE-BODY", rulesets={"cnlist": ".cn\n"}))
    orch = _FakeOrch()
    srv, port = _serve(cache, orch, "tok")
    try:
        assert _get(port, "/surge")[0] == 403                         # 无 token
        code, body = _get(port, "/surge?token=tok")
        assert code == 200 and body == b"SURGE-BODY"
        assert _get(port, "/ruleset/cnlist?token=tok") == (200, b".cn\n")
        assert _get(port, "/ruleset/nope?token=tok")[0] == 404
        assert _get(port, "/health")[0] == 200                        # health 免 token
    finally:
        srv.shutdown()


def test_refresh_triggers_orchestrator():
    orch = _FakeOrch()
    srv, port = _serve(Cache(Snapshot(surge_text="x")), orch, "tok")
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/refresh?token=tok", method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 202
        assert orch.refreshes == 1
    finally:
        srv.shutdown()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_http_server.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/http_server.py
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse


def build_server(cache, orchestrator, config) -> ThreadingHTTPServer:
    """ThreadingHTTPServer:一律秒回缓存(对 Surge 异步);token 护 /surge /ruleset /refresh。"""
    token = config.subscription_token

    def authed(qs: dict) -> bool:
        return token is None or qs.get("token", [None])[0] == token

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if parsed.path == "/health":
                body = json.dumps(orchestrator.health()).encode()
                self._send(200, body, "application/json")
                return
            if not authed(qs):
                self._send(403, b"forbidden\n")
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
            qs = parse_qs(parsed.query)
            if parsed.path == "/refresh":
                if not authed(qs):
                    self._send(403, b"forbidden\n")
                    return
                orchestrator.request_refresh()
                self._send(202, b"accepted\n")
                return
            self._send(404, b"not found\n")

        def log_message(self, *args) -> None:  # 静默默认访问日志
            pass

    return ThreadingHTTPServer((config.advertise_host, config.http_port), Handler)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_http_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/http_server.py tests/test_http_server.py
git commit -m "feat: http server serving cached config and rulesets with token"
```

---

### Task 14: `orchestrator.py` — 刷新流水线(refresh_once)

**Files:**
- Create: `src/surge_gw/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Config`、`fetcher`(注入对象,需 `fetch_text`/`fetch_via_socks`)、`MihomoManager`(注入,需 `reload`/`get_proxies`/`get_providers_proxies`)、`Cache`、`RulesetUrls`、`ports`、`port_store`、`nodes`、`mihomo_config`、`assemble`、`cache.persist`。
- Produces:
  - `Orchestrator(*, config, fetcher, manager, cache, urls, secret, geosite_source, clock=time.time)`。
  - `refresh_once() -> Snapshot | None` — 跑完整流水线;单飞(在途返回 None);成功换缓存 + 持久化 + 记 `last_success`;失败保 last-good + 记 `last_error` 返回 None。
  - `health() -> dict`。

- [ ] **Step 1: 写失败测试(全 fake 注入)**

```python
# tests/test_orchestrator.py
import yaml

from surge_gw.cache import Cache
from surge_gw.config import from_env
from surge_gw.orchestrator import Orchestrator
from surge_gw.refresh_policy import Snapshot
from surge_gw.urls import RulesetUrls

SUB = yaml.safe_dump({
    "proxies": [{"name": "A"}],
    "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["A"]}],
    "rules": ["RULE-SET,cnlist,Proxy", "MATCH,Proxy"],
    "rule-providers": {"cnlist": {"behavior": "domain", "format": "text", "url": "http://h/cn.txt"}},
})


class FakeFetcher:
    def __init__(self, sub, fail=False):
        self.sub = sub
        self.fail = fail
    def fetch_text(self, url, *, timeout=30.0):
        if self.fail:
            raise OSError("subscription down")
        return self.sub
    def fetch_via_socks(self, url, socks_port, *, timeout=30.0):
        return b"+.cn\n"


class FakeManager:
    def __init__(self):
        self.reloads = 0
    def reload(self, config):
        self.reloads += 1
    def get_proxies(self):
        return {"proxies": {"A": {"type": "Shadowsocks"}}}
    def get_providers_proxies(self):
        return {"providers": {}}


def _cfg(tmp_path):
    return from_env({"SUBSCRIPTION_URL": "http://h/sub", "SUBSCRIPTION_TOKEN": "t",
                     "DATA_DIR": str(tmp_path), "MIN_REFRESH_INTERVAL": "300"})


def _orch(tmp_path, fetcher):
    cfg = _cfg(tmp_path)
    urls = RulesetUrls(host=cfg.advertise_host, port=cfg.http_port, token="t")
    return Orchestrator(config=cfg, fetcher=fetcher, manager=FakeManager(),
                        cache=Cache(Snapshot(surge_text="placeholder")),
                        urls=urls, secret="s", geosite_source=None)


def test_refresh_once_builds_and_swaps_cache(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    snap = o.refresh_once()
    assert snap is not None
    assert "A = socks5, 127.0.0.1, 1200, udp-relay=true" in snap.surge_text
    assert snap.node_port_map == {"A": 1200}
    assert snap.rulesets["cnlist"] == ".cn\n"
    assert o.cache.get().surge_text == snap.surge_text       # 换了缓存
    assert (tmp_path / "cache" / "surge.conf").exists()      # 持久化 last-good
    assert o.health()["last_success"] is not None


def test_refresh_failure_keeps_last_good(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB, fail=True))
    o.cache.swap(Snapshot(surge_text="GOOD"))
    assert o.refresh_once() is None
    assert o.cache.get().surge_text == "GOOD"                # 未被污染
    assert o.health()["last_error"] is not None


def test_single_flight_rejects_reentrant(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    o._lock.acquire()                                        # 模拟在途
    try:
        assert o.refresh_once() is None
    finally:
        o._lock.release()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/orchestrator.py
from __future__ import annotations

import threading
import time

import yaml

from surge_gw import assemble, cache as cachemod, mihomo_config, nodes, port_store, ports
from surge_gw.refresh_policy import Snapshot


class Orchestrator:
    """串起刷新流水线;单飞 + 缓存原子替换 + 失败保 last-good。"""

    def __init__(self, *, config, fetcher, manager, cache, urls, secret,
                 geosite_source, clock=time.time):
        self.config = config
        self.fetcher = fetcher
        self.manager = manager
        self.cache = cache
        self.urls = urls
        self.secret = secret
        self.geosite_source = geosite_source     # geosite.dat URL(None=不取)
        self.clock = clock
        self._lock = threading.Lock()
        self._port_store_path = f"{config.data_dir}/port-map.json"
        self.last_success: float | None = None
        self.last_error: str | None = None
        self._last_snapshot = cache.get()

    def refresh_once(self) -> Snapshot | None:
        if not self._lock.acquire(blocking=False):
            return None                          # 单飞:在途直接放弃
        try:
            upstream = yaml.safe_load(self.fetcher.fetch_text(self.config.subscription_url)) or {}

            # listener 先空惰 reload 让 mihomo 加载 proxy-provider,再读全量节点
            self.manager.reload(mihomo_config.build_runtime_config(upstream, [], secret=self.secret))
            node_names = nodes.select_outbound_nodes(self.manager.get_proxies())

            prev = port_store.load(self._port_store_path)
            alloc = ports.allocate(node_names, prev,
                                   port_base=self.config.port_base, max_nodes=self.config.max_nodes)
            port_store.save(self._port_store_path, alloc.mapping)

            listeners = mihomo_config.build_listeners(node_names, alloc.mapping)
            self.manager.reload(mihomo_config.build_runtime_config(upstream, listeners, secret=self.secret))

            pmembers = nodes.provider_members(self.manager.get_providers_proxies())
            socks_port = min(alloc.mapping.values()) if alloc.mapping else None
            geosite_dat = self._load_geosite(socks_port) if upstream.get("rules") else None

            bundle = assemble.build_config_and_rulesets(
                upstream=upstream, node_port_map=alloc.mapping, provider_members=pmembers,
                urls=self.urls, host=self.config.advertise_host,
                fetch_ruleset_content=lambda url: self._fetch_ruleset(url, socks_port),
                geosite_dat=geosite_dat, update_interval=self.config.surge_update_interval,
            )
            snap = Snapshot(surge_text=bundle.surge_text, rulesets=bundle.rulesets,
                            node_port_map=alloc.mapping, skipped=bundle.skipped, dropped=alloc.dropped)
            self.cache.swap(snap)
            cachemod.persist(snap, self.config.data_dir)
            self._last_snapshot = snap
            self.last_success = self.clock()
            return snap
        except Exception as exc:               # noqa: BLE001 — 任何失败都退回 last-good
            self.last_error = repr(exc)
            return None
        finally:
            self._lock.release()

    def _fetch_ruleset(self, url: str, socks_port: int | None) -> str | None:
        try:
            if socks_port is not None:
                return self.fetcher.fetch_via_socks(url, socks_port).decode("utf-8")
            return self.fetcher.fetch_text(url)
        except OSError:
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

    def health(self) -> dict:
        snap = self._last_snapshot
        return {
            "nodes": len(snap.node_port_map),
            "dropped": snap.dropped,
            "skipped": len(snap.skipped),
            "last_success": self.last_success,
            "last_error": self.last_error,
        }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: refresh pipeline orchestrator with single-flight and last-good"
```

---

### Task 15: `orchestrator` — 后台循环 + 防抖踢一次

**Files:**
- Modify: `src/surge_gw/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Produces:`request_refresh() -> None`(非阻塞踢一次,与在途合并)、`start_background() -> None`(起 daemon 线程周期刷新)、`stop() -> None`;循环用 `should_refresh` 防抖。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_orchestrator.py(追加)
def test_request_refresh_runs_when_not_debounced(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.request_refresh()                      # 同步执行一次(测试用直驱)
    assert o.cache.get().node_port_map == {"A": 1200}


def test_request_refresh_debounced(tmp_path):
    ticks = [1000.0]
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.clock = lambda: ticks[0]
    o.request_refresh()                      # 第一次跑
    first = o.last_success
    ticks[0] = 1100.0                        # 距上次 100s < 300 防抖
    o.request_refresh()
    assert o.last_success == first           # 没再跑
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py::test_request_refresh_debounced -v`
Expected: FAIL(AttributeError: request_refresh)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/orchestrator.py(import 区追加)
from surge_gw.refresh_policy import should_refresh

# Orchestrator.__init__ 末尾追加:
        self._last_started: float | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()

# 追加方法:
    def request_refresh(self) -> None:
        """防抖判定后同步踢一次(在途由 refresh_once 的单飞合并)。"""
        if should_refresh(self.clock(), self._last_started,
                          self._lock.locked(), self.config.min_refresh_interval):
            self._last_started = self.clock()
            self.refresh_once()
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
```

> `request_refresh` 同步执行是为了让 HTTP `/refresh` 与测试可确定性地观察结果;真正的并发安全由 `refresh_once` 的单飞锁保证(后台循环与一个 `/refresh` 同时进入时,后者拿不到锁直接返回 None)。`_last_started` 在 `request_refresh`/`_loop` 里更新以驱动防抖。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: background refresh loop with debounced manual trigger"
```

---

### Task 16: `__main__.py` — 入口装配 + token/secret 自举

**Files:**
- Create: `src/surge_gw/__main__.py`
- Test: `tests/test_entrypoint.py`

**Interfaces:**
- Produces:
  - `ensure_token(config: Config) -> str`(token 为 None 则随机生成、持久化到 `DATA_DIR/token`、返回;已存在文件则读回)。
  - `random_secret() -> str`(mihomo external-controller secret)。
  - `build_app(config: Config) -> tuple` — 装配 manager/cache/orchestrator/server(不启动),返回 `(server, orchestrator, manager)`,供测试验证装配;`main()` 负责启动。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_entrypoint.py
from surge_gw.__main__ import ensure_token, random_secret
from surge_gw.config import from_env


def test_ensure_token_generates_and_persists(tmp_path):
    cfg = from_env({"SUBSCRIPTION_URL": "http://x/s", "DATA_DIR": str(tmp_path)})
    tok = ensure_token(cfg)
    assert tok and (tmp_path / "token").read_text().strip() == tok


def test_ensure_token_reads_existing(tmp_path):
    (tmp_path / "token").write_text("preset\n")
    cfg = from_env({"SUBSCRIPTION_URL": "http://x/s", "DATA_DIR": str(tmp_path)})
    assert ensure_token(cfg) == "preset"


def test_explicit_token_wins(tmp_path):
    cfg = from_env({"SUBSCRIPTION_URL": "http://x/s", "DATA_DIR": str(tmp_path),
                    "SUBSCRIPTION_TOKEN": "explicit"})
    assert ensure_token(cfg) == "explicit"


def test_random_secret_is_nonempty_and_varies():
    a, b = random_secret(), random_secret()
    assert a and b and a != b
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_entrypoint.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/__main__.py
from __future__ import annotations

import os
import secrets

from surge_gw import cache as cachemod
from surge_gw.config import Config, from_env
from surge_gw.http_server import build_server
from surge_gw.mihomo_manager import MihomoManager
from surge_gw.orchestrator import Orchestrator
from surge_gw.refresh_policy import Snapshot, placeholder_surge
from surge_gw.urls import RulesetUrls


def ensure_token(config: Config) -> str:
    """显式 token 优先;否则用持久化文件;再否则随机生成并持久化(首跑打印)。"""
    if config.subscription_token:
        return config.subscription_token
    path = os.path.join(config.data_dir, "token")
    try:
        with open(path, encoding="utf-8") as f:
            existing = f.read().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(24)
    os.makedirs(config.data_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token


def random_secret() -> str:
    return secrets.token_urlsafe(24)


def build_app(config: Config):
    """装配但不启动:返回 (server, orchestrator, manager)。"""
    token = ensure_token(config)
    secret = random_secret()
    urls = RulesetUrls(host=config.advertise_host, port=config.http_port, token=token)
    config = _with_token(config, token)

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


def _with_token(config: Config, token: str) -> Config:
    from dataclasses import replace
    return replace(config, subscription_token=token)


def _fetcher_module():
    from surge_gw import fetcher
    return fetcher


def main() -> None:
    config = from_env(os.environ)
    server, orchestrator, manager = build_app(config)
    manager.ensure_alive()
    orchestrator.start_background()
    print(f"surge-gw serving on {config.advertise_host}:{config.http_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
```

> `fetcher` 是模块级函数集合,`Orchestrator` 用 `self.fetcher.fetch_text/...` 调用,故传模块对象即可(与测试里的 `FakeFetcher` 接口一致)。`geosite_source` 取 `config.geosite_url`(为 None 时 orchestrator 不取 geosite;默认 geosite.dat 源的回退留待真实 smoke/Plan 4 视需要补)。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_entrypoint.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/__main__.py tests/test_entrypoint.py
git commit -m "feat: application entrypoint with token and secret bootstrap"
```

---

### Task 17: 手动 mihomo smoke 闸门文档

**Files:**
- Create: `smoke/plan3_runtime.md`

**Interfaces:**
- Produces: 人工验证步骤;不被 import,不进自动化测试。

- [ ] **Step 1: 写 smoke 文档**

```markdown
<!-- smoke/plan3_runtime.md -->
# 运行时 smoke(真实 mihomo + 真订阅)

自动化测试用进程内 fake,不跑真实 mihomo。本闸门用真实二进制端到端验证。

前置:本机装好 `mihomo`(在 PATH 或设 `MIHOMO_BIN`),准备一个真实 `SUBSCRIPTION_URL`。

步骤:
1. 起服务:
   `SUBSCRIPTION_URL=<你的订阅> DATA_DIR=./_smoke_data .venv/bin/python -m surge_gw`
   日志应打印 token 与监听地址。
2. 等首次刷新完成(看日志/`curl -s 127.0.0.1:8080/health | python3 -m json.tool`,`nodes>0`)。
3. 取订阅:`curl -s "127.0.0.1:8080/surge?token=<token>" | head -40`
   应见 `#!MANAGED-CONFIG` 头、`[Proxy]` 里的 `socks5, 127.0.0.1, 12xx`、`[Proxy Group]`、`[Rule]`。
4. 穿某个 socks 端口验出口:`curl -s --socks5-hostname 127.0.0.1:1200 https://api.ipify.org; echo`
   返回 IP 应是该节点出口,而非本机。
5. 验自托管 ruleset:从 `/surge` 里挑一条 `RULE-SET/DOMAIN-SET, http://127.0.0.1:8080/ruleset/<n>?token=...`,
   `curl -s` 该 URL 应返回转换后的域名/规则列表。
6. (可选)观察 reload 是否保连接:刷新时(`curl -XPOST .../refresh?token=`)看已建连接是否中断。

把结论(节点数、出口是否走对、reload 是否保连接)记到本文件末尾。
```

- [ ] **Step 2: Commit**

```bash
git add smoke/plan3_runtime.md
git commit -m "test: add runtime smoke checklist for real mihomo"
```

> 真实 mihomo 闸门由人工执行(像 Plan 1 的 fake-ip 闸门)。执行计划的 agent 跑完 Task 16 自动化全绿后,应停下提示用户做本 smoke,而非自行假设通过。

---

## Self-Review

**Spec 覆盖**:§1 范围(节点+serve 流水线,Task 1–17)、§2 模块图(每模块一 Task,纯先 impure 后)、§3 数据流(orchestrator Task 14/15 编排 fetch→build→reload→nodes→ports→convert→assemble→swap)、§4 测试策略(纯核 TDD + 进程内 fake Task 7/8/11/12/13/14 + 手动闸门 Task 17)、§5 配置/持久化/安全(Task 1 config、Task 9 port_store、Task 10 cache、Task 16 token/secret)、§6 错误处理(Task 14 失败保 last-good、assemble Task 6 跳过项、Task 16 占位/last-good 初始)。**留给 Plan 4**:Docker/compose/HEALTHCHECK/100 端口发布/镜像内 mihomo/端到端 smoke 自动化。

**占位符扫描**:无 TBD/TODO。Task 8 的测试夹具 `_forwarding_socks` 的握手读取在步骤说明里标注"先 RED 再按 socks5 协议修正至 GREEN",这是 TDD 调试指引而非未完成代码,实现时必须写对。

**类型一致性**:`Snapshot`(surge_text/rulesets/node_port_map/skipped/dropped)在 refresh_policy 定义、cache/orchestrator/assemble(经 Bundle 转换)一致;`Config` 字段贯穿 config/orchestrator/http_server/__main__;`RulesetUrls.ruleset/geosite/managed` 在 urls 定义、assemble/orchestrator/__main__ 使用,且与 http_server 端点路径段(`name` / `geosite-<cat>`)、reconcile 反填 URL 三处同源;`MihomoManager.reload/get_proxies/get_providers_proxies` 在 Task 11 定义、orchestrator 消费;`fetcher.fetch_text/fetch_via_socks` 在 Task 7/8 定义、orchestrator 经 `self.fetcher` 调用、`FakeFetcher` 同接口;`ports.allocate`/`port_store.load|save`/`nodes.select_outbound_nodes|provider_members`/`mihomo_config.build_listeners|build_runtime_config`/`assemble.build_config_and_rulesets`/`cache.persist|load_last_good` 签名在各自 Task 定义并在 orchestrator/__main__ 按签名调用。

---

**已知后续依赖(Plan 4)**:Docker 化(`python:3-slim` + 固定 mihomo 二进制 + 代码)、`docker compose`(`-p 127.0.0.1:1200-1299` + `8080`、`9090` 不发布、`/data` volume、`HEALTHCHECK` 打 `/health`)、HTTP server 在容器内可能需绑 `0.0.0.0`(由 Docker 发布到 `127.0.0.1`)而非 `advertise_host`——届时给 `build_server` 增一个 bind host 参数;geosite 默认源回退;100 端口开销评估与 `MAX_NODES` 调参;真实订阅端到端 smoke 自动化。
