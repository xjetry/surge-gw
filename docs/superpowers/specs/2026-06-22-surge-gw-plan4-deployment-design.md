# surge-gw Plan 4 部署层设计(单容器:Dockerfile + compose + HEALTHCHECK)

> 本文细化总设计 [`2026-06-21-surge-gw-design.md`](2026-06-21-surge-gw-design.md) 的「§13 Docker 与部署」。Plan 1(转换核心)、Plan 2(远程资源)、Plan 3(运行时层)已合并到 `main`,`SUBSCRIPTION_URL` → mihomo 落地 socks 端口 → HTTP 暴露 `#!MANAGED-CONFIG` + 自托管 ruleset 的整条链路已真实 mihomo smoke 验证。Plan 4 把现有 Python 控制器 + mihomo 打成**单容器一键起**。

- 日期:2026-06-22
- 状态:设计待评审
- 范围:Dockerfile + docker-compose + HEALTHCHECK + 部署逼出的代码 delta + 容器内 smoke。

---

## 1. 范围与关键决策

**范围(本计划)**:把运行时层打成单容器(Python 控制器为 PID 1 主进程、mihomo 为受托子进程),`docker compose` 一键起。产物 = `Dockerfile`、`docker-compose.yml`、`.dockerignore`、两处部署驱动的代码 delta、两个 minor 硬化、容器内 smoke 清单文档。终态:`docker compose up` 后宿主机 `127.0.0.1:8080/surge` 返回完整 Surge 订阅、`127.0.0.1:12xx` 穿出走对应节点、`HEALTHCHECK` 反映就绪。

**非目标(YAGNI)**:Kubernetes / 多副本 / 自动推送镜像的 CI / 局域网暴露 / 把 `geosite.dat` 烤进镜像 / Windows 或 Linux 宿主调优(部署目标是与 Surge 同机的 macOS + Docker Desktop)。

**关键决策**:

| 决策点 | 选择 | 理由 |
|---|---|---|
| 进程模型 | Python 控制器 = PID 1 主进程,mihomo = 受托子进程 | 沿用设计 §4;单容器、日志合流(mihomo 继承父进程 stdout/stderr) |
| 容器内绑定 vs 对外宣告 | **拆成两个独立 knob**:`HTTP_BIND`(容器内绑定,默认 `127.0.0.1`)与 `ADVERTISE_HOST`(URL/socks host,保持 `127.0.0.1`) | Docker 端口转发的连接来自 bridge 网关、非容器内回环;HTTP server 必须 bind `0.0.0.0` 才收得到,而宣告地址必须是宿主机回环 |
| 退出处理 | PID 1 装 SIGTERM/SIGINT handler 优雅退出 + compose `init: true` 兜底回收僵尸 | `docker stop` 秒退而非等 SIGKILL;mihomo 子进程不被孤儿化 |
| 基础镜像 | `python:3.13-slim` | 稳定;`pyyaml` 有 linux wheel 免编译;代码只要 ≥3.12 |
| mihomo 二进制 | **固定版本 1.19.27**,多阶段按 `TARGETARCH` 拉取 + sha256 校验,放 `/usr/local/bin/mihomo` | smoke 已验证该版本;校验防供应链篡改;不烤 geosite.dat |
| 端口发布 | 只发布到宿主机 `127.0.0.1`:`1200-1299` + `8080`;`9090` 不发布 | 设计 §13/§140:容器内 listen `0.0.0.0`,host 侧不暴露局域网;external-controller 仅容器内 |
| `/data` 持久化 | **命名 volume** | 避开 host bind-mount 的 uid 不匹配(非 root 用户要可写);token 持久化 → 订阅 URL 重启不变 |
| HEALTHCHECK | 就绪式:`/health` 返回 200 **且 `nodes>0`** 才算健康,配 `start_period` 宽限冷启动 | 真正反映「能出货」;`start_period` 防首刷未完成时假 unhealthy |
| 镜像探活实现 | `python -c` 单行(不装 curl) | slim 无 curl;复用容器自带解释器,镜像不膨胀 |
| 运行期依赖 | 仍仅 `pyyaml` | 不因部署引入新运行期依赖 |

---

## 2. 部署拓扑与进程模型

```
┌──────────────────── 容器(image: surge-gw) ────────────────────┐
│ PID 1: python -m surge_gw  (控制器)                            │
│   ├─ http_server   bind HTTP_BIND=0.0.0.0 : 8080              │
│   ├─ orchestrator  后台刷新线程 → 原子换缓存                    │
│   ├─ mihomo (子进程)  external-controller 127.0.0.1:9090       │
│   │     socks listeners 0.0.0.0 : 1200..1299  每端口钉一节点    │
│   └─ SIGTERM/SIGINT → 停后台线程 + 终止 mihomo + server 关停     │
│ /data (命名 volume): token · runtime.yaml · port-map.json ·    │
│                      cache · provider 摊平缓存 · geosite.dat    │
└───────────────────────────────────────────────────────────────┘
   host 发布(仅 127.0.0.1): 8080 + 1200-1299    (9090 不发布)
```

- 控制器与 mihomo 经 `127.0.0.1:9090` REST 联动,**该端口不发布**,仅容器内可达。
- mihomo 的 socks listeners 与 HTTP server 都 bind `0.0.0.0`(容器内),靠 Docker 只把它们发布到宿主机 `127.0.0.1`,既可达又不暴露局域网。

---

## 3. 代码 delta(必须):`HTTP_BIND` 与 `ADVERTISE_HOST` 拆分

**问题(部署逼出)**:当前 `http_server.build_server` 用 `(config.advertise_host, config.http_port)` 做容器内绑定,而 `ADVERTISE_HOST` 同时又是写进 Surge 配置的 `#!MANAGED-CONFIG` URL、`/ruleset` URL、`socks5,<host>,12xx` 里的 host(Surge 在宿主机连的地址)。这两件事在 Docker 下互斥:

- Surge 连的是**宿主机 `127.0.0.1`** → 宣告地址必须是 `127.0.0.1`。
- Docker `-p 127.0.0.1:8080:8080` 把宿主机连接 NAT 进容器,**源地址是 bridge 网关(非容器内 `127.0.0.1`)** → HTTP server 必须 bind `0.0.0.0` 才收得到。

单个 `ADVERTISE_HOST` 无法既当 `127.0.0.1`(宣告)又当 `0.0.0.0`(绑定)。mihomo listener 早已 bind `0.0.0.0`(对的),唯独 HTTP server 漏了。

**改动**:

- `config.py`:`Config` 增 `http_bind: str`;`from_env` 读 env `HTTP_BIND`,**默认 `127.0.0.1`**。默认保持回环,使本地裸跑行为不变、不意外暴露;容器内由 compose 显式设 `HTTP_BIND=0.0.0.0`。
- `http_server.build_server`:绑定改 `(config.http_bind, config.http_port)`。
- `ADVERTISE_HOST` 语义收窄为**纯宣告**:只进 `RulesetUrls`(`#!MANAGED-CONFIG` + `/ruleset` URL)与 `socks5,<host>,12xx`(`assemble` 的 `host`),保持默认 `127.0.0.1`,不再参与任何 socket 绑定。

**不变量**:容器内所有入站(HTTP + socks)bind `0.0.0.0`;所有写进 Surge 配置、供 Surge 在宿主机回连的 host 一律是 `ADVERTISE_HOST`(`127.0.0.1`)。绑定地址与宣告地址自此永不共用一个变量。

**测试(TDD)**:
- `from_env` 解析 `HTTP_BIND`;缺省回落 `127.0.0.1`;空串回落默认。
- `build_server` 的 `server_address` 取 `http_bind` 而非 `advertise_host`(给二者不同值,断言绑定取前者)。
- 既有 `ADVERTISE_HOST` 影响 URL/socks host 的测试保持绿(回归:拆分不改宣告路径)。

---

## 4. 代码 delta(必须):PID 1 优雅退出

**问题**:`main()` 现无信号处理器、`server.serve_forever()` 死等。作为 PID 1,SIGTERM 默认不终止进程,`docker stop` 要等 grace period 后 SIGKILL;且 mihomo 子进程可能被孤儿化。

**改动**:`main()` 重构为可注入、可测的关停流程:

- HTTP server 跑后台线程;主线程装 `SIGTERM`/`SIGINT` handler。
- 收到信号 → 触发一个 `threading.Event` → 依次 `orchestrator.stop()`(停后台刷新线程)、`manager.stop()`(`terminate` mihomo,超时 `kill`,已实现)、`server.shutdown()`。
- 把关停逻辑抽成独立函数(注入 server / orchestrator / manager / stop-event),便于进程内测试,不必真起子进程或真发信号。

**纵深防御**:compose 设 `init: true`,用最小 init 兜底回收任何意外僵尸子进程,与 Python 侧的优雅终止互补(Python 负责「干净停 mihomo」,init 负责「兜底 reap」)。

**测试(TDD)**:触发 stop-event 后断言 `orchestrator.stop` / `manager.stop` / `server.shutdown` 三者都被调用(用 fake/spy);不真起进程、不真发信号。

---

## 5. minor 硬化(顺带)

两项 Plan 3 记录的遗留,在部署暴露前一并清掉:

- **token 常数时间比较**:`http_server` 的 `authed` 当前用明文 `==` 比 token(非常数时间)。改 `hmac.compare_digest`,消除时序侧信道。需处理 `token is None`(未启用鉴权)与请求缺 `token` 参数两种边界,保持现有放行/拒绝语义不变。
- **无节点直连降级分支测试**:`orchestrator._fetch_ruleset` 在无可用节点(`socks_port=None`)时走 `fetcher.fetch_text` 直连,该分支当前未测。补一个能区分「订阅 URL」与「ruleset URL」的 fake,使直连降级路径被覆盖(现有 `FakeFetcher.fetch_text` 对任意 url 都回订阅体,无法区分,需增强)。

两项均纯代码 + 单测,不涉及容器。

---

## 6. Dockerfile 规格

多阶段:builder 拉取并校验 mihomo,final 只带运行所需。

```dockerfile
# syntax=docker/dockerfile:1.7

# --- mihomo 二进制:固定版本 + sha256 校验,按目标架构选取 ---
FROM debian:bookworm-slim AS mihomo
ARG TARGETARCH                       # buildx 注入:amd64 / arm64
ARG MIHOMO_VERSION=v1.19.27
ARG MIHOMO_SHA256_amd64=<pinned>     # 取自该 release 的发布校验和
ARG MIHOMO_SHA256_arm64=<pinned>
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && asset="mihomo-linux-${TARGETARCH}-${MIHOMO_VERSION}.gz" \
 && url="https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/${asset}" \
 && curl -fsSL "$url" -o /tmp/mihomo.gz \
 && case "$TARGETARCH" in \
      amd64) echo "${MIHOMO_SHA256_amd64}  /tmp/mihomo.gz" | sha256sum -c - ;; \
      arm64) echo "${MIHOMO_SHA256_arm64}  /tmp/mihomo.gz" | sha256sum -c - ;; \
      *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && gunzip -c /tmp/mihomo.gz > /usr/local/bin/mihomo \
 && chmod +x /usr/local/bin/mihomo

# --- 运行镜像 ---
FROM python:3.13-slim
COPY --from=mihomo /usr/local/bin/mihomo /usr/local/bin/mihomo
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .          # 带 pyyaml(linux wheel,免编译)
RUN useradd --system --uid 10001 --home-dir /data --no-create-home surgegw \
 && mkdir -p /data && chown surgegw:surgegw /data
USER surgegw
ENV DATA_DIR=/data MIHOMO_BIN=/usr/local/bin/mihomo HTTP_BIND=0.0.0.0
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python","-c","import json,urllib.request,sys; d=json.load(urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=4)); sys.exit(0 if d.get('nodes',0)>0 else 1)"]
ENTRYPOINT ["python","-m","surge_gw"]
```

要点:
- **校验和实现期填入**:`<pinned>` 取自 1.19.27 release 的发布校验和,实现时填真值;CI/构建不可绕过校验。
- **非 root**:监听端口均 >1024,无需 root;`/data` 在镜像内 chown 给该用户,配合命名 volume 可写。
- **geosite.dat 不进镜像**:运行时按 `GEOSITE_URL` 拉(可选),保持镜像精简、规则数据可独立更新。
- **日志**:沿用 `mihomo_manager` 现状(子进程继承父进程 stdout/stderr),容器内日志合流,`docker logs` 一处看全。

---

## 7. docker-compose.yml 规格

```yaml
services:
  surge-gw:
    build: .
    image: surge-gw
    init: true                       # 兜底回收僵尸子进程
    restart: unless-stopped
    environment:
      SUBSCRIPTION_URL: ${SUBSCRIPTION_URL:?set in .env}
      HTTP_BIND: 0.0.0.0             # 容器内绑定;宿主仅发布到 127.0.0.1
      ADVERTISE_HOST: 127.0.0.1     # 写进 Surge 配置、供宿主机回连
      DATA_DIR: /data
      # 可选:MAX_NODES / GEOSITE_URL / SUBSCRIPTION_TOKEN / REFRESH_INTERVAL ...
    ports:
      - "127.0.0.1:8080:8080"
      - "127.0.0.1:1200-1299:1200-1299"   # 见 §12 的 MAX_NODES 取舍
    volumes:
      - surge-gw-data:/data
volumes:
  surge-gw-data:
```

要点:
- `SUBSCRIPTION_URL` 从 `.env` 注入(必填,缺失即报错)。提供 `.env.example` 模板。
- HEALTHCHECK 在 Dockerfile 定义,compose 不重复(避免两处漂移);如需调 `start_period` 在 compose 覆盖。
- `9090` 不出现在 `ports`,external-controller 仅容器内可达。

---

## 8. HEALTHCHECK 语义

- **健康 = `/health` 返回 200 且 `nodes>0`**(真正「能出货」);`nodes==0`(完全无出口,如订阅全挂)判 unhealthy。
- `--start-period=120s`:冷启动首刷拉订阅 + reload mihomo + 拉 provider/ruleset 需时间,宽限期内失败不计入 `--retries`,避免首刷未完成被误判 unhealthy → 误重启。
- `--interval=30s --timeout=5s --retries=3`:稳态每 30s 探一次,连续 3 次失败才 unhealthy。
- 探活脚本 `python -c` 连容器内 `127.0.0.1:8080`(server bind `0.0.0.0` 同时接受回环),不依赖 curl。
- 取舍记录:就绪式探活意味着上游订阅长期全挂时容器会被标 unhealthy,配合 `restart` 可能反复重启;此为有意选择(宁可显式暴露「无出口」也不假绿)。

---

## 9. config 增补与 env 参考

新增一个配置项,其余沿用总设计 §16:

| 变量 | 默认 | 说明 |
|---|---|---|
| `HTTP_BIND` | `127.0.0.1` | HTTP server 容器内绑定地址;容器里设 `0.0.0.0`,本地裸跑保持回环 |

容器相关既有变量的容器内取值(由镜像 `ENV` / compose 设定,非新增):

| 变量 | 容器内值 | 说明 |
|---|---|---|
| `DATA_DIR` | `/data` | 命名 volume 挂载点 |
| `MIHOMO_BIN` | `/usr/local/bin/mihomo` | 镜像内固定版本二进制(也在 PATH,默认值即可命中) |
| `ADVERTISE_HOST` | `127.0.0.1` | 纯宣告:URL + socks host |

---

## 10. 容器内 smoke 清单(`smoke/plan4_container.md`)

在容器里复跑 Plan 3 Task 17 的等价端到端验证(真实子进程/容器不进自动化测试,沿用项目惯例):

1. `docker compose up -d --build`;`docker compose logs` 见 token 与监听地址。
2. 等就绪:`docker inspect` 健康状态转 healthy,或 `curl -s 127.0.0.1:8080/health` 见 `nodes>0`。
3. 取订阅:`curl -s "127.0.0.1:8080/surge?token=<token>" | head -40` —— 见 `#!MANAGED-CONFIG` 头、`socks5, 127.0.0.1, 12xx`、`[Proxy Group]`、`[Rule]`。
4. 穿出口:`curl -s --socks5-hostname 127.0.0.1:1200 https://api.ipify.org` —— 返回该节点出口 IP(非本机)。
5. 自托管 ruleset:从 `/surge` 取一条 `RULE-SET/DOMAIN-SET, http://127.0.0.1:8080/ruleset/<n>?token=...`,`curl` 该 URL 返回转换后规则。
6. 优雅退出:`docker stop` 观察容器秒退(非等 grace period 超时 SIGKILL),`docker logs` 无 mihomo 孤儿告警。
7. (可选)`GEOSITE_URL` 设定后 `GEOSITE,cn` 自托管 ruleset 可拉。

结论(节点数、出口是否走对、退出是否秒退)记到该文件末尾。

---

## 11. 测试策略

- **代码 delta(§3 / §4 / §5)走 TDD**:`HTTP_BIND` 解析与绑定、PID 1 关停流程、`hmac.compare_digest`、无节点直连降级分支,均可进程内单测(`.venv/bin/python -m pytest`),不需真实容器或子进程。
- **Docker 产物(§6 / §7)靠 §10 容器 smoke 验证**:Dockerfile/compose 不适合单元测试,以手动/脚本化 smoke 闸门把关,与 Plan 3 真实 mihomo smoke 同构。
- 全套既有测试在改动后保持绿;新增测试与硬化项的回归一并纳入。

---

## 12. 已知取舍与未决项

- **`MAX_NODES` 杠杆「半失效」(已接受)**:compose 的端口发布范围在容器创建时固定(默认发满 `1200-1299`,100 个)。调小 `MAX_NODES` 只减少容器内 mihomo 监听数与写进 Surge 的节点数,**不减少 Docker 发布的 100 个端口转发**。真要降发布开销须**同时手改 compose 的端口段**。不做 compose 内 env 算术联动(脆弱),改为在 compose 注释与 smoke 文档明确写出该约束。
- **命名 volume 的 uid**:用命名 volume(非 host bind-mount)让 Docker 用镜像内 `/data` 的属主初始化卷,非 root 用户可写;若用户改用 host bind-mount 需自行保证宿主目录对 uid `10001` 可写——文档点明。
- **geosite 自托管需 `GEOSITE_URL`**:未设时 `GEOSITE,cn` 干净跳过(无 dangling 行,Plan 3 已修),不影响其余规则。
- **mihomo 版本升级**:版本与 sha256 固定在 Dockerfile;升级需同时更新二者并复跑 smoke。

---

## 13. 参考资料

- 总设计 [`2026-06-21-surge-gw-design.md`](2026-06-21-surge-gw-design.md) §4 架构 / §13 Docker 与部署 / §14 安全 / §16 配置项
- 运行时层设计 [`2026-06-22-surge-gw-plan3-runtime-design.md`](2026-06-22-surge-gw-plan3-runtime-design.md)
- Plan 3 真实 mihomo smoke 结论 `smoke/plan3_runtime.md`
- [MetaCubeX/mihomo releases](https://github.com/MetaCubeX/mihomo/releases)(二进制 + 校验和来源)
- Docker:[Compose file](https://docs.docker.com/compose/compose-file/) · [HEALTHCHECK](https://docs.docker.com/reference/dockerfile/#healthcheck) · [`init`](https://docs.docker.com/reference/compose-file/services/#init)
