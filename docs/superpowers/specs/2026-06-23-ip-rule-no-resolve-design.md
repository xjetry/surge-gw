# 设计：上游 IP-CIDR/IP-CIDR6 自动补 no-resolve

日期：2026-06-23

## 背景

surge-gw 把上游 Clash 规则透传为 Surge 规则。IP 类规则（IP-CIDR/IP-CIDR6/GEOIP/IP-ASN）在目标是域名时会触发本地 DNS 解析以获得 IP 来匹配；`no-resolve` 选项跳过该解析（只对本身已是 IP 的连接匹配），是 IP 规则的常见最佳实践——减少不必要的 DNS 解析、避免过早 DNS 泄漏与延迟。

当前 surge-gw 对上游 IP-CIDR 规则**原样透传**：上游写了 `no-resolve` 就保留、没写就没有。于是生成配置里 IP-CIDR 是否带 `no-resolve` 取决于订阅作者，不统一（例：上游 `IP-CIDR,110.42.42.172/32,DIRECT` 未带，生成的也未带）。

注：surge-gw 自产的 bypass 规则（节点出口 IP 钉 DIRECT，`bypass.py`）已固定带 `no-resolve`，不在本设计范围。

## 目标

转换时对 **IP-CIDR / IP-CIDR6** 自动补 `no-resolve`（若未带）。GEOIP/IP-ASN 不动。幂等：上游已带的不重复加。

## 范围

**补：**
- 主配置 `[Rule]` 段的顶层 IP-CIDR/IP-CIDR6（`rules.py`，`is_subrule=False` 路径）→ `IP-CIDR,x,POLICY,no-resolve`。
- ipcidr behavior rule-provider 生成的 ruleset 文件行（`providers.py` `convert_ipcidr_provider`）→ `IP-CIDR,x,no-resolve` / `IP-CIDR6,x,no-resolve`。

**不补（保持现状）：**
- 逻辑规则（AND/OR/NOT）里的 IP-CIDR 子规则、classical rule-provider 里的 IP-CIDR。二者都经 `convert_rule_body` → `_convert_one` 的 `is_subrule=True` 路径。理由：逻辑子规则带 `no-resolve` 的语法 Surge 未明确支持，贸然补可能产生非法行拖垮整条逻辑规则/整份 ruleset；且这两类罕见。安全优先 + YAGNI。
- GEOIP / IP-ASN：用户明确不补。

## 设计

**`rules.py`**：在 `_convert_one` 的 `emit` 中，当 `stype in {"IP-CIDR","IP-CIDR6"}` 且 `not is_subrule` 且现有 options 不含 `no-resolve` 时，向 options 追加 `no-resolve`。`is_subrule` 标志天然把"主配置顶层规则"与"子规则/classical provider"区分开，故只补到范围内的主配置规则。

**`providers.py`**：`convert_ipcidr_provider` 生成每行时追加 `,no-resolve`。该函数本就不带 options，且 ipcidr provider 内容恒为纯 IP 段（输出恒为 `IP-CIDR`/`IP-CIDR6`），直接追加即可。

## 不变量

- 幂等：已带 `no-resolve` 的规则不产生重复的 `no-resolve`。
- 只影响 IP-CIDR/IP-CIDR6；GEOIP/IP-ASN 及其它规则类型一字不改。
- 子规则路径（逻辑规则、classical provider）行为零变化。

## 受影响范围

- `src/surge_gw/rules.py`（`emit` 补 `no-resolve`）
- `src/surge_gw/providers.py`（`convert_ipcidr_provider` 加 `no-resolve`）
- `tests/test_rules.py`、`tests/test_providers.py`（新增/更新用例）

## 测试计划

- 主配置 `IP-CIDR,x,DIRECT`（无 no-resolve）→ `IP-CIDR,x,DIRECT,no-resolve`。
- 主配置 `IP-CIDR,x,DIRECT,no-resolve`（已带）→ 不重复（仍单个 no-resolve）。
- 主配置 IP-CIDR6 同理补。
- `GEOIP,cn,DIRECT` → 不补（保持）。
- 逻辑规则 `AND,((IP-CIDR,x),(DOMAIN,y)),POLICY` 的 IP-CIDR 子规则 → 不补。
- classical provider 含 IP-CIDR → ruleset 行不补（保持现状，经 `convert_rule_body`）。
- ipcidr provider → ruleset 行为 `IP-CIDR,x,no-resolve` / `IP-CIDR6,x,no-resolve`。

## 生效

改完 `docker compose up -d --build` 重建容器；验证主配置 `[Rule]` 段 IP-CIDR 带 `no-resolve`，且 Surge 日志无 ruleset 解析错误（确认 ruleset 文件内 `no-resolve` 被 Surge 接受）。
