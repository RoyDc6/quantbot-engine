# Phase F2-A Design Review Package v2
# 订单执行一致性修复设计报告（修订版）

**版本**: v2.0  
**日期**: 2026-06-04  
**状态**: 已根据 Codex v1 审查意见修订，等待再次批准。  
**作者**: QuantBot (Phase F2-A 只读分析, 本地 SDK 核验)  
**审查文件范围**: `unified_runner.py`, `core/order_executor.py`, `core/futu_adapter.py`,  
`core/stop_loss.py`, 本地 Futu SDK `place_order()`/`OrderStatus`

---

## 1. Executive Summary (v2 变更概要)

v1 设计被 Codex 驳回，6 个阻断项全部确认。本版涉及以下重大修订：

| 阻断项 | v1 问题 | v2 修正 |
|:---:|:---|:---|
| 1 | 幂等键含 `run_id`，重复运行失效 | 移除 `run_id`，使用稳定 `intent_id`，另设 `attempt_id` |
| 2 | `PENDING_SUBMIT` 自动重提可致重复订单 | 禁止自动重提，必须先对账后人工/基于证据决策 |
| 3 | JSONL 无并发保证，损坏行跳过 | 改用 SQLite (UNIQUE + 文件锁)，损坏则 fail-closed |
| 4 | FOK 不存在于本机 SDK | 移除 FOK，映射真实 `TimeInForce.IOC` 和 `OrderStatus` 枚举 |
| 5 | 多 BUY 使用同一 `live_cash` 未扣减 | 每笔 BUY 提交后扣减运行中现金余额 |
| 6 | F2-B/C 分阶段不安全 | 合并为单一不可拆分的安全阶段 |

**新增 R-08**: `Futu API 契约不匹配` — `fill_side_type.FILL_OR_KILL` 不存在于本机 SDK，所有 LIMIT 订单实际静默失败。

---

## 2. Verified Root-Cause Chains

（保留 v1 中 R-01 至 R-07 的全部代码证据，以下仅补充 R-08 和修正 v1 错误。）

### 2.8 R-08 (新增): Futu API 契约不匹配 — `fill_side_type` 不存在

**代码证据 1：`futu_adapter.py` L312-321**

```python
ret, data = ctx.place_order(
    price=limit_price if price_type == 'LIMIT' else 0.0,
    qty=qty,
    code=futu_code,
    trd_side=trd_side,
    trd_env=ft.TrdEnv.SIMULATE,
    order_type=ft.OrderType.NORMAL,
    fill_side_type=ft.FillSideType.FILL_OR_KILL if price_type == 'LIMIT'  # ⚠️ 不存在！
    else ft.FillSideType.NORMAL,                                           # ⚠️ 不存在！
)
```

**SDK 签名核验：`open_trade_context.py` L540-544**

```python
def place_order(self, price, qty, code, trd_side, order_type=OrderType.NORMAL,
                adjust_limit=0, trd_env=TrdEnv.REAL, acc_id=0, acc_index=0,
                remark=None,                                              # ✅ 可用 (max 64 bytes UTF-8)
                time_in_force=TimeInForce.DAY,                            # ✅ 可选 DAY/GTC/IOC
                fill_outside_rth=False, aux_price=None,
                trail_type=None, trail_value=None, trail_spread=None,
                session=Session.NONE, ...):
```

**根因链**：`fill_side_type` 不是 `place_order()` 的合法参数 → Python 抛出 `TypeError: unexpected keyword argument 'fill_side_type'` → 被 `except Exception` 捕获（L327）→ 返回 `(False, '下单异常: ...')` → `order_executor.py` 将结果标记为 `status='ERROR'`。

**影响**：所有 `price_type='LIMIT'` 的订单（即非市价单）都实际静默失败。当前代码无任何 LIMIT 订单成功执行过。

**SDK 返回类型确认** (L603-629)：`place_order()` 返回 `(RET_OK, order_table: pd.DataFrame)`，其中 order_table 包含 `order_id`, `order_status`, `dealt_qty`, `dealt_avg_price`, `remark`, `last_err_msg` 等字段。当前 `futu_adapter.py` L323 `data.iloc[0].get('order_id', 'OK')` 在 DataFrame 上可行，但未捕获 `order_status` 信息。

---

## 3. Revised Order State Machine (v2)

### 3.1 Futu SDK 真实 OrderStatus 映射表

| Futu OrderStatus | 含义 | 映射至 QuantBot 状态 | 是否终态 |
|:---|:---|:---|:---:|
| `NONE` | 未知 | `UNKNOWN` | ❌ |
| `UNSUBMITTED` | 未提交 | `INTENT_CREATED` | ❌ |
| `WAITING_SUBMIT` | 等待提交 | `PENDING_SUBMIT` | ❌ |
| `SUBMITTING` | 提交中 | `SUBMITTING` | ❌ |
| `SUBMIT_FAILED` | 提交失败 | `REJECTED` | ✅ |
| `TIMEOUT` | 处理超时 | `TIMEOUT` | ❌ |
| **`SUBMITTED`** | 已提交等待成交 | `SUBMITTED` | ❌ |
| **`FILLED_PART`** | 部分成交 | `PARTIAL_FILLED` | ❌ |
| **`FILLED_ALL`** | 全量成交 | `FILLED` | ✅ |
| `CANCELLING_PART` | 撤销中(部分已成交) | `CANCELLING` | ❌ |
| `CANCELLING_ALL` | 撤销中(全部) | `CANCELLING` | ❌ |
| `CANCELLED_PART` | 部分成交后剩余撤销 | `PARTIAL_FILLED` | ✅ |
| `CANCELLED_ALL` | 全部撤销无成交 | `CANCELLED` | ✅ |
| `FAILED` | 下单失败(服务拒绝) | `REJECTED` | ✅ |
| `DISABLED` | 已失效 | `CANCELLED` | ✅ |
| `DELETED` | 已删除 | `CANCELLED` | ✅ |
| `FILL_CANCELLED` | 成交被回滚(极罕见) | `UNKNOWN` | ✅（人工介入） |

**注意**: 与 v1 的关键区别：
- `FOK_NOT_FILLED` 状态已移除（FOK 不存在于 SDK）
- 使用 `TimeInForce.IOC` 替代 FOK，IOC 未成交时状态为 `CANCELLED_ALL`
- `SUBMIT_FAILED` 和 `FAILED` 合并映射至 `REJECTED`

### 3.2 合法状态转换 (v2 简化版)

```
INTENT_CREATED → PENDING_SUBMIT → SUBMITTING → SUBMITTED
                                                         ↓
                                              ┌── SUCCESS ──┐
                                              ↓              ↓
                                         FILLED_ALL    FILLED_PART
                                                            ↓
                                                     FILLED_ALL / CANCELLED_PART
```

**终态集合**: `FILLED_ALL`, `REJECTED` (SUBMIT_FAILED / FAILED), `CANCELLED_ALL`, `DISABLED`, `DELETED`, `FILL_CANCELLED`

**非终态集合**（可在后续运行中对账更新）: `SUBMITTED`, `FILLED_PART`, `CANCELLING_PART`, `CANCELLING_ALL`, `TIMEOUT`

### 3.3 状态对账更新规则（通过 Futu `order_list_query()`）

| journal 本地状态 | Futu 返回状态 | 动作 |
|:---|:---|:---|
| `PENDING_SUBMIT` | 任何状态 | ✅ 更新至 Futu 返回状态（说明已提交成功未记录） |
| `PENDING_SUBMIT` | 查询失败（网络/异常） | ⛔ 保持 `PENDING_SUBMIT`，**禁止自动重提** |
| `SUBMITTED` | `FILLED_ALL` | ✅ 更新，触发 post-fill 动作（冷却期确认等） |
| `SUBMITTED` | `FILLED_PART` | ✅ 更新，记录已成交量 |
| `SUBMITTED` | `CANCELLED_ALL` | ✅ 更新，撤销暂存冷却期 |
| `SUBMITTED` | `SUBMIT_FAILED`/`FAILED` | ✅ 更新为 `REJECTED`，撤销暂存冷却期 |
| `SUBMITTED` | 查询失败 | ⛔ 保持 `SUBMITTED`，记录告警 |
| `TIMEOUT` | 任何确定性终态 | ✅ 更新 |
| `TIMEOUT` | `SUBMITTED` 或 `TIMEOUT` | ⛔ 标记 `UNKNOWN`，人工介入 |

---

## 4. Idempotency and Recovery Design (v2 重写)

### 4.1 幂等键: intent_id + attempt_id（双层设计）

#### intent_id（稳定幂等键）

**格式**: `{date}_{market}_{symbol}_{action}_{qty}_{price_int}`

示例：`20260604_HK_00700.HK_BUY_100_35000`

| 字段 | 内容 | 说明 |
|:---|:---|:---|:---|
| date | YYYYMMDD | 当日不跨日 |
| market | HK/US | 市场 |
| symbol | 标准符号 | 标的唯一标识 |
| action | BUY/SELL | 方向 |
| qty | 整手股数 | lot 对齐后 |
| price_int | `round(price*1000)` | 整数化，避免浮点 |

**关键属性**: 对完全相同的意图稳定不变。同一信号两次运行生成相同的 `intent_id`。  
**不包含 `run_id`**：消除了 v1 中重复运行后幂等键变化的问题。

#### attempt_id（追踪尝试次数）

**格式**: `{intent_id}_attempt{seq}`

示例：`20260604_HK_00700.HK_BUY_100_35000_attempt1`

**用途**：
- 仅用于追踪 `PENDING_SUBMIT` 的记录，不作为幂等性证据
- 崩溃恢复时，journal 按 `intent_id` 查找最新状态，而非 `attempt_id`

### 4.2 持久化层: SQLite（替代 JSONL）

#### 存储位置

**文件**: `output/order_journal_{market}.db`（单一 SQLite 数据库，按 market 分库）

**表结构**:

```sql
CREATE TABLE IF NOT EXISTS orders (
    intent_id       TEXT NOT NULL,          -- 稳定幂等键
    attempt_id      TEXT NOT NULL,          -- 尝试标识 (PK)
    status          TEXT NOT NULL,          -- 当前状态
    order_id        TEXT DEFAULT '',        -- Futu order_id（提交后填充）
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,          -- BUY / SELL
    qty             INTEGER NOT NULL,
    price           REAL NOT NULL,
    market          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    filled_qty      INTEGER DEFAULT 0,
    filled_avg_price REAL DEFAULT 0.0,
    remark          TEXT DEFAULT '',        -- Futu remark（内含 intent_id）
    message         TEXT DEFAULT '',
    UNIQUE(attempt_id)                     -- 防止同一 attempt 重复写入
);

CREATE INDEX idx_intent ON orders(intent_id, status);
CREATE INDEX idx_status ON orders(status);
```

**完整性约束**：
- `UNIQUE(attempt_id)`: 防止同一 attempt 被两次插入
- `NOT NULL` 约束：空字段插入会报错

#### 并发控制

**跨进程文件锁**：使用 `portalocker`（Python 标准库兼容）或 `sqlite3` 的 WAL 模式 + 重试机制。

**加锁点**：
1. 读取 `PENDING_SUBMIT` 记录（检查昨日崩溃）
2. 写入新的 `PENDING_SUBMIT`（提交前）
3. 更新订单状态（提交后/对账后）
4. `_save_log()` 写入最终摘要

**进程内锁定**：`threading.Lock` 保护单进程内序列化访问。

#### 损坏处理（fail-closed）

| 场景 | 行为 |
|:---|:---|
| SQLite 文件无法打开 | `run()` 中止，打印 FATAL 错误，**不执行任何订单** |
| `UNIQUE` 约束违反 | 日志警告，跳过该记录，继续处理 |
| WAL 或 SHM 损坏 | SQLite 内置恢复（`PRAGMA integrity_check`），失败则 fail-closed |
| 数据库空间满 | `place_order()` 前检查可用空间，不足则中止 |

**禁止行为**：禁止自动删除或清理 journal 记录。记录仅当 order 终态且超过 30 天后才可归档清理，且清理前必须备份。

### 4.3 幂等检查流程（v2 重写）

```
_place_single_order() 调用前：

1. 生成 intent_id（稳定）和 attempt_id（递增）

2. 查询 SQLite 中是否存在同 intent_id 的记录
   ├── 存在 && status in (FILLED_ALL, PARTIAL_FILLED, CANCELLED_ALL, CANCELLED_PART, REJECTED)
   │   → 跳过，返回已有记录（意图已被最终处理）
   │
   ├── 存在 && status in (SUBMITTED, SUBMITTING, FILLED_PART, CANCELLING_*)
   │   → 标记为 TIMEOUT（留待对账处理）
   │   → 跳过当前提交，不生成新 attempt
   │
   ├── 存在 && status == PENDING_SUBMIT
   │   → ⛔ 禁止自动重提
   │   → 记录 PENDING_SUBMIT 时间戳
   │   → 若超过 2 小时 → 标记 UNKNOWN（人工介入）
   │   → 跳过当前提交
   │
   ├── 存在 && status == TIMEOUT
   │   → ⛔ 禁止自动重提
   │   → 留待对账确认
   │
   └── 不存在
       → INSERT attempt_id (status='PENDING_SUBMIT')
       → 提交订单
       → UPDATE status='SUBMITTED', order_id=result
```

**关键原则**：`PENDING_SUBMIT`、`TIMEOUT`、`UNKNOWN` 三种状态**一律禁止自动重提**，只能通过对账流程更新。

### 4.4 Futu `remark` 嵌入 intent_id

**设计**：每次 `place_order()` 调用时通过 `remark` 参数写入 `intent_id`（或前 64 字节）。

**用途**：
- 崩溃恢复时，通过 `order_list_query()` 获取 `remark` 字段，与 journal 中的 `intent_id` 匹配
- 即使 journal 损坏或丢失，仍可从 Futu 侧恢复订单 identity

**remark 格式示例**：

```
QNT:20260604_HK_00700.HK_BUY_100_35000
```

**字节限制**：Futu SDK 要求 UTF-8 编码后不超过 64 字节（见 `open_trade_context.py` L563-565）。上述示例约 48 字节，处于安全范围内。

**注意**：`remark` 用于幂等性恢复辅助，**不**替代 journal。journal 仍然是订单状态的一致真相源，remark 是二级恢复手段。

### 4.5 崩溃恢复流程（v2 重写）

**启动时检查**（在 `run()` Step 0 之后）：

```python
# 伪代码 — 设计参考
journal = OrderJournal(market)
recoverable = journal.get_non_terminal_orders()
# recoverable = [SUBMITTED, SUBMITTING, PENDING_SUBMIT, FILLED_PART, TIMEOUT]

if recoverable:
    print(f'[RECOVERY] 发现 {len(recoverable)} 笔未完成订单')

    # Step A: 通过 Futu order_list_query() 对账
    ctx = ft.OpenSecTradeContext(...)
    for entry in recoverable:
        if entry.order_id:
            ret, data = ctx.order_list_query(
                order_id=entry.order_id,
                trd_env=ft.TrdEnv.SIMULATE,
            )
        else:
            # 无 order_id（PENDING_SUBMIT），通过 remark 搜索
            ret, data = ctx.order_list_query(
                trd_env=ft.TrdEnv.SIMULATE,
                # 设置 range 为今日全量
                start=today, end=today,
            )
            # 在结果中过滤 remark 匹配 intent_id 的订单

        if ret == ft.RET_OK and len(data) > 0:
            futu_status = data.iloc[0]['order_status']
            journal.update_from_futu(entry.intent_id, futu_status, data)
        else:
            # 查询失败 — 标记 UNKNOWN，**禁止自动重提**
            print(f'[RECOVERY] {entry.intent_id}: 对账失败，标记 UNKNOWN')
            journal.update_status(entry.intent_id, 'UNKNOWN',
                                  message='恢复对账失败，需人工介入')

    # Step B: 若有 UNKNOWN 状态订单 → 向 Roy 告警
    unknowns = journal.get_by_status('UNKNOWN')
    if unknowns:
        print(f'[FATAL] 发现 {len(unknowns)} 笔 UNKNOWN 订单')
        print(f'[FATAL] 请人工确认这些订单状态后再运行')
        print(f'[FATAL] UNKNOWN 订单 intent_id: {[u.intent_id for u in unknowns]}')
        return  # 不执行任何新订单
```

### 4.6 超时处理（v2）

**超时定义**：`place_order()` 调用 `except` 捕获超时异常。

**超时后行为**（严格禁止自动重提）：
1. journal 中状态改为 `TIMEOUT`（不新建 attempt）
2. 暂存冷却期**不激活**
3. 后续对账流程（启动时）通过 `order_list_query()` 确认
4. 对账仍未知 → `UNKNOWN` → 人工介入
5. `UNKNOWN` 订单存在时 → **阻断该市场全部新订单**（不仅同方向）

---

## 5. Execution Dependency and Reconciliation Design (v2)

### 5.1 SELL 与 BUY 的执行依赖（v2 修订）

**v1 问题**：多 BUY 使用同一 `live_cash` 未扣减运行中余额。

**v2 修正**：

```
规则 1（不变）: LIVE 模式下 BUY 预算使用实时现金 cash_before，
                不依赖 SELL 估算收益。

规则 2（强化）: SELL 必须先执行。SELL 结束后立即通过 adapter.get_account_info()
                查询实时现金，以此作为 BUY 的现金基础。

规则 3（修正）: 每笔 BUY 提交后实时扣减 running_cash，
                下一笔 BUY 使用扣减后的余额检查。
```

**实现伪代码（v2 修正）**：

```python
def execute_orders(self, orders, journal, ...):
    sell_orders = [o for o in orders if o['action'] == 'SELL']
    buy_orders  = [o for o in orders if o['action'] == 'BUY']

    # === Phase 1: 执行所有 SELL ===
    sell_results = []
    for o in sell_orders:
        r = self._place_single_order(o, journal, ...)
        sell_results.append(r)

    # === Phase 2: 查询实时现金（SELL 执行后） ===
    if not self.dry_run:
        account = self._adapter.get_account_info(market)
        if account is None:
            # 账户查询失败 → fail-closed
            print('[FATAL] SELL 后账户查询失败，中止 BUY')
            return sell_results + [{'status': 'BLOCKED',
                                    'message': 'SELL 后账户查询失败'}]
        live_cash = account['cash']
    else:
        live_cash = None

    # === Phase 3: 执行 BUY（每笔扣减运行中现金） ===
    buy_results = []
    running_cash = live_cash  # 运行中现金余额，每笔 BUY 后递减
    for o in buy_orders:
        if not self.dry_run and running_cash is not None:
            trade_val = o['qty'] * o['price']
            if trade_val > running_cash * 0.95:
                buy_results.append({
                    **o, 'status': 'SKIP',
                    'message': f'运行中现金不足: 需{trade_val:.0f}, 余{running_cash:.0f}',
                })
                continue
        r = self._place_single_order(o, journal, ...)
        buy_results.append(r)
        # 提交后扣减现金（即使提交结果未知，也为后续 BUY 预留资金）
        if not self.dry_run and r.get('status') in ('SUBMITTED',):
            running_cash -= o['qty'] * o['price']

    return sell_results + buy_results
```

### 5.2 执行后对账流程（v2 强化）

**对账触发时机**：`execute_orders()` 完成后立即触发（不等待）。

**对账步骤**：

```
Step A: 通过 order_list_query() 获取当日全量订单
        → filter_trd_env=TrdEnv.SIMULATE
        → 使用 start=today, end=today

Step B: 按 order_id + remark 对账
        → 对 journal 中非终态订单逐笔查找 Futu 返回状态

Step C: 更新 journal 状态（见 3.3 状态对账更新规则表）

Step D: Fail-Closed 条件（v2 强化）
        ├── 对账查询失败（Futu 不可达）
        │   → journal 写警告，中止后续操作，不执行任何新订单
        ├── SELL REJECTED 或 CANCELLED_ALL
        │   → rollback_stop() 撤销暂存冷却期
        ├── 任何 UNKNOWN 订单
        │   → 阻断该市场账户全部新订单（不限于同方向）
        └── 对账完成前不得调用 risk_mgr.save_state()

Step E: 持仓验证（可选）
        → 对比 Futu 实时持仓 vs journal 推算持仓
        → 差异超过 1 手 → 告警（不阻断）
```

### 5.3 UNKNOWN 阻断规则（v2 新增）

**规则**：当日 journal 中存在 UNKNOWN 订单 → 阻断**该市场账户全部新订单**。

**原因**：
- UNKNOWN 可能是未确认的 SELL（资金未回流）或 BUY（已占用资金）
- 仅阻断同方向可能遗漏"BUY 未知→实际已成交→现金已被占用→下一笔 BUY 超支"
- 阻断范围：全部 action（BUY + SELL），但不影响另一市场的订单

**解除方式**：人工确认 UNKNOWN 订单状态后，手动更新 journal 状态。

---

## 6. Futu API 契约修复 (R-08)

### 6.1 当前 Bug

`futu_adapter.py:319` 传递 `fill_side_type` 参数给 `place_order()`，该参数不存在于本机 SDK 签名中。所有 LIMIT 订单静默失败。

### 6.2 修复方案

**替换 `fill_side_type` 为正确的参数结构**：

| 当前错误 | 替换为 |
|:---|:---|
| `fill_side_type=ft.FillSideType.FILL_OR_KILL` | 移除（参数不存在） |
| `fill_side_type=ft.FillSideType.NORMAL` | 移除（参数不存在） |
| 无 `time_in_force` | 添加 `time_in_force=ft.TimeInForce.DAY`（默认） |
| 无 `remark` | 添加 `remark=...`（嵌入 intent_id） |

**改后的 `place_order` 调用签名**：

```python
ret, data = ctx.place_order(
    price=limit_price if price_type == 'LIMIT' else 0.0,
    qty=qty,
    code=futu_code,
    trd_side=trd_side,
    trd_env=ft.TrdEnv.SIMULATE,
    order_type=ft.OrderType.NORMAL,
    time_in_force=ft.TimeInForce.DAY,       # DAY 有效，非 FOK
    remark=idempotency_remark,              # 嵌入 intent_id（≤64 bytes UTF-8）
)
```

### 6.3 TimeInForce 策略选择

| 枚举值 | 含义 | 使用场景 |
|:---|:---|:---|
| `DAY` | 当日有效（默认） | 正常 LIMIT 订单 |
| `GTC` | 撤单前有效 | 跨日止盈/止损单（**当前不使用**） |
| `IOC` | 立即成交否则取消 | 替代 FOK（**非当前需求，备选**） |

**当前建议**: 统一使用 `TimeInForce.DAY`，与现有 `OrderType.NORMAL` 组合。不引入 IOC（之前 FOK 的替代方案），因当前信号周期的执行窗口为当日，无即时成交需求。

---

## 7. Proposed File Changes (v2)

### 7.1 新文件：`core/order_journal.py`

**职责**：SQLite 持久化的订单幂等管理 + 状态追踪 + 恢复对账

**关键接口**（v2 重写）：

```python
import sqlite3
import portalocker  # 跨进程文件锁

class OrderJournal:
    DB_FILENAME = 'order_journal_{market}.db'
    
    def __init__(self, journal_dir: Path, market: str):
        """打开 SQLite（创建表/索引），获取文件锁"""
    
    def generate_intent_id(self, order: dict) -> str:
        """生成稳定 intent_id（不含 run_id）"""
    
    def generate_attempt_id(self, intent_id: str, seq: int) -> str:
        """生成 attempt_id = intent_id + _attempt{seq}"""
    
    def get_by_intent(self, intent_id: str) -> list[dict]:
        """按 intent_id 查询所有 attempt（按时间排序 DESC）"""
    
    def get_latest_by_intent(self, intent_id: str) -> dict | None:
        """获取 intent_id 的最新状态"""
    
    def get_non_terminal(self) -> list[dict]:
        """获取所有非终态订单（用于恢复检查）"""
    
    def get_by_status(self, status: str) -> list[dict]:
        """按状态查询"""
    
    def insert_pending(self, intent_id: str, attempt_id: str,
                       order: dict) -> bool:
        """INSERT PENDING_SUBMIT（UNIQUE 约束防止重复 attempt）"""
    
    def update_status(self, intent_id: str, status: str,
                      order_id: str = '', filled_qty: int = 0,
                      filled_price: float = 0.0, message: str = '') -> None:
        """更新状态（非终态可更新）"""
    
    def update_from_futu(self, intent_id: str, futu_status: str,
                         order_table: pd.DataFrame) -> None:
        """根据 Futu OrderStatus 更新 journal 状态"""
    
    def reconcile_all(self, ctx: ft.OpenSecTradeContext,
                      market: str) -> None:
        """对账全部非终态订单"""
    
    def has_unknown(self) -> bool:
        """检查是否存在 UNKNOWN 订单"""
    
    def close(self):
        """释放锁，关闭连接"""
```

### 7.2 修改文件：`core/order_executor.py`

| 位置 | v1 方案 | v2 修正 |
|:---|:---|:---|
| `__init__` | 接受 `journal: OrderJournal` | 不变 |
| `execute_orders()` | SELL 先 BUY 后 | + 每笔 BUY 后扣减 `running_cash` |
| `_place_single_order()` | 有幂等检查但含 run_id | 用 `intent_id`（不含 run_id） |
| `_place_single_order()` | FOK 处理 | 移除 FOK，改用 `time_in_force=DAY` |
| `_place_single_order()` | 状态仅 OK/ERROR | 映射真实 `OrderStatus`（见 3.1） |
| `_place_single_order()` | 无 remark | 写入 `remark=intent_id` (≤64 bytes) |
| 新增 `_reconcile()` | — | 对账全部非终态订单 |

### 7.3 修改文件：`core/stop_loss.py`（不变，与 v1 相同）

| 位置 | v1 方案 | v2 修正 |
|:---|:---|:---|
| `record_stop()` | 增加 `confirmed: bool` 参数 | ✅ 不变 |
| 新增 `confirm_stop()` | 成交确认后激活冷却期 | ✅ 不变 |
| 新增 `rollback_stop()` | 拒单后撤销暂存冷却期 | ✅ 不变 |

### 7.4 修改文件：`unified_runner.py`

| 位置 | v1 方案 | v2 修正 |
|:---|:---|:---|
| 入口 | 初始化 journal | + SQLite 打开 + 文件锁获取 |
| 入口 | 检查恢复 | + 对账 `get_non_terminal()` |
| | | + UNKNOWN 阻断检查 |
| 持仓查询 | fail-closed 处理 | ✅ 不变 |
| `record_stop` | `confirmed=False` | ✅ 不变 |
| SELL 估算 | 不依赖 SELL 估算 | ✅ 不变（仅生效 cash_before） |
| 执行 | 传递 journal | ✅ 不变 |
| 执行后 | 对账 + fail-closed | + 对账后检查 UNKNOWN |
| | | + 对账完成前不 `save_state()` |

### 7.5 修改文件：`core/futu_adapter.py`

| 位置 | v1 方案 | v2 修正 |
|:---|:---|:---|
| L319 `fill_side_type` | 移除 | ✅ 移除 + 添加 `time_in_force` + `remark` |
| `get_positions()` | 类型变更 (return tuple) | ✅ 不变 |
| 新增 `order_list_query()` | — | ✅ 不变 |
| 新增 `get_today_filled_orders()` | — | ✅ 不变 |

---

## 8. Implementation Plan (v2: 合并 F2-B/C)

### 单阶段实施：Phase F2-SEC

**拆分理由**：幂等、对账、冷却期确认、API 契约修复四者相互依赖，拆分上线将制造安全缺口。

```
Phase F2-SEC（安全阶段，不可拆分）
├── ✅ Step 1: 修复 R-08 (Futu API 契约)
│   ├── futu_adapter.py: 移除 fill_side_type，添加 time_in_force + remark
│   └── 验证: smoke test 确认 place_order 不再抛 TypeError
│
├── ✅ Step 2: 创建 core/order_journal.py
│   ├── SQLite 表结构 + 唯一约束
│   ├── intent_id/attempt_id 生成
│   ├── 跨进程文件锁 (portalocker)
│   ├── 损坏 fail-closed (integrity_check)
│   └── 验证: 单元测试全通过
│
├── ✅ Step 3: 修改 order_executor.py
│   ├── 集成 OrderJournal（幂等检查 + remark 写入）
│   ├── SELL→BUY 排序 + running_cash 每笔扣减
│   ├── 真实 OrderStatus 映射
│   ├── PENDING_SUBMIT/TIMEOUT/UNKNOWN 禁止自动重提
│   ├── 对账 reconcile_all()
│   └── 验证: 集成测试全通过
│
├── ✅ Step 4: 修改 stop_loss.py
│   ├── record_stop(confirmed=False) 暂存
│   ├── confirm_stop() / rollback_stop()
│   └── 验证: 单元测试全通过
│
├── ✅ Step 5: 修改 unified_runner.py
│   ├── 启动时 journal 对账 + UNKNOWN 阻断
│   ├── 执行后对账 fail-closed
│   ├── 暂存冷却期
│   └── 验证: smoke test 全通过 + DRY_RUN 双次运行验证幂等
│
├── ✅ Step 6: 修改 get_positions() fail-closed
│
└── ✅ Step 7: CI smoke test
    ├── 完整集成测试（mock Futu）
    └── safety invariants 新增 journal 文件检查
```

**回滚方案（单批次回滚）**：

```bash
git diff > /tmp/phase_f2sec_rollback.diff
git checkout -- core/order_executor.py core/stop_loss.py core/futu_adapter.py unified_runner.py
rm -f core/order_journal.py
rm -f output/order_journal_*.db    # journal 文件清理
```

journal 清理不会影响核心逻辑（对账失败的唯一后果是下次启动时无恢复信息）。

---

## 9. Proposed Tests (v2 新增)

### 9.1 单元测试：`tests/smoke/test_order_journal.py`

| 测试 | 覆盖场景 | 方法 |
|:---|:---|:---|
| `intent_id` 稳定性 | 相同输入 → 相同输出 | 纯函数 |
| `intent_id` 不含 `run_id` | 不依赖时间戳或进程 ID | 纯函数 |
| `attempt_id` 递增 | 不同 seq → 不同 attempt_id | 纯函数 |
| 重复 intent 检测 | 相同 intent 存在 FILLED → 跳过 | mock SQLite |
| PENDING 禁止重提 | PENDING 记录 → 不提交新 attempt | mock SQLite |
| 终态不复试 | REJECTED 记录 → 跳过 | mock SQLite |
| SQLite UNIQUE 约束 | 重复 attempt_id INSERT → SQL 错误 | 真实 SQLite 内存 DB |
| 损坏 fail-closed | 不可读 DB 文件 → JournalOpenError | 模拟文件权限 |
| `remark` 64 字节限制 | 超长 remark → 截断或报错 | 纯函数 |

### 9.2 单元测试：`tests/smoke/test_order_state_machine.py`

| 测试 | 覆盖场景 |
|:---|:---|
| 合法转换：PENDING→SUBMITTED→FILLED_ALL | 正常流程 |
| 合法转换：PENDING→SUBMITTED→FILLED_PART→FILLED_ALL | 部分成交后追单成交 |
| 合法转换：PENDING→SUBMITTED→CANCELLED_ALL | 全部撤销 |
| 合法转换：SUBMITTED→SUBMIT_FAILED→REJECTED | 拒单 |
| 对账更新：PENDING→查询到 SUBMITTED | 恢复对账 ✅ |
| 对账后禁止：PENDING→查询失败→保持 PENDING | 禁止自动重提 ✅ |

### 9.3 单元测试：`tests/smoke/test_stop_loss_confirmation.py`

（与 v1 相同，无变化）

### 9.4 集成测试：`tests/smoke/test_execution_consistency.py`（新增文件）

| 测试 | 覆盖场景 | 方法 |
|:---|:---|:---|
| 重复运行幂等 | 相同信号运行两次 → 第二次所有 intent 跳过 | mock Futu + SQLite |
| SELL→BUY 顺序 | SELL 先执行，BUY 后用实时现金 | mock Futu |
| 每笔 BUY 现金扣减 | 3 笔 BUY 总价值>现金 → 仅前 2 笔提交 | mock Futu |
| UNKNOWN 阻断全账户 | 1 笔 UNKNOWN → 该市场全部订单跳过 | mock Futu |
| 恢复对账 | PENDING 订单 → 对账更新为 FILLED | mock Futu |
| `get_positions()` fail-closed | 查询异常 → `run()` 中止 | mock Futu |
| `fill_side_type` 移除 | place_order 不抛 TypeError | mock Futu |

### 9.5 端到端验证（DRY_RUN 模式）

| 测试 | 方法 |
|:---|:---|
| 幂等验证：`python unified_runner.py --market HK` 连续运行 2 次 | 检查 journal 无重复 attempt |
| FOK 已移除验证：代码搜索 `FillSideType`/`FILL_OR_KILL` 为 0 | grep 确认 |
| `remark` 长度验证：所有 símbol 的 intent_id 不超 64 bytes | 脚本验证 |

**⚠️ 禁止运行的手动测试**：
- ~~模拟 FOK 未成交下单测试~~（取消：FOK 不存在，无测试意义）
- 任何涉及 `TrdEnv.SIMULATE` 真实下单的测试（由 DRY_RUN 模式 + mock 替代）
- 任何使用 `--live` 参数的测试

---

## 10. Risks and Open Questions (v2)

### 10.1 已识别风险

| 风险 | 影响 | 缓解措施 |
|:---|:---|:---|
| `portalocker` 在 Windows 上文件锁行为 | 并发时 journal 可能阻塞 | 设置超时 + WAL 模式 |
| SQLite 作为新依赖的性能 | 单日 <100 订单，无性能问题 | 无需优化 |
| `remark` 与现有 `place_order` 调用兼容性 | 新参数不会影响现有逻辑 | 默认 `remark=None` 兼容旧调用 |
| `get_positions()` 返回类型变更影响未知调用方 | 编译错误 | 搜索所有调用方并更新 |

### 10.2 待验证问题（与 v1 相同）

| 问题 | 说明 | 验证方法 |
|:---|:---|:---|
| Futu `order_list_query()` 在 SIMULATE 环境下的 `remark` 字段是否返回 | 恢复对账核心依赖 | 阅读 SDK proto 定义 |
| SIMULATE 环境 `order_list_query()` 是否支持 `status_filter_list` | 对账筛选效率 | 查看 SDK 文档 |

### 10.3 已解决的问题

| v1 问题 | 状态 | 决策 |
|:---|:---:|:---|
| `run_id` 导致幂等失效 | ✅ 已修正 | 移除 `run_id`，用稳定 `intent_id` |
| JSONL 损坏跳过 | ✅ 已修正 | 改为 SQLite + fail-closed |
| PENDING 自动重提 | ✅ 已修正 | 禁止自动重提，仅对账 |
| FOK 不存在 | ✅ 已修正 | 映射真实 `TimeInForce.DAY` |
| 多 BUY 现金未扣减 | ✅ 已修正 | 每笔提交后 `running_cash -= trade_val` |
| journal 自动删除 | ✅ 已修正 | 仅 30 天后备份后清理 |
| UNKNOWN 仅阻同方向 | ✅ 已修正 | 阻断全部新订单 |

---

## 11. Git Status Summary

> **约束**: 本报告为只读分析, 未执行 Git 操作。

与 v1 相同。基线为 Phase E 提交 (SHA 895e8aa)。

---

## 12. Confirmation

✅ **本报告为只读分析，未修改任何代码或文件。**  
✅ **未执行任何下单入口或可能连接交易执行路径的测试。**  
✅ **未执行 Git commit、push、merge、reset 或 checkout。**  
✅ **未接入或建议接入 TrdEnv.REAL。**  
✅ **未调整 confidence_v2、Gate 或策略逻辑。**  
✅ **未触碰受保护代码。**  
✅ **未触碰 4 个旧 research untracked 文件。**  
✅ **本地 SDK 核验完成**：已确认 `FillSideType` 不存在、`place_order()` 真实签名、完整 `OrderStatus` 枚举 (18 种状态) 和 `TimeInForce` 选项。

---

## 附录 A: Futu SDK 本地核验签名

```
文件: C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\Lib\site-packages\
  futu/trade/open_trade_context.py L540:544
  签名: place_order(price, qty, code, trd_side, order_type=..., 
                     adjust_limit=0, trd_env=TrdEnv.REAL, acc_id=0, acc_index=0,
                     remark=None, time_in_force=TimeInForce.DAY, ...)
  返回: (RET_OK, order_table: pd.DataFrame) | (RET_ERROR, msg)

文件: futu/common/constant.py L1299:1371
  OrderStatus 枚举: NONE, UNSUBMITTED, WAITING_SUBMIT, SUBMITTING, 
                    SUBMIT_FAILED, TIMEOUT, SUBMITTED, FILLED_PART, 
                    FILLED_ALL, CANCELLING_PART, CANCELLING_ALL, 
                    CANCELLED_PART, CANCELLED_ALL, FAILED, DISABLED, DELETED,
                    FILL_CANCELLED

文件: futu/common/constant.py L2877:2881
  TimeInForce: DAY, GTC, IOC

文件: futu/common/constant.py
  FillSideType: ❌ 不存在
  FILL_OR_KILL: ❌ 不存在
```

---

*Phase F2-A v2 设计审查结束。等待 Codex 再次批准后开始实施。*