# surge-gw Plan 1 — 验证 + 转换核心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先验证 fake-ip 不破坏架构,再用纯函数把一份(已解析的)mihomo 配置 + 节点→端口映射,转换为完整的 Surge `#!MANAGED-CONFIG` 配置文本。

**Architecture:** 转换器是无副作用的纯函数,输入 Python dict / list,输出文本。分流/选择全部交给 Surge;节点统一写成 `socks5, 127.0.0.1, 12xx`。远程 ruleset / geosite 的**内容**转换留给 Plan 2,本计划只产出指向占位 URL 的 `RULE-SET` 引用并记录被引用的名字。

**Tech Stack:** Python 3.12+、PyYAML(仅测试夹具读取用)、pytest。无网络、无子进程。

## Global Constraints

- Python 3.12+;运行期依赖尽量少;本计划仅引入 `pyyaml`(测试夹具)与 `pytest`(开发)。
- socks 端口区间 `PORT_BASE .. PORT_BASE+MAX_NODES-1`,默认 `1200..1299`(`MAX_NODES=100`)。
- 默认 `ADVERTISE_HOST=127.0.0.1`。
- Surge 以逗号分隔字段:节点名/组名中的逗号必须消毒(剔除)。
- 提交信息与代码注释**禁止过程污染**:不得出现任务/步骤编号、方案代号、审阅轮次、临时引用;只解释 WHY 与必须保持的不变量。
- TDD:先写失败测试,再最小实现;频繁提交;DRY;YAGNI。

---

## File Structure

```
surge-gw/
├── pyproject.toml
├── src/surge_gw/
│   ├── __init__.py
│   ├── models.py          # 共享 dataclass(转换结果 / 跳过项)
│   ├── naming.py          # 名字消毒 + 名字映射
│   ├── ports.py           # 稳定端口分配
│   ├── proxies.py         # 节点 → [Proxy] socks5 行
│   ├── groups.py          # proxy-group → [Proxy Group]
│   ├── rules.py           # rule → [Rule](含 RULE-SET/GEOSITE 引用、逻辑规则)
│   └── surge_config.py    # 组装完整 Surge 配置 + MANAGED-CONFIG 头
├── tests/
│   ├── test_naming.py
│   ├── test_ports.py
│   ├── test_proxies.py
│   ├── test_groups.py
│   ├── test_rules.py
│   └── test_surge_config.py
└── smoke/
    ├── socks_logger.py    # 打印 CONNECT 目标的极简 socks5 server(验证用)
    └── README.md          # fake-ip 验证步骤
```

---

### Task 1: fake-ip / 域名透传冒烟验证(闸门)

验证 Surge 增强模式下交给 socks5 的是**域名**而非 `198.18.x.x`。这是整套架构的前提;不通过则需重评估,**先于一切转换代码**。

**Files:**
- Create: `smoke/socks_logger.py`
- Create: `smoke/README.md`

**Interfaces:**
- Produces: 一个独立可运行的 socks5 server,把每个 CONNECT 的目标地址打印到 stdout。不被后续任务 import。

- [ ] **Step 1: 写极简 socks5 logger(只做握手 + 打印 CONNECT 目标,不真正转发)**

```python
# smoke/socks_logger.py
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
            addr = conn.recv(length).decode("idna", "replace")
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
```

- [ ] **Step 2: 写验证步骤说明**

```markdown
<!-- smoke/README.md -->
# fake-ip / 域名透传验证

目的:确认 Surge 增强模式下,交给 socks5 代理的目标是**域名**(ATYP=domain),
而不是 fake-ip(198.18.x.x)。若是 fake-ip,整套架构不成立。

步骤:
1. 运行:`python3 smoke/socks_logger.py`(监听 127.0.0.1:11800)。
2. Surge 配置临时加一个代理与一条规则:
   `[Proxy]`  `SmokeTest = socks5, 127.0.0.1, 11800`
   `[Rule]`   `DOMAIN-SUFFIX,example.com,SmokeTest`
3. Surge 打开「增强模式」。
4. 浏览器/终端访问 `http://example.com`。
5. 看 logger 输出:
   - `CONNECT DOMAIN -> example.com:80`  → 通过 ✅(架构成立)
   - `CONNECT IP -> 198.18.x.x:80`       → 失败 ⛔(需重评估,见 spec §8/§18)

记录结论到本文件末尾。
```

- [ ] **Step 3: 本地起 logger,人工验证**

Run: `python3 smoke/socks_logger.py`
然后按 README 配置 Surge 并访问 example.com。
Expected: logger 打印 `CONNECT DOMAIN -> example.com:80`。

- [ ] **Step 4: 把结论追加到 README,提交**

```bash
git add smoke/
git commit -m "test: add fake-ip passthrough smoke check and record result"
```

> 若 Step 3 观察到 fake-ip(IP),**停止本计划**,回到 spec §8 重新评估;后续任务的前提不成立。

---

### Task 2: 项目骨架 + 共享模型

**Files:**
- Create: `pyproject.toml`
- Create: `src/surge_gw/__init__.py`
- Create: `src/surge_gw/models.py`
- Test: `tests/test_models_import.py`

**Interfaces:**
- Produces:
  - `SkippedItem(kind: str, detail: str, reason: str)` — frozen dataclass。
  - `RuleResult` dataclass:`lines: list[str]`、`rule_providers: set[str]`、`geosites: set[str]`、`skipped: list[SkippedItem]`。

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "surge-gw"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8", "pyyaml>=6"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: 写共享模型**

```python
# src/surge_gw/models.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkippedItem:
    """转换中被跳过的内容,用于汇总报告。"""
    kind: str      # "rule" / "geosite-regexp" / "group" / "proxy" ...
    detail: str    # 原始片段
    reason: str


@dataclass
class RuleResult:
    """规则转换的累积结果。"""
    lines: list[str] = field(default_factory=list)
    rule_providers: set[str] = field(default_factory=set)  # 被引用的 rule-provider 名
    geosites: set[str] = field(default_factory=set)        # 被引用的 geosite 分类(可含 @attr)
    skipped: list[SkippedItem] = field(default_factory=list)
```

- [ ] **Step 3: 写导入测试**

```python
# tests/test_models_import.py
from surge_gw.models import RuleResult, SkippedItem


def test_models_construct():
    r = RuleResult()
    r.lines.append("FINAL,DIRECT")
    r.skipped.append(SkippedItem("rule", "DSCP,1,DIRECT", "unsupported"))
    assert r.lines == ["FINAL,DIRECT"]
    assert r.skipped[0].kind == "rule"
```

- [ ] **Step 4: 建 venv、装 dev、跑测试**

Run:
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest tests/test_models_import.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
printf '.venv/\n__pycache__/\n*.egg-info/\n.pytest_cache/\n' > .gitignore
git add pyproject.toml .gitignore src/surge_gw/__init__.py src/surge_gw/models.py tests/test_models_import.py
git commit -m "chore: scaffold python package and shared models"
```

---

### Task 3: 名字消毒与名字映射

**Files:**
- Create: `src/surge_gw/naming.py`
- Test: `tests/test_naming.py`

**Interfaces:**
- Produces:
  - `sanitize(name: str) -> str` — 剔除逗号、首尾空白折叠;空名回退 `"node"`。
  - `build_name_map(names: Iterable[str]) -> dict[str, str]` — 原始名→消毒名,**保证唯一**(冲突追加 `-2`、`-3`…)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_naming.py
from surge_gw.naming import build_name_map, sanitize


def test_sanitize_removes_commas_keeps_emoji():
    assert sanitize("🇺🇸 US, Premium") == "🇺🇸 US Premium"


def test_sanitize_empty_falls_back():
    assert sanitize("   ") == "node"


def test_build_name_map_dedupes_after_sanitize():
    m = build_name_map(["A,1", "A 1", "B"])
    # "A,1" -> "A1"? 不,逗号剔除后是 "A1";"A 1" -> "A 1";两者不同,无需去重
    assert m["B"] == "B"
    assert len(set(m.values())) == 3


def test_build_name_map_collision_suffixes():
    m = build_name_map(["X,Y", "XY"])  # 都消毒成 "XY"
    assert m["X,Y"] != m["XY"]
    assert set(m.values()) == {"XY", "XY-2"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_naming.py -v`
Expected: FAIL(ModuleNotFoundError: surge_gw.naming)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/naming.py
from __future__ import annotations

from collections.abc import Iterable


def sanitize(name: str) -> str:
    """Surge 以逗号分隔字段,名字里不能含逗号;空名给个回退。"""
    cleaned = name.replace(",", "").strip()
    return cleaned if cleaned else "node"


def build_name_map(names: Iterable[str]) -> dict[str, str]:
    """原始名 → 消毒后唯一名。消毒后撞名的追加 -2/-3… 以保持引用可区分。"""
    result: dict[str, str] = {}
    used: set[str] = set()
    for original in names:
        base = sanitize(original)
        candidate = base
        n = 1
        while candidate in used:
            n += 1
            candidate = f"{base}-{n}"
        used.add(candidate)
        result[original] = candidate
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_naming.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/naming.py tests/test_naming.py
git commit -m "feat: sanitize and dedupe proxy/group names for surge"
```

---

### Task 4: 稳定端口分配

**Files:**
- Create: `src/surge_gw/ports.py`
- Test: `tests/test_ports.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `allocate(names: list[str], previous: dict[str, int], port_base: int = 1200, max_nodes: int = 100) -> AllocationResult`
  - `AllocationResult(mapping: dict[str, int], dropped: list[str])` — `mapping` 是被采纳节点名→端口;`dropped` 是超出上限被丢弃的节点名。
- 不变量:`previous` 里仍存在于 `names` 的节点保持原端口;新节点取最小空闲端口;超 `max_nodes` 的进 `dropped`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ports.py
from surge_gw.ports import allocate


def test_first_allocation_is_sequential():
    res = allocate(["a", "b", "c"], previous={}, port_base=1200, max_nodes=100)
    assert res.mapping == {"a": 1200, "b": 1201, "c": 1202}
    assert res.dropped == []


def test_existing_nodes_keep_their_port():
    prev = {"a": 1205, "b": 1200}
    res = allocate(["a", "b", "c"], previous=prev, port_base=1200, max_nodes=100)
    assert res.mapping["a"] == 1205
    assert res.mapping["b"] == 1200
    # 新节点取最小空闲端口(1200/1205 已占)
    assert res.mapping["c"] == 1201


def test_removed_node_frees_its_port():
    prev = {"a": 1200, "b": 1201}
    res = allocate(["b", "x"], previous=prev, port_base=1200, max_nodes=100)
    assert res.mapping["b"] == 1201
    assert res.mapping["x"] == 1200  # a 走了,1200 空出


def test_overflow_drops_extra_nodes():
    names = [f"n{i}" for i in range(102)]
    res = allocate(names, previous={}, port_base=1200, max_nodes=100)
    assert len(res.mapping) == 100
    assert res.dropped == ["n100", "n101"]
    assert max(res.mapping.values()) == 1299
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_ports.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/ports.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AllocationResult:
    mapping: dict[str, int]
    dropped: list[str]


def allocate(
    names: list[str],
    previous: dict[str, int],
    port_base: int = 1200,
    max_nodes: int = 100,
) -> AllocationResult:
    """同名节点跨刷新保持端口,避免 Surge 选中项漂移。超上限的丢弃。"""
    ports = range(port_base, port_base + max_nodes)
    mapping: dict[str, int] = {}
    taken: set[int] = set()

    # 保留仍存在节点的旧端口(必须在区间内且未被占)
    for name in names:
        port = previous.get(name)
        if port is not None and port in ports and port not in taken:
            mapping[name] = port
            taken.add(port)

    free = (p for p in ports if p not in taken)
    dropped: list[str] = []
    for name in names:
        if name in mapping:
            continue
        port = next(free, None)
        if port is None:
            dropped.append(name)
        else:
            mapping[name] = port
            taken.add(port)

    return AllocationResult(mapping=mapping, dropped=dropped)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_ports.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/ports.py tests/test_ports.py
git commit -m "feat: stable node-to-port allocation within fixed range"
```

---

### Task 5: 节点 → `[Proxy]` socks5 行

**Files:**
- Create: `src/surge_gw/proxies.py`
- Test: `tests/test_proxies.py`

**Interfaces:**
- Consumes: `naming.build_name_map`(Task 3)、`ports.allocate` 的 `mapping`(Task 4)。
- Produces:
  - `build_proxy_section(names: list[str], name_map: dict[str, str], port_map: dict[str, int], host: str = "127.0.0.1") -> list[str]`
  - 每行 `"<消毒名> = socks5, <host>, <port>, udp-relay=true"`;`port_map` 缺失的节点跳过(被端口分配丢弃的)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_proxies.py
from surge_gw.proxies import build_proxy_section


def test_build_proxy_lines():
    names = ["US-1", "JP,2"]
    name_map = {"US-1": "US-1", "JP,2": "JP2"}
    port_map = {"US-1": 1200, "JP,2": 1201}
    lines = build_proxy_section(names, name_map, port_map, host="127.0.0.1")
    assert lines == [
        "US-1 = socks5, 127.0.0.1, 1200, udp-relay=true",
        "JP2 = socks5, 127.0.0.1, 1201, udp-relay=true",
    ]


def test_dropped_node_without_port_is_skipped():
    names = ["a", "b"]
    name_map = {"a": "a", "b": "b"}
    port_map = {"a": 1200}  # b 被丢弃,无端口
    lines = build_proxy_section(names, name_map, port_map)
    assert lines == ["a = socks5, 127.0.0.1, 1200, udp-relay=true"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_proxies.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/proxies.py
from __future__ import annotations


def build_proxy_section(
    names: list[str],
    name_map: dict[str, str],
    port_map: dict[str, int],
    host: str = "127.0.0.1",
) -> list[str]:
    """每个有端口的节点写成一条 socks5 行;udp-relay 显式开启以支持 UDP。"""
    lines: list[str] = []
    for name in names:
        port = port_map.get(name)
        if port is None:
            continue  # 被端口分配丢弃的节点不进配置
        surge_name = name_map[name]
        lines.append(f"{surge_name} = socks5, {host}, {port}, udp-relay=true")
    return lines
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_proxies.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/proxies.py tests/test_proxies.py
git commit -m "feat: render nodes as local socks5 proxy lines"
```

---

### Task 6: proxy-group → `[Proxy Group]`

**Files:**
- Create: `src/surge_gw/groups.py`
- Test: `tests/test_groups.py`

**Interfaces:**
- Consumes: `naming` 名字映射;节点↔provider 归属(本计划用传入的 `provider_members: dict[str, list[str]]` 模拟,Plan 3 接 `/providers/proxies`)。
- Produces:
  - `convert_groups(groups: list[dict], name_map: dict[str, str], available_nodes: set[str], provider_members: dict[str, list[str]]) -> tuple[list[str], list[SkippedItem]]`
  - 返回 `[Proxy Group]` 文本行 + 跳过项。
- 规则:
  - `select`/`url-test`/`fallback` 直转;`load-balance` 降级为 `select` 并记 skipped;`relay` 跳过并记 skipped。
  - 成员展开:`proxies` 里的名字按 name_map 映射;`use`(provider 名)展开成该 provider 的成员;`include-all`/`include-all-proxies` 展开成全部 `available_nodes`;DIRECT/REJECT 原样。
  - 只保留 `available_nodes`/已知组名/DIRECT/REJECT 的成员;展开后为空补 `DIRECT`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_groups.py
from surge_gw.groups import convert_groups


def test_select_group_maps_members():
    groups = [{"name": "Proxy", "type": "select", "proxies": ["A", "B", "DIRECT"]}]
    name_map = {"A": "A", "B": "B", "Proxy": "Proxy"}
    lines, skipped = convert_groups(groups, name_map, {"A", "B"}, {})
    assert lines == ["Proxy = select, A, B, DIRECT"]
    assert skipped == []


def test_url_test_group_carries_params():
    groups = [{
        "name": "Auto", "type": "url-test", "proxies": ["A"],
        "url": "http://x/generate_204", "interval": 300, "tolerance": 50,
    }]
    name_map = {"A": "A", "Auto": "Auto"}
    lines, _ = convert_groups(groups, name_map, {"A"}, {})
    assert lines == [
        "Auto = url-test, A, url=http://x/generate_204, interval=300, tolerance=50"
    ]


def test_use_provider_expands_members():
    groups = [{"name": "G", "type": "select", "use": ["prov1"]}]
    name_map = {"P1": "P1", "P2": "P2", "G": "G"}
    lines, _ = convert_groups(groups, name_map, {"P1", "P2"}, {"prov1": ["P1", "P2"]})
    assert lines == ["G = select, P1, P2"]


def test_load_balance_degrades_to_select():
    groups = [{"name": "LB", "type": "load-balance", "proxies": ["A"]}]
    name_map = {"A": "A", "LB": "LB"}
    lines, skipped = convert_groups(groups, name_map, {"A"}, {})
    assert lines == ["LB = select, A"]
    assert any(s.kind == "group" and "load-balance" in s.reason for s in skipped)


def test_empty_group_gets_direct_fallback():
    groups = [{"name": "G", "type": "select", "proxies": ["GHOST"]}]
    name_map = {"G": "G"}
    lines, _ = convert_groups(groups, name_map, set(), {})
    assert lines == ["G = select, DIRECT"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_groups.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/groups.py
from __future__ import annotations

from surge_gw.models import SkippedItem

_BUILTINS = {"DIRECT", "REJECT", "REJECT-DROP"}
_TYPE_MAP = {"select": "select", "url-test": "url-test", "fallback": "fallback"}


def _members(group: dict, name_map: dict[str, str], available: set[str],
             provider_members: dict[str, list[str]], known_groups: set[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(node: str) -> None:
        if node not in seen:
            seen.add(node)
            out.append(node)

    raw: list[str] = list(group.get("proxies") or [])
    for prov in group.get("use") or []:
        raw.extend(provider_members.get(prov, []))
    if group.get("include-all") or group.get("include-all-proxies"):
        raw.extend(sorted(available))

    for member in raw:
        if member in _BUILTINS:
            add(member)
        elif member in available or member in known_groups:
            add(name_map[member])
    return out


def convert_groups(
    groups: list[dict],
    name_map: dict[str, str],
    available_nodes: set[str],
    provider_members: dict[str, list[str]],
) -> tuple[list[str], list[SkippedItem]]:
    """mihomo 策略组 → Surge [Proxy Group]。LB 降级 select,relay 跳过。"""
    known_groups = {g["name"] for g in groups}
    lines: list[str] = []
    skipped: list[SkippedItem] = []

    for group in groups:
        gtype = group["type"]
        if gtype == "relay":
            skipped.append(SkippedItem("group", group["name"], "relay (chaining) unsupported"))
            continue

        surge_type = _TYPE_MAP.get(gtype)
        if surge_type is None:
            if gtype == "load-balance":
                surge_type = "select"
                skipped.append(SkippedItem("group", group["name"], "load-balance degraded to select"))
            else:
                skipped.append(SkippedItem("group", group["name"], f"unknown type {gtype}"))
                continue

        members = _members(group, name_map, available_nodes, provider_members, known_groups)
        if not members:
            members = ["DIRECT"]

        parts = [name_map[group["name"]], "=", surge_type + ",", ", ".join(members)]
        line = f"{name_map[group['name']]} = {surge_type}, " + ", ".join(members)

        if surge_type in ("url-test", "fallback"):
            opts = []
            if group.get("url"):
                opts.append(f"url={group['url']}")
            if group.get("interval") is not None:
                opts.append(f"interval={group['interval']}")
            if surge_type == "url-test" and group.get("tolerance") is not None:
                opts.append(f"tolerance={group['tolerance']}")
            if opts:
                line += ", " + ", ".join(opts)

        lines.append(line)

    return lines, skipped
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_groups.py -v`
Expected: PASS

- [ ] **Step 5: 清理无用局部变量并提交**

删掉实现里多余的 `parts` 行(误留),确认测试仍 PASS。

```bash
git add src/surge_gw/groups.py tests/test_groups.py
git commit -m "feat: convert proxy-groups to surge policy groups with member expansion"
```

---

### Task 7: rule → `[Rule]`

**Files:**
- Create: `src/surge_gw/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: `models.RuleResult` / `SkippedItem`(Task 2);name_map / 已知组名 / available_nodes。
- Produces:
  - `convert_rules(rules: list[str], policy_map: dict[str, str], ruleset_url: Callable[[str], str], geosite_url: Callable[[str], str]) -> RuleResult`
  - `policy_map`:把 mihomo policy(节点名/组名)映射到 Surge 名;DIRECT/REJECT/REJECT-DROP 自动透传。
  - `ruleset_url(name)` / `geosite_url(cat)`:把被引用的 rule-provider / geosite 名转成本服务占位 URL(Plan 2 决定 DOMAIN-SET vs RULE-SET,本计划统一 `RULE-SET`)。
- 不变量:不可映射的整条规则进 `skipped`,不产出半条规则。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rules.py
from surge_gw.rules import convert_rules

PM = {"Proxy": "Proxy", "节点,A": "节点A"}


def url_rs(name): return f"http://h/ruleset/{name}"
def url_geo(cat): return f"http://h/ruleset/geosite-{cat}"


def conv(rules):
    return convert_rules(rules, PM, url_rs, url_geo)


def test_passthrough_and_rename_types():
    r = conv([
        "DOMAIN-SUFFIX,google.com,Proxy",
        "DST-PORT,443,Proxy",
        "SRC-IP-CIDR,192.168.0.0/16,Proxy",
        "NETWORK,udp,Proxy",
        "MATCH,Proxy",
    ])
    assert r.lines == [
        "DOMAIN-SUFFIX,google.com,Proxy",
        "DEST-PORT,443,Proxy",
        "SRC-IP,192.168.0.0/16,Proxy",
        "PROTOCOL,UDP,Proxy",
        "FINAL,Proxy",
    ]


def test_no_resolve_preserved():
    r = conv(["IP-CIDR,10.0.0.0/8,Proxy,no-resolve"])
    assert r.lines == ["IP-CIDR,10.0.0.0/8,Proxy,no-resolve"]


def test_policy_name_sanitized_and_builtins():
    r = conv(["DOMAIN,a.com,节点,A", "DOMAIN,b.com,REJECT"])
    assert r.lines == ["DOMAIN,a.com,节点A", "DOMAIN,b.com,REJECT"]


def test_rule_set_emits_reference_and_records():
    r = conv(["RULE-SET,mylist,Proxy"])
    assert r.lines == ["RULE-SET,http://h/ruleset/mylist,Proxy"]
    assert r.rule_providers == {"mylist"}


def test_geosite_emits_reference_and_records():
    r = conv(["GEOSITE,google@cn,Proxy"])
    assert r.lines == ["RULE-SET,http://h/ruleset/geosite-google@cn,Proxy"]
    assert r.geosites == {"google@cn"}


def test_logical_rule_converted_recursively():
    r = conv(["AND,((DOMAIN,a.com),(NETWORK,udp)),Proxy"])
    assert r.lines == ["AND,((DOMAIN,a.com),(PROTOCOL,UDP)),Proxy"]


def test_logical_rule_skipped_if_subrule_unsupported():
    r = conv(["AND,((DOMAIN,a.com),(DSCP,1)),Proxy"])
    assert r.lines == []
    assert any(s.kind == "rule" for s in r.skipped)


def test_unsupported_type_skipped():
    r = conv(["DOMAIN-REGEX,.*\\.cn,Proxy", "DSCP,4,Proxy"])
    assert r.lines == []
    assert len(r.skipped) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_rules.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/rules.py
from __future__ import annotations

from collections.abc import Callable

from surge_gw.models import RuleResult, SkippedItem

# 直接同名透传(不带值变换)
_PASSTHROUGH = {
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD",
    "IP-CIDR", "IP-CIDR6", "GEOIP", "IP-ASN",
    "SRC-PORT", "PROCESS-NAME",
}
# 改名透传(payload 不变)
_RENAME = {"DST-PORT": "DEST-PORT", "SRC-IP-CIDR": "SRC-IP"}
_BUILTIN_POLICIES = {"DIRECT", "REJECT", "REJECT-DROP"}
_SKIP_TYPES = {"DOMAIN-REGEX", "PROCESS-PATH", "DSCP", "IN-PORT", "IN-TYPE", "IN-USER", "IN-NAME"}
_LOGICAL = {"AND", "OR", "NOT"}


def _map_policy(policy: str, policy_map: dict[str, str]) -> str | None:
    if policy in _BUILTIN_POLICIES:
        return policy
    if policy == "PASS":
        return None
    return policy_map.get(policy)


def _split_logical_payload(payload: str) -> list[str]:
    """把 ((a),(b),(c)) 拆成 ['(a)','(b)','(c)'],尊重括号深度。"""
    inner = payload[1:-1]  # 去掉最外层括号
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                parts.append(inner[start : i + 1])
                start = i + 1
        elif depth == 0 and ch == ",":
            start = i + 1
    return [p for p in parts if p]


def _convert_subrule(sub: str, policy_map, ruleset_url, geosite_url) -> str | None:
    """子规则形如 '(DOMAIN,a.com)';返回 Surge 子规则或 None(不可转)。"""
    body = sub[1:-1]  # 去括号
    line = _convert_one(body + ",__NOPOLICY__", policy_map, ruleset_url, geosite_url)
    if line is None:
        return None
    # 子规则不带 policy:去掉占位 policy
    return "(" + line.rsplit(",__NOPOLICY__", 1)[0] + ")" if "__NOPOLICY__" in line else None


def _convert_one(rule: str, policy_map, ruleset_url, geosite_url) -> str | None:
    """转换单条(非逻辑)规则,policy 用占位 '__NOPOLICY__' 表示无 policy(逻辑子规则)。"""
    fields = rule.split(",")
    rtype = fields[0].strip()

    if rtype in _SKIP_TYPES:
        return None

    is_subrule = fields[-1] == "__NOPOLICY__"
    if is_subrule:
        policy_raw = "__NOPOLICY__"
        payload = fields[1] if len(fields) > 2 else ""
        options = []
    else:
        # TYPE, PAYLOAD, POLICY [, options...]
        if len(fields) < 3 and rtype not in ("MATCH",):
            return None
        if rtype == "MATCH":
            policy_raw, payload, options = fields[1].strip(), None, []
        else:
            payload = fields[1].strip()
            policy_raw = fields[2].strip()
            options = [o.strip() for o in fields[3:]]

    # policy 映射(子规则无 policy)
    if is_subrule:
        mapped_policy = None
    else:
        mapped_policy = _map_policy(policy_raw, policy_map)
        if mapped_policy is None:
            return None

    def emit(stype: str, spayload: str | None) -> str:
        parts = [stype]
        if spayload is not None:
            parts.append(spayload)
        if not is_subrule and mapped_policy is not None:
            parts.append(mapped_policy)
        parts.extend(options)
        if is_subrule:
            parts.append("__NOPOLICY__")
        return ",".join(parts)

    if rtype in _PASSTHROUGH:
        return emit(rtype, payload)
    if rtype in _RENAME:
        return emit(_RENAME[rtype], payload)
    if rtype == "NETWORK":
        return emit("PROTOCOL", payload.upper())
    if rtype in ("MATCH", "FINAL"):
        return emit("FINAL", None)
    if rtype == "RULE-SET":
        return emit("RULE-SET", ruleset_url(payload))
    if rtype == "GEOSITE":
        return emit("RULE-SET", geosite_url(payload))
    return None


def convert_rules(
    rules: list[str],
    policy_map: dict[str, str],
    ruleset_url: Callable[[str], str],
    geosite_url: Callable[[str], str],
) -> RuleResult:
    result = RuleResult()
    for rule in rules:
        rtype = rule.split(",", 1)[0].strip()

        if rtype in _LOGICAL:
            line = _convert_logical(rule, policy_map, ruleset_url, geosite_url)
            if line is None:
                result.skipped.append(SkippedItem("rule", rule, "logical rule has unconvertible subrule"))
            else:
                result.lines.append(line)
            continue

        line = _convert_one(rule, policy_map, ruleset_url, geosite_url)
        if line is None:
            result.skipped.append(SkippedItem("rule", rule, f"unsupported or unmapped: {rtype}"))
            continue
        result.lines.append(line)

        if rtype == "RULE-SET":
            result.rule_providers.add(rule.split(",")[1].strip())
        elif rtype == "GEOSITE":
            result.geosites.add(rule.split(",")[1].strip())
    return result


def _convert_logical(rule, policy_map, ruleset_url, geosite_url) -> str | None:
    # AND,((sub),(sub)),POLICY
    head = rule.split(",", 1)[0].strip()
    rest = rule[len(head) + 1 :]
    depth = 0
    for i, ch in enumerate(rest):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                payload = rest[: i + 1]
                policy_raw = rest[i + 2 :].strip()
                break
    else:
        return None

    mapped_policy = _map_policy(policy_raw, policy_map)
    if mapped_policy is None:
        return None

    subs = _split_logical_payload(payload)
    converted_subs = []
    for sub in subs:
        if sub[1:].split(",", 1)[0] in _LOGICAL:  # 嵌套逻辑
            nested = _convert_logical(sub[1:-1], policy_map, ruleset_url, geosite_url)
            if nested is None:
                return None
            converted_subs.append("(" + nested.rsplit(",", 1)[0] + ")")
        else:
            c = _convert_subrule(sub, policy_map, ruleset_url, geosite_url)
            if c is None:
                return None
            converted_subs.append(c)

    return f"{head},({''.join(converted_subs)}),{mapped_policy}"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_rules.py -v`
Expected: PASS。若逻辑规则用例不过,逐个打印中间值修正 `_convert_logical` 的切片边界(`rest[i+2:]` 跳过 `),`)。

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/rules.py tests/test_rules.py
git commit -m "feat: map mihomo rules to surge rules incl logical and ruleset refs"
```

---

### Task 8: 组装完整 Surge 配置

**Files:**
- Create: `src/surge_gw/surge_config.py`
- Test: `tests/test_surge_config.py`

**Interfaces:**
- Consumes: Task 5/6/7 的产物。
- Produces:
  - `build_surge_config(*, proxy_lines: list[str], group_lines: list[str], rule_lines: list[str], managed_url: str, update_interval: int = 3600, general: dict | None = None) -> str`
  - 产出含 `#!MANAGED-CONFIG` 头 + `[General]`(最简) + `[Proxy]` + `[Proxy Group]` + `[Rule]` 的完整文本。
- 不变量:首行必须是 `#!MANAGED-CONFIG ...`;每段之间空行分隔。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_surge_config.py
from surge_gw.surge_config import build_surge_config


def test_managed_header_first_and_sections_present():
    text = build_surge_config(
        proxy_lines=["A = socks5, 127.0.0.1, 1200, udp-relay=true"],
        group_lines=["Proxy = select, A, DIRECT"],
        rule_lines=["DOMAIN-SUFFIX,google.com,Proxy", "FINAL,DIRECT"],
        managed_url="http://127.0.0.1:8080/surge?token=abc",
        update_interval=3600,
    )
    lines = text.splitlines()
    assert lines[0] == "#!MANAGED-CONFIG http://127.0.0.1:8080/surge?token=abc interval=3600 strict=false"
    assert "[Proxy]" in text
    assert "[Proxy Group]" in text
    assert "[Rule]" in text
    assert "A = socks5, 127.0.0.1, 1200, udp-relay=true" in text
    assert text.index("[Proxy]") < text.index("[Proxy Group]") < text.index("[Rule]")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_surge_config.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/surge_config.py
from __future__ import annotations

_DEFAULT_GENERAL = {
    "loglevel": "notify",
    "skip-proxy": "127.0.0.1, localhost, *.local",
}


def build_surge_config(
    *,
    proxy_lines: list[str],
    group_lines: list[str],
    rule_lines: list[str],
    managed_url: str,
    update_interval: int = 3600,
    general: dict | None = None,
) -> str:
    """组装完整 Surge 配置文本。首行是 MANAGED-CONFIG 头,Surge 据此定期回取。"""
    general = {**_DEFAULT_GENERAL, **(general or {})}
    out: list[str] = []
    out.append(
        f"#!MANAGED-CONFIG {managed_url} interval={update_interval} strict=false"
    )
    out.append("")
    out.append("[General]")
    out.extend(f"{k} = {v}" for k, v in general.items())
    out.append("")
    out.append("[Proxy]")
    out.extend(proxy_lines)
    out.append("")
    out.append("[Proxy Group]")
    out.extend(group_lines)
    out.append("")
    out.append("[Rule]")
    out.extend(rule_lines)
    out.append("")
    return "\n".join(out)
```

- [ ] **Step 4: 运行全部测试确认通过**

Run: `pytest -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/surge_config.py tests/test_surge_config.py
git commit -m "feat: assemble full surge managed-config text"
```

---

## Self-Review

**Spec 覆盖(本计划范围)**:§8 fake-ip(Task 1)、§9 端口分配(Task 4)、§10.1 节点 socks5(Task 5)、§10.2 组(Task 6)、§10.3 规则映射含逻辑/RULE-SET/GEOSITE 引用(Task 7)、名字消毒(Task 3)、组装+MANAGED-CONFIG 头(Task 8)。**留给 Plan 2/3/4**:rule-provider/geosite **内容**转换、DOMAIN-SET 选择、fetcher、mihomo 运行时、orchestrator、HTTP、Docker —— 本计划只产出指向占位 URL 的引用并记录被引用名(`RuleResult.rule_providers/geosites`),Plan 2 据此抓取转换。

**占位符扫描**:无 TBD/TODO;每个代码步骤含完整代码与可跑命令。

**类型一致性**:`RuleResult`/`SkippedItem`(models)在 rules/groups 中一致使用;`name_map`/`port_map`/`policy_map` 命名贯穿 Task 5/6/7;`ruleset_url`/`geosite_url` 回调签名在 Task 7 定义并在测试中具体化。Task 6 实现 Step 5 提示清理误留的 `parts` 变量。

---

**已知后续依赖**:Plan 2 需要 Task 7 暴露的 `RuleResult.rule_providers` 与 `geosites` 作为"要抓取并转换的清单",并决定每个引用最终是 `RULE-SET` 还是 `DOMAIN-SET`(届时可能需回到 Task 7 让 `ruleset_url`/`geosite_url` 返回带类型的引用)。
