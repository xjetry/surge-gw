# surge-gw 部署层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 Python 控制器 + mihomo 打成单容器、`docker compose` 一键起,并修掉部署暴露前必须解决的绑定/退出缺陷。

**Architecture:** 单容器:Python 控制器为 PID 1 主进程、mihomo 为受托子进程。先做两处部署逼出的代码 delta(HTTP 绑定地址与对外宣告地址拆分、PID 1 优雅退出)+ 两个安全硬化(token 常数时间比较、无节点直连降级分支补测),再产出 Dockerfile / docker-compose / 容器内 smoke 清单。代码 delta 走 TDD;Docker 产物靠容器 smoke 闸门验证(真实子进程/容器不进自动化,沿用项目惯例)。

**Tech Stack:** Python 3.13(镜像)/ ≥3.12(代码);stdlib `http.server` + `signal` + `threading` + `hmac`;运行期依赖仅 `pyyaml`;Docker(`python:3.13-slim` + 固定版本 mihomo 二进制)。

## Global Constraints

- **过程污染禁止**:代码注释、docstring、commit message **绝不**出现任务/步骤编号、方案代号、审阅轮次、临时引用(如 "Task 5"、"Phase 2"、"Round 3"、"按上一轮")。只解释 WHY(为什么这样设计)与 invariant(必须保持的约束)。派 subagent 时必须把本约束写进 prompt;产出违反必须 follow-up 清理。
- **运行期依赖**:仅 `pyyaml`;不得为部署引入新的运行期 Python 依赖。
- **代码语言**:Python,`from __future__ import annotations`;注释沿用代码库惯例(中文)。
- **测试命令**:`.venv/bin/python -m pytest`(venv 已存在,不要重建)。
- **绑定 vs 宣告不变量**:容器内所有入站(HTTP + socks)bind `0.0.0.0`;所有写进 Surge 配置、供 Surge 在宿主机回连的 host 一律是 `ADVERTISE_HOST`(默认 `127.0.0.1`)。**绑定地址与宣告地址永不共用一个变量。**
- **基础镜像**:`python:3.13-slim`。
- **mihomo**:固定版本 `v1.19.27`,按 `TARGETARCH` 取 linux/amd64|arm64 二进制 + sha256 校验,放 `/usr/local/bin/mihomo`。
- **端口发布**:只发布到宿主机 `127.0.0.1`(`8080` + `1200-1299`);external-controller `9090` **不发布**。
- **分支与合并**:在 `plan4-deployment` 分支上做;完成后用 superpowers:finishing-a-development-branch 合回 `main`(本仓库无 remote,通常 fast-forward + 删分支)。

---

## File Structure

新增 / 修改:

- Modify `src/surge_gw/config.py` — `Config` 增 `http_bind` 字段;`from_env` 读 `HTTP_BIND`。
- Modify `src/surge_gw/http_server.py` — `build_server` 绑定改用 `config.http_bind`;`authed` 用 `hmac.compare_digest`。
- Modify `src/surge_gw/__main__.py` — PID 1 信号优雅退出(`shutdown` / `serve_until_stopped` / `install_signal_handlers`)。
- Modify `tests/test_config.py`、`tests/test_http_server.py`、`tests/test_orchestrator.py`、`tests/test_entrypoint.py` — 对应单测。
- Create `Dockerfile`、`.dockerignore`。
- Create `docker-compose.yml`、`.env.example`。
- Create `smoke/plan4_container.md` — 容器内 smoke 清单(手动闸门)。

---

## Task 1: HTTP 绑定地址与对外宣告地址拆分

把 HTTP server 的容器内绑定地址从 `ADVERTISE_HOST` 中分离为独立的 `HTTP_BIND`,默认仍回环。这样容器内可 bind `0.0.0.0` 让 Docker 端口转发可达,而写进 Surge 配置的 host 仍是宿主机回环 `127.0.0.1`。

**Files:**
- Modify: `src/surge_gw/config.py`
- Modify: `src/surge_gw/http_server.py:8` (`build_server`)
- Test: `tests/test_config.py`, `tests/test_http_server.py`

**Interfaces:**
- Produces: `Config.http_bind: str`(env `HTTP_BIND`,默认 `"127.0.0.1"`);`build_server(cache, orchestrator, config)` 绑定 `(config.http_bind, config.http_port)`。
- Consumes: 既有 `from_env`、`build_server` 签名不变。

- [ ] **Step 1: 写失败测试(config)**

在 `tests/test_config.py` 追加:

```python
def test_http_bind_defaults_to_loopback():
    c = from_env({"SUBSCRIPTION_URL": "http://x/sub"})
    assert c.http_bind == "127.0.0.1"


def test_http_bind_override_and_empty_falls_back():
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub", "HTTP_BIND": "0.0.0.0"}).http_bind == "0.0.0.0"
    assert from_env({"SUBSCRIPTION_URL": "http://x/sub", "HTTP_BIND": ""}).http_bind == "127.0.0.1"
```

- [ ] **Step 2: 写失败测试(http_server 绑定取 http_bind)**

在 `tests/test_http_server.py` 追加(顶部已 `from surge_gw.cache import Cache` 等;复用既有 `_FakeOrch`):

```python
def test_server_binds_http_bind_not_advertise_host():
    cfg = from_env({"SUBSCRIPTION_URL": "http://x/s", "HTTP_PORT": "0",
                    "HTTP_BIND": "127.0.0.1", "ADVERTISE_HOST": "203.0.113.9"})
    srv = build_server(Cache(Snapshot(surge_text="x")), _FakeOrch(), cfg)
    try:
        assert srv.server_address[0] == "127.0.0.1"   # 绑定取 HTTP_BIND,而非 ADVERTISE_HOST
    finally:
        srv.server_close()
```

- [ ] **Step 3: 运行,确认失败**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_http_server.py -q`
Expected: FAIL（`Config` 无 `http_bind` / `AttributeError`）。

- [ ] **Step 4: 实现 config**

`src/surge_gw/config.py`:在 `Config` 的 `advertise_host` 之后加字段:

```python
    advertise_host: str
    http_bind: str
    http_port: int
```

`from_env` 在 `advertise_host=...` 之后加:

```python
        advertise_host=env.get("ADVERTISE_HOST") or "127.0.0.1",
        http_bind=env.get("HTTP_BIND") or "127.0.0.1",
        http_port=_int(env, "HTTP_PORT", 8080),
```

- [ ] **Step 5: 实现 http_server 绑定**

`src/surge_gw/http_server.py` 末行:

```python
    return ThreadingHTTPServer((config.http_bind, config.http_port), Handler)
```

并把 `build_server` docstring 末尾补一句不变量(WHY,不含过程信息):

```python
    """ThreadingHTTPServer:一律秒回缓存(对 Surge 异步);token 护 /surge /ruleset /refresh。
    绑定用 http_bind(容器内可 0.0.0.0);写进 Surge 配置的 host 用 advertise_host(宿主回环),二者不共用。"""
```

- [ ] **Step 6: 运行,确认通过**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_http_server.py -q`
Expected: PASS。

- [ ] **Step 7: 全量回归**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS（既有用例不受影响)。

- [ ] **Step 8: 提交**

```bash
git add src/surge_gw/config.py src/surge_gw/http_server.py tests/test_config.py tests/test_http_server.py
git commit -m "feat: separate HTTP bind address from the advertised host"
```

---

## Task 2: token 常数时间比较

`authed` 当前用明文 `==` 比 token(非常数时间)。改 `hmac.compare_digest`,消除时序侧信道,保持现有放行/拒绝语义(未启用鉴权→放行;缺 token→拒绝;不匹配→拒绝)。

**Files:**
- Modify: `src/surge_gw/http_server.py`
- Test: `tests/test_http_server.py`

**Interfaces:**
- Consumes: `config.subscription_token`(可能为 `None`)。
- Produces: `authed` 行为不变,仅比较方式改为常数时间。

- [ ] **Step 1: 写失败测试**

在 `tests/test_http_server.py` 追加:

```python
def test_wrong_and_missing_token_forbidden():
    srv, port = _serve(Cache(Snapshot(surge_text="x")), _FakeOrch(), "right")
    try:
        assert _get(port, "/surge?token=wrong")[0] == 403   # 不匹配
        assert _get(port, "/surge")[0] == 403               # 缺 token
        assert _get(port, "/surge?token=right")[0] == 200   # 匹配
    finally:
        srv.shutdown()
```

- [ ] **Step 2: 运行,确认现状(可能已部分通过)**

Run: `.venv/bin/python -m pytest tests/test_http_server.py::test_wrong_and_missing_token_forbidden -q`
Expected: 现有 `==` 实现下大概率 PASS（语义已对）;本步是把行为钉死,下一步换实现后必须仍 PASS。

- [ ] **Step 3: 实现常数时间比较**

`src/surge_gw/http_server.py`:顶部 `import hmac`(放在现有 import 区)。把 `authed` 改为:

```python
    def authed(qs: dict) -> bool:
        if token is None:                       # 未启用鉴权
            return True
        provided = qs.get("token", [None])[0]
        return provided is not None and hmac.compare_digest(provided, token)
```

- [ ] **Step 4: 运行,确认通过**

Run: `.venv/bin/python -m pytest tests/test_http_server.py -q`
Expected: PASS（含既有 token 用例)。

- [ ] **Step 5: 提交**

```bash
git add src/surge_gw/http_server.py tests/test_http_server.py
git commit -m "harden: compare subscription token in constant time"
```

---

## Task 3: PID 1 优雅退出

`main()` 现无信号处理、`serve_forever()` 死等。作为 PID 1,`docker stop` 的 SIGTERM 默认不退、要等 grace period 后 SIGKILL,且 mihomo 子进程可能被孤儿化。装 SIGTERM/SIGINT handler → 停后台刷新 → 终止 mihomo → 关停 HTTP server。

**Files:**
- Modify: `src/surge_gw/__main__.py`
- Test: `tests/test_entrypoint.py`

**Interfaces:**
- Produces:
  - `shutdown(server, orchestrator, manager) -> None` — 依次 `orchestrator.stop()`、`manager.stop()`、`server.shutdown()`。
  - `serve_until_stopped(server, orchestrator, manager, stop_event) -> None` — server 跑后台线程,阻塞到 `stop_event` 置位后调用 `shutdown`。
  - `install_signal_handlers(stop_event) -> None` — SIGTERM/SIGINT 置位 `stop_event`。
- Consumes: `orchestrator.stop()`(已存在)、`manager.stop()`(已存在)、`server.shutdown()`/`server.serve_forever()`(stdlib)。

- [ ] **Step 1: 写失败测试**

在 `tests/test_entrypoint.py` 追加:

```python
import signal
import threading


def test_shutdown_quiesces_then_stops_server():
    from surge_gw.__main__ import shutdown
    order = []
    class S:
        def shutdown(self): order.append("server")
    class O:
        def stop(self): order.append("orch")
    class M:
        def stop(self): order.append("mgr")
    shutdown(S(), O(), M())
    assert order == ["orch", "mgr", "server"]   # 先停工再杀子进程,最后关 server


def test_serve_until_stopped_starts_server_then_tears_down():
    from surge_gw.__main__ import serve_until_stopped
    order = []
    started = threading.Event()
    class S:
        def serve_forever(self): started.set()
        def shutdown(self): order.append("server")
    class O:
        def stop(self): order.append("orch")
    class M:
        def stop(self): order.append("mgr")
    ev = threading.Event()
    ev.set()                                    # 已请求停止 → 立即走关停
    serve_until_stopped(S(), O(), M(), ev)
    assert started.wait(timeout=2)              # server 线程已起
    assert order == ["orch", "mgr", "server"]


def test_install_signal_handlers_sets_event_on_sigterm():
    from surge_gw.__main__ import install_signal_handlers
    orig_term, orig_int = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        ev = threading.Event()
        install_signal_handlers(ev)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert ev.is_set()
    finally:
        signal.signal(signal.SIGTERM, orig_term)
        signal.signal(signal.SIGINT, orig_int)
```

- [ ] **Step 2: 运行,确认失败**

Run: `.venv/bin/python -m pytest tests/test_entrypoint.py -q`
Expected: FAIL（`ImportError` / 函数未定义)。

- [ ] **Step 3: 实现**

`src/surge_gw/__main__.py`:顶部 import 区加 `import signal` 与 `import threading`。在 `main` 之前加三个函数:

```python
def shutdown(server, orchestrator, manager) -> None:
    """停后台刷新、终止 mihomo 子进程、关停 HTTP server。
    次序:先让刷新线程停手、再杀子进程,避免在途 reload 比子进程活得久;server 最后关。"""
    orchestrator.stop()
    manager.stop()
    server.shutdown()


def serve_until_stopped(server, orchestrator, manager, stop_event) -> None:
    """server 跑后台线程,主线程阻塞到 stop_event 置位再优雅关停。
    作为容器 PID 1,显式关停让 docker stop 立即返回(而非等 SIGKILL),并回收 mihomo 子进程。"""
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop_event.wait()
    shutdown(server, orchestrator, manager)


def install_signal_handlers(stop_event) -> None:
    def _handler(signum, frame):
        stop_event.set()
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
```

把 `main()` 末尾的 `server.serve_forever()` 替换为:

```python
    stop_event = threading.Event()
    install_signal_handlers(stop_event)
    serve_until_stopped(server, orchestrator, manager, stop_event)
```

- [ ] **Step 4: 运行,确认通过**

Run: `.venv/bin/python -m pytest tests/test_entrypoint.py -q`
Expected: PASS。

- [ ] **Step 5: 全量回归**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/surge_gw/__main__.py tests/test_entrypoint.py
git commit -m "feat: shut down gracefully on SIGTERM and SIGINT as container PID 1"
```

---

## Task 4: 无节点直连降级分支补测

`orchestrator._fetch_ruleset` 在无可钉定节点(`socks_port=None`)时走 `fetcher.fetch_text` 直连;该分支当前未测。补一个能区分订阅 URL 与 ruleset URL 的 fake,覆盖该路径。纯测试任务,不改源码。

**Files:**
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Orchestrator.refresh_once`;`fetcher.fetch_text(url, *, timeout=...)`、`fetcher.fetch_via_socks(url, socks_port, *, timeout=...)`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_orchestrator.py` 追加(复用顶部已有的 `yaml`、`Cache`、`from_env`、`Orchestrator`、`Snapshot`、`RulesetUrls`、`FakeManager`):

```python
class UrlAwareFetcher:
    """fetch_text 区分订阅 URL 与 ruleset URL,以便覆盖无节点(socks_port=None)的直连拉取路径。"""
    def __init__(self, sub_url, sub_body, ruleset_body):
        self.sub_url = sub_url
        self.sub_body = sub_body
        self.ruleset_body = ruleset_body
        self.socks_calls = 0
    def fetch_text(self, url, *, timeout=30.0):
        return self.sub_body if url == self.sub_url else self.ruleset_body
    def fetch_via_socks(self, url, socks_port, *, timeout=30.0):
        self.socks_calls += 1
        return self.ruleset_body.encode()


class NoNodeManager(FakeManager):
    def get_proxies(self):
        return {"proxies": {}}                 # 无任何节点 → 无端口 → socks_port=None


def test_no_nodes_fetches_rulesets_directly(tmp_path):
    sub = yaml.safe_dump({
        "proxies": [],
        "rules": ["RULE-SET,cnlist,DIRECT", "MATCH,DIRECT"],
        "rule-providers": {"cnlist": {"behavior": "domain", "format": "text", "url": "http://h/cn.txt"}},
    })
    fetcher = UrlAwareFetcher("http://h/sub", sub, "+.cn\n")
    cfg = _cfg(tmp_path)
    urls = RulesetUrls(host=cfg.advertise_host, port=cfg.http_port, token="t")
    o = Orchestrator(config=cfg, fetcher=fetcher, manager=NoNodeManager(),
                     cache=Cache(Snapshot(surge_text="placeholder")),
                     urls=urls, secret="s", geosite_source=None)
    snap = o.refresh_once()
    assert snap is not None
    assert snap.node_port_map == {}            # 无可钉定节点
    assert snap.rulesets["cnlist"] == ".cn\n"  # ruleset 经直连(fetch_text)托管
    assert fetcher.socks_calls == 0            # 无 socks 端口 → 未走 socks 路径
```

- [ ] **Step 2: 运行,确认通过(分支已存在,补的是覆盖)**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py::test_no_nodes_fetches_rulesets_directly -q`
Expected: PASS。若 FAIL,先用 systematic-debugging 定位(不要盲改源码:本任务是补测,源码分支应已正确)。

- [ ] **Step 3: 全量回归**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add tests/test_orchestrator.py
git commit -m "test: cover the no-nodes direct ruleset fetch fallback"
```

---

## Task 5: Dockerfile 与 .dockerignore

多阶段镜像:builder 拉取并校验固定版本 mihomo,运行镜像基于 `python:3.13-slim`、非 root、`/data` 可写、`HEALTHCHECK` 就绪式探活。**mihomo 二进制必须 sha256 校验,校验值由本任务第 1 步实测得出并填入。**

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Consumes: `pyproject.toml`、`src/`(`python -m surge_gw` 入口)、`Config` 的 `HTTP_BIND`/`DATA_DIR`/`MIHOMO_BIN`。
- Produces: 镜像 `surge-gw`(`ENTRYPOINT python -m surge_gw`,`/data` 为 volume,8080 + 1200-1299 监听)。

- [ ] **Step 1: 实测两个架构的 mihomo sha256**

固定版本 `v1.19.27`。下载两个 linux 架构的二进制并算 sha256(macOS 用 `shasum -a 256`):

```bash
for arch in amd64 arm64; do
  url="https://github.com/MetaCubeX/mihomo/releases/download/v1.19.27/mihomo-linux-${arch}-v1.19.27.gz"
  echo -n "${arch}: "; curl -fsSL "$url" | shasum -a 256 | awk '{print $1}'
done
```

记下输出的两个 64 位十六进制串,Step 2 填入 `Dockerfile` 的对应 `ARG`。**不得跳过校验、不得使用未经核对的值。** 若 release 资产命名与上面不符,以 MetaCubeX/mihomo `v1.19.27` 实际资产名为准并同步修正 Dockerfile 里的 `asset` 拼接。

- [ ] **Step 2: 写 Dockerfile**

把 Step 1 实测的两个 sha256 分别替换下面 `ARG MIHOMO_SHA256_amd64=` / `ARG MIHOMO_SHA256_arm64=` 的等号右侧(必须是实测值,非占位):

```dockerfile
# syntax=docker/dockerfile:1.7

FROM debian:bookworm-slim AS mihomo
ARG TARGETARCH
ARG MIHOMO_VERSION=v1.19.27
ARG MIHOMO_SHA256_amd64=
ARG MIHOMO_SHA256_arm64=
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && asset="mihomo-linux-${TARGETARCH}-${MIHOMO_VERSION}.gz" \
 && curl -fsSL "https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/${asset}" -o /tmp/mihomo.gz \
 && case "$TARGETARCH" in \
      amd64) echo "${MIHOMO_SHA256_amd64}  /tmp/mihomo.gz" | sha256sum -c - ;; \
      arm64) echo "${MIHOMO_SHA256_arm64}  /tmp/mihomo.gz" | sha256sum -c - ;; \
      *) echo "unsupported TARGETARCH ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && gunzip -c /tmp/mihomo.gz > /usr/local/bin/mihomo \
 && chmod +x /usr/local/bin/mihomo

FROM python:3.13-slim
COPY --from=mihomo /usr/local/bin/mihomo /usr/local/bin/mihomo
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --home-dir /data --no-create-home surgegw \
 && mkdir -p /data \
 && chown surgegw:surgegw /data
USER surgegw
ENV DATA_DIR=/data \
    MIHOMO_BIN=/usr/local/bin/mihomo \
    HTTP_BIND=0.0.0.0
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python", "-c", "import json,urllib.request,sys; d=json.load(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)); sys.exit(0 if d.get('nodes', 0) > 0 else 1)"]
ENTRYPOINT ["python", "-m", "surge_gw"]
```

- [ ] **Step 3: 写 .dockerignore**

`.dockerignore`:

```
.git/
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
_smoke_data/
data/
docs/
smoke/
tests/
```

- [ ] **Step 4: 构建镜像验证**

Run: `docker build -t surge-gw .`
Expected: 构建成功,`sha256sum -c` 校验通过(校验失败会中止构建)。

> 注:`docker build` 需本机 Docker Desktop 运行 + 可联网拉基础镜像/二进制。若执行环境无 Docker,本步与后续容器验证一并交由 Task 7 的手动 smoke 闸门执行(沿用项目惯例:真实容器不进自动化)。

- [ ] **Step 5: 提交**

```bash
git add Dockerfile .dockerignore
git commit -m "build: containerize controller and mihomo into a single image"
```

---

## Task 6: docker-compose.yml 与 .env.example

一键起:端口只发布到 `127.0.0.1`、命名 volume 持久化 `/data`、`init: true` 兜底回收僵尸、`restart: unless-stopped`、`HTTP_BIND=0.0.0.0`(容器内)+ `ADVERTISE_HOST=127.0.0.1`(宣告)。

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`

**Interfaces:**
- Consumes: Task 5 的镜像/Dockerfile;`SUBSCRIPTION_URL`(必填)。

- [ ] **Step 1: 写 docker-compose.yml**

```yaml
services:
  surge-gw:
    build: .
    image: surge-gw
    init: true                       # 兜底回收任何意外僵尸子进程,与 PID 1 的优雅终止互补
    restart: unless-stopped
    environment:
      SUBSCRIPTION_URL: ${SUBSCRIPTION_URL:?set SUBSCRIPTION_URL in .env}
      HTTP_BIND: 0.0.0.0             # 容器内绑定;宿主仅把端口发布到 127.0.0.1
      ADVERTISE_HOST: 127.0.0.1     # 写进 Surge 配置、供宿主机回连的 host
      DATA_DIR: /data
      # 可选覆盖:MAX_NODES / GEOSITE_URL / SUBSCRIPTION_TOKEN / REFRESH_INTERVAL ...
    ports:
      # 端口发布范围在容器创建时固定。调小 MAX_NODES 只减少容器内监听数,
      # 不会减少这里发布的端口数;要真减发布开销须同步缩小下面的 1200-1299 段。
      - "127.0.0.1:8080:8080"
      - "127.0.0.1:1200-1299:1200-1299"
    volumes:
      - surge-gw-data:/data          # 命名 volume:让非 root 用户(uid 10001)可写、token 重启持久

volumes:
  surge-gw-data:
```

- [ ] **Step 2: 写 .env.example**

```
# 必填:远程 mihomo 订阅 URL
SUBSCRIPTION_URL=https://example.com/your-subscription.yaml

# 可选覆盖(留空即用默认):
# MAX_NODES=100
# GEOSITE_URL=https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat
# SUBSCRIPTION_TOKEN=          # 不设则首次运行自动生成并持久化到 /data/token
# REFRESH_INTERVAL=21600
```

- [ ] **Step 3: 校验 compose 语法**

Run: `SUBSCRIPTION_URL=http://x docker compose config -q`
Expected: 无输出、退出码 0（YAML 合法、插值正确)。

> 若执行环境无 Docker,本步交由 Task 7 手动 smoke 闸门执行。

- [ ] **Step 4: 提交**

```bash
git add docker-compose.yml .env.example
git commit -m "build: one-command compose stack publishing only to host loopback"
```

---

## Task 7: 容器内 smoke 清单(手动闸门)

写容器内端到端验证清单并交用户执行(真实容器 + 真实订阅;不进自动化,沿用 Plan 3 Task 17 惯例)。

**Files:**
- Create: `smoke/plan4_container.md`

- [ ] **Step 1: 写 smoke 清单**

`smoke/plan4_container.md`:

```markdown
# 容器内 smoke(真实 mihomo + 真订阅,单容器)

自动化测试不跑真实容器。本闸门用 docker compose 端到端验证部署层。

前置:Docker Desktop 运行;`cp .env.example .env` 并把 `SUBSCRIPTION_URL` 改成真订阅。

步骤:
1. 起服务:`docker compose up -d --build`。
2. 看日志拿 token 与监听:`docker compose logs | grep -E "serving on|subscription token"`。
3. 等就绪:`docker inspect --format '{{.State.Health.Status}}' $(docker compose ps -q surge-gw)` 转 `healthy`,
   或 `curl -s 127.0.0.1:8080/health | python3 -m json.tool` 见 `nodes>0`。
4. 取订阅:`curl -s "127.0.0.1:8080/surge?token=<token>" | head -40`
   应见 `#!MANAGED-CONFIG` 头、`[Proxy]` 里 `socks5, 127.0.0.1, 12xx`、`[Proxy Group]`、`[Rule]`。
5. 穿某 socks 端口验出口:`curl -s --socks5-hostname 127.0.0.1:1200 https://api.ipify.org; echo`
   返回 IP 应是该节点出口而非本机。
6. 验自托管 ruleset:从 `/surge` 取一条 `RULE-SET/DOMAIN-SET, http://127.0.0.1:8080/ruleset/<n>?token=...`,
   `curl -s` 该 URL 应返回转换后的域名/规则列表。
7. 验优雅退出:`time docker compose stop surge-gw` 应秒级返回(非等 10s SIGKILL);
   `docker compose logs --tail=20` 无 mihomo 孤儿/异常退出告警。
8. (可选)设 `GEOSITE_URL` 后复跑,`GEOSITE,cn` 应有自托管 ruleset 可拉。

把结论(就绪用时、节点数、出口是否走对、stop 是否秒退)记到本文件末尾。

## 结论

(待执行后填写)
```

- [ ] **Step 2: 提交**

```bash
git add smoke/plan4_container.md
git commit -m "docs: container smoke checklist for the deployment stack"
```

- [ ] **Step 3: 交用户执行手动闸门**

把 `smoke/plan4_container.md` 交用户跑(需 Docker Desktop + 真实订阅)。这是部署层的端到端验收,等价 Plan 3 Task 17。不要假定其通过;若 smoke 暴露问题,在 `plan4-deployment` 分支(或新分支)上用 systematic-debugging 修复。

---

## Self-Review(已对照 spec)

- **Spec 覆盖**:§3 绑定/宣告拆分→Task 1;§4 PID 1 优雅退出→Task 3;§5 token 硬化→Task 2、无节点测试→Task 4;§6 Dockerfile→Task 5;§7 compose+.env→Task 6;§8 healthcheck→Task 5 的 HEALTHCHECK;§9 config 增补→Task 1;§10 smoke→Task 7;§12 取舍(MAX_NODES 端口段、命名 volume、geosite 可选)→分别落在 Task 6 注释/Task 5 volume/Task 7 step 8 与 .env.example。
- **占位符扫描**:Dockerfile 的 `MIHOMO_SHA256_*` 不是 TODO 占位,而是 Task 5 Step 1 实测得出、Step 2 必填的可验证值,并给了精确命令;构建步 `sha256sum -c` 强制校验。无 "TBD/handle edge cases/similar to" 类空话。
- **类型一致**:`Config.http_bind: str`、`build_server(cache, orchestrator, config)`、`shutdown/serve_until_stopped/install_signal_handlers` 的签名在定义任务与引用处一致;`UrlAwareFetcher`/`NoNodeManager` 与既有 `FakeFetcher`/`FakeManager` 接口(`fetch_text(url,*,timeout)`、`fetch_via_socks(url,socks_port,*,timeout)`、`get_proxies`)一致。
- **过程污染**:全部 commit message 与代码注释只述 WHAT/WHY,无任务/步骤/轮次编号。
```
