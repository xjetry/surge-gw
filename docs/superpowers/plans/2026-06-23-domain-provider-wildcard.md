# Domain Provider 通配符保留命中 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让含通配符（`*`/`?`）的 Clash `behavior: domain` rule-provider 在 Surge 下仍能命中——含通配符的 provider 整表降级为 RULE-SET 并用 `DOMAIN-WILDCARD` 表达，纯域名 provider 维持 DOMAIN-SET。

**Architecture:** 改动收敛在 `src/surge_gw/providers.py` 的 `convert_domain_provider`：按"是否含通配符"分派两种产出模式。`assemble.py` 由 `art.kind` 驱动引用行反填，含通配符 provider 产出 `kind="RULE-SET"` 即天然保持 `RULE-SET,<url>,<policy>` 引用行，无需改动。

**Tech Stack:** Python 3，pytest，现有 `surge_gw` 模块（无新依赖）。

## Global Constraints

- DOMAIN-SET 与 RULE-SET 文件均为 Surge **严格校验**：单行非法整份资源失效。两种模式写出的每一行都必须通过对应正则校验；无法表达的条目计入 `skipped`，**绝不写入非法行**。
- 含通配符的条目采用 Surge 原生 `DOMAIN-WILDCARD` 语义（`*` 跨段匹配、`?` 单字符），**不**对 pattern 做单段化改写。
- 仅当 provider **含**通配符条目时才整表降级 RULE-SET；不含通配符的 provider 行为与现状完全一致（保留 DOMAIN-SET 大列表性能优化）。
- `DOMAIN-WILDCARD` 值的字符集禁止逗号/空格/控制字符（逗号会破坏 RULE-SET 行的字段切分）。
- 注释/commit 不得含任务编号、方案代号等过程信息；只解释 WHY 与 invariant。

---

### Task 1: providers.py 通配符保留映射

**Files:**
- Modify: `src/surge_gw/providers.py`（新增正则与映射 helper，改写 `convert_domain_provider`）
- Test: `tests/test_providers.py`（替换旧的"剔除通配符"测试，新增 RULE-SET 模式用例）

**Interfaces:**
- Consumes: `RulesetArtifact`、`SkippedItem`（来自 `surge_gw.models`，已存在）。
- Produces:
  - `convert_domain_provider(entries: list[str]) -> RulesetArtifact`（签名不变；含通配符时 `kind=="RULE-SET"`，否则 `kind=="DOMAIN-SET"`）。
  - `_has_wildcard(entry: str) -> bool`、`_domain_entry_to_rule_line(entry: str) -> str | None`（模块内 helper）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_providers.py` 中**删除**现有的 `test_domain_provider_drops_wildcard_entries_that_break_surge_domain_set`（约 30-39 行），并在 `test_domain_provider_to_domain_set` 之后插入以下测试：

```python
def test_domain_provider_with_wildcard_becomes_rule_set():
    # 含通配符 → 整表降级 RULE-SET;通配符用 DOMAIN-WILDCARD 表达(剥离前导 +. / .),
    # 纯域名/后缀按类型映射。通配符域名因此仍可命中,而非被丢弃。
    art = convert_domain_provider([
        "+.github.com", ".objectstorage.*.oraclecloud.com", "localhost.*.qq.com", "ok.com",
    ])
    assert art.kind == "RULE-SET"
    assert art.lines == [
        "DOMAIN-SUFFIX,github.com",
        "DOMAIN-WILDCARD,objectstorage.*.oraclecloud.com",
        "DOMAIN-WILDCARD,localhost.*.qq.com",
        "DOMAIN,ok.com",
    ]
    assert art.skipped == []


def test_domain_provider_rule_set_maps_suffix_and_exact():
    # 进入 RULE-SET 模式后,各形态条目的映射:通配符 / 前导点后缀 / 精确 / +. 后缀
    art = convert_domain_provider(["*.wild.com", ".cdn.net", "exact.com", "+.suf.org"])
    assert art.kind == "RULE-SET"
    assert art.lines == [
        "DOMAIN-WILDCARD,*.wild.com",
        "DOMAIN-SUFFIX,cdn.net",
        "DOMAIN,exact.com",
        "DOMAIN-SUFFIX,suf.org",
    ]
    assert art.skipped == []


def test_domain_provider_rule_set_skips_invalid_lines():
    # RULE-SET 同样严格校验:含逗号/空格的条目无法成行,计入 skipped 而非写入非法行
    art = convert_domain_provider(["*.ok.com", "bad,comma.com", "has space.com"])
    assert art.kind == "RULE-SET"
    assert art.lines == ["DOMAIN-WILDCARD,*.ok.com"]
    assert {s.detail for s in art.skipped} == {"bad,comma.com", "has space.com"}
    assert all(s.kind == "ruleset" for s in art.skipped)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_providers.py -q`
Expected: FAIL —— `test_domain_provider_with_wildcard_becomes_rule_set` 等断言 `kind=="RULE-SET"`，而现状 `convert_domain_provider` 仍产出 `DOMAIN-SET` 并剔除通配符。

- [ ] **Step 3: 改写 providers.py**

把 `src/surge_gw/providers.py` 顶部的正则定义替换为下面三条，并保留 `_domain_entry_to_set_line` 不变（其对通配符返回 `None` 在新逻辑下成为无害的双重保险）：

```python
# Surge DOMAIN-SET / RULE-SET 均为严格校验:任一非法行会让整份资源失效。故据此校验,
# 不可表达的条目剔除并计入 skipped,以保资源整体有效。
# 裸域名(可选前导点 = 后缀),用于 DOMAIN-SET 行。
_DOMAIN_SET_LINE = re.compile(r"^\.?[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
# 裸域名(无前导点),用作 DOMAIN / DOMAIN-SUFFIX 的值。
_BARE_DOMAIN = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
# 通配符域名:裸域名字符集 + '*' / '?'(Surge DOMAIN-WILDCARD 的通配符)。
# 禁止逗号/空格/控制字符 —— 逗号会破坏 RULE-SET 行的字段切分。
_WILDCARD_DOMAIN = re.compile(r"^[A-Za-z0-9_*?-]+(?:\.[A-Za-z0-9_*?-]+)*$")
```

在 `_domain_entry_to_set_line` 之后新增两个 helper：

```python
def _has_wildcard(entry: str) -> bool:
    return "*" in entry or "?" in entry


def _domain_entry_to_rule_line(entry: str) -> str | None:
    """RULE-SET 模式:单个 domain 条目 → 一行 Surge 规则体(无 policy),不可表达返回 None。
    含通配符 → DOMAIN-WILDCARD(剥离前导 '+.' / '.';其'子域'语义被 '*' 跨段匹配覆盖);
    '+.x' / '.x' 后缀 → DOMAIN-SUFFIX;精确域名 → DOMAIN。"""
    if _has_wildcard(entry):
        pattern = entry[2:] if entry.startswith("+.") else entry.lstrip(".")
        return f"DOMAIN-WILDCARD,{pattern}" if _WILDCARD_DOMAIN.match(pattern) else None
    if entry.startswith("+."):
        body = entry[2:]
        return f"DOMAIN-SUFFIX,{body}" if _BARE_DOMAIN.match(body) else None
    if entry.startswith("."):
        body = entry[1:]
        return f"DOMAIN-SUFFIX,{body}" if _BARE_DOMAIN.match(body) else None
    return f"DOMAIN,{entry}" if _BARE_DOMAIN.match(entry) else None
```

把 `convert_domain_provider` 整体替换为：

```python
def convert_domain_provider(entries: list[str]) -> RulesetArtifact:
    """纯域名表 → Surge DOMAIN-SET(裸域名;前导点 = 后缀)。
    含通配符(* / ?)的条目无法在 DOMAIN-SET 表达,则整表降级为 RULE-SET,逐条映射为
    DOMAIN / DOMAIN-SUFFIX / DOMAIN-WILDCARD,使通配符域名仍可命中。
    两种模式写出的每行都过严格校验,非法条目计入 skipped —— DOMAIN-SET / RULE-SET 均为
    严格校验,单行非法会让整份资源失效。"""
    if any(_has_wildcard(e) for e in entries):
        art = RulesetArtifact(kind="RULE-SET")
        to_line = _domain_entry_to_rule_line
    else:
        art = RulesetArtifact(kind="DOMAIN-SET")
        to_line = _domain_entry_to_set_line
    for entry in entries:
        line = to_line(entry)
        if line is None:
            art.skipped.append(SkippedItem("ruleset", entry, "domain entry not representable"))
        else:
            art.lines.append(line)
    return art
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_providers.py -q`
Expected: PASS（全部用例通过）。

- [ ] **Step 5: 跑相关测试确认无回归**

Run: `python3 -m pytest tests/test_providers.py tests/test_reconcile.py tests/test_geosite.py -q`
Expected: PASS（纯域名仍走 DOMAIN-SET，reconcile/geosite 不受影响）。

- [ ] **Step 6: Commit**

```bash
git add src/surge_gw/providers.py tests/test_providers.py
git commit -m "Preserve wildcard domains in domain rule-providers via DOMAIN-WILDCARD"
```

---

### Task 2: assemble 集成验证（含通配符 provider 引用行保持 RULE-SET）

**Files:**
- Test: `tests/test_assemble.py`（新增一例；**不修改任何生产代码**）

**Interfaces:**
- Consumes: `build_config_and_rulesets(...)`、`RulesetUrls`（已存在）；Task 1 后的 `convert_domain_provider` 对含通配符 provider 产出 `kind=="RULE-SET"`。
- Produces: 无新接口（仅验证 invariant：`art.kind` 驱动反填，含通配符 provider 引用行不被改写为 DOMAIN-SET）。

- [ ] **Step 1: 写集成测试**

在 `tests/test_assemble.py` 末尾追加：

```python
def test_assemble_keeps_rule_set_for_wildcard_domain_provider():
    # 含通配符的 domain provider → 整表 RULE-SET(DOMAIN-WILDCARD 行);
    # 反填由 art.kind 驱动,故引用行保持 RULE-SET,不被改写为 DOMAIN-SET。
    up = {**UP, "rules": ["RULE-SET,cnlist,Proxy", "MATCH,Proxy"],
          "rule-providers": {"cnlist": {"behavior": "domain", "format": "text", "url": "http://h/cn.txt"}}}
    def fetch_rs(url):
        return "+.cn\nobjectstorage.*.oraclecloud.com\n"
    b = build_config_and_rulesets(
        upstream=up, node_port_map={"A": 1200}, provider_members={}, urls=URLS,
        host="127.0.0.1", fetch_ruleset_content=fetch_rs, geosite_dat=None, update_interval=3600)
    assert "RULE-SET,http://127.0.0.1:8080/ruleset/cnlist,Proxy" in b.surge_text
    assert "DOMAIN-SET,http://127.0.0.1:8080/ruleset/cnlist" not in b.surge_text
    assert b.rulesets["cnlist"] == "DOMAIN-SUFFIX,cn\nDOMAIN-WILDCARD,objectstorage.*.oraclecloud.com\n"
```

- [ ] **Step 2: 跑集成测试确认通过**

Run: `python3 -m pytest tests/test_assemble.py::test_assemble_keeps_rule_set_for_wildcard_domain_provider -v`
Expected: PASS。此为集成验证：Task 1 让 provider 产出 `kind=="RULE-SET"`，`assemble.py` 现有的 `rewrite_ruleset_types` 仅对 `DOMAIN-SET` 类产物反填，故引用行天然保持 RULE-SET。若 FAIL，说明反填未如设计由 kind 驱动，需排查 `assemble.py:98-99` 与 `reconcile.rewrite_ruleset_types`。

- [ ] **Step 3: 跑全量测试套件**

Run: `python3 -m pytest -q`
Expected: PASS（全绿，无回归）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_assemble.py
git commit -m "Verify wildcard domain provider keeps RULE-SET reference"
```

---

## Self-Review

**1. Spec coverage：**
- provider 级决策（含通配符→RULE-SET / 否则 DOMAIN-SET）→ Task 1 `convert_domain_provider`。✓
- 条目映射表（`+.x`/`.x`→DOMAIN-SUFFIX、通配符→DOMAIN-WILDCARD、精确→DOMAIN）→ Task 1 `_domain_entry_to_rule_line` + 测试 `test_domain_provider_rule_set_maps_suffix_and_exact`。✓
- 行合法性校验 invariant（非法条目 skipped，不写非法行）→ Task 1 正则 + `test_domain_provider_rule_set_skips_invalid_lines`。✓
- assemble 无需改动、引用行保持 RULE-SET → Task 2 集成测试。✓
- 纯域名仍 DOMAIN-SET（性能保留）→ Task 1 保留 `test_domain_provider_to_domain_set` + Step 5 回归。✓
- 边界 `+.<含通配符>` 近似（剥离 `+.` 后走 DOMAIN-WILDCARD）→ Task 1 `_domain_entry_to_rule_line` 首分支已覆盖（`entry[2:]`）。✓

**2. Placeholder scan：** 无 TBD/TODO；每个代码步骤含完整代码与确切命令、预期输出。✓

**3. Type consistency：** `convert_domain_provider` 签名跨任务一致；`_domain_entry_to_rule_line` / `_has_wildcard` 名称与用法在 Task 1 内自洽；Task 2 仅消费 Task 1 产出的 `kind` 语义，无新签名。`DOMAIN-WILDCARD` 输出格式在 spec、Task 1 测试、Task 2 测试中一致（`DOMAIN-WILDCARD,<pattern>`，pattern 已剥离前导 `+.`/`.`）。✓
