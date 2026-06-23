# IP-CIDR/IP-CIDR6 自动补 no-resolve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 转换上游规则时，对 IP-CIDR/IP-CIDR6 自动补 `no-resolve`（若未带），避免为匹配 IP 规则而对域名触发 DNS 解析。

**Architecture:** 两处生成 IP-CIDR 行的地方各补一次：`rules.py` 主配置规则在 `_convert_one` 的 `emit` 里补（靠 `is_subrule=False` 只限定主配置顶层规则）；`providers.py:convert_ipcidr_provider` 在生成 ruleset 文件行时补。逻辑子规则与 classical provider（走 `is_subrule=True`）、GEOIP/IP-ASN 不动。

**Tech Stack:** Python 3，pytest（本环境用 `.venv/bin/python -m pytest`，系统 python3 无 pytest）。

## Global Constraints

- 只补 `IP-CIDR` / `IP-CIDR6`；`GEOIP` / `IP-ASN` 及其它规则类型一字不改。
- 幂等：上游已带 `no-resolve` 的规则不产生重复的 `no-resolve`。
- 只补主配置顶层规则（`is_subrule=False`）与 ipcidr rule-provider 的 ruleset 行；逻辑规则子规则、classical provider 的 IP-CIDR（均走 `is_subrule=True`）保持现状。
- pytest 命令用 `.venv/bin/python -m pytest`（系统 `python3` 无 pytest）。
- 注释与 commit message 不得含任务编号、方案代号、审阅轮次等过程信息；只解释 WHY 与 invariant。

---

### Task 1: IP-CIDR/IP-CIDR6 补 no-resolve

**Files:**
- Modify: `src/surge_gw/rules.py`（新增 `_NO_RESOLVE_TYPES` 常量；`emit` 内补 no-resolve）
- Modify: `src/surge_gw/providers.py`（`convert_ipcidr_provider` 每行加 `,no-resolve`）
- Test: `tests/test_rules.py`、`tests/test_providers.py`

**Interfaces:**
- Consumes: `convert_rules(rules, policy_map, ruleset_url, geosite_url) -> RuleResult`、`convert_rule_body(body) -> str | None`、`convert_ipcidr_provider(entries) -> RulesetArtifact`（均已存在，签名不变）。
- Produces: 无新接口；改变上述函数对 IP-CIDR/IP-CIDR6 的输出（追加 `no-resolve`）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_rules.py` 末尾追加：

```python
def test_ip_cidr_gets_no_resolve_appended():
    # 顶层 IP 段规则未带 no-resolve → 自动补,避免为匹配 IP 规则而对域名触发 DNS 解析
    r = conv(["IP-CIDR,1.2.3.0/24,Proxy", "IP-CIDR6,2001:db8::/32,Proxy"])
    assert r.lines == [
        "IP-CIDR,1.2.3.0/24,Proxy,no-resolve",
        "IP-CIDR6,2001:db8::/32,Proxy,no-resolve",
    ]


def test_geoip_and_ipasn_not_auto_resolved():
    # 只补 IP-CIDR/IP-CIDR6;GEOIP/IP-ASN 不动
    r = conv(["GEOIP,cn,DIRECT", "IP-ASN,4538,DIRECT"])
    assert r.lines == ["GEOIP,cn,DIRECT", "IP-ASN,4538,DIRECT"]


def test_logical_subrule_ip_cidr_not_auto_resolved():
    # 逻辑子规则里的 IP-CIDR(未带 no-resolve)保持现状,不补
    r = conv(["AND,((IP-CIDR,1.2.3.0/24),(NETWORK,udp)),Proxy"])
    assert r.lines == ["AND,((IP-CIDR,1.2.3.0/24),(PROTOCOL,UDP)),Proxy"]


def test_convert_rule_body_ip_cidr_not_auto_resolved():
    # classical provider 体走 convert_rule_body(is_subrule 路径),不补
    from surge_gw.rules import convert_rule_body
    assert convert_rule_body("IP-CIDR,1.2.3.0/24") == "IP-CIDR,1.2.3.0/24"
```

在 `tests/test_providers.py` 中，把现有 `test_ipcidr_provider_to_rule_set` 的断言更新为带 `no-resolve`：

```python
def test_ipcidr_provider_to_rule_set():
    art = convert_ipcidr_provider(["1.2.3.0/24", "2001:db8::/32"])
    assert art.kind == "RULE-SET"
    assert art.lines == ["IP-CIDR,1.2.3.0/24,no-resolve", "IP-CIDR6,2001:db8::/32,no-resolve"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_rules.py tests/test_providers.py -q`
Expected: FAIL —— `test_ip_cidr_gets_no_resolve_appended` 与更新后的 `test_ipcidr_provider_to_rule_set` 断言含 `no-resolve`，现状未补故不匹配。（`test_geoip_and_ipasn_not_auto_resolved`、两个 subrule/body 测试在现状下应已 PASS——它们是回归护栏。）

- [ ] **Step 3: 改 rules.py**

在 `src/surge_gw/rules.py` 的 `_RULE_OPTIONS = {"no-resolve"}` 之后新增常量：

```python
# IP 段规则补 no-resolve 的类型:这两类匹配连接目标 IP,加 no-resolve 可跳过为域名触发的 DNS 解析。
_NO_RESOLVE_TYPES = {"IP-CIDR", "IP-CIDR6"}
```

把 `_convert_one` 内的 `emit` 函数（当前为）：

```python
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
```

替换为：

```python
    def emit(stype: str, spayload: str | None) -> str:
        parts = [stype]
        if spayload is not None:
            parts.append(spayload)
        if not is_subrule and mapped_policy is not None:
            parts.append(mapped_policy)
        rule_options = options
        # IP 段规则补 no-resolve:仅顶层规则(逻辑子规则/classical provider 体走 is_subrule,
        # 保持现状);幂等,已带的不重复。
        if stype in _NO_RESOLVE_TYPES and not is_subrule and "no-resolve" not in options:
            rule_options = [*options, "no-resolve"]
        parts.extend(rule_options)
        if is_subrule:
            parts.append("__NOPOLICY__")
        return ",".join(parts)
```

- [ ] **Step 4: 改 providers.py**

把 `src/surge_gw/providers.py` 的 `convert_ipcidr_provider`（当前为）：

```python
def convert_ipcidr_provider(entries: list[str]) -> RulesetArtifact:
    """IP 段表 → Surge RULE-SET(IP-CIDR / IP-CIDR6 行)。"""
    art = RulesetArtifact(kind="RULE-SET")
    for entry in entries:
        cidr = entry.strip()
        rtype = "IP-CIDR6" if ":" in cidr else "IP-CIDR"
        art.lines.append(f"{rtype},{cidr}")
    return art
```

替换为：

```python
def convert_ipcidr_provider(entries: list[str]) -> RulesetArtifact:
    """IP 段表 → Surge RULE-SET(IP-CIDR / IP-CIDR6 行)。
    每行补 no-resolve:IP 段规则只需匹配连接目标 IP,无需为域名触发 DNS 解析。"""
    art = RulesetArtifact(kind="RULE-SET")
    for entry in entries:
        cidr = entry.strip()
        rtype = "IP-CIDR6" if ":" in cidr else "IP-CIDR"
        art.lines.append(f"{rtype},{cidr},no-resolve")
    return art
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_rules.py tests/test_providers.py -q`
Expected: PASS（含新增/更新用例）。

- [ ] **Step 6: 跑全套确认无回归**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS（全绿）。特别确认 `test_no_resolve_preserved`（已带 no-resolve 不重复）、`test_logical_subrule_preserves_options`（子规则已带的保留）、`test_assemble.py` 的 prepend bypass 规则不受影响。

- [ ] **Step 7: Commit**

```bash
git add src/surge_gw/rules.py src/surge_gw/providers.py tests/test_rules.py tests/test_providers.py
git commit -m "Auto-append no-resolve to IP-CIDR/IP-CIDR6 rules"
```

---

## Self-Review

**1. Spec coverage：**
- 主配置顶层 IP-CIDR/IP-CIDR6 补 → Step 3 `emit` + `test_ip_cidr_gets_no_resolve_appended`。✓
- ipcidr provider ruleset 行补 → Step 4 + 更新的 `test_ipcidr_provider_to_rule_set`。✓
- 幂等（已带不重复）→ `emit` 的 `"no-resolve" not in options` 守卫 + 现有 `test_no_resolve_preserved`（全套回归覆盖）。✓
- 逻辑子规则/classical 不补 → `is_subrule` 守卫 + `test_logical_subrule_ip_cidr_not_auto_resolved` / `test_convert_rule_body_ip_cidr_not_auto_resolved`。✓
- GEOIP/IP-ASN 不动 → `_NO_RESOLVE_TYPES` 仅含两类 + `test_geoip_and_ipasn_not_auto_resolved`。✓

**2. Placeholder scan：** 无 TBD/TODO；每个代码步骤含完整 before/after 代码与确切命令、预期输出。✓

**3. Type consistency：** `_NO_RESOLVE_TYPES` 命名在 Step 3 定义与使用一致；`emit` 改动后签名不变；输出格式 `<TYPE>,<value>,<policy>,no-resolve`（主配置）/ `<TYPE>,<cidr>,no-resolve`（ruleset 文件）与测试断言一致。✓
