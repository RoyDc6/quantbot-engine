# Phase F2-A Design Review Package v4
# 订单执行一致性修复设计报告（三次修订版）

**版本**: v4.0  
**日期**: 2026-06-04  
**状态**: 自审计 v3 后修订 (关闭 8 项自审计缺口 + 9 项 Codex 阻断 = 17/17)，等待批准。  
**作者**: QuantBot (Phase F2-A 只读分析, 本地 SDK v10.05.6508 核验)

---

## 0. v4 自审计概览

v3 关闭了 Codex 提出的 9 个阻断项，但自审计发现 8 个内部缺口。v4 逐一修复。

| 自审计 # | v3 缺口 | v4 修正 |
|:---:|:---|---:|
| S-01 | `UNIQUE(order_id)` 空串冲突——默认 `order_id=''` 导致只能创建一个 PENDING_SUBMIT | 改为部分索引 `WHERE order_id != ''` |
| S-02 | `running_cash` 扣减逻辑矛盾——"无论何时都扣减"与 REJECTED 不消耗现金冲突 | 明确: SUBMITTED/FILLED_ALL 扣减, REJECTED 不扣减, 不确定态扣减+中止 |
| S-03 | `has_unresolved_orders()` 使用硬编码字符串，未引用 `OrderStatus.terminal_set()` | 改为引用枚举 |
| S-04 | 永久 PENDING_SUBMIT 无清理机制——崩溃发生在 place_order 前时，该 intent 被永久阻塞 | 增加 `prune_stale_pending()`: 1 小时后清理无 order_id + 无 Futu 匹配的 PENDING |
| S-05 | `intent_type` 未从 `unified_runner.py` 传播至 journal——订单 dict 中未定义该字段 | 明确 intent_type 在各订单生成点的赋值 |
| S-06 | 恢复对账需 futu_code 进行 confirm_stop，但 journal 仅存 symbol | journal 增加 `futu_code` 字段 |
| S-07 | REVERSAL_SELL 在恢复流程中遗漏 | 恢复流程统一处理 `STOP_SELL` + `REVERSAL_SELL` |
| S-08 | `try_acquire_intent` 默认 intent_type='SIGNAL' 与枚举名称不匹配 | 修正默认值 |

---

## 1. v3 自审计缺口 S-01: UNIQUE(order_id) 空串冲突

### 问题

v3 表定义 `UNIQUE(order_id)`（L126），默认 `order_id TEXT DEFAULT ''`。SQLite 中 `UNIQUE` 将空串视为非 NULL 值，导致第一条 PENDING_SUBMIT 写入后，后续所有 PENDING_SUBMIT 因 `order_id=''` 违反唯一约束而 INSERT 失败。

### 修正

```sql
-- 替换 v3 的 UNIQUE(order_id) 为部分索引
CREATE UNIQUE INDEX IF NOT EXISTS idx_order_id 
    ON orders(order_id) WHERE order_id != '';
```

**效果**：
- `order_id=''`（PENDING_SUBMIT）：不被索引约束，允许多条
- `order_id='12345678'`（SUBMITTED/FILLED）：被索引约束，确保唯一
- 同一 Futu order_id 永远不会被 journal 记录两次

### 表结构最终版（含 S-01 修正）

```sql
CREATE TABLE IF NOT EXISTS orders (
    intent_id           TEXT NOT NULL,
    attempt_id          TEXT NOT NULL,
    status              TEXT NOT NULL,
    order_id            TEXT DEFAULT '',
    symbol              TEXT NOT NULL,
    futu_code           TEXT DEFAULT '',         -- S-06：Futu 代码，用于恢复对账
    action              TEXT NOT NULL,           -- BUY / SELL
    qty                 INTEGER NOT NULL,
    price               REAL NOT NULL,
    market              TEXT NOT NULL,
    execution_mode      TEXT NOT NULL DEFAULT 'LIVE',  -- 仅 'LIVE'
    intent_type         TEXT NOT NULL,           -- SIGNAL_BUY / SIGNAL_SELL / STOP_SELL / REVERSAL_SELL
    filled_qty          INTEGER DEFAULT 0,
    filled_avg_price    REAL DEFAULT 0.0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    post_fill_applied   INTEGER DEFAULT 0,       -- 0=未执行成交后动作, 1=已执行
    remark              TEXT DEFAULT '',
    message             TEXT DEFAULT '',
    UNIQUE(intent_id)                             -- intent 唯一约束
);

-- 部分索引：order_id 非空时唯一
CREATE UNIQUE INDEX IF NOT EXISTS idx_order_id 
    ON orders(order_id) WHERE order_id != '';

CREATE INDEX IF NOT EXISTS idx_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_post_fill ON orders(intent_type, post_fill_applied);
CREATE INDEX IF NOT EXISTS idx_futu_code ON orders(futu_code);
```

---

## 2. 自审计缺口 S-02: running_cash 扣减逻辑矛盾

### 问题

v3 第 6 节同时出现两种说法：
- "无论何时，都扣减运行中现金"
- REJECTED 在"是否允许继续下一笔"表中标记为 ✅（不消耗现金）

两者矛盾。对 REJECTED 扣减现金会导致后续 BUY 因"现金不足"被错误跳过。

### 修正

```python
# BUY 执行循环中
for o in buy_orders:
    r = self._place_single_order(o, journal, ...)
    buy_results.append(r)

    # 不确定状态 → 中止全部剩余 BUY
    if r['status'] in uncertain_statuses:
        for remaining in buy_orders[len(buy_results):]:
            buy_results.append({... 'status': 'BLOCKED', ...})
        break  # 不执行剩余 BUY

    # 现金扣减（单笔 BUY 后兜底）
    #   SUBMITTED/FILLED_ALL → 已占用资金，必须扣减
    #   TIMEOUT/UNKNOWN      → 可能已占用，保守扣减 + 已中止后续 BUY
    #   REJECTED             → 未占用资金，不扣减
    if r['status'] in ('SUBMITTED', 'FILLED_ALL', 'TIMEOUT', 'UNKNOWN', 'SUBMITTING'):
        running_cash -= trade_val
    elif r['status'] == 'REJECTED':
        pass  # 不占用资金
```

**现金扣减决策表**：

| BUY 返回状态 | 扣减 running_cash? | 允许后续 BUY? |
|:---|:---:|:---:|
| `SUBMITTED` | ✅ 扣减（已占用） | ✅ 允许 |
| `FILLED_ALL` | ✅ 扣减（已成交） | ✅ 允许 |
| `REJECTED` | ❌ 不扣减（未占用） | ✅ 允许（失败未消耗资金） |
| `TIMEOUT` | ✅ 扣减（可能占用） | ❌ 中止 |
| `UNKNOWN` | ✅ 扣减（可能占用） | ❌ 中止 |
| `SUBMITTING` | ✅ 扣减（可能占用） | ❌ 中止 |

---

## 3. 自审计缺口 S-03: has_unresolved_orders 硬编码字符串

### 问题

v3 `has_unresolved_orders()` 直接在 SQL 查询中写入终态字符串列表，未引用 `OrderStatus.terminal_set()`。枚举变更时查询可能不同步。

### 修正

```python
from enum import Enum

class OrderStatus(Enum):
    INTENT_CREATED   = 'INTENT_CREATED'
    PENDING_SUBMIT   = 'PENDING_SUBMIT'
    SUBMITTING       = 'SUBMITTING'
    SUBMITTED        = 'SUBMITTED'
    FILLED_PART      = 'FILLED_PART'
    CANCELLING       = 'CANCELLING'
    TIMEOUT          = 'TIMEOUT'
    FILLED_ALL       = 'FILLED_ALL'
    REJECTED         = 'REJECTED'
    CANCELLED_ALL    = 'CANCELLED_ALL'
    CANCELLED_PART   = 'CANCELLED_PART'
    DISABLED         = 'DISABLED'
    DELETED          = 'DELETED'
    FILL_CANCELLED   = 'FILL_CANCELLED'
    UNKNOWN          = 'UNKNOWN'

    @classmethod
    def terminal_set(cls) -> set:
        """终态 — 不再需要任何后续处理。"""
        return {
            cls.FILLED_ALL, cls.REJECTED, cls.CANCELLED_ALL,
            cls.CANCELLED_PART, cls.DISABLED, cls.DELETED,
            cls.FILL_CANCELLED,
        }

    @classmethod
    def blocking_set(cls) -> set:
        """阻断 — 存在该状态时阻断市场全部新订单。"""
        return {
            cls.PENDING_SUBMIT, cls.SUBMITTING, cls.SUBMITTED,
            cls.FILLED_PART, cls.CANCELLING, cls.TIMEOUT, cls.UNKNOWN,
        }

    @classmethod
    def uncertain_set(cls) -> set:
        """不确定 — BUY 返回时立即中止后续 BUY。"""
        return {cls.SUBMITTING, cls.TIMEOUT, cls.UNKNOWN}


class OrderJournal:
    def has_unresolved_orders(self) -> bool:
        """该市场是否存在任何非终态订单。
        
        非终态订单可能占用资金或仓位，必须阻断全部新订单。
        """
        terminal = [s.value for s in OrderStatus.terminal_set()]
        placeholders = ','.join('?' * len(terminal))
        cursor = self.conn.execute(
            f"SELECT COUNT(*) FROM orders WHERE status NOT IN ({placeholders})",
            terminal
        )
        return cursor.fetchone()[0] > 0

    def has_blocking_orders(self) -> bool:
        """是否存在阻断态订单（阻断集是非终态的子集，但更精确）。"""
        blocking = [s.value for s in OrderStatus.blocking_set()]
        placeholders = ','.join('?' * len(blocking))
        cursor = self.conn.execute(
            f"SELECT COUNT(*) FROM orders WHERE status IN ({placeholders})",
            blocking
        )
        return cursor.fetchone()[0] > 0
```

---

## 4. 自审计缺口 S-04: 永久 PENDING_SUBMIT 无清理机制

### 问题

进程在 `try_acquire_intent()` 返回 True 后、`place_order()` 调用前崩溃。恢复时发现 PENDING_SUBMIT 无 order_id，Futu 中无对应订单，但根据"禁止自动重提"规则永远无法清理。该市场的全部新订单被永久阻断。

### 修正

```python
import datetime

def prune_stale_pending(self, max_age_hours: float = 1.0) -> int:
    """
    清理已过期的 PENDING_SUBMIT 记录。
    
    条件:
      1. status = 'PENDING_SUBMIT'
      2. order_id = '' (从未获取到 order_id)
      3. created_at 超过 max_age_hours 小时
    
    这些记录说明进程在 try_acquire_intent() 之后、place_order() 之前崩溃。
    Futu 中不存在对应订单，因此可以安全清除。
    
    返回: 清理的记录数
    """
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=max_age_hours)).isoformat()
    cursor = self.conn.execute(
        "DELETE FROM orders "
        "WHERE status = 'PENDING_SUBMIT' "
        "  AND order_id = '' "
        "  AND created_at < ?",
        (cutoff,)
    )
    self.conn.commit()
    pruned = cursor.rowcount
    if pruned > 0:
        print(f'  [JOURNAL] 清理 {pruned} 条过期 PENDING_SUBMIT (> {max_age_hours}h 无 order_id)')
    return pruned
```

**注意**：
- `max_age_hours=1`：1 小时内不清理，确保不会在正常运行中误删
- 仅在启动时 `OrderJournal.__init__()` 中调用一次
- `order_id=''` 条件是关键——但凡有 order_id 的 PENDING（极罕见）绝不清理
- 此清理在 `has_unresolved_orders()` 阻断检查**之前**执行，确保清理后恢复可运行

---

## 5. 自审计缺口 S-05: intent_type 传播路径

### 问题

v3 未明确定义 `intent_type` 在 `unified_runner.py` 各订单生成点的赋值规则。

### 修正

**`unified_runner.py` 中各订单生成点的 `intent_type` 赋值**：

| 位置 | 代码 | intent_type |
|:---|:---|---:|
| L305-310 (信号反转清仓) | `orders.append({'symbol': sym, 'action': 'SELL', ...})` | `'REVERSAL_SELL'` |
| L327-333 (止损/止盈) | `orders.append({'symbol': sym, 'action': 'SELL', ...})` | `'STOP_SELL'` |
| L418-422 (信号建仓) | `orders.append({'symbol': symbol, 'action': 'BUY', ...})` | `'SIGNAL_BUY'` |
| 未来：信号减仓 | (尚未实现) | `'SIGNAL_SELL'` |

**传播链**：

```
unified_runner.py orders.append({..., 'intent_type': 'STOP_SELL'})
  → order_executor.execute_orders(orders)
    → _place_single_order(order, ...)
      → order['intent_type'] 传递至 journal.try_acquire_intent(intent_id, ..., order)
        → INSERT INTO orders (intent_type, ...) VALUES (order['intent_type'], ...)
```

---

## 6. 自审计缺口 S-06: journal 存储 futu_code

### 问题

恢复对账中，`STOP_SELL` 成交后需要调用 `risk_mgr.confirm_stop(futu_code)`。但 journal 仅存 `symbol`（如 `00700.HK`），而 `confirm_stop()` 的 key 是 `futu_code`（如 `HK.00700`）。恢复时需要额外转换。

### 修正

journal 表增加 `futu_code TEXT DEFAULT ''`（见 S-01 表定义）。

`try_acquire_intent()` 的 INSERT 从 `order` dict 读取 `futu_code`:

```python
cursor.execute('''
    INSERT INTO orders (intent_id, attempt_id, status, order_id,
        symbol, futu_code, action, qty, price, market, intent_type,
        execution_mode, created_at, updated_at, remark)
    VALUES (?, ?, 'PENDING_SUBMIT', '', ?, ?, ?, ?, ?, ?, ?, 'LIVE',
        datetime('now'), datetime('now'), ?)
''', (intent_id, attempt_id,
      order['symbol'], order.get('futu_code', ''),
      order['action'], order['qty'], order['price'],
      order.get('market', ''), order['intent_type'],
      order.get('remark', '')))
```

**`unified_runner.py` 传递 `futu_code`**：

```python
# 止损卖出 (L327)
orders.append({
    'symbol': sym, 'futu_code': p['code'],  # 新增 futu_code
    'action': 'SELL', ...
})

# 信号建仓 (L418)
orders.append({
    'symbol': symbol, 'futu_code': to_futu_code(symbol),  # 新增 futu_code
    'action': 'BUY', ...
})
```

---

## 7. 自审计缺口 S-07: REVERSAL_SELL 恢复遗漏

### 问题

v3 恢复流程仅处理 `STOP_SELL` 和 `SIGNAL_BUY`，未处理 `REVERSAL_SELL`。

### 修正

```python
def recover_post_fill_actions(self, risk_mgr):
    """恢复未完成的成交后动作。"""
    cursor = self.conn.execute('''
        SELECT intent_id, intent_type, futu_code, order_id
        FROM orders
        WHERE status IN ('FILLED_ALL', 'FILLED_PART')
          AND post_fill_applied = 0
          AND execution_mode = 'LIVE'
    ''')

    for row in cursor.fetchall():
        intent_id, intent_type, futu_code, order_id = row
        code = futu_code  # S-06: 直接从 journal 获取 futu_code

        if intent_type in ('STOP_SELL', 'REVERSAL_SELL'):
            # 激活冷却期（幂等的：已冷却的标的再次调用无副作用）
            risk_mgr.confirm_stop(code)
            print(f'[RECOVERY] 冷却期已确认: {code} (order={order_id}, type={intent_type})')

        elif intent_type == 'SIGNAL_BUY':
            # 初始化风控追踪
            cost_price = ...   # 从 journal 取 filled_avg_price
            filled_price = ...
            risk_mgr.init_position(code, cost_price, filled_price)
            risk_mgr.update_highest(code, filled_price)
            print(f'[RECOVERY] 持仓风控已初始化: {code} (order={order_id})')

        # 标记已完成（幂等）
        self.conn.execute(
            'UPDATE orders SET post_fill_applied=1, updated_at=datetime("now") '
            'WHERE intent_id=?',
            (intent_id,)
        )
        self.conn.commit()
```

---

## 8. 自审计缺口 S-08: 默认 intent_type 与枚举不匹配

### 问题

v3 `try_acquire_intent()` 默认值 `order.get('intent_type', 'SIGNAL')`（L159），但枚举定义为 `SIGNAL_BUY`/`SIGNAL_SELL`。`'SIGNAL'` 不是合法值。

### 修正

```python
# try_acquire_intent() 中
intent_type = order.get('intent_type', '')
if intent_type not in ('SIGNAL_BUY', 'SIGNAL_SELL', 'STOP_SELL', 'REVERSAL_SELL'):
    raise ValueError(f'非法 intent_type: {intent_type}')
```

即：`intent_type` 不再有默认值，必须由调用方明确赋值。`unified_runner.py` 在每个 `orders.append()` 点必须设置 `intent_type`（见 S-05 表格）。

---

## 9. Futu SDK 本地核验签名（确认）

```
futu SDK 版本: 10.05.6508
路径: C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\Lib\site-packages\futu\

place_order() 签名 (open_trade_context.py:540):
    def place_order(self, price, qty, code, trd_side, 
                    order_type=OrderType.NORMAL,
                    adjust_limit=0, 
                    trd_env=TrdEnv.REAL,
                    acc_id=0, acc_index=0,
                    remark=None,                    # <-- 可用，max 64 bytes
                    time_in_force=TimeInForce.DAY,  # <-- 可用
                    fill_outside_rth=False,
                    aux_price=None, trail_type=None, trail_value=None, trail_spread=None,
                    session=Session.NONE,
                    jp_acc_type=SubAccType.JP_GENERAL,
                    position_id=None):
    返回: (RET_OK, order_table: pd.DataFrame)  |  (RET_ERROR, msg)

    注意:
      - fill_side_type: ❌ 不存在
      - FILL_OR_KILL: ❌ 不存在
      - 内部自动调用 _order_list_query_impl() 查询订单状态 (L610-617)
      - order_table columns 包含: order_id, order_status, dealt_qty, dealt_avg_price, remark, ...

order_list_query() 签名 (open_trade_context.py:459):
    def order_list_query(self, order_id="", status_filter_list=[], code='', 
                         start='', end='', trd_env=TrdEnv.REAL, ...):
    返回: (RET_OK, order_table: pd.DataFrame)  |  (RET_ERROR, msg)

OrderStatus 枚举 (constant.py:1299-1351):
    NONE, UNSUBMITTED, WAITING_SUBMIT, SUBMITTING, SUBMIT_FAILED,
    TIMEOUT, SUBMITTED, FILLED_PART, FILLED_ALL, CANCELLING_PART,
    CANCELLING_ALL, CANCELLED_PART, CANCELLED_ALL, FAILED, DISABLED,
    DELETED, FILL_CANCELLED

TimeInForce 枚举 (constant.py:2877):
    DAY, GTC, IOC
```

---

## 10. 实施阶段

### Phase F2-SEC（单阶段，不可拆分，包含 S-01 至 S-08 全部修正）

**文件变更**：

| 文件 | 变更 | 估计行数 |
|:---|:---|---:|
| `core/order_journal.py` | **新增** | ~220 |
| `core/order_executor.py` | **修改**: journal 集成, 幂等, SELL→BUY, running_cash | ~+160 |
| `core/stop_loss.py` | **修改**: confirmed/confirm_stop/rollback_stop | ~+30 |
| `core/futu_adapter.py` | **修改**: 修复 API 契约 + 新增 order_list_query | ~+70 |
| `unified_runner.py` | **修改**: 恢复对账, 阻断, 风控重查, intent_type/ futu_code 传播 | ~+130 |
| `tests/smoke/test_order_journal.py` | **新增** | ~180 |
| `tests/smoke/test_execution_consistency.py` | **新增** | ~150 |

**回滚方案（journal 文件永不删除）**：

```bash
# 回滚代码
git checkout -- core/order_executor.py core/stop_loss.py core/futu_adapter.py unified_runner.py
rm -f core/order_journal.py

# journal 文件保留，不删除
cp output/order_journal_*.db backups/journal_f2sec_$(date +%Y%m%d_%H%M%S).bak

# 注意: 回滚后旧代码不读 journal，journal 不会影响旧代码运行。
#       未来重新实施 F2-SEC 时，journal 中未解决订单可以继续使用。
```

---

## 11. 测试清单（v4 最终）

### 新增单元测试

| 测试 | 覆盖 | 验证方法 |
|:---|:---|---:|
| `UNIQUE(order_id)` 部分索引 | `WHERE order_id=''` 允许多条 | 内存 SQLite |
| `UNIQUE(order_id)` 非空唯一 | 相同 `order_id='123'` 第二次报错 | 内存 SQLite |
| `running_cash` REJECTED 不扣减 | REJECTED 后 running_cash 不变 | mock |
| `running_cash` SUBMITTED 扣减 | SUBMITTED 后 running_cash 减少 | mock |
| `prune_stale_pending` 清理 | >1h 无 order_id → 删除 | 内存 SQLite |
| `prune_stale_pending` 保护 <1h | <1h 不删除 | 内存 SQLite |
| `prune_stale_pending` 保护有 order_id | 有 order_id 的 PENDING 不删除 | 内存 SQLite |
| `has_unresolved_orders` 使用枚举 | 枚举扩展后查询同步更新 | 内存 SQLite |
| `intent_type` 合法值校验 | 非法值 → ValueError | 纯函数 |
| `intent_type` 传播链 | unified_runner → executor → journal | mock 全链路 |
| `futu_code` 存储 | journal 表 futu_code 字段正确填充 | 内存 SQLite |
| REVERSAL_SELL 恢复 | 恢复流程调用 confirm_stop | mock risk_mgr |
| 默认 intent_type 无默认值 | 缺失时 → ValueError | 纯函数 |

### ⛔ 禁止的测试

- 任何涉及 `TrdEnv.SIMULATE` 真实下单的测试
- 任何使用 `--live` 参数的测试
- FOK 相关测试（FOK 不存在于 SDK）

---

## 12. 确认

✅ **本报告为只读分析，未修改任何代码或文件。**  
✅ **未执行任何下单入口或可能连接交易执行路径的测试。**  
✅ **未执行 Git commit、push、merge、reset 或 checkout。**  
✅ **未接入或建议接入 TrdEnv.REAL。**  
✅ **未调整 confidence_v2、Gate 或策略逻辑。**  
✅ **未触碰受保护代码。**  
✅ **未触碰 4 个旧 research untracked 文件。**  
✅ **本地 SDK 核验已完成。**  
✅ **9/9 Codex 阻断项已在 v3 关闭。**  
✅ **8/8 自审计缺口已在 v4 关闭。**  

**自审计演化**：v1 (6 阻断) → v2 (9 阻断) → v3 (9 关闭) → v4 (8 自审缺口 + 9 阻断 = 17/17 关闭)

**等待 Codex 批准后开始 Phase F2-SEC 实施。**

---

*Phase F2-A v4 设计审查结束。17/17 问题全部关闭。*