# 设计：/surge 访问异步触发上游刷新

日期：2026-06-23

## 背景

surge-gw 的刷新流水线（`orchestrator.refresh_once`）抓上游订阅 → reload mihomo 解析 proxy-provider → 重新转换 → 原子 swap 缓存。这是一次**重操作**（抓上游 + 两次 mihomo reload + 抓所有 ruleset + geosite），可能耗时数秒到数十秒。

当前 `/surge` endpoint 一律**秒回缓存**（刻意设计，对 Surge 异步），不触发重抓。surge-gw 重抓上游只由三种方式触发：后台每 `refresh_interval`(默认 21600s=6h)、`POST /refresh`(同步阻塞)、或重启。

问题：用户在订阅源更新配置后，需要等最多 6 小时、或手动 `POST /refresh`，才能让 surge-gw 重抓 + 重转生效。希望「Surge 拉配置」这个动作本身能触发上游刷新。

## 目标

让 `GET /surge` 在返回缓存的同时，**异步**触发一次后台上游刷新（带 debounce），使下一次拉取拿到最新转换结果——且**不改变** `/surge` 的秒回语义、不阻塞响应、不超时、不因高频访问打爆上游。

## 设计

### orchestrator.nudge()（新增，非阻塞）

```python
def nudge(self) -> None:
    """/surge 用:Surge 拉配置即唤醒后台刷新线程重抓上游(debounced),不在调用线程跑
    refresh_once。与同步阻塞的 request_refresh 区分——/surge 必须保持秒回、不阻塞。"""
    if should_refresh(self.clock(), self._last_started, self._lock.locked(),
                      self.config.min_refresh_interval):
        self._wake.set()
```

复用现有 `_wake` event、`should_refresh` debounce、后台 `_loop`。`nudge` 只在 debounce 允许时 set wake 唤醒后台线程；**绝不**在调用者线程执行 `refresh_once`。

### http_server `/surge` handler

在返回缓存前调用 `nudge`：

```python
if parsed.path == "/surge":
    orchestrator.nudge()
    self._send(200, cache.get().surge_text.encode())
    return
```

`nudge` 是微秒级（一次 `should_refresh` 判断 + 可能一次 `Event.set`），不影响 `/surge` 响应延迟。

## 数据流

Surge `GET /surge` → `nudge` set wake（若 debounce 允许）→ 秒回缓存 → 后台 `_loop` 醒来执行 `refresh_once`（抓上游 + reload mihomo + 重转）→ swap 缓存 → Surge 下次 `GET /surge` 拿到新配置。

## 不变量

- `/surge` 响应延迟与语义不变（秒回缓存；`nudge` 不跑重操作、不阻塞调用线程）。
- 后台刷新频率仍受 `min_refresh_interval`(300s) debounce 约束：`/surge` 高频访问**不会**触发多于每 300s 一次的后台刷新，不会打爆上游或频繁 reload mihomo。
- `refresh_once` 失败保留 last-good（已有机制不变）。
- `nudge` 与 `request_refresh` 语义分离：前者非阻塞（仅唤醒），后者同步阻塞（`POST /refresh` 仍用它，不变）。

## 受影响范围

- `src/surge_gw/orchestrator.py`（新增 `nudge`）
- `src/surge_gw/http_server.py`（`/surge` handler 调 `nudge`）
- `tests/`（`nudge` debounce 行为；`/surge` 调 `nudge` 且仍返回缓存）

## 测试计划

- `nudge`：`in_flight`(锁持有)时不 set wake；距上次刷新 < `min_refresh_interval` 时不 set wake；≥ 时 set wake；首次（`_last_started=None`）set wake；`nudge` 不调用 `refresh_once`（不阻塞、不改缓存）。
- `/surge` handler：被请求时调用 `orchestrator.nudge()` 恰一次，并返回当前缓存的 `surge_text`（用 fake orchestrator 记录 nudge 调用次数）。

## 生效

改完 `docker compose up -d --build` 重建容器。验证：连续 `curl /surge` 仍秒回；首次访问（距上次刷新 ≥300s）后台触发一次刷新（`/health` 的 `last_success` 时间戳前移）；高频访问不产生多于每 300s 一次的刷新。
