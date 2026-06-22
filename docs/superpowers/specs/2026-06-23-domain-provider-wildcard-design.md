# 设计：domain rule-provider 通配符域名保留命中

日期：2026-06-23

## 背景与问题

surge-gw 把 Clash 的 `behavior: domain` rule-provider 转成 Surge 的 `DOMAIN-SET` 外部文件
（裸域名 = 精确，前导点 `.x` = 后缀）。但上游订阅的 domain provider 常含**通配符域名**，例如：

- `*.example.com`（前导单段通配）
- `objectstorage.*.oraclecloud.com`、`localhost.*.qq.com`（中段通配）

Surge 的 `DOMAIN-SET` 文件只接受裸域名与前导点后缀两种形式，**不接受通配符**。更关键的是
Surge 对 `DOMAIN-SET`／`RULE-SET` 都采用**严格校验**：文件中只要有一行非法，整份资源即判定失效
（其余所有域名一并失配）。现象就是 Surge 日志里的 `Failed to load external resource ... Invalid line`，
对应"生成的规则无法使用、匹配不到内容"。

当前工作区已有一版临时改动：把含通配符的条目从 DOMAIN-SET 中**剔除**并计入 `skipped`。这能让资源
整体有效，但代价是这些通配符域名**永久匹配不到**——丢失了订阅作者本要表达的分流意图。

本设计的目标：**保留这些通配符域名的命中能力**，而不是丢弃它们。

## 查证结论（Surge 6.6.0）

- Surge 6 起提供 `DOMAIN-WILDCARD` 规则类型；通配符为 simple string match：`*` 匹配任意数量字符
  （**会跨 `.` 段**），`?` 匹配单个字符。这比 Clash 的 `*`（只匹配单段）**略宽**——属于"宁可多覆盖、
  不漏匹配"的安全方向，本设计接受该语义差异，不对 pattern 做单段化改写。
- `RULE-SET` 外部文件可混放多种规则类型（官方："can contain all types of sub-rules"），每行为
  `TYPE,VALUE` 且不含 policy，policy 由引用方 `RULE-SET,<url>,<policy>` 那行统一给定。`DOMAIN-WILDCARD`
  属于合法子规则类型，可写入 RULE-SET 文件。项目内 `geosite.py` 已在 RULE-SET 文件里混放 `DOMAIN-KEYWORD`，
  此为同类先例。
- `RULE-SET` 文件同样是严格校验：单行非法同样会使整份资源失效。因此降级到 RULE-SET **不是免罪金牌**，
  每行仍须保证合法。
- 性能：`DOMAIN-SET` 是为大列表优化的 fast-search 结构，官方称超过约 1000 条才有显著优势；普通 RULE-SET
  逐条 top-down 匹配，效率等同主配置规则。故纯域名表应尽量保留 DOMAIN-SET。

来源：manual.nssurge.com（domain-based / ruleset / understanding-surge）、kb.nssurge.com（release notes，严格校验）。

## 设计

核心思路沿用 `geosite.py` 已确立的惯例——**当一批条目中存在无法用 DOMAIN-SET 表达的形式时，整表降级为
RULE-SET，逐条映射到对应规则类型**。

### provider 级决策

`convert_domain_provider(entries)`：

- 若 entries 中**存在任一通配符条目**（含 `*` 或 `?`）→ 产出 `kind = "RULE-SET"`，逐条按下表映射。
- 否则（纯域名表）→ 维持现状，产出 `kind = "DOMAIN-SET"`（保留大列表性能优化）。

这与"保留方案"一致：只有含通配符的 provider 才整表降级；不含通配符的 provider 行为完全不变。

### 条目映射（RULE-SET 模式）

按以下优先级把单个 Clash 条目转为一行 Surge 规则（无 policy）：

| Clash 条目形态 | Surge 行 | 说明 |
|---|---|---|
| `+.x`（域名及子域） | `DOMAIN-SUFFIX,x` | 剥离 `+.` 前缀 |
| 含 `*`/`?` 的通配符 | `DOMAIN-WILDCARD,<原样 pattern>` | 采用 Surge 原生 `*`/`?` 语义 |
| `.x`（前导点 = 后缀） | `DOMAIN-SUFFIX,x` | 与 DOMAIN-SET 后缀语义等价 |
| `x`（精确域名） | `DOMAIN,x` | |

边界：`+.<含通配符>`（如 `+.*.x`，极罕见）——`+` 的"含本域"语义无法用单条 DOMAIN-WILDCARD 完整表达，
以 `DOMAIN-WILDCARD,<剥离+.后的pattern>` 近似（覆盖子域方向，可能丢失对本域的精确命中）。

### 行合法性校验（核心 invariant）

**无论 DOMAIN-SET 还是 RULE-SET 模式，写出的每一行都必须通过对应的合法性校验；无法生成合法行的条目计入
`skipped`，绝不写入非法行。** 这是保证"不再触发 Surge 整份资源失效"的根本不变量——否则只是把
"DOMAIN-SET 整份失效"换成"RULE-SET 整份失效"。

- DOMAIN / DOMAIN-SUFFIX 的值：合法域名字符集（复用现有域名正则，不含通配符）。
- DOMAIN-WILDCARD 的值：域名字符 + `*` + `?`，**禁止逗号/空格/控制字符**（逗号会破坏 RULE-SET 行的字段切分）。

### 不需要改动的部分

`assemble.py` 由 `art.kind` 驱动反填：仅当 `kind == "DOMAIN-SET"` 时才把引用行从 `RULE-SET` 改写为
`DOMAIN-SET`（`rewrite_ruleset_types`）。含通配符的 provider 产出 `kind == "RULE-SET"`，因此其引用行
天然保持 `RULE-SET,<url>,<policy>`，无需任何改动。`reconcile.py`、`urls.py`、`http_server` 均不变。

## 受影响范围

- `src/surge_gw/providers.py`：改写 `convert_domain_provider` 与相关 helper（新增通配符判定、RULE-SET 模式
  条目映射、放宽的通配符行校验）。改动收敛于此文件。
- `tests/test_providers.py`：改写原"剔除通配符"测试，新增 RULE-SET 模式映射与校验用例。
- `tests/test_assemble.py`：新增一例——含通配符 provider 的引用行在 surge_text 中保持 `RULE-SET`（不被反填为 DOMAIN-SET）。

## 测试计划

1. 纯域名 provider（无通配符）→ `kind == "DOMAIN-SET"`，行为与现状一致（保留 `test_domain_provider_to_domain_set`）。
2. 含通配符 provider → `kind == "RULE-SET"`；`*.example.com` / 中段通配 → `DOMAIN-WILDCARD,...`，
   `+.x` → `DOMAIN-SUFFIX,x`，`.x` → `DOMAIN-SUFFIX,x`，精确 `x` → `DOMAIN,x`。
3. 含通配符 + 含非法字符（逗号/空格）的条目 → 非法条目计入 `skipped`，其余正常映射，输出无非法行。
4. assemble 层：含通配符 provider 的引用行为 `RULE-SET,<url>,<policy>`；纯域名 provider 仍为 `DOMAIN-SET,...`。

## 风险与取舍

- **语义略宽**：Surge `*` 跨段 vs Clash 单段，会多覆盖（如 `objectstorage.*.oraclecloud.com` 也命中
  `objectstorage.a.b.oraclecloud.com`）。已确认接受。
- **性能**：含通配符的 provider 即便大部分是纯域名，也整表逐条匹配，失去 DOMAIN-SET 优化。已确认接受
  （不做拆分，保持"一 provider 一文件一引用行"的简单映射）。
- **罕见组合** `+.<含通配符>` 的近似处理可能丢失对本域的精确命中，影响面极小。

## 生效说明

改动属于配置生成逻辑。运行中的 surge-gw 需重新生成产物（重启 / 触发 reconcile）后，Surge 才会拉取到修正后的
ruleset 并使通配符域名命中。
