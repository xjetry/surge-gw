# surge-gw

把 **mihomo / Clash.Meta 格式的订阅**转换成 **Surge 可用的 `#!MANAGED-CONFIG`**，并以网关形式秒回给 Surge。

Surge 原生不支持 mihomo 的 `proxy-providers`、`rule-providers`、`geosite.dat` 等特性。surge-gw 在本地跑一个内嵌 mihomo 作为代理引擎来解析这些上游特性，把每个选中的节点钉定到一个本地 SOCKS 监听端口，再生成一份让 Surge 的 proxy 指向这些本地端口的配置——于是你可以在 Surge 里直接用 Clash 格式的订阅。

## 工作原理

```
上游订阅(mihomo yaml)
   │  fetch
   ▼
内嵌 mihomo ──reload──► 解析 proxy-providers / 节点列表
   │
   ├─ 把选中节点钉定到本地 SOCKS 监听端口(port_base 起)
   ├─ rule-providers / geosite 转换为自托管 ruleset
   └─ 节点服务器地址置顶为 DIRECT(断开 TUN 重捕 egress 的环路)
   │
   ▼
组装 Surge #!MANAGED-CONFIG ──原子 swap──► 缓存
   │
   ▼
GET /surge  ──秒回缓存──►  Surge
```

刷新流水线（抓上游 + 两次 mihomo reload + 抓所有 ruleset + geosite）是一次重操作，因此 `/surge` **一律秒回缓存、对 Surge 异步**，不在请求线程里跑刷新。

## 快速开始

需要：Docker + Docker Compose。镜像在 CI 构建并发布到 GHCR（见[部署](#部署)）。

```bash
cp .env.example .env
# 编辑 .env，至少填 SUBSCRIPTION_URL
docker compose up -d
```

容器用 **host 网络**、仅绑回环 `127.0.0.1:8080`。在 Surge 里把托管配置 URL 指到：

```
http://127.0.0.1:8080/surge
```

健康检查：

```bash
curl -s 127.0.0.1:8080/health
# {"nodes": 19, "dropped": [], "skipped": 3, "last_success": ..., "last_error": null}
```

## 配置

全部经环境变量注入（`SUBSCRIPTION_URL` 必填，其余可选）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SUBSCRIPTION_URL` | （必填） | 上游 mihomo 订阅 URL |
| `HTTP_BIND` | `127.0.0.1` | HTTP server 绑定地址（仅回环，不向局域网暴露） |
| `HTTP_PORT` | `8080` | HTTP server 端口 |
| `ADVERTISE_HOST` | `127.0.0.1` | 写进 Surge 配置、供 Surge 回连的 host |
| `PORT_BASE` | `1200` | 本地 SOCKS 节点监听端口的起始值 |
| `MAX_NODES` | `100` | 钉定节点数上限 |
| `REFRESH_INTERVAL` | `21600` | 后台自动刷新间隔（秒，默认 6h） |
| `MIN_REFRESH_INTERVAL` | `300` | 刷新防抖窗口（秒）；`/surge` nudge 与 `POST /refresh` 共用 |
| `SURGE_UPDATE_INTERVAL` | `3600` | 写进 `#!MANAGED-CONFIG` 的 `interval`（秒） |
| `GEOSITE_URL` | （空） | `geosite.dat` 下载 URL，留空则跳过 geosite |
| `GEOSITE_TTL` | `86400` | geosite 缓存 TTL（秒） |
| `MIHOMO_BIN` | `mihomo` | mihomo 可执行文件路径 |
| `DATA_DIR` | `./data` | 端口映射、last-good 缓存等持久化目录 |

## HTTP 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/surge` | 返回 Surge 托管配置（秒回缓存）。访问会**异步**唤醒一次后台上游刷新（带防抖），不阻塞响应。 |
| `GET` | `/ruleset/<key>` | 返回自托管的某个 ruleset 内容；不存在返回 404。 |
| `GET` | `/health` | JSON：`nodes` / `dropped` / `skipped` / `last_success` / `last_error`。 |
| `POST` | `/refresh` | 同步触发一次刷新（防抖），返回 `202`。 |

### 刷新触发

上游重抓由四种方式触发，均受 `MIN_REFRESH_INTERVAL` 防抖：

- 后台每 `REFRESH_INTERVAL` 一次；
- `POST /refresh`（同步阻塞）；
- **`GET /surge` 访问**（非阻塞 nudge：仅唤醒后台刷新线程，不在请求线程跑刷新，秒回不变）；
- 重启时冷启动刷新。

> 想让「Surge 拉配置」本身成为刷新的唯一驱动、便于观察 nudge 效果，可把 `REFRESH_INTERVAL` 调大（如 `21600`），这样只有 `/surge` 访问会推动刷新（仍每 `MIN_REFRESH_INTERVAL` 至多一次）。

## 网络说明

- 容器仅绑回环、走 host 网络，不 publish 端口，不向局域网暴露；所有端点无鉴权（依赖回环隔离）。
- 节点服务器地址会以 `DIRECT` 置顶到 `[Rule]`，避免宿主侧代理（如 Surge TUN 模式）重新捕获本网关出向流量、把节点连接绕回节点造成的死循环。

## 本地开发

需要 Python ≥ 3.12 与一个本地 `mihomo` 二进制（仅运行 / 冒烟时需要；单元测试用内存 fake，不依赖真 mihomo）。

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q          # 133 passed
```

直接在宿主跑（非容器）：

```bash
set -a; . ./.env; set +a
MIHOMO_BIN=$(which mihomo) DATA_DIR=./_smoke_data \
  .venv/bin/python -m surge_gw
```

## 部署

镜像由 GitHub Actions 构建并推送到 GHCR：`ghcr.io/xjetry/surge-gw`（多架构 `linux/amd64` + `linux/arm64`，每架构 mihomo 二进制按 sha256 钉定）。

**为何不本地 `docker build`**：构建期需从 `github.com` 下 mihomo release，若宿主出向被代理（Surge）重置 `github.com:443` 则必失败。CI 网络干净，构建完推到 GHCR，再由宿主拉回——`docker-compose.yml` 因此用 `image: ghcr.io/...` + `pull_policy: always`。

构建触发收窄为仅在镜像输入变化时（`Dockerfile` / `.dockerignore` / `src/**` / `pyproject.toml` / workflow 本身），外加手动 `workflow_dispatch`；文档/配置改动不触发。

更新部署：

```bash
git push            # 改了 src/Dockerfile 时,CI 自动构建并推 :latest
docker compose up -d   # 拉取新 :latest 并重建容器
```

## 项目结构

```
src/surge_gw/
  __main__.py        # 入口:装配 + bootstrap mihomo + 后台刷新 + HTTP server
  orchestrator.py    # 刷新流水线(single-flight + 原子 swap + 失败保留 last-good)
  http_server.py     # /surge /ruleset /health /refresh
  mihomo_config.py   # 生成 mihomo 运行时配置(empty-listener / pinned 两阶段)
  mihomo_manager.py  # mihomo 子进程生命周期 + REST 控制器 reload
  assemble.py        # 组装 Surge 配置 + ruleset 集合
  surge_config.py    # Surge 配置文本渲染
  rules.py groups.py proxies.py providers.py nodes.py ...  # 各转换环节
  bypass.py          # 节点服务器 DIRECT 置顶规则
  refresh_policy.py  # should_refresh 防抖 + Snapshot
docs/                # 设计 spec 与实现计划
tests/               # 单元测试(内存 fake,不依赖真 mihomo)
Dockerfile           # 两阶段:下 mihomo + python:3.13-slim 运行时
docker-compose.yml   # host 网络 + 回环绑定 + 命名 volume
```
