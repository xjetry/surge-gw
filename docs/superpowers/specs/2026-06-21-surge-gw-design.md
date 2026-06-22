# surge-gw 设计文档

> 本地代理服务:把 mihomo 订阅转换为 Surge 远程订阅,并把每个节点(尤其 Surge 原生不支持的 vless 等)落地为本地 socks5 端口供 Surge 使用。Docker 一站式。

- 日期:2026-06-21
- 状态:设计待评审

---

## 1. 背景与目标

Surge 原生不支持 vless 等协议。本服务在 Docker 内运行,充当 **Surge 的远程订阅中转/转换层**:

1. 拉取一个远程 mihomo(Clash.Meta)订阅;
2. 用 mihomo 内核把**每个节点**落地为一个本地 socks5 端口(`1200–1299`,最多 100 个);
3. 生成一份 Surge 配置,所有节点写成 `socks5, 127.0.0.1, 12xx`,并完整还原策略组与分流规则;
4. 通过 HTTP 暴露为 Surge 的 `#!MANAGED-CONFIG` 远程订阅。

核心思想:**mihomo 当"协议适配器",Surge 端只需会用 socks5**。100 个节点 ↔ `1200–1299` 共 100 个端口,一一对应。

## 2. 范围与非目标

**范围**
- 单个远程 mihomo 订阅 URL 作为唯一输入。
- 全部节点统一落地为本地 socks5(不区分协议)。
- 还原策略组(select / url-test / fallback)与分流规则(覆盖主流规则类型)。
- 远程资源(proxy-provider、rule-provider、geosite)经代理拉取并转换/自托管。
- 部署目标:与 Surge 同机的 macOS + Docker Desktop。

**非目标(YAGNI)**
- 不支持多订阅合并、本地覆盖文件(后续可加)。
- 不支持负载均衡(load-balance)与链式(relay)策略组。
- 不解码 `mrs` 二进制 rule-provider。
- 不预设 Go 重写;以 Python 实现并长期维护,仅保持模块边界清晰以便**万一**移植。
- 不面向 iOS / 局域网其它机器(`ADVERTISE_HOST` 预留可配,但非当前目标)。

## 3. 关键决策摘要

| 决策点 | 选择 |
|---|---|
| 落地策略 | **全部节点**统一落地为本地 socks5 |
| 输入 | 单个远程订阅 URL |
| 规则转换深度 | 覆盖主流;mrs 跳过;**GEOSITE 解码转换** |
| 部署拓扑 | 与 Surge 同机 Mac,socks host = `127.0.0.1` |
| 语言 | Python(不预设 Go 重写) |
| mihomo 角色 | **纯静态 socks provider**:无分流、无策略选择、无 DNS 劫持 |
| 路由归属 | 分流/选择全部发生在 **Surge**;mihomo 每端口钉死一个节点 |
| 远程规则 | RULE-SET 指向本服务 URL,后台经代理拉取+转换后秒回 |
| 转换时机 | 对 Surge 拉取**异步**:后台流水线刷新 + serve-from-cache |
| GEOSITE 源 | 解码 mihomo 实际使用的 `geosite.dat`(v2ray `GeoSiteList` protobuf) |

## 4. 架构与进程模型

一个容器,Python 控制器为主进程,把 mihomo 当子进程托管。

```
┌─────────────────────── Docker 容器 ─────────────────────────┐
│  Python 控制器 (主进程)                                       │
│   ├─ http_server :8080  /surge /ruleset/<n> /health /refresh │
│   │     └─ 一律秒回缓存(对 Surge 异步)                        │
│   ├─ orchestrator  后台周期流水线 → 原子替换缓存               │
│   └─ REST API 控制 mihomo (127.0.0.1:9090, secret)           │
│  mihomo (子进程) —— 纯静态 socks provider                     │
│   ├─ 无 rules / 无分流 / 无 DNS 劫持 / 无策略选择              │
│   └─ socks :1200..:1299  每端口用 SpecialProxy 钉死一个节点    │
│  /data: runtime.yaml · port-map.json · cache · geosite.dat    │
└──────────────────────────────────────────────────────────────┘
        host 发布: 127.0.0.1:8080 + 127.0.0.1:1200-1299
```

- 控制器⇄mihomo 用 REST API 联动:配置变更走 `PUT /configs`(reload,尽量不断开 Surge 已有连接);失败降级重启。
- proxy-provider 由 mihomo 自行加载(原生能力、可经代理拉取、自带 bootstrap 重试);控制器 `GET /proxies` 读取扁平节点表。

## 5. 组件边界

每块单一职责、接口清晰、可独立测试。

| 模块 | 职责 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|
| `fetcher` | 拉远程订阅;经 socks 拉 rule-provider / geosite.dat | URL、socks 端口 | 原始 YAML / 规则文本 / 二进制 | 网络;(provider 时)活的 socks 端口 |
| `mihomo_cfg_builder` | 生成 mihomo runtime 配置(N 个钉死 listener + 关闭劫持) | 上游配置、节点表、端口映射 | `runtime.yaml`(纯转换) | 无 |
| `mihomo_manager` | 写配置、起/守护 mihomo、reload、健康检查、读 `/proxies` 与 `/providers/proxies` | runtime 配置、API 地址 | 运行中的 mihomo + 节点表/归属 | mihomo 二进制、REST API |
| `port_allocator` | 节点名→端口(`1200-1299`)**稳定**映射,封顶 100 | 节点表、上次映射 | `{node_name: port}` | 持久化 `port-map.json` |
| `converter`(纯函数,核心) | 节点→socks5、组→`[Proxy Group]`、规则→`[Rule]`、RULE-SET 改写 | 上游配置、端口映射、host、自身 URL、规则原文 | Surge 配置文本 + `{ruleset名: surge文本}` | 无 |
| `geosite_decoder` | 解码 `geosite.dat` protobuf,按引用分类展开为 DOMAIN-SET/RULE-SET | `.dat` 字节、引用分类(含 `@attr`) | `{geosite名: surge文本}` | 无 |
| `http_server` | 服务 `/surge`、`/ruleset/<n>`、`/health`、`/refresh`;一律 serve-from-cache | 缓存、orchestrator | HTTP 响应 | cache、orchestrator |
| `orchestrator` | 串起刷新流水线 + 缓存原子替换 + 单飞/防抖 | 各模块 | 缓存的 Surge 产物 | 上面所有 |
| `cache` | `{surge_config_text, ruleset_texts, node_port_map, status}` 的原子快照 + 持久化 | 产物 | 当前快照 | `/data` |

`converter` / `geosite_decoder` 为纯函数,是将来移植 Go 时最值钱、最好搬的部分。

## 6. 数据流

```
后台刷新流水线(周期 + POST /refresh,单飞 + 防抖 MIN_REFRESH_INTERVAL):
   1. fetcher 直连拉远程订阅 YAML
   2. mihomo_cfg_builder 生成 runtime.yaml(无 rules / 无劫持 / 保留节点来源)→ reload
        若有 proxy-provider:先加载 → GET /proxies 拿全量节点 → 补 listener 再 reload
   3. port_allocator 稳定分端口(端口尽量不漂移)
   4. converter(纯函数):
        节点 → socks5,<host>,12xx
        proxy-group → [Proxy Group]   (LB→降级 select;relay→跳过+标注)
        内联规则 → [Rule]
        rule-provider(yaml/text) → fetcher 经 socks 拉原文 → 转 Surge → 备好 /ruleset/<n>
        GEOSITE → geosite_decoder 解码引用分类 → DOMAIN-SET/RULE-SET → 备好 /ruleset/geosite-<cat>
        规则行写 RULE-SET/DOMAIN-SET, http://<host>:<port>/ruleset/<n>?token=...
        mrs / regexp 条目 / 不可映射 → 跳过 + 计数
   5. 构建完整新缓存 → 原子换指针(先保证 listener 在位,再换引用端口的 Surge 配置)
对 Surge(秒回缓存,异步):
   GET /surge / /ruleset/<n> / /health,  POST /refresh
```

## 7. mihomo runtime 配置规格

```yaml
# 不开任何全局入站(无 mixed-port/port/socks-port)→ mihomo 不做全局代理
external-controller: 127.0.0.1:9090
secret: "<随机>"              # API 鉴权,仅容器内
mode: rule
log-level: warning
dns:
  enable: false              # 不劫持/不 fake-ip;destination 域名透传给节点在【出口】解析

# 节点来源(供 mihomo 取节点)
proxies: [ ...上游 inline... ]
proxy-providers: { ...上游原样... }   # mihomo 自己加载,可经代理拉
proxy-groups: [ ...上游原样... ]       # 惰性保留:仅供 provider 的 proxy: 引用,不参与路由
rules:
  - MATCH,DIRECT             # 惰性占位;listener 用 SpecialProxy 钉死后走不到

# 控制器按 GET /proxies 注入,每节点一个
listeners:
  - { name: p1200, type: socks, port: 1200, listen: 0.0.0.0, udp: true, proxy: "<节点名>" }
  - { name: p1201, type: socks, port: 1201, listen: 0.0.0.0, udp: true, proxy: "<节点名>" }
  # ...
```

关键不变量:
- listener 的 `proxy:`(mihomo `SpecialProxy`)把该端口所有流量钉死走该出站、**绕过规则引擎** —— 所以 `rules` 保持空惰、mihomo 真的不分流。(已对 mihomo 源码 `listener/inbound/base.go` 核实。)
- `listen: 0.0.0.0`(容器内)+ host 侧只发布到 `127.0.0.1`,既能被 Docker 发布又不暴露局域网。
- 不开全局入站端口;mihomo 只通过 listeners 提供 socks。

## 8. DNS / fake-ip 处理(头号正确性前提)

Surge 增强模式给客户端返回 fake-ip(默认 `198.18.0.0/15` 一带)。**危险**:若 Surge 把 fake-ip 当目标地址交给 socks5,mihomo 拿假 IP 去连必然失败(无法反查回域名)。

责任划分:

| | 负责 | 约束 |
|---|---|---|
| Surge | 面向客户端 DNS(fake-ip)+ 按域名路由 + 交给对应 socks 端口 | 交给 socks5 时传**域名**,不是 fake-ip |
| mihomo | 把域名透传给节点、在**出口**解析;只解析节点 server 自身域名以连上节点 | **绝不**开 fake-ip/劫持 |

- Surge 对走代理的连接默认把域名透传给上游(socks5 ATYP=domain),fake-ip 在交给代理前被还原回域名 —— 正常路径下 fake-ip 不到达 mihomo。
- mihomo 侧 `dns.enable: false`、无规则、listener 钉死 → 域名透传到节点出口解析,不在容器本地解析、不 fake-ip。
- mihomo 侧**无法**加 fake-ip 段拒绝护栏(钉死 listener 绕过规则引擎)。万一泄漏,单条连接失败、不污染全局;护栏只能在 Surge 侧(`always-real-ip` / fake-ip TTL)。
- **必须最早期冒烟验证**(见 §17):确认 Surge 把域名而非 `198.18.x.x` 交给 socks5。这是整套架构成立的前提。

## 9. 节点筛选与稳定端口映射

- **筛选**:`GET /proxies` 含策略组(Selector/URLTest/Fallback/LoadBalance)与内置项(DIRECT/REJECT/GLOBAL/PASS/COMPATIBLE),只给**具体出站节点**(ss/vmess/vless/trojan/hysteria2/tuic/snell/socks/http/wg 等)建 listener。
- **稳定映射**:以**节点名**为 key(Surge 按名引用节点/组成员),持久化 `port-map.json`。同名节点跨刷新拿同一端口 → reload 不抖动、Surge 选中项不乱跳。
- 新节点取下一空闲端口;消失节点释放端口。
- **封顶 100**:超出不分配、不进 Surge 配置,在 `/health` 列出被丢弃项。
- 取舍:上游**改名** = 视作新节点(换端口)+ 旧名释放。

## 10. 转换规格

### 10.1 节点 → Surge `[Proxy]`

- `名字 = socks5, <host>, <port>`(`host` 默认 `127.0.0.1`)。需 UDP 时附 `udp-relay=true`(配合 mihomo listener `udp: true`)。
- **名字消毒**:Surge 以逗号分隔,节点/组名里的逗号必须剔除/转义;emoji、空格 Surge 可接受。两边用同一套(消毒后)名字。

### 10.2 策略组 → Surge `[Proxy Group]`

| mihomo | Surge | 说明 |
|---|---|---|
| select | `select` | 直接对应 |
| url-test | `url-test`(url/interval/tolerance/timeout) | 直接对应 |
| fallback | `fallback`(url/interval/timeout) | 直接对应 |
| load-balance | 降级 `select`(保留成员,丢 LB 语义)+ 标注 | 非目标,容错 |
| relay(链式) | 跳过 + 标注;被规则引用则该目标降级 | 非目标,容错 |

- 成员展开:组里的 `use:`(provider 引用)、`include-all` 展开成具体节点名(靠 `GET /providers/proxies` 拿节点↔provider 归属);DIRECT/REJECT/其它组名按名映射。
- `filter` / `exclude-filter`:对 `use:`/`include-all` **收集来的成员名**按正则保留/剔除(`re.search` 非锚定,对齐 mihomo Go `MatchString`;需精确则上游自带 `^…$`);**显式 `proxies:` 成员不受影响**(如用作默认的 REJECT/DIRECT 始终保留);非法正则该组降级 `DIRECT` + 计数。
- provider 成员归属须在钉定 reload(摊平 provider 为顶层 proxy 并删除 `proxy-providers`)**之前**抓取,否则 `use:` 引用解析为空。
- 组展开后为空 → 自动补 `DIRECT`,避免非法组。

Surge 组语法示例:
```
Manual = select, A, B, DIRECT
Auto   = url-test, A, B, url=http://www.gstatic.com/generate_204, interval=300, tolerance=50
Back   = fallback, A, B, url=http://www.gstatic.com/generate_204, interval=300
```

### 10.3 规则 → Surge `[Rule]`(已对 Surge 文档核实)

| mihomo | Surge | 状态 |
|---|---|---|
| DOMAIN / DOMAIN-SUFFIX / DOMAIN-KEYWORD | 同名 | ✅ |
| IP-CIDR / IP-CIDR6 | 同名(保留 `no-resolve`) | ✅ |
| GEOIP | `GEOIP`(保留 `no-resolve`) | ✅ |
| IP-ASN | `IP-ASN` | ✅ |
| SRC-IP-CIDR → `SRC-IP` / SRC-PORT → `SRC-PORT` / DST-PORT → `DEST-PORT` | 改名映射 | ✅ |
| PROCESS-NAME | `PROCESS-NAME`(macOS) | ✅ |
| NETWORK,tcp/udp → `PROTOCOL,TCP/UDP` | 映射(值大小写落地核) | ✅ |
| AND / OR / NOT(逻辑) | Surge `AND/OR/NOT`(嵌套括号 `AND,((R1),(R2)),POLICY`) | ✅(内层子规则须可转,否则整条降级跳过) |
| RULE-SET(yaml/text · domain/ipcidr/classical) | `RULE-SET, <self-url>` | ✅ |
| GEOSITE | 解码展开为 `DOMAIN-SET`/`RULE-SET, <self-url>` | ✅(见 §10.5) |
| MATCH / FINAL | `FINAL` | ✅ |
| RULE-SET(**mrs**)/ DOMAIN-REGEX / PROCESS-PATH / DSCP / IN-* | 跳过 + 计数 | ⛔ |

- **目标(policy)映射**:节点名/组名按名对应;`REJECT→REJECT`、`REJECT-DROP→REJECT-DROP`、`DIRECT→DIRECT`、`PASS→`跳过。
- 标"值大小写落地核"的项,实现时对 Surge 文档逐一核;核不过并入"跳过+计数"。

### 10.4 rule-provider(yaml/text)→ 自托管 ruleset

- 经 socks 拉原文 → 按 behavior 转换:
  - `domain`:`+.x`→DOMAIN-SUFFIX、`x`→DOMAIN;**纯域名表优先输出 `DOMAIN-SET`**(Surge 性能优化)。
  - `ipcidr`:→ IP-CIDR/IP-CIDR6。
  - `classical`:逐行按 §10.3 规则映射(去掉 policy)。
- 托管 `/ruleset/<n>`;规则行 `RULE-SET/DOMAIN-SET, <self-url>?token=...`。
- `mrs` 格式:跳过 + 标注。

### 10.5 GEOSITE → 解码 `geosite.dat`

- **源**:上游 `geox-url.geosite`(若指定且为 `.dat`),否则 mihomo 默认 `…/meta-rules-dat/releases/download/latest/geosite.dat`。控制器经 socks 拉取 + 缓存(长 TTL;mihomo 自身不加载 geosite)。
- **格式**:v2ray `GeoSiteList` protobuf。`.dat` 内各分类的 `include:` 已在编译期展平,**无需递归**;仅做类型映射 + `@attr` 过滤。
- **类型映射**(`Domain.Type`):

| .dat 类型 | 含义 | Surge |
|---|---|---|
| `Full`(3) | 精确域名 | `DOMAIN` |
| `Domain`(2, RootDomain) | 域名+子域 | `DOMAIN-SUFFIX` |
| `Plain`(0) | 子串关键字 | `DOMAIN-KEYWORD` |
| `Regex`(1) | 正则 | 跳过 + 计数 |

- 只展开**配置真正引用到**的分类。`GEOSITE,google@cn` → 取 `GOOGLE` 分类按 `cn` 属性过滤。
- 纯 domain/full(无 keyword)→ `DOMAIN-SET`;含 keyword → `RULE-SET`。托管 `/ruleset/geosite-<cat>[-<attr>]`。
- protobuf 用极简手写解码(GeoSiteList 仅三层嵌套),不引重型工具链。

## 11. HTTP 接口

| 方法/路径 | 说明 | 鉴权 |
|---|---|---|
| `GET /surge?token=` | 返回缓存的 `#!MANAGED-CONFIG` Surge 配置文本 | token |
| `GET /ruleset/<n>?token=` | 返回缓存的转换后 ruleset/domainset | token |
| `GET /health` | 节点数 / 端口占用 / 被丢弃节点 / 跳过项报告 / last-success / last-error | 无 |
| `POST /refresh?token=` | 触发一次后台刷新(非阻塞,202,与在途合并) | token |

- 订阅头:`#!MANAGED-CONFIG http://127.0.0.1:8080/surge?token=... interval=3600 strict=false`。
- 冷启动未就绪:`/surge` 回最简合法占位(DIRECT + FINAL),Surge 按 interval 重取;若有持久化 last-good 缓存则直接秒回。

## 12. 刷新策略与缓存

- 后台周期 `REFRESH_INTERVAL`(默认 6h);单飞 + 防抖 `MIN_REFRESH_INTERVAL`(默认 5min)。
- 触发:周期 / `POST /refresh` / `/surge` 取时若超 MIN 非阻塞踢一次。
- 分资源 TTL:订阅每轮;rule-provider 每轮或自带 TTL;`geosite.dat` 默认 24h。
- 缓存原子替换;持久化 last-good 至 `/data`,重启秒回。

## 13. Docker 与部署

- 发布:`-p 127.0.0.1:1200-1299:1200-1299 -p 127.0.0.1:8080:8080`;`:9090` 不发布。
- 100 端口在 Docker Desktop for Mac 偏重但可用;杠杆:调小 `MAX_NODES`。端口映射创建时固定,默认发满 100。
- Volume `/data`:runtime.yaml / port-map.json / 缓存 / geosite.dat。
- 镜像:`python:3-slim` + 固定版本 mihomo 二进制 + 代码;geosite.dat 运行时拉取,不烤进镜像。
- `docker compose` 一键起 + `HEALTHCHECK` 打 `/health`。

## 14. 安全

- **订阅 token**:首次运行随机生成、持久化、打印日志;`/surge`、`/ruleset`、`/refresh` 需 token,`/health` 不需。我们生成的 ruleset URL 也带同一 token。默认开。
- socks 端口仅 `127.0.0.1`,本机任意进程可用(明示取舍,默认不加 socks 鉴权)。
- mihomo external-controller 绑 `127.0.0.1` + secret,不发布。
- 日志不打节点凭据,仅打名字/计数。

## 15. 错误处理与降级

- 上游拉取失败 → 服务 last-good 缓存,`/health` 显示 last-success/last-error;配合 `strict=false`,Surge 无感。
- reload 失败 → 保留正在跑的旧 mihomo,不切到引用未就绪端口的配置,`/health` 标红。
- 某 rule-provider/geosite 拉取失败 → 用其 last-good;从未成功则该规则降级跳过 + 记;不连累整体。
- 无可用节点 bootstrap → 降级尝试直连;再不行服务现有 + 标注。
- 跳过项(mrs / regexp 条目 / 不可映射规则)全部计数,汇总进 `/health`。
- 原子性:构建完整新缓存后再换指针,绝不服务半成品。

## 16. 配置项(env)

| 变量 | 默认 | 说明 |
|---|---|---|
| `SUBSCRIPTION_URL` | (必填) | 远程 mihomo 订阅 URL |
| `ADVERTISE_HOST` | `127.0.0.1` | socks 行与订阅 URL 里的 host |
| `HTTP_PORT` | `8080` | HTTP 服务端口 |
| `PORT_BASE` | `1200` | socks 起始端口 |
| `MAX_NODES` | `100` | 节点/端口上限 |
| `REFRESH_INTERVAL` | `21600` | 后台刷新间隔(秒) |
| `MIN_REFRESH_INTERVAL` | `300` | 最小刷新间隔/防抖(秒) |
| `GEOSITE_TTL` | `86400` | geosite.dat 缓存 TTL(秒) |
| `GEOSITE_URL` | (可选) | 覆盖 geosite.dat 源 |
| `SUBSCRIPTION_TOKEN` | (自动生成) | 订阅鉴权 token |
| `SURGE_UPDATE_INTERVAL` | `3600` | MANAGED-CONFIG 头里的 interval |

## 17. 测试与冒烟验证

落地走 TDD;`converter` / `geosite_decoder` 是纯函数,适合 golden test。

**单元**
- converter:各协议→socks5 行;各组类型;各规则映射;名字消毒;RULE-SET URL 改写;DOMAIN-SET vs RULE-SET 选择;跳过项计数。
- geosite_decoder:小 `.dat` fixture → 类型映射、`@attr` 过滤、纯域名→DOMAIN-SET。
- port_allocator:同名跨刷新同端口、超 100、释放、持久化。

**冒烟(按优先级)**
1. **【头号】fake-ip / 域名透传**:socks shim(或 mihomo 日志)确认 Surge 增强模式下交来的是**域名**而非 `198.18.x.x`。**先于转换器开发**。
2. 端到端:真实订阅 → Surge 订阅 `/surge` → 选节点 → 验证出口 IP 走对节点。
3. reload 是否保连接(或可接受抖动)。
4. provider / geosite 经 socks 拉取链路通。

## 18. 已知风险与未决项

- **fake-ip 透传(头号)**:整套前提;以 §17.1 冒烟验证前置,验证不过需重新评估架构。
- **100 端口发布开销**:Docker Desktop for Mac;若过重则调小 `MAX_NODES`。
- **规则保真**:NETWORK→PROTOCOL 值大小写、逻辑规则内层可转性,落地逐一核文档。
- **GEOSITE 源一致性**:用 `.dat` 与 mihomo 实际匹配高度一致;若上游用非 `.dat`(如 `.metadb`)则回退默认 `.dat` + 标注。
- **reload 是否保连接**:待冒烟确认;不保则刷新瞬间可能断流。

## 19. 参考资料

- mihomo 源码 `listener/inbound/base.go`(`SpecialProxy` = listener `proxy:` 字段)
- Surge:[proxy 策略](https://manual.nssurge.com/policy/proxy.html) · [Profile 总览](https://manual.nssurge.com/overview/configuration.html) · [IP 规则](https://manual.nssurge.com/rule/ip-based.html) · [逻辑规则](https://manual.nssurge.com/rule/logical-rule.html) · [Ruleset](https://manual.nssurge.com/rule/ruleset.html) · [Managed Profile](https://manual.nssurge.com/others/managed-profile.html)
- [MetaCubeX/meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat) · [mihomo geox-url 配置](https://wiki.metacubex.one/en/config/general/)
