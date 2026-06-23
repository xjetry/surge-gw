# /surge 访问异步触发上游刷新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `GET /surge` 在秒回缓存的同时，异步（非阻塞、带 debounce）唤醒后台刷新线程重抓上游，使下一次拉取拿到最新转换结果。

**Architecture:** 在 `Orchestrator` 上新增非阻塞的 `nudge()`：仅在 `should_refresh` debounce 允许时 `set` 既有的 `_wake` event 唤醒后台 `_loop`，**绝不**在调用者线程执行 `refresh_once`。`/surge` handler 在返回缓存前调 `nudge()`。复用既有 `_wake`/`should_refresh`/`_last_started`/`min_refresh_interval`，与同步阻塞的 `request_refresh`（`POST /refresh` 专用）语义分离。

**Tech Stack:** Python ≥3.12，标准库 `threading`（`Event`/`Lock`）；测试用 pytest。无新增依赖。

## Global Constraints

- 运行时仅依赖标准库 + `pyyaml>=6`；本特性不得引入任何新依赖。
- `nudge()` 必须非阻塞：只做一次 `should_refresh` 判断 + 至多一次 `Event.set()`，**不得**在调用线程调用 `refresh_once` 或持有/等待任何会阻塞的锁。
- `/surge` 的秒回缓存语义与响应延迟不变。
- 后台刷新频率仍受 `min_refresh_interval`（默认 300s）debounce 约束；`/surge` 高频访问不得触发多于每 `min_refresh_interval` 一次的后台刷新。
- `request_refresh`（同步阻塞，`POST /refresh` 专用）行为保持不变。
- 测试运行器：`.venv/bin/python -m pytest`。
- 注释/docstring/commit message 只解释 WHY 与不变量，不得出现任务编号、方案代号、审阅轮次等过程性信息。

---

## File Structure

- `src/surge_gw/orchestrator.py` — 新增 `Orchestrator.nudge()`（非阻塞 debounced wake）。复用既有 `_wake`、`should_refresh`、`_last_started`、`_lock`、`config.min_refresh_interval`、`clock`。
- `src/surge_gw/http_server.py` — `/surge` handler 在 `_send` 返回缓存前调用 `orchestrator.nudge()`。
- `tests/test_orchestrator.py` — `nudge` 的 debounce / in-flight / 不跑 refresh 行为。
- `tests/test_http_server.py` — `_FakeOrch` 加 `nudge` 计数；`/surge` 调 `nudge` 恰一次且仍返回缓存。

---

### Task 1: `Orchestrator.nudge()` — 非阻塞 debounced wake

**Files:**
- Modify: `src/surge_gw/orchestrator.py`（在 `request_refresh` 之后、`start_background` 之前新增 `nudge` 方法）
- Test: `tests/test_orchestrator.py`（文件末尾追加 4 个测试）

**Interfaces:**
- Consumes: `should_refresh(now, last_started, in_flight, min_interval) -> bool`（`surge_gw.refresh_policy`，已导入）；实例属性 `self.clock`、`self._last_started: float | None`、`self._lock: threading.Lock`、`self._wake: threading.Event`、`self.config.min_refresh_interval`。
- Produces: `Orchestrator.nudge(self) -> None` —— Task 2 的 `/surge` handler 调用它。语义：debounce 允许时 `self._wake.set()`，否则 no-op；任何情况下都不调用 `refresh_once`、不更新 `_last_started`（`_last_started` 由 `_loop` 真正起跑刷新时更新）。

- [ ] **Step 1: 追加 4 个失败测试**

在 `tests/test_orchestrator.py` 末尾追加（复用文件内已有的 `_orch`、`FakeFetcher`、`SUB`）：

```python
def test_nudge_sets_wake_on_first_call(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.nudge()
    assert o._wake.is_set()                              # 唤醒后台 _loop
    assert o.cache.get().surge_text == "placeholder"     # 未在调用线程跑 refresh_once
    assert o.last_success is None                        # refresh 从未执行


def test_nudge_debounced_within_min_interval(tmp_path):
    ticks = [1000.0]
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.clock = lambda: ticks[0]
    o._last_started = 1000.0
    ticks[0] = 1100.0                # 距上次起跑 100s < 300 防抖窗口
    o.nudge()
    assert not o._wake.is_set()      # 防抖窗口内不唤醒,避免高频 /surge 打爆上游


def test_nudge_wakes_after_min_interval(tmp_path):
    ticks = [1000.0]
    o = _orch(tmp_path, FakeFetcher(SUB))
    o.clock = lambda: ticks[0]
    o._last_started = 1000.0
    ticks[0] = 1400.0                # 距上次起跑 400s >= 300
    o.nudge()
    assert o._wake.is_set()


def test_nudge_skips_when_in_flight(tmp_path):
    o = _orch(tmp_path, FakeFetcher(SUB))
    o._lock.acquire()                # 模拟刷新在途
    try:
        o.nudge()
        assert not o._wake.is_set()  # 在途时不唤醒,不叠加重操作
    finally:
        o._lock.release()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -k nudge -q`
Expected: FAIL —— `AttributeError: 'Orchestrator' object has no attribute 'nudge'`（4 个测试报错）

- [ ] **Step 3: 实现 `nudge`**

在 `src/surge_gw/orchestrator.py` 的 `request_refresh` 方法之后、`start_background` 之前插入：

```python
    def nudge(self) -> None:
        """Wake the background refresh loop (debounced) without running refresh_once on the
        caller's thread. /surge calls this so a config pull can trigger an upstream refresh
        while still returning cache instantly. Distinct from request_refresh, which refreshes
        synchronously; _last_started is left for _loop to set when it actually starts the refresh."""
        if should_refresh(self.clock(), self._last_started,
                          self._lock.locked(), self.config.min_refresh_interval):
            self._wake.set()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -k nudge -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add src/surge_gw/orchestrator.py tests/test_orchestrator.py
git commit -m "Add non-blocking debounced nudge to orchestrator"
```

---

### Task 2: `/surge` handler 调用 `nudge`

**Files:**
- Modify: `src/surge_gw/http_server.py:26-28`（`/surge` 分支，`_send` 之前插入 `orchestrator.nudge()`）
- Test: `tests/test_http_server.py`（`_FakeOrch` 加 `nudge`；新增一个断言测试）

**Interfaces:**
- Consumes: Task 1 的 `orchestrator.nudge() -> None`。

- [ ] **Step 1: `_FakeOrch` 加 `nudge` 计数并追加失败测试**

在 `tests/test_http_server.py` 中，把 `_FakeOrch` 改为（加 `nudges` 计数与 `nudge` 方法）：

```python
class _FakeOrch:
    def __init__(self):
        self.refreshes = 0
        self.nudges = 0
    def request_refresh(self):
        self.refreshes += 1
    def nudge(self):
        self.nudges += 1
    def health(self):
        return {"nodes": 1}
```

并在文件末尾追加：

```python
def test_surge_nudges_and_serves_cache():
    orch = _FakeOrch()
    cache = Cache(Snapshot(surge_text="SURGE-BODY"))
    srv, port = _serve(cache, orch)
    try:
        assert _get(port, "/surge") == (200, b"SURGE-BODY")   # 仍秒回缓存
        assert orch.nudges == 1                               # 触发一次异步刷新唤醒
    finally:
        srv.shutdown()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_http_server.py::test_surge_nudges_and_serves_cache -q`
Expected: FAIL —— `assert 0 == 1`（handler 尚未调用 `nudge`，计数为 0）

- [ ] **Step 3: handler 调用 `nudge`**

在 `src/surge_gw/http_server.py` 的 `/surge` 分支插入 `orchestrator.nudge()`：

```python
            if parsed.path == "/surge":
                orchestrator.nudge()   # 拉配置即异步唤醒上游刷新(debounced),不阻塞秒回
                self._send(200, cache.get().surge_text.encode())
                return
```

- [ ] **Step 4: 运行测试确认通过（含既有用例不回归）**

Run: `.venv/bin/python -m pytest tests/test_http_server.py -q`
Expected: PASS（含既有 `test_serves_cache_unauthenticated` 等，全部 passed）

- [ ] **Step 5: 全量回归 + 提交**

```bash
.venv/bin/python -m pytest tests/ -q
git add src/surge_gw/http_server.py tests/test_http_server.py
git commit -m "Nudge async upstream refresh on /surge access"
```

Expected: 全量 pytest passed（在原 128 基础上新增 5 个 = 133 passed）。

---

## 生效与手动验证（实现完成后，非自动化）

按 spec「生效」节：`docker compose up -d --build` 重建容器后——

- 连续 `curl http://<gw>/surge` 仍秒回缓存。
- 距上次刷新 ≥ `min_refresh_interval`(300s) 后首次访问，`/health` 的 `last_success` 时间戳前移（后台触发了一次刷新）。
- 300s 内高频访问不产生多于一次的刷新（`last_success` 不再变化）。
