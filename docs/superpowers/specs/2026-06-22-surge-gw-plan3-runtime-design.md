# surge-gw Plan 3 运行时层设计(节点流水线 + serve 流水线)

> 本文细化总设计 [`2026-06-21-surge-gw-design.md`](2026-06-21-surge-gw-design.md) 的运行时层。Plan 1(转换核心)与 Plan 2(远程资源转换)已合并到 `main`、全部为无副作用纯函数。Plan 3 首次引入网络 I/O、mihomo 子进程、并发与持久化,把订阅变成本地 socks 端口并通过 HTTP 暴露 Surge 订阅。

- 日期:2026-06-22
- 状态:设计待评审
- 范围:节点流水线 + serve 流水线。**Docker / 部署留给 Plan 4。**

---

## 1. 范围与关键决策

**范围(本计划)**:订阅 fetcher、mihomo runtime 配置生成、mihomo 子进程管理、稳定端口持久化、rule-provider/geosite 经 socks 拉取、刷新 orchestrator、缓存原子替换、HTTP 服务(`/surge` `/ruleset/<n>` `/health` `/refresh`)。终态:`SUBSCRIPTION_URL` → mihomo 落地 socks 端口 → `/surge` 返回完整 `#!MANAGED-CONFIG`、`/ruleset/<n>` 返回转换后 ruleset。

**非目标(留 Plan 4)**:Dockerfile / compose / HEALTHCHECK / 100 端口发布 / 镜像内 mihomo 二进制打包。

**关键决策**:

| 决策点 | 选择 |
|---|---|
| HTTP 服务器 | stdlib `http.server.ThreadingHTTPServer`(零额外运行期依赖) |
| 并发模型 | threading:一个后台刷新线程 + 单飞锁;HTTP 由 ThreadingHTTPServer 多线程处理 |
| 测试策略 | 纯核 TDD;impure 层对**进程内 fake**(假 HTTP origin / 假 socks / 假 mihomo REST / 进程内 HTTP 客户端)测;真实 mihomo 子进程用**手动 smoke 闸门**,不进自动化 |
| mihomo 角色 | 纯静态 socks provider:无分流、无策略、`dns.enable:false`、listener `proxy:` 钉死(绕过规则引擎) |
| reload | `PUT /configs`(reload,尽量保连接);失败降级重启 |
| provider/geosite 拉取 | 经一个活 socks 端口;无可用节点降级直连 |
| 运行期依赖 | 仅 `pyyaml`(已是 Plan 2 运行期依赖);其余全 stdlib |

---

## 2. 模块图(纯 / impure 划分)

新增模块全部在 `src/surge_gw/` 下,复用 Plan 1/2 纯函数(`proxies`/`groups`/`rules`/`surge_config`/`providers`/`geosite`/`reconcile`/`ports`/`naming`/`models`)。

**纯函数(TDD,golden/单元):**

- `mihomo_config.py`
  - `build_listeners(node_names: list[str], port_map: dict[str, int], *, udp: bool = True) -> list[dict]` — 每个有端口的节点一个 socks listener:`{name: "p<port>", type: "socks", port: <port>, listen: "0.0.0.0", udp: True, proxy: "<节点原名>"}`。
  - `build_runtime_config(upstream: dict, listeners: list[dict], *, secret: str, controller: str = "127.0.0.1:9090") -> dict` — 生成 mihomo runtime:`external-controller`/`secret`/`mode: rule`/`log-level: warning`/`dns: {enable: false}`;保留 upstream 的 `proxies`/`proxy-providers`/`proxy-groups`;`rules: ["MATCH,DIRECT"]` 惰性占位;`listeners` 注入;**不开**任何全局入站。
- `nodes.py`
  - `select_outbound_nodes(proxies_resp: dict) -> list[str]` — 从 mihomo `/proxies` 响应里滤掉策略组(Selector/URLTest/Fallback/LoadBalance)与内置项(DIRECT/REJECT/REJECT-DROP/GLOBAL/PASS/COMPATIBLE),只留具体出站节点名(spec §9)。
  - `provider_members(providers_resp: dict) -> dict[str, list[str]]` — 从 `/providers/proxies` 取 provider→成员名,供 Plan 1 `convert_groups` 的 `provider_members`。
- `urls.py`
  - `RulesetUrls(host: str, port: int, token: str)`,方法 `ruleset(name) -> str`、`geosite(cat) -> str`、`managed() -> str`、`endpoint_name(name) / endpoint_name_geosite(cat)`(自托管路径与 token 一致拼接)。喂给 Plan 1 `convert_rules` 的 `ruleset_url`/`geosite_url` 回调,并供 Plan 2 `reconcile` 拼 `domain_set_urls`。
- `refresh_policy.py`
  - `should_refresh(now: float, last_started: float | None, in_flight: bool, min_interval: float) -> bool` — 单飞 + 防抖判定(纯)。
  - `Snapshot` dataclass `{surge_text: str, rulesets: dict[str, str], node_port_map: dict[str, int], skipped: list[SkippedItem], dropped: list[str], last_success: float | None, last_error: str | None}`;`assemble_snapshot(...) -> Snapshot`(纯组装,不写盘)。
  - `placeholder_surge(managed_url, update_interval) -> str` — 冷启动占位(`DIRECT`+`FINAL`,复用 `surge_config.build_surge_config`)。

**impure(进程内 fake + 手动 smoke):**

- `fetcher.py` — `fetch_text(url: str, *, timeout: float) -> str`(urllib 直连);`fetch_via_socks(url: str, socks_port: int, *, timeout: float) -> bytes`(经本地 socks5 CONNECT 后发 HTTP)。
- `mihomo_manager.py` — `MihomoManager(bin_path, config_path, controller, secret)`:`start()`/`stop()`/`ensure_alive()`(Popen + 看护重启);REST:`reload(config: dict)`(`PUT /configs?force=true` 写文件后触发)、`get_proxies() -> dict`、`get_providers_proxies() -> dict`、`healthy() -> bool`(`GET /version`)。
- `port_store.py` — `load(path) -> dict[str, int]` / `save(path, mapping)`(原子写 `port-map.json`);包住 `ports.allocate` 的持久化前后读写。
- `cache.py` — `Cache`:持有当前 `Snapshot` 的原子引用(`get()`/`swap(snapshot)`);`persist(snapshot, data_dir)` 原子落 last-good;`load_last_good(data_dir) -> Snapshot | None`。
- `orchestrator.py` — `Orchestrator`,依赖注入 fetcher / manager / port_store / cache / config / 转换函数。`refresh_once()` 跑完整流水线(单飞 + 防抖 + 原子换指针);`start_background()` 起周期线程;`request_refresh()` 非阻塞踢一次(与在途合并)。
- `http_server.py` — `build_server(cache, orchestrator, config) -> ThreadingHTTPServer`;handler 路由 `/surge` `/ruleset/<n>` `/health` `/refresh`;token 鉴权(`/health` 免);一律秒回缓存。
- `config.py` — `Config.from_env(env: Mapping[str, str]) -> Config`(纯解析 + 校验 + token 自举判定);见 §5。
- `__main__.py` — 入口:读 config → 起 mihomo manager → 起 orchestrator 后台线程 → 起 HTTP server(阻塞)。

依赖方向:`orchestrator` → 上述 impure + Plan 1/2 纯函数;`__main__` → 全部;无环。

---

## 3. 数据流与编排

后台刷新流水线(触发:周期 `REFRESH_INTERVAL` / `POST /refresh` / `/surge` 取时超 `MIN_REFRESH_INTERVAL` 踢一次;单飞 + 防抖):

1. `fetch_text(SUBSCRIPTION_URL)` 直连拉订阅 YAML → `yaml.safe_load`。
2. `build_runtime_config`(此时 listeners 可为空)→ `manager.reload`;若 upstream 含 `proxy-providers`:reload 让 mihomo 加载 → `get_proxies` → `select_outbound_nodes` → `build_listeners` 补 listener → 再 `reload`。
3. `port_store.load` → `ports.allocate`(同名保端口、超 `MAX_NODES` 丢弃)→ `port_store.save`。
4. 转换(复用 Plan 1/2):
   - `naming.build_name_map` → `proxies.build_proxy_section` → `groups.convert_groups`(`provider_members` 来自 `/providers/proxies`)→ `rules.convert_rules`(`ruleset_url`/`geosite_url` 用 `urls.RulesetUrls`)。
   - 对 `RuleResult.rule_providers`:`fetch_via_socks` 经活 socks 拉原文 → `providers.extract_provider_entries` + `convert_*_provider` → `RulesetArtifact`。
   - 对 `RuleResult.geosites`:取 geosite.dat(`GEOSITE_URL` 或默认源,经 socks 拉 + 长 TTL 缓存)→ `geosite.decode_geosite_dat` + `split_geosite_ref` + `build_geosite_artifact`。
   - 据各 artifact 的 `kind` 收集 `domain_set_urls` → `reconcile.rewrite_ruleset_types` 反填规则行 → `surge_config.build_surge_config` 组装。
5. `assemble_snapshot` 建完整新缓存 → `cache.swap` **原子换指针**(先保证 listener 在位再换引用端口的 Surge 配置)→ `cache.persist` 落 last-good。

对 Surge:`/surge`/`/ruleset/<n>`/`/health` 秒回缓存;`POST /refresh` 非阻塞 202、与在途合并。冷启动未就绪:有持久化 last-good 秒回,否则 `/surge` 回 `placeholder_surge`(`DIRECT`+`FINAL`),Surge 按 interval 重取。

---

## 4. 测试策略

- **纯核 TDD**:`build_listeners`/`build_runtime_config`/`select_outbound_nodes`/`provider_members`/`urls`/`should_refresh`/`assemble_snapshot`/`placeholder_surge` 全部单元/golden 测试。
- **进程内 fake(hermetic、快、无外部依赖)**:
  - 假 HTTP origin(stdlib `ThreadingHTTPServer` 起在临时端口)测 `fetch_text`。
  - 假 socks5(转发到假 origin)测 `fetch_via_socks`。
  - 假 mihomo REST(stdlib HTTP server 模拟 `/version` `/configs` `/proxies` `/providers/proxies`)测 `mihomo_manager` REST 客户端;子进程看护用一个假长驻进程(`python -c "import time;time.sleep(...)"`)测重启逻辑。
  - `http_server` 用进程内 urllib 客户端打真实端点(临时端口),验 token 鉴权、秒回缓存、占位回退、404。
  - `orchestrator.refresh_once` 注入以上全部 fake,验流水线顺序、单飞防抖、原子换指针、失败降级 last-good、provider 拉取失败该规则降级。
  - `port_store`/`cache` 持久化用临时目录测原子写与 last-good 回读。
- **手动 mihomo 闸门(不进自动化,像 Plan 1 fake-ip 闸门)**:真实 `mihomo` 二进制 + 真订阅,`curl` 穿某 socks 端口验出口 IP 走对节点;并观察 reload 是否保连接(spec §17.2/§18)。

---

## 5. 配置、持久化、安全

**env**(spec §16 + 本计划新增):

| 变量 | 默认 | 说明 |
|---|---|---|
| `SUBSCRIPTION_URL` | (必填) | 远程 mihomo 订阅 |
| `ADVERTISE_HOST` | `127.0.0.1` | socks 行与订阅/ruleset URL 的 host |
| `HTTP_PORT` | `8080` | HTTP 服务端口 |
| `PORT_BASE` | `1200` | socks 起始端口 |
| `MAX_NODES` | `100` | 节点/端口上限 |
| `REFRESH_INTERVAL` | `21600` | 后台刷新间隔(秒) |
| `MIN_REFRESH_INTERVAL` | `300` | 最小刷新间隔/防抖(秒) |
| `GEOSITE_TTL` | `86400` | geosite.dat 缓存 TTL(秒) |
| `GEOSITE_URL` | (可选) | 覆盖 geosite.dat 源 |
| `SUBSCRIPTION_TOKEN` | (自动生成) | 订阅鉴权 token |
| `SURGE_UPDATE_INTERVAL` | `3600` | MANAGED-CONFIG 头 interval |
| `MIHOMO_BIN` | `mihomo` | mihomo 二进制(PATH 上;Plan 4 镜像内固定路径) |
| `DATA_DIR` | `./data` | 持久化目录(Plan 4 Docker 映射为 `/data`) |

**`DATA_DIR` 布局**:`runtime.yaml`、`port-map.json`、`token`、`cache/`(last-good surge + rulesets)、`geosite.dat`。

**token**:首次随机生成、持久化到 `DATA_DIR/token`、打日志;`/surge`/`/ruleset`/`/refresh` 需 token,`/health` 不需;自托管 ruleset URL 带同一 token。**mihomo external-controller** 绑 `127.0.0.1:9090` + 随机 secret。日志不打凭据,只打名字/计数。

---

## 6. 错误处理与降级(spec §15)

- 上游订阅拉取失败 → 服务 last-good,`/health` 显示 last-success/last-error;配合 `strict=false` Surge 无感。
- reload 失败 → 保留正在跑的旧 mihomo,不切到引用未就绪端口的配置,`/health` 标红;必要时降级重启。
- 某 rule-provider/geosite 拉取失败 → 用其 last-good;从未成功则该规则降级跳过 + 记 `SkippedItem`;不连累整体。
- 无可用节点 bootstrap → provider 拉取降级直连;再不行服务现有 + 标注。
- 跳过项(mrs/regexp/不可映射,来自 Plan 1/2 `SkippedItem` + 端口溢出 `dropped`)汇总进 `/health`。
- 原子性:构建完整新缓存后再换指针,绝不服务半成品。

---

## 7. 留给 Plan 4

Dockerfile(`python:3-slim` + 固定版本 mihomo 二进制 + 代码)、`docker compose`(`-p 127.0.0.1:1200-1299` + `-p 127.0.0.1:8080`、`:9090` 不发布、`/data` volume、`HEALTHCHECK` 打 `/health`)、100 端口发布开销评估与 `MAX_NODES` 调参、端到端 smoke(真订阅 → Surge 取 `/surge` → 选节点 → 验出口)。
