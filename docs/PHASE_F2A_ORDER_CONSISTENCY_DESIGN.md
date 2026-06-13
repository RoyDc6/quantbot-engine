# Phase F2-A Design Review Package
# 订单执行一致性修复设计报告

**版本**: v1.0  
**日期**: 2026-06-04  
**状态**: 仅设计，未实施。  
**作者**: QuantBot (Phase F2-A 只读分析)  
**审查文件范围**: `unified_runner.py`, `core/order_executor.py`, `core/futu_adapter.py`, `core/stop_loss.py`

---

## 1. Executive Summary

Phase F1 审计识别出 7 类订单执行一致性风险。本报告基于对核心调用链的逐行代码分析，确认所有风险在当前代码库中均有具体证据，并提出可落地的修复设计。

**关键结论**：

| 风险编号 | 风险描述 | 严重程度 | 是否有代码证据 |
|:---:|:---|:---:|:---:|
| R-01 | 持仓查询失败时 fail-open（返回空仓） | 🔴 高 | ✅ 确认 |
| R-02 | 无订单幂等机制，重复运行可重复下单 | 🔴 高 | ✅ 确认 |
| R-03 | 订单无逐单持久化，崩溃后无法恢复 | 🟡 中 | ✅ 确认 |
| R-04 | 仅区分 OK/ERROR，无完整状态机 | 🔴 高 | ✅ 确认 |
| R-05 | BUY 依赖未确认成交的 SELL 资金 | 🟡 中 | ✅ 确认 |
| R-06 | 止损冷却由 `record_stop()` 驱动而非成交确认 | 🟡 中 | ✅ 确认 |
| R-07 | 无执行后对账机制 | 🟡 中 | ✅ 确认 |

**修复范围**：2 个现有文件（`order_executor.py`, `stop_loss.py`）大幅修改 + 1 个新文件（`core/order_journal.py`）。  
**不涉及受保护代码**：`config.py` L-001/L-002、`xmm-strategy/`、`volume_profile.py`、`fusion_engine.py`、`fusion_controller.py`、`chan/`。

---

## 2. Verified Root-Cause Chains

### 2.1 R-01: 持仓查询失败 → fail-open（返回空仓）

**代码证据 1：`futu_adapter.py` L242-280**

```python
def get_positions(self, market: str = 'HK') -> List[Dict]:
    if not self._available:
        return []              # ⚠️ futu-api 未安装时静默返回空仓
    ...
    try:
        ctx = ft.OpenSecTradeContext(...)
        try:
            ret, pdata = ctx.position_list_query(trd_env=ft.TrdEnv.SIMULATE)
            if ret != ft.RET_OK or pdata is None:
                return []      # ⚠️ API 返回错误时静默返回空仓
            ...
        finally:
            ctx.close()
    except Exception:
        return []              # ⚠️ 连接异常时静默返回空仓
```

**代码证据 2：`unified_runner.py` L241-248**

```python
positions = adapter.get_positions(market)
held_map = {}
print(f'  持仓: {len(positions)} 只')
for p in positions:
    std_sym = _futu_to_std(p['code'])
    ...
    held_map[std_sym] = p
```

**根因链**：`get_positions()` 在三种情况下（未安装、API 错误、连接异常）均返回 `[]` → `unified_runner.py` 对空返回与"真实空仓"无法区分 → `held_map` 为空 → 系统认为当前无任何持仓 → **触发对所有 BUY 信号建仓** → 实盘下可能造成严重超仓。

**问题级别**：`get_account_info()` 有 `if account is None: return`（L233-235）的 fail-closed 保护，但 `get_positions()` 完全缺乏对等的错误传播机制。

---

### 2.2 R-02: 无幂等机制，重复运行可重复下单

**代码证据：`order_executor.py` L96-169（`_place_single_order`）**

```python
def _place_single_order(self, order, market):
    ...
    if self.dry_run:
        log_entry = {..., 'status': 'DRY-RUN', ...}
        self.trade_log.append(log_entry)
        return log_entry

    # 实际下单（通过 FutuAdapter 统一下单）
    try:
        ok, result = self._adapter.place_order(...)
        if ok:
            log_entry = {..., 'order_id': result, 'status': 'OK', ...}
            ...
```

**根因链**：`_place_single_order()` 无任何幂等性检查。同一笔意图订单（相同 symbol + action + qty + price + 日期）在以下场景会被重复提交：

1. **进程在 `execute_orders()` 中途崩溃后重启**：崩溃前已提交的订单被重新提交。
2. **重复运行**（Roy 手动运行两次，或定时任务重叠）：同一信号生成两笔订单。
3. **超时后重试**：Futu API 超时 `ok=False` 后再次调用，但原始订单可能已被接受。

`unified_runner.py` 的 `_save_signals()` 保存了信号，但 **不检查今日是否已对相同 symbol 下过单**（代码 L810-833）。

---

### 2.3 R-03: 订单无逐单持久化，崩溃后无法恢复

**代码证据：`order_executor.py` L171-184（`_save_log`）**

```python
def _save_log(self, results, log_path):
    ...
    log = {
        'timestamp': ...,
        'mode': ...,
        'total_orders': len(results),
        'ok': ...,
        'errors': ...,
        'orders': results,       # ⚠️ 批量写入，execute_orders() 结束后才持久化
    }
    with open(log_path, 'w', ...) as f:
        json.dump(log, ...)
```

**代码证据：`unified_runner.py` L484**

```python
results = executor.execute_orders(orders, log_path=log_path)
```

**根因链**：`_save_log()` 在 `execute_orders()` 全部返回后才一次性写文件。若进程在第 2 笔订单提交后、`_save_log()` 调用前崩溃：

- 第 1 笔订单已在 Futu 系统中（**已提交未记录**）
- 重启后系统无法判断哪些已提交，会重新提交所有订单（见 R-02）

---

### 2.4 R-04: 仅区分 OK/ERROR，无完整状态机

**代码证据：`order_executor.py` L120-169**

当前代码中，订单状态仅有以下枚举：

| status 值 | 含义 |
|:---|:---|
| `'DRY-RUN'` | 模拟执行（干跑） |
| `'OK'` | 已提交并获得 order_id |
| `'ERROR'` | 下单失败或异常 |
| `'SKIP'` | 股数不足一手 |

**缺失状态**：

- `'SUBMITTED'`（已提交等待确认，区别于已成交）
- `'FILLED'`（已成交）
- `'REJECTED'`（Futu 系统拒单，非 API 错误）
- `'CANCELLED'`（用户/系统撤单）
- `'PARTIAL'`（部分成交）
- `'TIMEOUT'`（提交超时，状态未知）
- `'UNKNOWN'`（超时或崩溃后状态未知）
- `'FOK_NOT_FILLED'`（Fill-Or-Kill 未成交）

**代码证据：`futu_adapter.py` L319**

```python
fill_side_type=ft.FillSideType.FILL_OR_KILL if price_type == 'LIMIT'
else ft.FillSideType.NORMAL,
```

**特殊风险**：LIMIT 订单使用 FOK（Fill-Or-Kill）模式。FOK 未成交不是"错误"，而是合法的"未成交"结果，当前代码对此无专门处理——FOK 失败时 `ret != RET_OK` 或 `data` 为空，直接归类为 `ERROR`，与真实错误（网络断线、账户异常）混同。

---

### 2.5 R-05: BUY 依赖未确认成交的 SELL 估算资金

**代码证据：`unified_runner.py` L358-363**

```python
# 卖出先释放现金（估算）
for o in orders:
    if o['action'] == 'SELL':
        available_cash += o['qty'] * o['price']        # ⚠️ 按信号价格假设全部成交
        current_exposure -= o['qty'] * o['price']
```

**代码证据：`unified_runner.py` L391-392**

```python
budget = calc_signal_budget(level, sig['fusion_score'], total_assets, len(held_syms), max_positions)
budget = min(budget, available_cash * 0.8)             # 限制为 available_cash 的 80%
```

**根因链**：在建仓循环执行前，系统将 SELL 订单的预期收益（按 `p['current_price']` 估算）加入 `available_cash`，然后用这个"估算现金"计算 BUY 的预算。**SELL 尚未实际成交**，可能的问题：

1. FOK SELL 未成交 → SELL 资金实际上不存在 → BUY 超出实际可用现金
2. SELL 部分成交 → 实际回流资金小于估算
3. SELL 在盘后或隔日才成交 → 当日 BUY 已用不存在的资金

**当前缓解**：`_should_block_live()` 的 `cash_after_orders` 检查基于 `cash_before + gross_sell - gross_buy`，能在 pre-trade 阶段发现现金不足，但无法防止"SELL 估算现金释放 → BUY 决策"的这一依赖关系。

---

### 2.6 R-06: 止损冷却由 record_stop() 驱动，非成交确认

**代码证据：`unified_runner.py` L326**

```python
risk_mgr.record_stop(p['code'])     # ⚠️ 在 orders.append() 时调用，不是成交确认时
```

**代码证据：`stop_loss.py` L208-211**

```python
def record_stop(self, code):
    """记录止损事件，启动冷却期。"""
    self.stop_timestamps[code] = datetime.now().isoformat()
    self.save_state()
```

**根因链**：`record_stop()` 在**订单意图生成时**就被调用并持久化（L326，比 `execute_orders()` 早），而非订单成交确认时。这导致：

1. **订单提交失败**（`ERROR`）→ 实际未卖出 → 冷却期已启动 → 后续止损检查被跳过
2. **FOK 未成交**（见 R-04）→ 实际持仓仍在 → 冷却期已启动 → 止损保护失效
3. **进程崩溃**（见 R-03）→ `save_state()` 已写入 → 重启后冷却期已激活但持仓实际未平

---

### 2.7 R-07: 无执行后对账机制

**代码证据：`unified_runner.py` L468-530（Step 7 之后）**

在 `executor.execute_orders(orders, log_path=log_path)` 返回 `results` 之后，`run()` 函数直接：

1. 调用 `risk_mgr.save_state()`（L490）
2. 调用 `_save_signals()`（L493）
3. 打印摘要（L496）
4. 生成 v3 日报（L499-526）

**缺失**：`results` 的处理只是打印日志，没有任何逻辑：
- 回查 Futu API 确认订单状态
- 对比预期订单与实际成交结果
- 更新 `risk_state.json` 时剔除"已提交但未成交"的订单
- 对失败订单触发告警或回滚逻辑

---

## 3. Proposed Order State Machine

```
                         ┌─────────────────────────────────────┐
                         │           INTENT_CREATED            │
                         │  (order dict 生成于建仓决策循环)      │
                         └─────────────┬───────────────────────┘
                                       │ _place_single_order() 调用前持久化
                                       ▼
                         ┌─────────────────────────────────────┐
                         │           PENDING_SUBMIT            │
                         │  幂等键已生成并写入 journal           │
                         └──────┬──────────────────────────────┘
                                │ ctx.place_order() 返回
              ┌─────────────────┼────────────────────────────┐
              │                 │                            │
              ▼                 ▼                            ▼
    ┌──────────────┐   ┌──────────────────┐       ┌──────────────────┐
    │  SUBMITTED   │   │    REJECTED      │       │     TIMEOUT      │
    │  order_id 已 │   │  Futu 拒单,无    │       │  超时未返回,      │
    │  获取        │   │  order_id        │       │  status 未知     │
    └──────┬───────┘   └──────────────────┘       └────────┬─────────┘
           │                                               │
           │ 轮询 order_list_query()                       │ 恢复轮询
           ▼                                               ▼
    ┌──────────────────────────────────────────────────────────────┐
    │                    ORDER STATUS QUERY                        │
    │      (单独轮询步骤，与提交分离，支持崩溃恢复)                     │
    └──────────┬───────────────────────────────┬──────────────────┘
               │                               │
    ┌──────────▼──────┐              ┌──────────▼──────────┐
    │     FILLED      │              │   PARTIAL_FILLED    │
    │  全量成交         │              │  部分成交             │
    └──────────────────┘              └──────────────────────┘
               │                               │
    ┌──────────▼──────────┐         ┌──────────▼──────────────┐
    │  POST_FILL_ACTIONS  │         │  PARTIAL_FILL_ACTIONS   │
    │  • 更新冷却期         │         │  • 记录已成交量           │
    │  • 释放资金确认       │         │  • 剩余量后续处理         │
    │  • 对账               │         │  • 不启动完整冷却期       │
    └─────────────────────┘         └─────────────────────────┘

    额外终态（无需后续轮询）：
    FOK_NOT_FILLED:   FOK 单未成交，属合法结果，不是 ERROR
    CANCELLED:        用户或系统撤单
    UNKNOWN:          多次轮询后仍未知（人工介入）
```

### 合法状态转换规则

| 当前状态 | 合法后续状态 | 非法转换 |
|:---|:---|:---|
| INTENT_CREATED | PENDING_SUBMIT | 不可跳转到 SUBMITTED |
| PENDING_SUBMIT | SUBMITTED, REJECTED, TIMEOUT | 不可复用幂等键 |
| SUBMITTED | FILLED, PARTIAL_FILLED, CANCELLED, TIMEOUT | 不可回到 PENDING |
| FILLED | 终态，只读 | 不可再次提交 |
| REJECTED | 终态（可人工重试，生成新幂等键） | |
| FOK_NOT_FILLED | 终态（可重新生成意图，新幂等键） | |
| TIMEOUT | 轮询恢复 → SUBMITTED/FILLED/UNKNOWN | |
| UNKNOWN | 人工介入后 → FILLED/CANCELLED | |
| PARTIAL_FILLED | FILLED（追加成交），CANCELLED（剩余撤销） | |

---

## 4. Idempotency and Recovery Design

### 4.1 幂等键（Idempotency Key）生成规则

**格式**：`{date}_{market}_{symbol}_{action}_{qty}_{price_int}_{run_id}`

示例：`20260604_HK_00700.HK_BUY_100_35000_r1751631928`

**字段说明**：

| 字段 | 内容 | 说明 |
|:---|:---|:---|
| date | YYYYMMDD | 当日不跨日 |
| market | HK/US | 市场 |
| symbol | 标准符号 | 标的唯一标识 |
| action | BUY/SELL | 方向 |
| qty | 整手股数 | lot 对齐后 |
| price_int | `round(price*1000)` | 整数化，避免浮点 |
| run_id | `int(datetime.now().timestamp())` | 进程唯一标识 |

**注意**：`run_id` 在同一次 `run()` 调用中保持不变（进程级别），防止重复运行同一个 `run()` 时生成相同的键。建议在 `run()` 入口处生成，传入 `executor`。

### 4.2 幂等键存储位置

**文件**：`output/order_journal_{market}_{date}.jsonl`  
**格式**：JSON Lines（每行一个 JSON 对象）  
**生命周期**：当天保留 + 归档 7 天后删除

### 4.3 幂等检查流程

```
_place_single_order() 调用前：

1. 生成 idempotency_key
2. 查询 order_journal 是否存在相同 idempotency_key
   ├── 存在 && status in (SUBMITTED, FILLED, PARTIAL_FILLED)
   │   → 跳过，返回已有记录（防重复提交）
   ├── 存在 && status == PENDING_SUBMIT
   │   → 说明上次提交中途崩溃，继续提交（合法恢复）
   ├── 存在 && status in (REJECTED, FOK_NOT_FILLED, CANCELLED)
   │   → 跳过（意图已被处理，不重试）
   └── 不存在
       → 写入 PENDING_SUBMIT 状态，然后提交
```

### 4.4 进程崩溃恢复流程

**启动时检查**（在 `run()` 开始处，Step 0 之后）：

```python
# 伪代码（设计参考，非实现）
journal = OrderJournal.load_today(market)
pending = journal.get_by_status('PENDING_SUBMIT')
if pending:
    print(f'[RECOVERY] 发现 {len(pending)} 笔未完成订单，尝试状态确认...')
    for entry in pending:
        # 通过 order_id（若有）或 Futu 订单查询 API 确认状态
        status = query_order_status(entry)
        journal.update_status(entry.idempotency_key, status)
```

### 4.5 提交超时处理

**超时定义**：`place_order()` 调用超过 10 秒无响应（Futu API 一般在 2-3s 内返回）。

**超时后行为**：
1. 将 journal 中该订单状态改为 `TIMEOUT`
2. **不**将 `record_stop()` 的冷却期激活
3. 在下次运行开始前，通过 `order_list_query()` API 确认是否已成交
4. 未知状态超过 24 小时 → `UNKNOWN`，需人工介入

---

## 5. Execution Dependency and Reconciliation Design

### 5.1 SELL 与 BUY 的执行依赖规则

**当前问题**（R-05）：建仓决策循环中使用 SELL 估算资金（L358-363），导致 BUY 依赖未确认成交的 SELL。

**建议规则**：

```
规则 1: 在 LIVE 模式下，BUY 预算计算严格使用 Futu 账户实时现金（cash_before），
        不将 SELL 估算收益加入 available_cash。

规则 2: SELL 必须在 BUY 之前执行（订单排序）。
        若 SELL 状态未达到 FILLED 或 PARTIAL_FILLED，BUY 订单不提交。

规则 3: 在 DRY_RUN 模式下，可保留当前估算逻辑（仅影响日志，不影响实际资金）。
```

**实现思路（伪代码）**：

```python
# order_executor.py 中
def execute_orders(self, orders, ...):
    sell_orders = [o for o in orders if o['action'] == 'SELL']
    buy_orders  = [o for o in orders if o['action'] == 'BUY']

    sell_results = []
    for o in sell_orders:
        r = self._place_single_order(o, market)
        sell_results.append(r)

    # LIVE 模式：等待 SELL 成交确认后再执行 BUY
    if not self.dry_run:
        confirmed_sell_cash = self._wait_and_confirm_sells(sell_results)
        # 重新查询实时现金（成交后）
        live_cash = self._adapter.get_account_info(market)['cash']
    else:
        live_cash = None  # dry_run 不需要

    buy_results = []
    for o in buy_orders:
        if not self.dry_run and live_cash is not None:
            # 基于实时现金做最终检查
            if o['qty'] * o['price'] > live_cash * 0.95:
                buy_results.append({**o, 'status': 'SKIP', 'message': '实时现金不足'})
                continue
        r = self._place_single_order(o, market)
        buy_results.append(r)

    return sell_results + buy_results
```

**建议等待时间**：SELL 提交后最多等待 30 秒；FOK 单理论上即时成交，如 30 秒后仍 SUBMITTED → 标记 TIMEOUT，取消 BUY。

### 5.2 执行后对账流程

**对账触发时机**：`execute_orders()` 完成后 30-60 秒（等待 Futu 成交回报）。

**对账步骤**：

```
Step A: 获取 Futu 当日成交记录
        → adapter.get_today_filled_orders(market)

Step B: 按 order_id 对比 journal 中 SUBMITTED 订单
        → 确认成交量、成交价、状态

Step C: 更新 journal 状态
        SUBMITTED → FILLED / PARTIAL_FILLED / CANCELLED / REJECTED

Step D: Fail-Closed 条件
        ├── SELL 订单 REJECTED 或未成交 → 不确认冷却期（冷却期不启动）
        ├── BUY 订单 REJECTED → 不计入持仓，不消耗 available_cash
        └── 任何 UNKNOWN 订单 → 记录告警，阻止后续当日同方向操作

Step E: 持仓验证（可选，高频时每次运行后做）
        → 对比 Futu 实时持仓 vs journal 推算持仓
        → 差异超过 1 手 → 触发告警
```

**Fail-Closed 条件（强制）**：

| 场景 | 当前行为 | 期望行为 |
|:---|:---|:---|
| 对账查询失败 | 忽略，继续 | 中止后续操作，记录错误 |
| SELL REJECTED 但冷却期已启动 | 冷却期维持（错误） | 撤销冷却期，保留止损检查 |
| BUY 已提交但未查到成交记录 | 无处理 | 标记 UNKNOWN，人工确认 |

---

## 6. Proposed File Changes

### 6.1 新文件：`core/order_journal.py`（全新）

**职责**：订单幂等管理 + 逐单持久化 + 状态追踪

**关键接口**：

```python
class OrderJournal:
    def __init__(self, journal_dir: Path, market: str, date: str): ...
    
    def generate_key(self, order: dict, run_id: str) -> str:
        """生成幂等键"""
    
    def is_duplicate(self, idempotency_key: str) -> tuple[bool, dict | None]:
        """检查是否重复提交，返回 (is_dup, existing_entry)"""
    
    def record_pending(self, idempotency_key: str, order: dict) -> None:
        """在提交前写入 PENDING_SUBMIT（原子操作）"""
    
    def update_status(self, idempotency_key: str, status: str,
                      order_id: str = None, filled_qty: int = 0,
                      filled_price: float = 0.0, message: str = '') -> None:
        """更新订单状态"""
    
    def get_by_status(self, status: str) -> list[dict]:
        """按状态查询订单"""
    
    def load_today(cls, journal_dir: Path, market: str, date: str) -> 'OrderJournal':
        """类方法：加载当日 journal"""
```

**持久化格式**（JSON Lines）：

```jsonl
{"idempotency_key": "20260604_HK_00700.HK_BUY_100_35000_r1751631928", "status": "PENDING_SUBMIT", "order": {...}, "created_at": "2026-06-04T09:35:01", "updated_at": "2026-06-04T09:35:01"}
{"idempotency_key": "20260604_HK_00700.HK_BUY_100_35000_r1751631928", "status": "SUBMITTED", "order_id": "12345678", "updated_at": "2026-06-04T09:35:03"}
{"idempotency_key": "20260604_HK_00700.HK_BUY_100_35000_r1751631928", "status": "FILLED", "filled_qty": 100, "filled_price": 350.00, "updated_at": "2026-06-04T09:35:45"}
```

（每次状态变更追加一行，最后一行为最新状态——append-only 设计，崩溃安全）

---

### 6.2 修改文件：`core/order_executor.py`

**改动概要**：

| 位置 | 当前 | 修改为 |
|:---|:---|:---|
| `__init__` | 无 journal | 接受 `journal: OrderJournal` 参数 |
| `execute_orders()` | 混合排序 | SELL 先于 BUY 执行；LIVE 模式下等待 SELL 确认 |
| `_place_single_order()` | 无幂等检查 | 调用前检查 journal；写 PENDING_SUBMIT；提交后写 SUBMITTED |
| `_place_single_order()` | 仅 OK/ERROR | 区分 SUBMITTED/REJECTED/FOK_NOT_FILLED/TIMEOUT |
| `_save_log()` | 批量末尾写 | 保留（作为最终摘要，不替代 journal 的逐单持久化） |

**新增方法**：

| 方法 | 职责 |
|:---|:---|
| `_confirm_and_reconcile()` | 提交后轮询确认成交状态 + 对账 |
| `_wait_for_sell_confirmation()` | LIVE 模式下等待 SELL 成交确认 |

---

### 6.3 修改文件：`core/stop_loss.py`

**改动概要**：

| 位置 | 当前 | 修改为 |
|:---|:---|:---|
| `record_stop()` (L208) | 无条件立即持久化 | 增加 `confirmed: bool = False` 参数 |
| | | `confirmed=False` 时仅暂存（不持久化） |
| | | `confirmed=True` 时才写入 `stop_timestamps` 并持久化 |
| `confirm_stop()`（新增） | — | 成交确认后调用，将暂存冷却期激活 |
| `rollback_stop()`（新增） | — | 订单 REJECTED/FOK_NOT_FILLED 后撤销暂存冷却期 |

**修改后逻辑**：

```python
# 在 unified_runner.py 中（订单意图生成时）
# 只暂存，不持久化
risk_mgr.record_stop(p['code'], confirmed=False)

# 在 OrderExecutor 对账后（成交确认时）：
if result['status'] == 'FILLED':
    risk_mgr.confirm_stop(code)      # 激活冷却期
elif result['status'] == 'REJECTED':
    risk_mgr.rollback_stop(code)     # 撤销暂存冷却期
```

---

### 6.4 修改文件：`unified_runner.py`

**改动概要**：

| 位置 | 当前 | 修改为 |
|:---|:---|:---|
| L232-248（持仓查询） | `get_positions()` 静默失败 | 异常时打印错误并 `return`（fail-closed） |
| L326（`record_stop`） | `record_stop(p['code'])` | `record_stop(p['code'], confirmed=False)` |
| L358-363（SELL 估算） | SELL 估算加入 `available_cash` | 仅生效 `cash_before`，不依赖 SELL 估算 |
| L474-484（执行） | `executor = OrderExecutor(...)` | 注入 `journal`；接收 results 并调用对账 |
| L490（风控状态） | `risk_mgr.save_state()` | 在 journal 对账完成后才保存 |
| `_is_live_confirmed()` | 已有 | 无需修改 |
| `run()` 入口 | 无 journal | 初始化 OrderJournal；检查恢复 |

**新增方法**：

| 方法 | 职责 |
|:---|:---|
| `_run_post_execution_reconciliation()` | 调用 journal 对账和 fail-closed 检查 |

### 6.5 修改文件：`core/futu_adapter.py`

**改动概要**：

| 位置 | 当前 | 修改为 |
|:---|:---|:---|
| `get_positions()` (L242-280) | 异常/错误时返回 `[]` | 异常/错误时返回 `(None, error_msg)` 或 `raise` |
| | | 调用方 `unified_runner.py` 检查 `None` 并中止 |
| 新增 `get_order_list()` | — | 查询 Futu 当日订单列表，供对账使用 |
| 新增 `get_today_filled_orders()` | — | 查询当日已成交订单明细 |

**注意**：`get_positions()` 的返回类型需要从 `List[Dict]` 改为 `Tuple[bool, List[Dict]]` 以传播错误。这是一个不兼容的 API 变更，所有调用方（如 `unified_runner.py`、`fusion_controller.py`）都需要检查。

**备选方案**：改为返回 `(ok: bool, positions: List[Dict])` 元组，`ok=False` 时 positions 仍为 `[]`，但调用方可以区分"查询失败"和"持仓为空"。

---

## 7. Proposed Tests

### 7.1 单元测试（新文件：`tests/smoke/test_order_journal.py`）

| 测试 | 覆盖场景 | 方法 |
|:---|:---|:---|
| 幂等键生成一致性 | 相同输入生成相同 key | 纯函数 |
| 幂等键唯一性 | 不同输入生成不同 key | 纯函数 |
| 重复提交检测 | 相同 key 的 SUBMITTED 记录 → is_duplicate=True | mock filesystem |
| PENDING_SUBMIT 恢复 | 相同 key 的 PENDING 记录 → 允许重试提交 | mock filesystem |
| 终态不重试 | REJECTED/FOK_NOT_FILLED 记录 → is_duplicate=True 但可跳过 | mock filesystem |
| append-only 崩溃安全 | 写入一半时崩溃 → 已写入部分不丢失 | 模拟文件截断 |
| journal 加载 | 空文件 → 返回空列表 | 纯文件 IO |
| journal 加载损坏 | 损坏的 JSONL → 跳过损坏行，不报错退出 | 文件 IO |

### 7.2 单元测试（新文件：`tests/smoke/test_order_state_machine.py`）

| 测试 | 覆盖场景 | 方法 |
|:---|:---|:---|
| 合法状态转换 | INTENT→PENDING→SUBMITTED→FILLED | 纯函数 |
| 非法状态转换 | INTENT→FILLED 应报错 | 纯函数 |
| 部分成交 | SUBMITTED→PARTIAL_FILLED→FILLED | 纯函数 |
| FOK 未成交 | SUBMITTED→FOK_NOT_FILLED | 纯函数 |
| 超时恢复 | SUBMITTED→TIMEOUT→UNKNOWN | 纯函数 |

### 7.3 单元测试（新文件：`tests/smoke/test_stop_loss_confirmation.py`）

| 测试 | 覆盖场景 | 方法 |
|:---|:---|:---|
| 暂存冷却期不持久化 | `confirmed=False` 不写文件 | mock filesystem |
| 成交后确认冷却期 | `confirm_stop()` 持久化冷却期 | mock filesystem |
| 拒单后回滚冷却期 | `rollback_stop()` 移除暂存 | mock filesystem |
| 未确认时重启不激活冷却期 | 进程崩溃后冷却期不存在 | 模拟状态文件 |

### 7.4 集成测试（修改 `tests/smoke/test_live_guardrails.py`）

| 测试 | 覆盖场景 | 方法 |
|:---|:---|:---|
| 重复执行检测 | 同一信号运行两次 → 第二次跳过 | mock + 模拟 journal |
| SELL 未成交不释放资金 | SELL REJECTED → BUY 使用实时现金 | mock adapter |
| 查询失败 fail-closed | `get_positions()` 返回错误 → `run()` 退出 | mock adapter |
| 对账后更新状态 | journal SUBMITTED → 对账 → FILLED | mock Futu API |

### 7.5 集成测试（修改 `tests/smoke/test_live_safety_invariants.py`）

| 测试 | 覆盖场景 | 方法 |
|:---|:---|:---|
| journal 文件不包含 TrdEnv.REAL | 安全扫描 | 文件内容检查 |
| 新 API 方法使用 SIMULATE | `get_order_list()` 使用 `TrdEnv.SIMULATE` | 文件内容检查 |

### 7.6 端到端测试（手动运行）

| 测试 | 覆盖场景 | 执行方式 |
|:---|:---|:---|
| DRY_RUN 模式幂等 | 连续运行两次 → 日志不变 | `python unified_runner.py` x2 |
| 对账流程正确性 | 模拟 LIMIT FOK 未成交 | 手动设置价格偏离 |
| 恢复流程 | 模拟崩溃 → 重新运行 | 手动 kill + restart |

---

## 8. Risks and Open Questions

### 8.1 已识别风险

| 风险 | 影响 | 缓解措施 |
|:---|:---|:---|
| `get_positions()` 返回类型变更影响未知调用方 | 编译错误，需逐文件修复 | 仅修改已知调用方，增加类型检查 |
| `get_order_list()` 和 `get_today_filled_orders()` 依赖 Futu API 支持 | 对账流程受阻 | 调研 Futu API 文档确认（OpenSecTradeContext.order_list_query） |
| SELL→BUY 等待时间（30s）过长影响用户体验 | 扫描器运行时间延长 | 30s 仅在 LIVE 模式触发；DRY_RUN 不等待 |
| journal `output/` 目录文件累积 | 占用磁盘 | 自动删除 7 天前的 `.jsonl` 文件 |
| `record_stop(confirmed=False)` 暂存状态未持久化，进程崩溃后丢失 | 暂存冷却期在崩溃后丢失 | 可接受：未确认的冷却期不应存在，崩溃后恢复时重新检查持仓 |

### 8.2 待验证问题

| 问题 | 说明 | 验证方法 |
|:---|:---|:---|
| Futu API 的 `order_list_query()` 是否支持 SIMULATE 环境 | 对账流程核心依赖 | 阅读 Futu SDK 文档 |
| Futu API 的 `order_list_query()` 返回的 order_id 格式与 `place_order()` 返回的是否一致 | 幂等检查依赖 | 查看 SDK 源码或 API 文档 |
| Futu FOK 失败时的 `ret` 值和 `data` 内容 | REJECTED vs FOK_NOT_FILLED 区分 | 查看 SDK 源码 |
| Futu OpenD 在 SIMULATE 环境下的最小轮询间隔 | 对账轮询避免过载 | 现有经验：建议 3-5s |
| `get_positions()` 返回类型变更是否影响 `fusion_controller.py` 调用 | 确保所有调用方同步更新 | 代码搜索 `get_positions` 使用点 |

### 8.3 未解决的问题（需 Roy 决策）

1. **对账失败时的重试策略**：BUY 提交后对账失败（Futu 查询无响应等），是阻塞等待还是提交由后续运行处理？
   - 建议：**阻塞等待最多 60s**，之后标记 TIMEOUT。
2. **journal 文件清理策略**：保留多久？
   - 建议：7 天自动清理，配置化。
3. **`unified_runner.py` 中 SELL→BUY 依赖关系的 DRY_RUN 行为**：DRY_RUN 模式下是否保持当前估算逻辑？
   - 建议：DRY_RUN 不变，LIVE 改。

---

## 9. Git Status Summary

> **约束**: 本报告为只读分析, 未执行 Git commit、push、merge、reset 或 checkout。

当前 Git 状态（Phase E 之后的基线）：

- `unified_runner.py`: 含 Phase B/C 修改（Live Guardrails, MarketState, 报告生成）
- `core/order_executor.py`: 版本 v1（Phase A 原始版本）
- `core/futu_adapter.py`: 版本 v1（Phase A 原始版本）
- `core/stop_loss.py`: 版本 v1（Phase A 原始版本）
- `tests/smoke/test_live_guardrails.py`: 含 Phase B 测试
- `tests/smoke/test_live_safety_invariants.py`: 含 Phase E 测试

**变更前需确认**：当前工作区无未提交但需要保留的修改。建议在开始 Phase F2-B 实施前进行一次 `git status` 确认。

---

## 10. Confirmation

✅ **本报告为只读分析，未修改任何代码或文件。**  
✅ **未执行任何下单入口或可能连接交易执行路径的测试。**  
✅ **未执行 Git commit、push、merge、reset 或 checkout。**  
✅ **未接入或建议接入 TrdEnv.REAL。**  
✅ **未调整 confidence_v2、Gate 或策略逻辑。**  
✅ **未触碰受保护代码（config.py L-001/L-002、xmm-strategy/、volume_profile.py、fusion_engine.py、fusion_controller.py、chan/）。**  
✅ **未触碰 4 个旧 research untracked 文件。**  

**所有结论均基于代码证据；无法确认的内容已在 8.2（待验证问题）中明确标记。**

---

## 附录 A: 修改文件变更总览

| 文件 | 变更类型 | 行数估计 | 风险等级 |
|:---|:---:|:---:|:---:|
| `core/order_journal.py` | 新增 | ~150 | 🟢 低（全新，不影响现有代码） |
| `core/order_executor.py` | 修改 | ~+100 | 🟡 中（SELL→BUY 排序变化） |
| `core/stop_loss.py` | 修改 | ~+30 | 🟢 低（`confirmed` 参数 + 新增方法） |
| `core/futu_adapter.py` | 修改 | ~+60 | 🟡 中（`get_positions()` 返回类型变更 + 新增方法） |
| `unified_runner.py` | 修改 | ~+80 | 🔴 高（入口逻辑变化，需仔细审查） |
| `tests/smoke/test_order_journal.py` | 新增 | ~120 | 🟢 低 |
| `tests/smoke/test_order_state_machine.py` | 新增 | ~80 | 🟢 低 |
| `tests/smoke/test_stop_loss_confirmation.py` | 新增 | ~80 | 🟢 低 |
| `tests/smoke/test_live_guardrails.py` | 修改 | ~+50 | 🟢 低 |
| `tests/smoke/test_live_safety_invariants.py` | 修改 | ~+20 | 🟢 低 |

**总计**: ~770 行，其中新增 ~430 行，修改 ~340 行。

---

## 附录 B: 分阶段实施计划

### Phase F2-B（核心幂等 + 状态机）

**目标**：建立订单持久化和幂等机制，解决 R-02/R-03/R-04。

**步骤**：
1. 创建 `core/order_journal.py`：JSON Lines 持久化 + 幂等键系统
2. 修改 `core/order_executor.py`：集成 Journal，状态机扩展
3. 新增单元测试
4. CI smoke test 验证
5. DRY_RUN 模式下运行 2 次验证幂等性

**回滚方案**：回滚 `order_executor.py`，删除 `order_journal.py`。journal 文件不影响现有功能。

### Phase F2-C（冷却期确认 + 对账）

**目标**：解决 R-01/R-06/R-07。

**步骤**：
1. 修改 `core/futu_adapter.py`：`get_positions()` fail-closed + 新增对账 API
2. 修改 `core/stop_loss.py`：`confirmed` 参数 + 回滚机制
3. 修改 `unified_runner.py`：对账流程 + fail-closed 检查
4. 排查 `get_positions()` 所有调用方并更新
5. 新增集成测试

**回滚方案**：回滚 `futu_adapter.py`（`get_positions()` 返回类型）、`stop_loss.py`、`unified_runner.py`。

### Phase F2-D（SELL→BUY 依赖）

**目标**：解决 R-05。

**步骤**：
1. 修改 `order_executor.py`：SELL 先执行，LIVE 等待确认
2. 新增 `_wait_for_sell_confirmation()` 方法
3. 修改 `unified_runner.py`：不再用 SELL 估算资金
4. 集成测试验证

**回滚方案**：回滚 `order_executor.py` SELL→BUY 逻辑，保留其他 Phase 的改动。

### 总回滚方案

如果 Phase F2-B/C/D 整体需要回滚：
```
git diff > /tmp/phase_f2_rollback.diff    # 保存当前修改
git checkout -- core/order_executor.py core/stop_loss.py core/futu_adapter.py unified_runner.py
rm -f core/order_journal.py               # 删除新增文件
rm -f tests/smoke/test_order_journal.py test_order_state_machine.py test_stop_loss_confirmation.py
```

journal 文件（`output/*.jsonl`）可删除，不影响核心逻辑。

---

*Phase F2-A 设计审查结束。等待 Codex 批准后开始 Phase F2-B 实施。*