# surge-gw Plan 2 — 远程资源转换(rule-provider 内容 / geosite 解码 / 类型反填)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Plan 1 里只产出占位 `RULE-SET` 引用的远程资源(rule-provider 文件、geosite.dat),用纯函数转换成 Surge 的 ruleset/domainset 文本,并把"内容确认为纯域名表"的引用从 `RULE-SET` 反填为 `DOMAIN-SET`。

**Architecture:** 全部是无副作用纯函数:输入已抓取的字节/文本 + 行为/格式,输出 Surge 文本与一个 `kind`(`DOMAIN-SET`/`RULE-SET`)。不碰网络、不起子进程。抓取(经 socks 拉取)、mihomo 运行时、orchestrator、HTTP、Docker 全部留给后续 Plan。DOMAIN-SET vs RULE-SET 的类型决策发生在内容转换时;Plan 1 产出的规则行通过一个纯函数 `rewrite_ruleset_types` 在事后按 URL 精确匹配反填,Plan 1 已合并代码保持不动。

**Tech Stack:** Python 3.12+、PyYAML(rule-provider yaml 解析,**本计划起升为运行期依赖**)、pytest。geosite.dat 用极简手写 protobuf 解码,不引重型工具链。

## Global Constraints

- Python 3.12+;运行期依赖尽量少。本计划把 `pyyaml` 从开发依赖升为**运行期依赖**(rule-provider 与订阅本身都是 yaml,运行期必然需要)。
- 类型决策:rule-provider `domain` 行为 → `DOMAIN-SET`;`ipcidr` / `classical` → `RULE-SET`;geosite 分类无 keyword(仅 Full/Domain)→ `DOMAIN-SET`,含 keyword(Plain)→ `RULE-SET`。
- Surge DOMAIN-SET 行:精确域名写裸名 `x`,后缀写前导点 `.x`。Surge RULE-SET 行:去掉 policy 的规则体(如 `DOMAIN-SUFFIX,x` / `IP-CIDR,x` / `DOMAIN-KEYWORD,x`)。
- geosite `Domain.Type` 映射:`Full(3)`→`DOMAIN`、`Domain(2)`→`DOMAIN-SUFFIX`、`Plain(0)`→`DOMAIN-KEYWORD`、`Regex(1)`→跳过+计数。`@attr` 过滤:有 attr 时只保留带该 attribute key 的域名。
- mrs 二进制 rule-provider、`DOMAIN-REGEX`/正则条目、不可映射条目 → 跳过 + 计入 `SkippedItem`,绝不产出半条。
- 提交信息与代码注释**禁止过程污染**:不得出现任务/步骤编号、方案代号、审阅轮次、临时引用;只解释 WHY 与必须保持的不变量。
- TDD:先写失败测试,再最小实现;频繁提交;DRY;YAGNI。

---

## File Structure

```
surge-gw/
├── pyproject.toml              # 修改:pyyaml 升为运行期依赖
├── src/surge_gw/
│   ├── models.py               # 修改:新增 RulesetArtifact
│   ├── rules.py                # 修改:子规则保留选项 + 暴露 convert_rule_body(DRY 复用 §10.3)
│   ├── providers.py            # 新建:rule-provider 内容 → RulesetArtifact(domain/ipcidr/classical)+ payload 提取
│   ├── geosite.py              # 新建:geosite.dat 解码 + 分类 → RulesetArtifact + @attr 过滤
│   └── reconcile.py            # 新建:rewrite_ruleset_types 类型反填
└── tests/
    ├── test_providers.py
    ├── test_geosite.py
    ├── test_reconcile.py
    └── test_rules.py           # 修改:新增子规则保留选项的回归测试
```

依赖方向:`providers` → `rules`(复用 `convert_rule_body`);`providers`/`geosite` → `models`;`reconcile` 独立。无环。

---

### Task 1: rule-provider payload 提取(yaml / text)

把 rule-provider 文件原文拆成"条目字符串列表",供后续按 behavior 转换。yaml 取 `payload:` 列表,text 逐行去注释。

**Files:**
- Modify: `pyproject.toml`
- Create: `src/surge_gw/providers.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Produces:
  - `extract_provider_entries(raw: str, fmt: str) -> list[str]` — `fmt` 为 `"yaml"` 时取 `payload` 列表并字符串化;否则按 text:逐行 strip,跳过空行与 `#` 注释。

- [ ] **Step 1: 把 pyyaml 升为运行期依赖**

```toml
[project]
name = "surge-gw"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pyyaml>=6"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_providers.py
from surge_gw.providers import extract_provider_entries


def test_extract_text_skips_comments_and_blanks():
    raw = "# header\n+.a.com\n\n  b.com  \n# tail\n"
    assert extract_provider_entries(raw, "text") == ["+.a.com", "b.com"]


def test_extract_yaml_reads_payload_list():
    raw = "payload:\n  - '+.a.com'\n  - b.com\n  - 'IP-CIDR,1.2.3.0/24'\n"
    assert extract_provider_entries(raw, "yaml") == [
        "+.a.com", "b.com", "IP-CIDR,1.2.3.0/24",
    ]


def test_extract_yaml_empty_payload_is_empty_list():
    assert extract_provider_entries("payload:\n", "yaml") == []
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/test_providers.py -v`
Expected: FAIL(ModuleNotFoundError: surge_gw.providers)

- [ ] **Step 4: 实现**

```python
# src/surge_gw/providers.py
from __future__ import annotations

import yaml


def extract_provider_entries(raw: str, fmt: str) -> list[str]:
    """rule-provider 原文 → 条目列表。yaml 取 payload 列表;text 逐行去注释。
    抓取(经 socks)由后续 Plan 负责,本函数只做纯文本解析。"""
    if fmt == "yaml":
        data = yaml.safe_load(raw) or {}
        payload = data.get("payload") or []
        return [str(item) for item in payload]
    entries: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append(stripped)
    return entries
```

- [ ] **Step 5: 装运行期依赖、运行测试确认通过**

Run:
```bash
.venv/bin/pip install -q -e ".[dev]"
.venv/bin/python -m pytest tests/test_providers.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/surge_gw/providers.py tests/test_providers.py
git commit -m "feat: parse rule-provider payload from yaml and text"
```

---

### Task 2: `RulesetArtifact` 模型 + rule-provider `domain` → DOMAIN-SET

定义贯穿本计划的产物类型;实现纯域名表 → Surge DOMAIN-SET(裸域名,前导点=后缀)。

**Files:**
- Modify: `src/surge_gw/models.py`
- Modify: `src/surge_gw/providers.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Produces:
  - `RulesetArtifact` dataclass:`lines: list[str]`、`kind: str`(`"DOMAIN-SET"` / `"RULE-SET"`,默认 `"RULE-SET"`)、`skipped: list[SkippedItem]`。
  - `convert_domain_provider(entries: list[str]) -> RulesetArtifact` — `kind="DOMAIN-SET"`;`+.x`→`.x`,前导点保留,其余按精确域名原样。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_providers.py(追加)
from surge_gw.providers import convert_domain_provider


def test_domain_provider_to_domain_set():
    art = convert_domain_provider(["+.google.com", "example.com", ".cdn.net"])
    assert art.kind == "DOMAIN-SET"
    assert art.lines == [".google.com", "example.com", ".cdn.net"]
    assert art.skipped == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_providers.py::test_domain_provider_to_domain_set -v`
Expected: FAIL(ImportError: cannot import name 'convert_domain_provider')

- [ ] **Step 3: 在 models.py 新增 RulesetArtifact**

```python
# src/surge_gw/models.py(在文件末尾追加)
@dataclass
class RulesetArtifact:
    """一个转换后的 ruleset/domainset 产物;kind 决定 Surge 引用关键字。"""
    lines: list[str] = field(default_factory=list)
    kind: str = "RULE-SET"          # "DOMAIN-SET" | "RULE-SET"
    skipped: list[SkippedItem] = field(default_factory=list)
```

- [ ] **Step 4: 在 providers.py 实现 convert_domain_provider**

```python
# src/surge_gw/providers.py(追加 import 与函数)
from surge_gw.models import RulesetArtifact


def _domain_entry_to_set_line(entry: str) -> str:
    """behavior=domain 条目 → Surge DOMAIN-SET 行。
    '+.x'(后缀)→ '.x';前导点原样;其余视作精确域名原样。"""
    if entry.startswith("+."):
        return "." + entry[2:]
    return entry


def convert_domain_provider(entries: list[str]) -> RulesetArtifact:
    """纯域名表 → Surge DOMAIN-SET(裸域名;前导点 = 后缀匹配)。"""
    art = RulesetArtifact(kind="DOMAIN-SET")
    for entry in entries:
        art.lines.append(_domain_entry_to_set_line(entry))
    return art
```

将 `from surge_gw.models import RulesetArtifact` 放在文件顶部已有 import 区。

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_providers.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/surge_gw/models.py src/surge_gw/providers.py tests/test_providers.py
git commit -m "feat: convert domain rule-provider to surge domain-set"
```

---

### Task 3: rule-provider `ipcidr` → RULE-SET

**Files:**
- Modify: `src/surge_gw/providers.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Produces:
  - `convert_ipcidr_provider(entries: list[str]) -> RulesetArtifact` — `kind="RULE-SET"`;含 `:` 判为 IPv6 → `IP-CIDR6`,否则 `IP-CIDR`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_providers.py(追加)
from surge_gw.providers import convert_ipcidr_provider


def test_ipcidr_provider_to_rule_set():
    art = convert_ipcidr_provider(["1.2.3.0/24", "2001:db8::/32"])
    assert art.kind == "RULE-SET"
    assert art.lines == ["IP-CIDR,1.2.3.0/24", "IP-CIDR6,2001:db8::/32"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_providers.py::test_ipcidr_provider_to_rule_set -v`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/providers.py(追加)
def convert_ipcidr_provider(entries: list[str]) -> RulesetArtifact:
    """IP 段表 → Surge RULE-SET(IP-CIDR / IP-CIDR6 行)。"""
    art = RulesetArtifact(kind="RULE-SET")
    for entry in entries:
        cidr = entry.strip()
        rtype = "IP-CIDR6" if ":" in cidr else "IP-CIDR"
        art.lines.append(f"{rtype},{cidr}")
    return art
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_providers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/providers.py tests/test_providers.py
git commit -m "feat: convert ipcidr rule-provider to surge ip rules"
```

---

### Task 4: rule-provider `classical` → RULE-SET(复用 §10.3 映射)

classical 条目就是"去掉 policy 的规则体",与逻辑子规则同形。本任务把 Plan 1 `rules.py` 里隐藏在 `_convert_subrule` 中的"无 policy 规则体转换"提炼为公开的 `convert_rule_body`,两处共用;顺带修复子规则丢弃尾部选项(如 `no-resolve`)的缺陷。

**Files:**
- Modify: `src/surge_gw/rules.py`
- Modify: `src/surge_gw/providers.py`
- Test: `tests/test_rules.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: `rules.convert_rule_body`。
- Produces:
  - `rules.convert_rule_body(body: str) -> str | None` — 把无 policy 的 `TYPE,PAYLOAD[,opts]` 转成 Surge 规则体;不可映射返回 `None`。
  - `providers.convert_classical_provider(entries: list[str]) -> RulesetArtifact` — `kind="RULE-SET"`;逐条调 `convert_rule_body`,`None` 进 `skipped`。

- [ ] **Step 1: 写失败测试(子规则保留选项的回归 + classical 转换)**

```python
# tests/test_rules.py(追加)
def test_logical_subrule_preserves_options():
    r = conv(["AND,((IP-CIDR,1.2.3.0/24,no-resolve),(NETWORK,udp)),Proxy"])
    assert r.lines == ["AND,((IP-CIDR,1.2.3.0/24,no-resolve),(PROTOCOL,UDP)),Proxy"]


def test_convert_rule_body_maps_and_rejects():
    from surge_gw.rules import convert_rule_body
    assert convert_rule_body("DST-PORT,443") == "DEST-PORT,443"
    assert convert_rule_body("IP-CIDR,1.2.3.0/24,no-resolve") == "IP-CIDR,1.2.3.0/24,no-resolve"
    assert convert_rule_body("DOMAIN-REGEX,.*\\.cn") is None
```

```python
# tests/test_providers.py(追加)
from surge_gw.providers import convert_classical_provider


def test_classical_provider_maps_bodies():
    art = convert_classical_provider([
        "DOMAIN-SUFFIX,example.com",
        "IP-CIDR,1.2.3.0/24,no-resolve",
        "DST-PORT,443",
    ])
    assert art.kind == "RULE-SET"
    assert art.lines == [
        "DOMAIN-SUFFIX,example.com",
        "IP-CIDR,1.2.3.0/24,no-resolve",
        "DEST-PORT,443",
    ]


def test_classical_provider_skips_unsupported():
    art = convert_classical_provider(["DOMAIN-REGEX,.*\\.cn", "DOMAIN,ok.com"])
    assert art.lines == ["DOMAIN,ok.com"]
    assert len(art.skipped) == 1
    assert art.skipped[0].kind == "ruleset"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_rules.py::test_convert_rule_body_maps_and_rejects tests/test_providers.py -v`
Expected: FAIL(ImportError: convert_rule_body / convert_classical_provider)

- [ ] **Step 3: 重构 rules.py — 子规则保留选项 + 暴露 convert_rule_body**

把 `_convert_one` 中 `is_subrule` 分支的选项捕获改为保留 payload 与 sentinel 之间的字段(原先丢弃):

```python
# src/surge_gw/rules.py —— is_subrule 分支
    is_subrule = fields[-1] == "__NOPOLICY__"
    if is_subrule:
        policy_raw = "__NOPOLICY__"
        payload = fields[1] if len(fields) > 2 else ""
        # 保留 payload 与占位 policy 之间的选项(如 no-resolve),供 classical
        # rule-provider 与逻辑子规则共用同一套 §10.3 映射时不丢信息。
        options = [o.strip() for o in fields[2:-1]]
```

新增公开函数(放在 `_convert_subrule` 之前):

```python
# src/surge_gw/rules.py —— 新增
def convert_rule_body(body: str) -> str | None:
    """把无 policy 的规则体 'TYPE,PAYLOAD[,opts]' 转成 Surge 规则体,不可映射返回 None。
    rule-provider(classical)与逻辑子规则共用,保证 §10.3 映射只有一处实现。"""
    line = _convert_one(f"{body},__NOPOLICY__", {}, lambda name: name, lambda cat: cat)
    if line is None or "__NOPOLICY__" not in line:
        return None
    return line.rsplit(",__NOPOLICY__", 1)[0]
```

把 `_convert_subrule` 改为复用它(去掉重复的 sentinel 拼接逻辑),并更新调用处:

```python
# src/surge_gw/rules.py —— 替换原 _convert_subrule
def _convert_subrule(sub: str) -> str | None:
    """子规则形如 '(DOMAIN,a.com)';返回 Surge 子规则或 None(不可转)。"""
    body = convert_rule_body(sub[1:-1])
    return f"({body})" if body is not None else None
```

```python
# src/surge_gw/rules.py —— _convert_logical 内调用处改为单参
        else:
            c = _convert_subrule(sub)
            if c is None:
                return None
            converted_subs.append(c)
```

- [ ] **Step 4: 在 providers.py 实现 convert_classical_provider**

```python
# src/surge_gw/providers.py(追加 import 与函数)
from surge_gw.models import RulesetArtifact, SkippedItem
from surge_gw.rules import convert_rule_body


def convert_classical_provider(entries: list[str]) -> RulesetArtifact:
    """classical 表 → Surge RULE-SET(逐条按 §10.3 去 policy 映射;不可映射跳过)。"""
    art = RulesetArtifact(kind="RULE-SET")
    for entry in entries:
        body = convert_rule_body(entry)
        if body is None:
            art.skipped.append(SkippedItem("ruleset", entry, "unsupported classical rule"))
        else:
            art.lines.append(body)
    return art
```

(把已有的 `from surge_gw.models import RulesetArtifact` 一行合并为上面的 `RulesetArtifact, SkippedItem`。)

- [ ] **Step 5: 运行测试确认全绿(含 Plan 1 规则回归)**

Run: `pytest tests/test_rules.py tests/test_providers.py -v`
Expected: PASS(原 `test_rules.py` 全部仍绿,证明重构无回归)

- [ ] **Step 6: Commit**

```bash
git add src/surge_gw/rules.py src/surge_gw/providers.py tests/test_rules.py tests/test_providers.py
git commit -m "feat: convert classical rule-provider via shared rule-body mapper"
```

---

### Task 5: geosite.dat protobuf 解码

把 v2ray `GeoSiteList` 二进制解码为 `{分类大写名: [GeoDomain]}`。极简手写 varint + length-delimited 解码,只认本 schema 用到的 wire type(0 varint / 2 length-delimited)。

**Files:**
- Create: `src/surge_gw/geosite.py`
- Test: `tests/test_geosite.py`

**Interfaces:**
- Produces:
  - 常量 `TYPE_PLAIN=0`、`TYPE_REGEX=1`、`TYPE_DOMAIN=2`、`TYPE_FULL=3`。
  - `GeoDomain` frozen dataclass:`type: int`、`value: str`、`attrs: frozenset[str]`。
  - `decode_geosite_dat(data: bytes) -> dict[str, list[GeoDomain]]` — 分类名按 mihomo 习惯大写。

- [ ] **Step 1: 写失败测试(自带极简 protobuf 编码器造夹具)**

```python
# tests/test_geosite.py
from surge_gw.geosite import (
    GeoDomain, decode_geosite_dat, TYPE_FULL, TYPE_DOMAIN, TYPE_PLAIN,
)


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _tag(field_no: int, wire: int) -> bytes:
    return _varint((field_no << 3) | wire)


def _ld(field_no: int, data: bytes) -> bytes:        # length-delimited
    return _tag(field_no, 2) + _varint(len(data)) + data


def _vint(field_no: int, n: int) -> bytes:           # varint
    return _tag(field_no, 0) + _varint(n)


def _str(field_no: int, s: str) -> bytes:
    return _ld(field_no, s.encode("utf-8"))


def _attr(key: str) -> bytes:                        # Domain.attribute(field 3)
    return _ld(3, _str(1, key))


def _domain(dtype: int, value: str, attr_keys=()) -> bytes:   # GeoSite.domain(field 2)
    body = _vint(1, dtype) + _str(2, value) + b"".join(_attr(k) for k in attr_keys)
    return _ld(2, body)


def _geosite(code: str, domains) -> bytes:           # GeoSiteList.entry(field 1)
    return _ld(1, _str(1, code) + b"".join(domains))


def test_decode_categories_types_and_attrs():
    dat = (
        _geosite("google", [
            _domain(TYPE_FULL, "google.com"),
            _domain(TYPE_DOMAIN, "google.com.hk"),
            _domain(TYPE_PLAIN, "googlevideo", ["ads"]),
        ])
        + _geosite("cn", [_domain(TYPE_DOMAIN, "qq.com")])
    )
    cats = decode_geosite_dat(dat)
    assert set(cats) == {"GOOGLE", "CN"}
    g = cats["GOOGLE"]
    assert (g[0].type, g[0].value) == (TYPE_FULL, "google.com")
    assert (g[1].type, g[1].value) == (TYPE_DOMAIN, "google.com.hk")
    assert g[2].attrs == frozenset({"ads"})
    assert cats["CN"] == [GeoDomain(TYPE_DOMAIN, "qq.com", frozenset())]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_geosite.py -v`
Expected: FAIL(ModuleNotFoundError: surge_gw.geosite)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/geosite.py
from __future__ import annotations

from dataclasses import dataclass

# v2ray Domain.Type
TYPE_PLAIN = 0     # 子串关键字
TYPE_REGEX = 1     # 正则
TYPE_DOMAIN = 2    # 域名 + 子域(后缀)
TYPE_FULL = 3      # 精确域名


@dataclass(frozen=True)
class GeoDomain:
    type: int
    value: str
    attrs: frozenset[str]


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _iter_fields(buf: bytes):
    """遍历一个 protobuf 消息,产出 (field_no, value)。
    只支持本 schema 用到的 wire type:0(varint)、2(length-delimited)。"""
    pos, n = 0, len(buf)
    while pos < n:
        tag, pos = _read_varint(buf, pos)
        field_no, wire = tag >> 3, tag & 0x7
        if wire == 0:
            val, pos = _read_varint(buf, pos)
            yield field_no, val
        elif wire == 2:
            length, pos = _read_varint(buf, pos)
            yield field_no, buf[pos : pos + length]
            pos += length
        else:
            raise ValueError(f"unsupported wire type {wire}")


def _decode_attribute(buf: bytes) -> str:
    for field_no, val in _iter_fields(buf):
        if field_no == 1 and isinstance(val, bytes):
            return val.decode("utf-8")
    return ""


def _decode_domain(buf: bytes) -> GeoDomain:
    dtype, value, attrs = TYPE_PLAIN, "", set()
    for field_no, val in _iter_fields(buf):
        if field_no == 1 and isinstance(val, int):
            dtype = val
        elif field_no == 2 and isinstance(val, bytes):
            value = val.decode("utf-8")
        elif field_no == 3 and isinstance(val, bytes):
            key = _decode_attribute(val)
            if key:
                attrs.add(key)
    return GeoDomain(dtype, value, frozenset(attrs))


def _decode_geosite(buf: bytes) -> tuple[str, list[GeoDomain]]:
    code, domains = "", []
    for field_no, val in _iter_fields(buf):
        if field_no == 1 and isinstance(val, bytes):
            code = val.decode("utf-8")
        elif field_no == 2 and isinstance(val, bytes):
            domains.append(_decode_domain(val))
    return code, domains


def decode_geosite_dat(data: bytes) -> dict[str, list[GeoDomain]]:
    """解码 v2ray GeoSiteList protobuf 为 {分类大写名: [GeoDomain]}。
    .dat 内 include 已在编译期展平,无需递归;只做结构解码。"""
    result: dict[str, list[GeoDomain]] = {}
    for field_no, val in _iter_fields(data):
        if field_no == 1 and isinstance(val, bytes):   # GeoSiteList.entry
            code, domains = _decode_geosite(val)
            result[code.upper()] = domains
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_geosite.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/geosite.py tests/test_geosite.py
git commit -m "feat: decode v2ray geosite protobuf into categories"
```

---

### Task 6: geosite 分类 → RulesetArtifact(类型映射 + @attr 过滤 + DOMAIN-SET/RULE-SET 决策)

**Files:**
- Modify: `src/surge_gw/geosite.py`
- Test: `tests/test_geosite.py`

**Interfaces:**
- Consumes: `decode_geosite_dat` 的 `list[GeoDomain]`;`models.RulesetArtifact` / `SkippedItem`。
- Produces:
  - `split_geosite_ref(ref: str) -> tuple[str, str | None]` — `"google@cn"`→`("GOOGLE","cn")`;`"google"`→`("GOOGLE", None)`。
  - `build_geosite_artifact(domains: list[GeoDomain], attr: str | None) -> RulesetArtifact` — 按 attr 过滤;`Regex` 跳过+计数;无 keyword → `DOMAIN-SET`(裸域名,后缀加前导点),含 keyword → `RULE-SET`(去 policy 规则体)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_geosite.py(追加)
from surge_gw.geosite import split_geosite_ref, build_geosite_artifact
from surge_gw.geosite import TYPE_REGEX


def test_split_geosite_ref():
    assert split_geosite_ref("google@cn") == ("GOOGLE", "cn")
    assert split_geosite_ref("google") == ("GOOGLE", None)


def test_geosite_pure_domain_is_domain_set():
    domains = [
        GeoDomain(TYPE_FULL, "a.com", frozenset()),
        GeoDomain(TYPE_DOMAIN, "b.com", frozenset()),
    ]
    art = build_geosite_artifact(domains, None)
    assert art.kind == "DOMAIN-SET"
    assert art.lines == ["a.com", ".b.com"]
    assert art.skipped == []


def test_geosite_with_keyword_is_rule_set():
    domains = [
        GeoDomain(TYPE_DOMAIN, "b.com", frozenset()),
        GeoDomain(TYPE_PLAIN, "ads", frozenset()),
    ]
    art = build_geosite_artifact(domains, None)
    assert art.kind == "RULE-SET"
    assert art.lines == ["DOMAIN-SUFFIX,b.com", "DOMAIN-KEYWORD,ads"]


def test_geosite_attr_filter_and_regex_skipped():
    domains = [
        GeoDomain(TYPE_DOMAIN, "keep.com", frozenset({"cn"})),
        GeoDomain(TYPE_DOMAIN, "drop.com", frozenset()),
        GeoDomain(TYPE_REGEX, ".*ads.*", frozenset({"cn"})),
    ]
    art = build_geosite_artifact(domains, "cn")
    assert art.kind == "DOMAIN-SET"
    assert art.lines == [".keep.com"]
    assert len(art.skipped) == 1
    assert art.skipped[0].kind == "geosite-regexp"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_geosite.py -v`
Expected: FAIL(ImportError: split_geosite_ref / build_geosite_artifact)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/geosite.py(追加 import 与函数)
from surge_gw.models import RulesetArtifact, SkippedItem

_TYPE_TO_RULE = {TYPE_FULL: "DOMAIN", TYPE_DOMAIN: "DOMAIN-SUFFIX", TYPE_PLAIN: "DOMAIN-KEYWORD"}


def split_geosite_ref(ref: str) -> tuple[str, str | None]:
    """'google@cn' → ('GOOGLE', 'cn');'google' → ('GOOGLE', None)。"""
    if "@" in ref:
        category, attr = ref.split("@", 1)
        return category.upper(), (attr or None)
    return ref.upper(), None


def build_geosite_artifact(domains: list[GeoDomain], attr: str | None) -> RulesetArtifact:
    """按 @attr 过滤后做类型映射。无 keyword(仅 Full/Domain)→ DOMAIN-SET;
    含 keyword(Plain)→ RULE-SET;Regex 跳过 + 计数。"""
    selected = [d for d in domains if attr is None or attr in d.attrs]
    skipped: list[SkippedItem] = []
    kept: list[GeoDomain] = []
    for d in selected:
        if d.type == TYPE_REGEX:
            skipped.append(SkippedItem("geosite-regexp", d.value, "regex domain unsupported"))
        else:
            kept.append(d)

    if any(d.type == TYPE_PLAIN for d in kept):
        lines = [f"{_TYPE_TO_RULE[d.type]},{d.value}" for d in kept]
        return RulesetArtifact(lines=lines, kind="RULE-SET", skipped=skipped)

    # 纯 domain/full → DOMAIN-SET:精确写裸名,后缀加前导点
    lines = [("." + d.value if d.type == TYPE_DOMAIN else d.value) for d in kept]
    return RulesetArtifact(lines=lines, kind="DOMAIN-SET", skipped=skipped)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_geosite.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/geosite.py tests/test_geosite.py
git commit -m "feat: map geosite category to surge ruleset with attr filter"
```

---

### Task 7: `rewrite_ruleset_types` 类型反填

Plan 1 的规则行统一写了 `RULE-SET,<url>,<policy>`。内容转换确认为纯域名表(`DOMAIN-SET`)的引用,按 URL 精确匹配把对应行的关键字改成 `DOMAIN-SET`。纯函数,反填编排留给后续 Plan(届时由它据各引用的 `kind` 与 url 组装 `domain_set_urls`)。

**Files:**
- Create: `src/surge_gw/reconcile.py`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Consumes: Plan 1 `[Rule]` 文本行;`domain_set_urls`(应升级为 DOMAIN-SET 的引用 URL 集合)。
- Produces:
  - `rewrite_ruleset_types(rule_lines: list[str], domain_set_urls: set[str]) -> list[str]` — 仅改 `RULE-SET,` 开头且 url 字段(第二段)命中集合的行;其余原样。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_reconcile.py
from surge_gw.reconcile import rewrite_ruleset_types


def test_rewrite_promotes_matching_urls():
    lines = [
        "RULE-SET,http://h/ruleset/cn?token=t,Proxy",
        "RULE-SET,http://h/ruleset/geosite-google?token=t,Proxy",
        "DOMAIN,x.com,Proxy",
    ]
    out = rewrite_ruleset_types(lines, {"http://h/ruleset/cn?token=t"})
    assert out == [
        "DOMAIN-SET,http://h/ruleset/cn?token=t,Proxy",
        "RULE-SET,http://h/ruleset/geosite-google?token=t,Proxy",
        "DOMAIN,x.com,Proxy",
    ]


def test_rewrite_noop_when_no_match():
    lines = ["RULE-SET,http://h/ruleset/cn,Proxy"]
    assert rewrite_ruleset_types(lines, set()) == lines
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_reconcile.py -v`
Expected: FAIL(ModuleNotFoundError: surge_gw.reconcile)

- [ ] **Step 3: 实现**

```python
# src/surge_gw/reconcile.py
from __future__ import annotations


def rewrite_ruleset_types(rule_lines: list[str], domain_set_urls: set[str]) -> list[str]:
    """把内容确认为纯域名表的引用从 RULE-SET 改写成 DOMAIN-SET。
    按 url(第二段,含 token/query)精确匹配;其余行不动。"""
    out: list[str] = []
    for line in rule_lines:
        if line.startswith("RULE-SET,"):
            fields = line.split(",")
            if len(fields) >= 2 and fields[1] in domain_set_urls:
                fields[0] = "DOMAIN-SET"
                out.append(",".join(fields))
                continue
        out.append(line)
    return out
```

- [ ] **Step 4: 运行全部测试确认通过**

Run: `pytest -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/surge_gw/reconcile.py tests/test_reconcile.py
git commit -m "feat: promote pure-domain ruleset references to domain-set"
```

---

## Self-Review

**Spec 覆盖(本计划范围)**:§10.4 rule-provider(domain→DOMAIN-SET / ipcidr→RULE-SET / classical 复用 §10.3,Task 1–4)、§10.5 GEOSITE(geosite.dat 解码 + 类型映射 + `@attr` 过滤 + DOMAIN-SET vs RULE-SET,Task 5–6)、Plan 1 遗留的类型反填(Task 7)。**留给 Plan 3+**:fetcher(经 socks 拉取订阅 / rule-provider / geosite.dat)、mihomo_cfg_builder、mihomo_manager(`/proxies`、`/providers/proxies`、reload)、orchestrator(刷新流水线 + 单飞防抖 + 缓存原子替换)、http_server(`/surge` `/ruleset/<n>` `/health` `/refresh` + token)、cache、Docker。Plan 3 据各 `RulesetArtifact.kind` 与其 url 组装 `domain_set_urls` 调 `rewrite_ruleset_types`,并把 artifact 文本以正确 content 托管到 `/ruleset/<n>`。

**占位符扫描**:无 TBD/TODO;每个代码步骤含完整代码与可跑命令。

**类型一致性**:`RulesetArtifact`(`lines`/`kind`/`skipped`)在 providers/geosite 一致使用;`SkippedItem`(Plan 1 models)沿用,`kind` 取值 `"ruleset"`/`"geosite-regexp"`;`convert_rule_body` 在 rules 定义、providers 消费,签名一致;`decode_geosite_dat` → `list[GeoDomain]` → `build_geosite_artifact` 类型贯通;`rewrite_ruleset_types` 的 `domain_set_urls` 与 Plan 1 规则行第二段(url)同源精确匹配。

**已知后续依赖**:Plan 3 的 orchestrator 必须在调用 `rewrite_ruleset_types` 前完成所有引用的内容转换(才能知道每个 url 的 `kind`);`/ruleset/<n>` 返回 artifact 文本时,DOMAIN-SET 与 RULE-SET 的 content-type/格式由 Surge 按引用关键字解释,服务端按行原样吐出即可。

---

**重构说明(WHY)**:Task 4 把 §10.3 的"无 policy 规则体映射"从 `rules._convert_subrule` 提炼为公开的 `convert_rule_body`,使 classical rule-provider 与逻辑子规则共用同一处实现(DRY),并修复原 `is_subrule` 分支丢弃尾部选项(如 `no-resolve`)的缺陷——该缺陷此前因无逻辑子规则带选项的测试而未暴露。
