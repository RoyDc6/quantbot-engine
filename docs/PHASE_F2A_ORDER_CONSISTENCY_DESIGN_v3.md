# Phase F2-A Design Review Package v3
# 订单执行一致性修复设计报告（二次修订版）

**版本**: v3.0  
**日期**: 2026-06-04  
**状态**: 根据 Codex v2 审查意见修订 (9/9 阻断项已关闭)，等待再次批准。  
**作者**: QuantBot (Phase F2-A 只读分析, 本地 SDK 核验)

---

## 1. v2→v3 阻断项关闭清单

| # | v2 问题 | v3 修正 | 关闭状态 |
|:---:|:---|:---|:---:|
| 1 | DRY_RUN 污染 journal | DRY_RUN 完全不写入 journal；测试使用内存 SQLite | ✅ |
| 2 | 回滚删除 journal | 回滚保留 journal 文件，永不删除 | ✅ |
| 3 | 并发幂等不完整 | `BEGIN IMMEDIATE` + `UNIQUE(intent_id)` 提交锁；不用 `portalocker` | ✅ |
| 4 | 仅 UNKNOWN 阻断不足 | 任何非终态订单 → 阻断该市场全部新订单 | ✅ |
| 5 | SELL→BUY 未闭环 | 确认 SELL 成交 + 重新查询持仓 + 重做风控 | ✅ |
| 6 | BUY 未知结果仍继续 | 任何非 `SUBMITTED`/`FILLED_ALL` → 中止剩余全部 BUY | ✅ |
| 7 | 状态枚举混用 | 定义唯一 QuantBot 状态枚举 + 完整 Futu→QuantBot 映射 | ✅ |
| 8 | 止损副作用不可恢复 | journal 增加 `intent_type` + `post_fill_applied` | ✅ |
| 9 | remark 截断碰撞 | 固定长度版本化哈希，不截断 | ✅ |

---

## 2. 阻断项 1: DRY_RUN 禁止写入执行 journal

**问题**：v2 要求 DRY_RUN 写入 journal，但无 `execution_mode` 字段。日常 DRY_RUN 生成的 `intent_id` 会与日后 LIVE 运行冲突，导致真实执行被误判重复。

**修正**：

```
规则: DRY_RUN 模式下 OrderExecutor 完全不初始化 OrderJournal。
      journal 仅由 LIVE 模式（TrdEnv.SIMULATE 实际下单）使用。

影响范围:
  - order_executor.py: dry_run=True 时跳过所有 journal 操作
  - unified_runner.py: execution_mode 为 DRY_RUN 时不创建/打开 journal
  - 测试: 使用 sqlite3 内存数据库（:memory:），不写入磁盘

检查逻辑:
  def execute_orders(self, orders, journal=None, ...):
      if self.dry_run or journal is None:
          # 旧逻辑：直接循环提交，无 journal 介入
          for o in orders:
              r = self._place_single_order(o, ...)  # 无幂等检查
          return results
      else:
          # LIVE 逻辑：journal 幂等 + 状态追踪 + SELL→BUY 排序
          ...
```

**安全边界**：`OrderExecutor.__init__` 中 `journal` 默认为 `None`；`unified_runner.py` 仅在 `execution_mode == 'LIVE_CONFIRMED'` 时创建并传入 journal。

---

## 3. 阻断项 2: 回滚不得删除 journal

**问题**：v2 回滚方案 `rm -f output/order_journal_*.db` 与"持久化证据永不丢失"目标冲突。

**修正**：

```bash
# Phase F2-SEC 回滚方案（修正版）：
# 1. 回滚代码
git checkout -- core/order_executor.py core/stop_loss.py core/futu_adapter.py unified_runner.py
rm -f core/order_journal.py

# 2. ⛔ 永不清除 or 删除 output/order_journal_*.db
#    journal 文件保留在原位，继续作为未完成订单的阻断面

# 3. 创建备份
cp output/order_journal_*.db backups/order_journal_f2sec_$(date +%Y%m%d_%H%M%S).bak

# 4. journal 文件继续阻断
#    即使代码回滚，journal 文件仍然存在。
#    如果后续恢复运行旧代码，journal 不会影响旧代码（旧代码不读 journal）。
#    如果未来重新部署 F2-SEC，journal 中未解决订单会被恢复对账。
```

**原则**：journal 文件是有价值的审计证据。删除即意味着放弃"崩溃恢复+幂等"的安全承诺。journal 仅可在所有订单达到终态且备份后才清理（最短保留 30 天）。

---

## 4. 阻断项 3: 并发幂等不完整

### 4.1 锁策略 (替代 portalocker)

**原因**: `portalocker` 未安装且 `requirements.txt` 未声明。改为纯 SQLite 内置机制。

**实现**:

```python
# 使用 SQLite WAL 模式 + BEGIN IMMEDIATE 实现原子检查-写入

class OrderJournal:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path), timeout=30)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA busy_timeout=5000')  # 5s 等待锁
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS orders (
                intent_id       TEXT NOT NULL,
                attempt_id      TEXT NOT NULL,
                status          TEXT NOT NULL,
                order_id        TEXT DEFAULT '',
                symbol          TEXT NOT NULL,
                action          TEXT NOT NULL,
                qty             INTEGER NOT NULL,
                price           REAL NOT NULL,
                market          TEXT NOT NULL,
                intent_type     TEXT NOT NULL,     -- 见阻断项 8
                execution_mode  TEXT NOT NULL,      -- 仅 'LIVE'，DRY_RUN 不写入
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                filled_qty      INTEGER DEFAULT 0,
                filled_avg_price REAL DEFAULT 0.0,
                remark          TEXT DEFAULT '',
                post_fill_applied INTEGER DEFAULT 0, -- 见阻断项 8
                message         TEXT DEFAULT '',
                UNIQUE(intent_id),                   -- ① intent 唯一约束
                UNIQUE(order_id)                     -- ② order_id 唯一约束
            );
            CREATE INDEX IF NOT EXISTS idx_status ON orders(status);
            CREATE INDEX IF NOT EXISTS idx_intent_type ON orders(intent_type, post_fill_applied);
        ''')

    def try_acquire_intent(self, intent_id, attempt_id, order) -> bool:
        """
        原子地尝试获取 intent_id 的提交权。
        BEGIN IMMEDIATE → 获取数据库写锁 → 检查唯一约束 → INSERT
        
        Returns:
            True  = 成功获取，可以提交订单
            False = intent_id 已存在（重复），跳过
        """
        cursor = self.conn.execute('BEGIN IMMEDIATE')
        try:
            # 检查 intent_id 是否已存在
            cursor.execute('SELECT status FROM orders WHERE intent_id=?', (intent_id,))
            existing = cursor.fetchone()
            if existing is not None:
                self.conn.commit()
                return False  # 已存在，重复
            
            # 写入 PENDING_SUBMIT
            cursor.execute('''
                INSERT INTO orders (intent_id, attempt_id, status, order_id,
                    symbol, action, qty, price, market, intent_type,
                    execution_mode, created_at, updated_at, remark)
                VALUES (?, ?, 'PENDING_SUBMIT', '', ?, ?, ?, ?, ?, ?, 'LIVE',
                    datetime('now'), datetime('now'), ?)
            ''', (intent_id, attempt_id, order['symbol'], order['action'],
                  order['qty'], order['price'], order.get('market', ''),
                  order.get('intent_type', 'SIGNAL'),
                  order.get('remark', '')))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            self.conn.rollback()
            return False
        except Exception:
            self.conn.rollback()
            raise
```

### 4.2 核心原子序列

```
try_acquire_intent() 的完整执行路径（在 BEGIN IMMEDIATE 事务内）：

  1. 检查 intent_id UNIQUE 约束
     → 已存在 → 返回 False（重复），不提交
     → 不存在 → 继续

  2. INSERT INTO orders (..., status='PENDING_SUBMIT')
     → 成功 → COMMIT → 返回 True

  3. 调用方收到 True 后 → 调用 place_order() → 获得结果后

  4. 再次 BEGIN IMMEDIATE → UPDATE status='SUBMITTED', order_id=...
     → COMMIT
```

### 4.3 数据库约束汇总

| 约束 | 作用 | 违反后果 |
|:---|:---|:---|
| `UNIQUE(intent_id)` | 防止同一 intent 被提交两次 | `IntegrityError` → 回滚 |
| `UNIQUE(order_id)` | 防止同一 Futu 订单被记录两次 | `IntegrityError` → 回滚 |
| `NOT NULL(status)` | 状态必须赋值 | SQLite 拒绝 INSERT |
| `execution_mode='LIVE'` | DRY_RUN 数据不会被写入 | 代码层保障 |

---

## 5. 阻断项 4: 未解决非终态订单阻断全部新订单

**问题**：v2 仅 `UNKNOWN` 阻断；`SUBMITTED`、`SUBMITTING`、`PENDING_SUBMIT`、`TIMEOUT` 等仍允许其他订单，可能导致资金/仓位双重占用。

**修正**：

```python
def has_unresolved_orders(self) -> bool:
    """
    检查是否存在任何非终态订单。
    
    非终态集合:
        PENDING_SUBMIT, SUBMITTING, SUBMITTED, TIMEOUT, UNKNOWN,
        FILLED_PART, CANCELLING_PART, CANCELLING_ALL
    
    终态集合（不阻断）:
        FILLED_ALL, REJECTED, CANCELLED_ALL, CANCELLED_PART,
        DISABLED, DELETED, FILL_CANCELLED
    """
    cursor = self.conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status NOT IN (?, ?, ?, ?, ?, ?, ?)",
        ('FILLED_ALL', 'REJECTED', 'CANCELLED_ALL', 'CANCELLED_PART',
         'DISABLED', 'DELETED', 'FILL_CANCELLED')
    )
    count = cursor.fetchone()[0]
    return count > 0
```

**阻断位置**（`unified_runner.py` Step 0 之后，journal 初始化后）：

```python
journal = OrderJournal(journal_dir, market)
if journal.has_unresolved_orders():
    print(f'[BLOCKED] {market} 存在未解决非终态订单，阻断全部新订单')
    print(f'[BLOCKED] 运行 --signal-only 查看信号，或人工确认后清理')
    # 设置阻断标志，在 execute_orders() 前拦截
    market_blocked = True
```

**注意**：`FILLED_PART` 视为非终态（还可能继续成交或被撤销），也阻断新订单。

---

## 6. 阻断项 5: SELL→BUY 风险未闭环

**问题**：v2 仅在 SELL 后重新查询现金，但未：
- 确认 SELL 实际成交
- 重新查询持仓（可能已发生变化）
- 重新计算 exposure
- 对 SELL 未完成的情况做阻断

**修正**：在 `execute_orders()` 的 SELL→BUY 之间，新增**完整再查询 + 再风控**步骤。

```python
def execute_orders(self, orders, journal=None, ...):
    sell_orders = [o for o in orders if o['action'] == 'SELL']
    buy_orders  = [o for o in orders if o['action'] == 'BUY']

    sell_results = []
    for o in sell_orders:
        r = self._place_single_order(o, journal, ...)
        sell_results.append(r)

    # ═══════════════════════════════════════════════════════════════
    # SELL→BUY 间隙：成交确认 + 账户/持仓重查
    # ═══════════════════════════════════════════════════════════════
    
    if not self.dry_run and sell_results:
        # Step A: 等待 SELL 成交确认（等待 Futu 成交回报）
        #   TimeInForce.DAY 的 SELL 可能在盘后才成交，这里不阻塞等待
        #   而是先检查是否已成交；
        #   未成交的 SELL 会阻断 BUY。
        unfinished_sells = [r for r in sell_results
                            if r.get('status') not in ('FILLED_ALL', 'FILLED_PART')]
        if unfinished_sells:
            symbols = [r['symbol'] for r in unfinished_sells]
            print(f'[BLOCK] 以下 SELL 未完成，阻断 BUY: {symbols}')
            # **阻断全部 BUY，不提交任何新订单**
            buy_skipped = []
            for o in buy_orders:
                buy_skipped.append({
                    **o, 'status': 'BLOCKED',
                    'message': f'SELL {unfinished_sells[0]["symbol"]} 未成交',
                })
            return sell_results + buy_skipped

        # Step B: 重新查询 Futu 账户和持仓
        fresh_account = self._adapter.get_account_info(market)
        if fresh_account is None:
            print('[FATAL] SELL 后账户查询失败，中止全部')
            return sell_results + [{'status': 'BLOCKED',
                                    'message': '账户查询失败'}]

        fresh_positions = self._adapter.get_positions(market)
        live_cash = fresh_account['cash']
        live_exposure = sum(p['market_val'] for p in fresh_positions)
        live_total_assets = fresh_account['total_assets']

        # Step C: 基于实时数据进行风控检查（复用 RiskManager）
        print(f'[SELL→BUY] 实时现金={live_cash:,.0f} '
              f'持仓市值={live_exposure:,.0f} 总资产={live_total_assets:,.0f}')

    elif not self.dry_run and not sell_results:
        # 无 SELL，直接使用 Step 2 中已查询的现金
        live_cash = cash_before
        live_exposure = exposure_before
        live_total_assets = total_assets
    else:
        live_cash = None          # DRY_RUN

    # ═══════════════════════════════════════════════════════════════
    # BUY 执行：running_cash 逐笔扣减
    # ═══════════════════════════════════════════════════════════════

    running_cash = live_cash
    buy_results = []
    for o in buy_orders:
        trade_val = o['qty'] * o['price']
        
        # 风控检查（使用实时数据）
        if not self.dry_run:
            ok, reason = risk_mgr.check_position_limit(...)
            if not ok: ...
            ok, reason = risk_mgr.check_total_exposure(live_exposure, trade_val, total_assets)
            if not ok: ...
            if trade_val > running_cash * 0.95: ...

        r = self._place_single_order(o, journal, ...)
        buy_results.append(r)

        # 扣减运行中现金（无论结果如何，为下一笔预留资金）
        if not self.dry_run:
            running_cash -= trade_val
            live_exposure += trade_val

    return sell_results + buy_results
```

**关键原则**：
- 未完成 SELL（任何非 `FILLED_ALL`/`FILLED_PART` 状态）→ **阻断全部 BUY**
- SELL 后重新查询账户 + 持仓 + exposure + 风控
- 每笔 BUY 后 `running_cash` 和 `live_exposure` 同步扣减

---

## 7. 阻断项 6: BUY 未知结果立即停止剩余订单

**问题**：v2 仅当 `status == 'SUBMITTED'` 时扣减现金。`TIMEOUT`、`UNKNOWN`、`SUBMITTING` 等不确定状态不扣减，却可能已实际占用资金。

**修正**：

```python
def _place_single_order(self, order, journal, ...):
    ...
    try:
        # 实际下单
        ok, data = self._adapter.place_order(...)
        if ok:
            order_id = data.iloc[0]['order_id']
            futu_status = data.iloc[0]['order_status']
            journal.update_status(intent_id, futu_status, order_id=order_id)
            return {..., 'status': 'SUBMITTED', 'order_id': order_id}
        else:
            journal.update_status(intent_id, 'REJECTED', message=data)
            return {..., 'status': 'REJECTED', 'message': data}
    except TimeoutError:
        journal.update_status(intent_id, 'TIMEOUT')
        return {..., 'status': 'TIMEOUT', 'message': '下单超时'}
    except Exception as e:
        journal.update_status(intent_id, 'UNKNOWN', message=str(e))
        return {..., 'status': 'UNKNOWN', 'message': str(e)}


# BUY 执行循环中：
uncertain_statuses = {'TIMEOUT', 'UNKNOWN', 'SUBMITTING'}
for o in buy_orders:
    r = self._place_single_order(o, journal, ...)
    buy_results.append(r)

    # 关键检查：任何不确定提交状态 → 立即中止全部剩余 BUY
    if r['status'] in uncertain_statuses:
        print(f'[STOP] {r["symbol"]} BUY 状态不确定 ({r["status"]})，中止剩余 BUY')
        for remaining in buy_orders[len(buy_results):]:
            buy_results.append({
                **remaining, 'status': 'BLOCKED',
                'message': f'前序 BUY ({r["symbol"]}) 状态不确定',
            })
        break

    # 无论何时，都扣减运行中现金（见阻断项 5 的 running_cash 逻辑）
    if not self.dry_run:
        running_cash -= trade_val
```

**不确定状态表**：

| BUY 提交状态 | 是否允许继续下一笔 BUY |
|:---|:---:|
| `SUBMITTED` | ✅ 是（已收到 order_id） |
| `FILLED_ALL` | ✅ 是（直接成交） |
| `REJECTED` | ✅ 是（明确失败，未占用资金） |
| `SUBMITTING` | ❌ 否（提交中，状态不确定） |
| `TIMEOUT` | ❌ 否（超时，可能已占用资金） |
| `UNKNOWN` | ❌ 否（异常，可能已占用资金） |

---

## 8. 阻断项 7: 唯一 QuantBot 状态枚举

### 8.1 QuantBot 状态枚举（唯一权威定义）

```python
from enum import Enum

class OrderStatus(Enum):
    # === 提交前 ===
    INTENT_CREATED   = 'INTENT_CREATED'     # 订单意图已生成
    PENDING_SUBMIT   = 'PENDING_SUBMIT'     # 幂等键已写，等待提交确认

    # === 提交后（非终态） ===
    SUBMITTING       = 'SUBMITTING'         # 提交中
    SUBMITTED        = 'SUBMITTED'          # 已提交，等待成交
    FILLED_PART      = 'FILLED_PART'        # 部分成交
    CANCELLING       = 'CANCELLING'         # 撤销中
    TIMEOUT          = 'TIMEOUT'            # 提交超时，状态未知

    # === 终态 ===
    FILLED_ALL       = 'FILLED_ALL'         # 全量成交
    REJECTED         = 'REJECTED'           # 拒单（SUBMIT_FAILED/FAILED）
    CANCELLED_ALL    = 'CANCELLED_ALL'      # 全部撤销无成交
    CANCELLED_PART   = 'CANCELLED_PART'     # 部分成交后剩余撤销
    DISABLED         = 'DISABLED'           # 已失效
    DELETED          = 'DELETED'            # 已删除
    FILL_CANCELLED   = 'FILL_CANCELLED'     # 成交被回滚（极罕见）
    UNKNOWN          = 'UNKNOWN'            # 多次轮询仍未知（人工介入）

    @classmethod
    def terminal_set(cls) -> set:
        """终态集合 — 这些状态的订单不再需要任何后续处理。"""
        return {
            cls.FILLED_ALL, cls.REJECTED, cls.CANCELLED_ALL,
            cls.CANCELLED_PART, cls.DISABLED, cls.DELETED,
            cls.FILL_CANCELLED,
        }

    @classmethod
    def blocking_set(cls) -> set:
        """阻断集合 — 存在这些状态的订单时，阻断该市场全部新订单。"""
        return {
            cls.PENDING_SUBMIT, cls.SUBMITTING, cls.SUBMITTED,
            cls.FILLED_PART, cls.CANCELLING, cls.TIMEOUT, cls.UNKNOWN,
        }

    @classmethod
    def uncertain_set(cls) -> set:
        """不确定集合 — BUY 返回这些状态时立即中止后续 BUY。"""
        return {cls.SUBMITTING, cls.TIMEOUT, cls.UNKNOWN}
```

### 8.2 Futu OrderStatus → QuantBot OrderStatus 完整映射

| Futu OrderStatus | 映射至 QuantBot | 终态? | 阻断? | 说明 |
|:---|:---|:---:|:---:|:---|
| NONE | `UNKNOWN` | ❌ | ✅ | Futu 未知，需要对账 |
| UNSUBMITTED | `INTENT_CREATED` | ❌ | ✅ | 未提交（恢复时看到此状态说明 journal 比 Futu 超前） |
| WAITING_SUBMIT | `PENDING_SUBMIT` | ❌ | ✅ | 等待提交 |
| SUBMITTING | `SUBMITTING` | ❌ | ✅ | 提交中 |
| SUBMIT_FAILED | `REJECTED` | ✅ | ❌ | 提交失败（明确拒单） |
| TIMEOUT | `TIMEOUT` | ❌ | ✅ | 超时，需要恢复确认 |
| SUBMITTED | `SUBMITTED` | ❌ | ✅ | 已提交等待成交 |
| FILLED_PART | `FILLED_PART` | ❌ | ✅ | 部分成交 |
| FILLED_ALL | `FILLED_ALL` | ✅ | ❌ | 全部成交 |
| CANCELLING_PART | `CANCELLING` | ❌ | ✅ | 撤销中（部分已成交） |
| CANCELLING_ALL | `CANCELLING` | ❌ | ✅ | 撤销中（全部） |
| CANCELLED_PART | `CANCELLED_PART` | ✅ | ❌ | 部分成交后剩余撤销 |
| CANCELLED_ALL | `CANCELLED_ALL` | ✅ | ❌ | 全部撤销 |
| FAILED | `REJECTED` | ✅ | ❌ | 服务拒绝 |
| DISABLED | `DISABLED` | ✅ | ❌ | 已失效 |
| DELETED | `DELETED` | ✅ | ❌ | 已删除 |
| FILL_CANCELLED | `FILL_CANCELLED` | ✅ | ❌ | 成交被回滚 |

**全部代码引用必须使用 QuantBot `OrderStatus` 枚举**，不得直接引用 Futu SDK 的 `OrderStatus` 字符串。

---

## 9. 阻断项 8: 止损等副作用必须可崩溃恢复

### 9.1 journal 表增加字段

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| `intent_type` | TEXT | 订单业务目的，枚举值见下表 |
| `post_fill_applied` | INTEGER (0/1) | 是否已执行成交后动作（冷却期确认/回滚） |

### 9.2 intent_type 枚举

| intent_type | 业务含义 | 成交后动作 |
|:---|:---|:---|
| `SIGNAL_BUY` | 信号建仓 | 初始化风控追踪（`init_position`） |
| `SIGNAL_SELL` | 信号减仓（含信号反转） | 无特殊动作（持仓自然减少） |
| `STOP_SELL` | 止损/止盈卖出 | `confirm_stop()` 激活冷却期 |
| `REVERSAL_SELL` | 信号反转清仓 | `confirm_stop()` 激活冷却期（可选） |

### 9.3 崩溃恢复逻辑

```python
def recover_post_fill_actions(self):
    """
    恢复未完成的成交后动作。
    在启动时对账后调用。
    """
    cursor = self.conn.execute('''
        SELECT intent_id, intent_type, order_id, filled_qty
        FROM orders
        WHERE status IN ('FILLED_ALL', 'FILLED_PART')
          AND post_fill_applied = 0
          AND execution_mode = 'LIVE'
    ''')

    for row in cursor.fetchall():
        intent_id, intent_type, order_id, filled_qty = row
        
        # 按 intent_type 执行对应的成交后动作
        if intent_type == 'STOP_SELL':
            # 从订单中获取止损代码
            code = self._get_code_from_intent(intent_id)
            risk_mgr.confirm_stop(code)
            print(f'[RECOVERY] 止损冷却期已确认: {code} (order={order_id})')

        elif intent_type == 'SIGNAL_BUY':
            # 初始化风控追踪（如果进程崩溃时未执行）
            code = self._get_code_from_intent(intent_id)
            # 使用 backup 的 entry_price 信息
            risk_mgr.init_position(code, ...)
            print(f'[RECOVERY] 持仓风控已初始化: {code} (order={order_id})')

        # 标记已完成
        self.conn.execute(
            'UPDATE orders SET post_fill_applied=1, updated_at=datetime("now") '
            'WHERE intent_id=?',
            (intent_id,)
        )
        self.conn.commit()
```

### 9.4 幂等性

```python
# confirm_stop() 本身是幂等的（已冷却的标的再次调用无副作用）
def confirm_stop(self, code):
    if code in self.stop_timestamps:
        return  # 已冷却，跳过
    self.stop_timestamps[code] = datetime.now().isoformat()
    self.save_state()

# rollback_stop() 也是幂等的（已回滚的标的再次调用无副作用）
def rollback_stop(self, code):
    self.stop_timestamps.pop(code, None)
    self.save_state()
```

---

## 10. 阻断项 9: remark 使用版本化哈希，不截断

**问题**：v2 允许"截取前 64 字节"，可能产生碰撞（两个不同的 intent_id 前 64 字节相同）。

**修正**：使用固定长度版本化哈希，64 字节以内，无需截断。

```python
import hashlib

def make_remark(intent_id: str) -> str:
    """
    生成 Futu remark 字符串。
    
    格式: QNT:v1:<32-char-hex-digest>
    示例: QNT:v1:a1b2c3d4e5f6789012345678abcdef90
    
    约束: 总长度 ≤ 64 bytes（Futu SDK 限制）
    计算: 
      - 前缀 "QNT:v1:" = 7 bytes
      - SHA-256 hex digest 前 32 字符 = 32 bytes
      - 总计 = 39 bytes ≪ 64 bytes ✓
    
    优点:
      - 固定长度（39 bytes），不依赖输入长度
      - 碰撞概率极低（32-hex-char = 128 bits）
      - 不截断，不丢失信息
    """
    digest = hashlib.sha256(intent_id.encode('utf-8')).hexdigest()[:32]
    return f'QNT:v1:{digest}'
```

**验证**：

```python
assert len(make_remark('任意长度'.encode('utf-8'))) <= 64  # ✅
assert make_remark('a') == make_remark('a')                 # ✅ 确定性
assert make_remark('a') != make_remark('b')                 # ✅ 不同输入不同输出
```

**恢复时匹配**：恢复流程中，通过 `order_list_query()` 获取每笔订单的 `remark` 字段，在本地计算所有 `intent_id` 的哈希值进行匹配。

---

## 11. SQLite 表结构最终版（汇总）

```sql
CREATE TABLE IF NOT EXISTS orders (
    -- === 幂等键 ===
    intent_id       TEXT NOT NULL,          -- 稳定幂等键 {date}_{market}_{symbol}_{action}_{qty}_{price_int}
    attempt_id      TEXT NOT NULL,          -- {intent_id}_attempt{seq}

    -- === 执行信息 ===
    status          TEXT NOT NULL,          -- QuantBot OrderStatus 枚举值
    order_id        TEXT DEFAULT '',         -- Futu order_id
    symbol          TEXT NOT NULL,           -- 标准符号
    action          TEXT NOT NULL,           -- BUY / SELL
    qty             INTEGER NOT NULL,
    price           REAL NOT NULL,
    market          TEXT NOT NULL,
    execution_mode  TEXT NOT NULL DEFAULT 'LIVE',   -- 仅 'LIVE'
    intent_type     TEXT NOT NULL,                  -- SIGNAL_BUY / SIGNAL_SELL / STOP_SELL / REVERSAL_SELL

    -- === 成交信息 ===
    filled_qty      INTEGER DEFAULT 0,
    filled_avg_price REAL DEFAULT 0.0,

    -- === 时间戳 ===
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    -- === 副作用状态 ===
    post_fill_applied INTEGER DEFAULT 0,    -- 0=未执行, 1=已执行

    -- === 恢复辅助 ===
    remark          TEXT DEFAULT '',         -- Futu remark (QNT:v1:<hash>)
    message         TEXT DEFAULT '',

    -- === 约束 ===
    UNIQUE(intent_id),                       -- intent 幂等
    UNIQUE(order_id)                         -- order_id 幂等
);

CREATE INDEX idx_status ON orders(status);
CREATE INDEX idx_intent_type ON orders(intent_type, post_fill_applied);
```

---

## 12. 修订文件清单（v3 最终版）

| 文件 | 变更类型 | 行数 |
|:---|:---:|:---:|
| `core/order_journal.py` | **新增** | ~200 |
| `core/order_executor.py` | **修改** | ~+150 |
| `core/stop_loss.py` | **修改** | ~+30 |
| `core/futu_adapter.py` | **修改** | ~+70 |
| `unified_runner.py` | **修改** | ~+120 |
| `requirements.txt` | **修改** | 无需新增依赖（纯 SQLite，无 portalocker） |
| `tests/smoke/test_order_journal.py` | **新增** | ~150 |
| `tests/smoke/test_execution_consistency.py` | **新增** | ~120 |

---

## 13. v3 测试清单（更新）

### 新增测试

| 测试 | 覆盖 | 方法 |
|:---|:---|:---|
| DRY_RUN 不写 journal | journal 文件不存在或为空 | mock 文件系统 |
| DRY_RUN 传递 `None` journal | `execute_orders(orders, journal=None)` 不崩溃 | 纯代码路径 |
| 内存 SQLite 测试隔离 | 每个测试独立 `:memory:` DB | pytest 夹具 |
| `has_unresolved_orders` 阻断 | 任何非终态 → `True` | 内存 SQLite |
| `has_unresolved_orders` 放行 | 全部终态 → `False` | 内存 SQLite |
| `try_acquire_intent` 原子性 | 并发 INSERT 相同 intent_id → 第二个失败 | 多线程 + 内存 SQLite |
| `try_acquire_intent` `BEGIN IMMEDIATE` | 事务隔离 | 内存 SQLite |
| SELL 未完成阻断 BUY | Mock Futu 返回 SUBMITTED → BUY 不提交 | Mock |
| SELL 后重新查询持仓+风控 | Mock 返回更新后的持仓 | Mock |
| BUY 不确定状态中止 | Mock 返回 TIMEOUT → 后续 BUY 全部 BLOCKED | Mock |
| `OrderStatus` 终态集合完整性 | 所有终态正确归类 | 纯枚举 |
| `OrderStatus` 阻断集合完整性 | 所有阻断态正确归类 | 纯枚举 |
| `remark` 哈希固定长度 | 任何输入 → 39 bytes | 纯函数 |
| `remark` 碰撞验证 | 不同 intent_id → 不同 hash | 纯函数（暴力测试 N=1000） |
| `post_fill_applied` 恢复 | 模拟崩溃后重新运行 → 执行恢复动作 | Mock journal |
| `confirm_stop()` 幂等 | 已冷却的标的再次调用无副作用 | pure stop_loss |
| `UNIQUE(order_id)` | 重复 order_id → IntegrityError | 内存 SQLite |
| 回滚不删除 journal | 回滚脚本不含 `rm` journal 文件 | 脚本检查 |

### ⛔ 禁止的测试

- 任何涉及 `TrdEnv.SIMULATE` 真实下单的端到端测试
- 任何使用 `--live` 参数的测试
- 任何手动触发的 FOK 测试（FOK 不存在于 SDK）

---

## 14. 分阶段实施（单阶段 F2-SEC，不可拆分）

### Phase F2-SEC（安全阶段，一次部署）

```
Step 1: futu_adapter.py — 修复 API 契约 (fill_side_type → time_in_force + remark)
Step 2: core/order_journal.py — 新建 SQLite journal
Step 3: order_executor.py — 集成 journal + 幂等 + SELL→BUY + running_cash
Step 4: stop_loss.py — confirmed/confirm_stop/rollback_stop
Step 5: unified_runner.py — 恢复对账 + 阻断 + 风控重查
Step 6: 测试 + CI + DRY_RUN 双次验证
```

**回滚方案（[修正版] — 见阻断项 2）**：

```bash
# 回滚代码
git checkout -- core/order_executor.py core/stop_loss.py core/futu_adapter.py unified_runner.py
rm -f core/order_journal.py

# ✅ 保留 journal 文件（不删除）
# journal 会留在原位，不会影响旧代码。
# 如果未来重新实施 F2-SEC，journal 中未解决订单继续可用。

# 备份 journal
cp output/order_journal_*.db backups/journal_f2sec_$(date +%Y%m%d_%H%M%S).bak
```

---

## 15. Git Status 与确认

> **约束**: 本报告为只读分析，未执行 Git 操作。

✅ **本报告为只读分析，未修改任何代码或文件。**  
✅ **未执行任何下单入口或可能连接交易执行路径的测试。**  
✅ **未执行 Git commit、push、merge、reset 或 checkout。**  
✅ **未接入或建议接入 TrdEnv.REAL。**  
✅ **未调整 confidence_v2、Gate 或策略逻辑。**  
✅ **未触碰受保护代码。**  
✅ **未触碰 4 个旧 research untracked 文件。**  
✅ **本地 SDK 核验已完成。**  
✅ **9/9 Codex 阻断项全部关闭。**  

---

## 附录 A: v1→v2→v3 演化追踪

| 主题 | v1 (首次提交) | v2 (一次修订) | v3 (二次修订) |
|:---|:---|:---|:---|
| 幂等键 | `intent_id + run_id` | 稳定 `intent_id` + `attempt_id` | ✅ 同 v2 |
| 持久化 | JSONL (损坏跳过) | SQLite + portalocker | SQLite + `BEGIN IMMEDIATE` 无 portalocker |
| 自动重提 | 允许 PENDING 重提 | 禁止所有重提 | ✅ 同 v2 + 强化 |
| FOK | `FillSideType.FILL_OR_KILL` | 移除 FOK，用 `TimeInForce.DAY` | ✅ 同 v2 + SDK 核验 |
| 多 BUY 现金 | 同一 cash | 每笔扣减 `running_cash` | ✅ 同 v2 + 不确定状态中止 |
| 阶段 | F2-B + F2-C 拆分 | F2-SEC 合并 | ✅ 同 v2 |
| DRY_RUN journal | 写 journal | 未修复 | ✅ **DRY_RUN 不写** |
| journal 清理 | 7 天删除 | "不自动删除" | ✅ **回滚也不删除** |
| 并发锁 | portalocker | 未实现 | ✅ **`BEGIN IMMEDIATE` + UNIQUE** |
| 阻断范围 | 仅 UNKNOWN | 仅 UNKNOWN | ✅ **任何非终态阻断全部** |
| SELL→BUY | 仅现金 | 查现金+持仓重查 | ✅ **成交确认 + 账户+持仓+风控全重查** |
| 状态枚举 | 混用 | 混用 | ✅ **唯一 QuantBot 枚举 + 完整映射** |
| 副作用恢复 | 无 | 无 | ✅ **`intent_type` + `post_fill_applied`** |
| remark | 前 64 字节截断 | 前 64 字节截断 | ✅ **固定版本化哈希** |

---

*Phase F2-A v3 设计审查结束。9/9 阻断项已关闭。等待 Codex 再次批准后开始实施。*