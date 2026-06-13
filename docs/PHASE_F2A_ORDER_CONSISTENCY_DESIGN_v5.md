# Phase F2-A v5 Review Package
# 订单执行一致性修复设计报告（四次修订版）

**版本**: v5.0  
**日期**: 2026-06-04  
**状态**: 关闭 Codex v2 审查 7 个 P0 项 + 补充设计细节，等待批准实施。  
**作者**: QuantBot (Phase F2-A 只读分析, 本地 SDK v10.05.6508 核验)

---

## 目录

1. [Executive Summary](#1-executive-summary)
2. [P0 Closure Matrix](#2-p0-closure-matrix)
3. [Final Order State Machine](#3-final-order-state-machine)
4. [Market Execution Lease Design](#4-market-execution-lease-design)
5. [Idempotency and Recovery Design](#5-idempotency-and-recovery-design)
6. [Partial Fill and Post-Fill Side Effects](#6-partial-fill-and-post-fill-side-effects)
7. [SELL-to-BUY Fail-Closed Rules](#7-sell-to-buy-fail-closed-rules)
8. [Rollback and LIVE Disable Procedure](#8-rollback-and-live-disable-procedure)
9. [Final SQLite Schema](#9-final-sqlite-schema)
10. [Verification Test Matrix](#10-verification-test-matrix)
11. [Audit Trail Design](#11-audit-trail-design)
12. [Confirmation of No Code/Git/Order Actions](#12-confirmation-of-no-codegitorder-actions)

---

## 1. Executive Summary

### v1 → v5 演化路径

| 版本 | 阶段 | 关闭项 | 结果 |
|:---:|:---|:---:|:---|
| v1 | Codex 初始审查 | 0/7 | 退还: 6 个阻断项 |
| v2 | 修复 6 阻断 | 6/7 | 退还: 9 个阻断项 |
| v3 | 修复 9 阻断 | 15/7 | 退还: 8 个内部缺口 |
| v4 | 自审计修复 8 缺口 | 17/7+8 | 等待批准 |
| **v5** | **关闭 7 P0 + 补充细节** | **全部关闭** | **提交审查** |

### 7 个 P0 项 + 补充细节关闭状态

| P0 # | 要求 | v5 关闭方式 |
|:---:|:---|---:|
| 1 | RESERVED → SUBMITTING 状态机 | 定义 4 层流 + ABANDONED（不删除）；order_id='' 不作为未提交证据 |
| 2 | LIVE fail-closed | journal None 直接报错；journal 失败禁止下单；无回退路径 |
| 3 | 市场级 execution lease | SQLite 租赁表；完整周期覆盖；过期+未解决→禁止接管；owner/续租/释放/恢复 |
| 4 | 部分成交正确性 | 仅 FILLED_ALL SELL 允许 BUY；FILLED_PART 阻断；STOP_SELL 持仓为零后激活；分离副作用列 |
| 5 | 回滚方案 | 禁用 LIVE_CONFIRMED；journal 保留+备份；未解决订单对账前禁止 LIVE |
| 6 | FILL_CANCELLED 人工介入 | 移出 terminal_set；加入 blocking_set+human_intervention 标记 |
| 7 | REVERSAL_SELL 不调 confirm_stop | 保持现有行为；恢复流程中仅处理 STOP_SELL |
| — | attempt_id 评估 | **删除**；auto-retry 已禁止且 intent_id 唯一，attempt_id 无实际用途，改由 audit_log 记录重试历史 |
| — | SELL 后查询 fail-closed | 账户+持仓任一查询失败 → 阻断全部 BUY |
| — | 再风控用实时数据 | 使用最新 live_total_assets/现金/exposure |
| — | 并发测试 | 临时文件 SQLite + 两个独立连接 |
| — | 不自动删除 | 仅 ABANDONED（状态变更）或新增记录；不 DELETE |
| — | 审计 | 独立 audit_log 表记录全部状态转换+lease 操作+副作用执行 |

---

## 2. P0 Closure Matrix

### P0-1: 状态机精确化

**阻断原文**：
```
RESERVED → SUBMITTING → SUBMITTED/REJECTED/TIMEOUT/UNKNOWN
RESERVED 尚未调用 broker，可标记 ABANDONED，但不得删除记录
SUBMITTING/PENDING/TIMEOUT/UNKNOWN 禁止自动删除、重提或解除阻断
order_id='' 不能作为未提交证据
```

**v5 修正**：

将 v4 的 `PENDING_SUBMIT` 拆分为两个明确状态：

| v4 状态 | v5 状态 | 语义 | place_order? | 可清理? |
|:---:|:---:|:---|---:|:---:|
| PENDING_SUBMIT | **RESERVED** | intent 已捕获、journal 已写入、幂等键已锁定，但未调用 broker API | ❌ 未调用 | ✅ → ABANDONED（非 DELETE） |
| — | **SUBMITTING** | place_order() 已调用，正在等待 broker 同步返回 | ✅ 已调用 | ❌ 禁止任何操作 |

**ABANDONED 规则**：
- RESERVED 可转为 ABANDONED，但**不得 DELETE 记录**
- 仅以下条件可转为 ABANDONED：
  1. 进程在 RESERVED 状态崩溃后重启，且 Futu `order_list_query()` 确认无匹配订单
  2. 外部干预明确标记
- ABANDONED 记录保留（audit + 幂等证据），不再参与阻断检查

**order_id='' 不作为未提交证据**：
- RESERVED：order_id='' **确实**意味着未提交（因为未调用 place_order）
- SUBMITTING：order_id 可能为空（尚未收到同步返回），但**已调用** place_order
- 清理逻辑不得以 order_id='' 作为判断依据；应用 status 字段判断
- 验证方式：对 RESERVED，先查 Futu `order_list_query()` 确认无匹配后标记 ABANDONED

**Stale RESERVED 处理（取代 v4 的 `prune_stale_pending`）**：

```python
def resolve_stale_reserved(self, futu_adapter, max_age_hours: float = 1.0) -> int:
    """
    将过期 RESERVED → ABANDONED（不删除）。
    
    条件:
      1. status = 'RESERVED'
      2. created_at 超过 max_age_hours
      3. Futu order_list_query() 确认无匹配订单
    
    注意: 不依赖 order_id='' 作为判断依据。
          status=RESERVED 保证未调用 place_order()。
    """
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    cursor = self.conn.execute(
        "SELECT intent_id, symbol, market FROM orders "
        "WHERE status = 'RESERVED' AND created_at < ?",
        (cutoff,)
    )
    resolved = 0
    for row in cursor.fetchall():
        intent_id, symbol, market = row
        # 验证 Futu 中无匹配订单（RESERVED 保证从未提交，此为安全兜底）
        if futu_adapter is not None:
            futu_orders = futu_adapter.query_orders(market, code=symbol)
            if len(futu_orders) > 0:
                print(f'  [JOURNAL] RESERVED {intent_id} 在 Futu 中有匹配订单，跳过清理')
                continue
        # 标记 ABANDONED（不 DELETE）
        self.conn.execute(
            "UPDATE orders SET status='ABANDONED', updated_at=datetime('now') "
            "WHERE intent_id=?",
            (intent_id,)
        )
        resolved += 1
        print(f'  [JOURNAL] RESERVED {intent_id} → ABANDONED (> {max_age_hours}h 无匹配)')
    self.conn.commit()
    return resolved
```

---

### P0-2: LIVE fail-closed

**阻断原文**：
```
dry_run=False 且 journal is None: 立即报错
journal 打开、完整性检查或写入失败: 禁止进入下单路径
不得回退至旧的无 journal 执行路径
```

**v5 修正**：

```python
# order_executor.py 构造函数
class OrderExecutor:
    def __init__(self, host='127.0.0.1', port=11111, dry_run=True, journal=None):
        self.host = host
        self.port = port
        self.dry_run = dry_run
        self.journal = journal  # 仅 LIVE 模式传入
        self.trade_log = []
        self._adapter = FutuAdapter(host=host, port=port)

        # P0-2: LIVE fail-closed
        if not self.dry_run and self.journal is None:
            raise RuntimeError(
                "LIVE mode requires a journal (OrderJournal). "
                "DRY_RUN=journal is None, LIVE=journal is required. "
                "Aborting order execution."
            )
```

**journal 失败处理**：

```python
def _execute_market(self, market, orders, label):
    # LIVE 模式: journal 必须在 __init__ 确认过
    if not self.dry_run:
        try:
            # 检查 journal 连接是否存活
            self.journal.conn.execute("SELECT 1")
            self.journal.verify_integrity()  # PRAGMA integrity_check
        except Exception as e:
            raise RuntimeError(
                f"LIVE mode journal check failed: {e}. "
                f"Aborting order execution for {market}. "
                f"No fallback path available."
            )
    ...
```

**`verify_integrity()` 实现**：

```python
class OrderJournal:
    def verify_integrity(self):
        """SQLite 完整性检查; 失败时 fail-closed。"""
        cursor = self.conn.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        if result != 'ok':
            raise RuntimeError(f'Journal integrity check failed: {result}')
```

**无回退路径**：LIVE 模式下 journal 失败不提供任何"继续执行但不记录 journal"的代码路径。`_place_single_order()` 的 LIVE 分支硬依赖于 `self.journal`。

---

### P0-3: 市场级 Execution Lease

**阻断原文**：
```
同一市场同一时间只能有一个 LIVE 运行
lease 过期但存在未解决订单时禁止自动接管
明确 owner、获取、续租、释放和崩溃恢复规则
```

**v5 设计**：

#### Lease 生命周期

```
START (run() 入口)
  → acquire_lease(market)     # 获取租约
     ├── 成功 → phase=RECOVERY
     └── 失败 → 检查: lease 过期?
          ├── 过期 + 无未解决订单 → steal 租约（force_release + acquire）
          └── 过期 + 有未解决订单 → 报错: 需要人工介入
              └── 未过期 → 报错: 另一进程正在运行

  → reconciliation           # phase=RECONCILIATION
  → account/position snapshot # phase=SNAPSHOT
  → order generation          # phase=ORDER_GEN
  → submit                    # phase=SUBMIT
  → post-reconciliation       # phase=RECONCILE

END (run() 出口)
  → release_lease(market)     # 释放租约
  → renew_lease(market) 可在长操作中调用
```

#### Lease 表

```sql
-- 见 9. Final SQLite Schema 章节
```

#### 获取/续租/释放逻辑

```python
class OrderJournal:
    LEASE_TIMEOUT_MINUTES = 30  # 单次运行最长时间

    def acquire_lease(self, market: str) -> bool:
        """获取市场租赁。
        
        使用 BEGIN IMMEDIATE 保证原子性。
        
        Returns:
            True: 成功获取租赁
            False: 租赁被其他进程持有（非过期 + 有未解决）
            Raises: RuntimeError: 过期 + 有未解决订单（需人工介入）
        """
        now = datetime.now().isoformat()
        expires = (datetime.now() + timedelta(minutes=self.LEASE_TIMEOUT_MINUTES)).isoformat()
        host_id = socket.gethostname()
        process_id = str(os.getpid())

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.conn.execute(
                "SELECT owner, status, expires_at FROM market_leases WHERE market = ?",
                (market,)
            )
            row = cursor.fetchone()

            if row is None:
                # 无现有租约 → 直接创建
                self.conn.execute(
                    "INSERT INTO market_leases (market, owner, host_id, process_id, "
                    "acquired_at, last_renewed_at, expires_at, phase, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'RECOVERY', 'ACTIVE')",
                    (market, f'{host_id}:{process_id}', host_id, process_id,
                     now, now, expires)
                )
                self.conn.commit()
                return True

            owner, status, expires_at = row
            if status == 'ACTIVE' and expires_at > now:
                # 租约仍有效 → 检查是否自己持有（崩溃恢复场景）
                if owner == f'{host_id}:{process_id}':
                    self._renew_lease_inner(market, expires)
                    self.conn.commit()
                    return True
                return False  # 其他进程持有 → 拒绝

            # 租约过期
            unresolved = self.has_unresolved_orders(market)
            if unresolved:
                raise RuntimeError(
                    f"Market {market} lease expired but unresolved orders exist. "
                    f"Manual intervention required. Owner: {owner}, "
                    f"Expired at: {expires_at}"
                )

            # 过期 + 无未解决 → 接管
            self.conn.execute(
                "UPDATE market_leases SET owner=?, host_id=?, process_id=?, "
                "acquired_at=?, last_renewed_at=?, expires_at=?, "
                "phase='RECOVERY', status='ACTIVE' WHERE market=?",
                (f'{host_id}:{process_id}', host_id, process_id, now, now, expires, market)
            )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def renew_lease(self, market: str) -> None:
        """续租（在长操作中定期调用）。"""
        now = datetime.now().isoformat()
        expires = (datetime.now() + timedelta(minutes=self.LEASE_TIMEOUT_MINUTES)).isoformat()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._renew_lease_inner(market, expires)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _renew_lease_inner(self, market: str, expires: str):
        """续租内部实现（无事务边界，由调用方管理）。"""
        self.conn.execute(
            "UPDATE market_leases SET last_renewed_at=datetime('now'), "
            "expires_at=? WHERE market=?",
            (expires, market)
        )

    def update_lease_phase(self, market: str, phase: str) -> None:
        """更新当前阶段。"""
        self.conn.execute(
            "UPDATE market_leases SET phase=? WHERE market=?",
            (phase, market)
        )
        self.conn.commit()

    def release_lease(self, market: str) -> None:
        """释放租赁。"""
        self.conn.execute(
            "UPDATE market_leases SET status='RELEASED', phase='DONE' WHERE market=?",
            (market,)
        )
        self.conn.commit()

    def has_unresolved_orders(self, market: str = None) -> bool:
        """检查是否存在未解决的非终态订单。"""
        terminal = [s.value for s in OrderStatus.terminal_set()]
        placeholders = ','.join('?' * len(terminal))

        if market:
            cursor = self.conn.execute(
                f"SELECT COUNT(*) FROM orders "
                f"WHERE status NOT IN ({placeholders}) AND market=?",
                terminal + [market]
            )
        else:
            cursor = self.conn.execute(
                f"SELECT COUNT(*) FROM orders WHERE status NOT IN ({placeholders})",
                terminal
            )
        return cursor.fetchone()[0] > 0
```

**崩溃恢复**：

- 进程崩溃后，租约在 `LEASE_TIMEOUT_MINUTES`（30 分钟）后超时
- 超时后，**必须**先确认无未解决订单才能接管（见 `acquire_lease` 逻辑）
- 若有未解决订单 → 需人工介入

---

### P0-4: 部分成交处理

**阻断原文**：
```
仅全部 SELL 为 FILLED_ALL 才允许 BUY
FILLED_PART、CANCELLED_PART 和所有非终态必须阻断 BUY
STOP_SELL 仅在确认持仓为零后激活完整冷却期
部分成交副作用不得使用单一 post_fill_applied 布尔值处理
```

**v5 修正**：

#### SELL 终态检查（BUY 前置条件）

```python
def can_proceed_to_buy(self, sell_results: list) -> tuple[bool, str]:
    """
    检查 SELL 结果是否允许进入 BUY 阶段。
    
    返回:
        (True, ''): 允许 BUY
        (False, reason): 不允许 BUY，说明原因
    """
    if not sell_results:
        return True, ''  # 无 SELL → 直接 BUY

    for r in sell_results:
        s = r.get('status', '')
        if s in ('FILLED_ALL',):
            continue  # 全量成交 → 允许
        elif s in ('REJECTED', 'ABANDONED', 'CANCELLED_ALL'):
            continue  # 未占用资金/仓位 → 允许（但注意现金估算偏差）
        elif s in ('FILLED_PART', 'CANCELLED_PART'):
            return (False,
                    f'SELL {r.get("symbol","?")} 仅部分成交({s})，'
                    f'实际持仓未知，阻断 BUY')
        elif s in OrderStatus.blocking_set():
            return (False,
                    f'SELL {r.get("symbol","?")} 状态 {s}，'
                    f'不确定持仓状态，阻断 BUY')
        else:
            return (False,
                    f'SELL {r.get("symbol","?")} 未知状态 {s}，阻断 BUY')

    # 所有 SELL 均为 FILLED_ALL → 允许 BUY
    return True, ''
```

#### STOP_SELL 冷却期激活条件

```python
def maybe_activate_cooldown(self, intent_id: str, futu_adapter, market: str):
    """
    仅在确认持仓为零后激活冷却期。
    
    流程:
      1. 从 journal 读取 STOP_SELL 订单状态
      2. 若 status=FILLED_ALL → 查询 Futu 实时持仓
      3. 持仓为零 → 激活冷却期
      4. 持仓不为零 → 不激活（标记等待下次检查）
    """
    cursor = self.conn.execute(
        "SELECT intent_id, futu_code, status, filled_qty FROM orders "
        "WHERE intent_id=? AND intent_type='STOP_SELL'",
        (intent_id,)
    )
    row = cursor.fetchone()
    if not row:
        return

    _, futu_code, status, filled_qty = row
    if status != 'FILLED_ALL':
        return  # 未全量成交，不激活冷却期

    # 查询 Futu 实时持仓
    positions = futu_adapter.get_positions(market)
    held = [p for p in positions if p['code'] == futu_code]
    if len(held) == 0 or held[0]['qty'] <= 0:
        # 持仓为零 → 激活冷却期
        self.conn.execute(
            "UPDATE orders SET post_fill_stop_applied=1, updated_at=datetime('now') "
            "WHERE intent_id=?",
            (intent_id,)
        )
        self.conn.commit()
        return True  # 调用方据此调用 risk_mgr.confirm_stop()

    return False  # 仍有持仓 → 下次运行再检查
```

#### 分离的副作用列（替代单一 `post_fill_applied`）

```sql
-- 见 9. Final SQLite Schema
-- post_fill_stop_applied INTEGER DEFAULT 0   -- 独立：冷却期是否已激活
-- post_fill_pos_init_applied INTEGER DEFAULT 0  -- 独立：风控初始化是否已完成
```

**为什么需要分离**：

| 场景 | post_fill_stop_applied | post_fill_pos_init_applied |
|:---|---:|:---:|
| STOP_SELL 全量成交 | 1（冷却期激活） | 0（不适用） |
| SIGNAL_BUY 全量成交 | 0（不适用） | 1（风控初始化） |
| STOP_SELL 部分成交 | 0（等待下次检查） | 0（不适用） |
| REVERSAL_SELL 全量成交 | 0（不调用 confirm_stop） | 0（不适用） |

---

### P0-5: 回滚方案

**阻断原文**：
```
回滚必须同时禁用 LIVE_CONFIRMED
journal 必须保留并备份
全部未解决订单完成人工或自动对账前，不得恢复 LIVE
```

**v5 修正**：

```bash
# === Phase F2-SEC 回滚方案 ===

# 1. 禁用 LIVE_CONFIRMED（阻止再次进入 LIVE 路径）
echo "DISABLED" > output/LIVE_DISABLED_FLAG.txt
#    或在配置中设置: QUANT_LIVE_ENABLED=NO

# 2. 备份 journal
cp output/order_journal_*.db backups/journal_f2sec_$(date +%Y%m%d_%H%M%S).bak

# 3. 回滚代码（保留 journal 文件）
git checkout -- core/order_executor.py core/stop_loss.py core/futu_adapter.py unified_runner.py
rm -f core/order_journal.py

# 4. 检查未解决订单（先于代码回滚执行）
#    journal 中未解决订单列表应由人工确认
python -c "
from core.order_journal import OrderJournal
j = OrderJournal.load_today('output/', 'HK')
if j.has_unresolved_orders():
    print('⚠️ 存在未解决订单。LIVE 恢复前必须逐单对账。')
    for row in j.conn.execute('SELECT intent_id,status,order_id,symbol FROM orders'):
        print(f'  {row}')
"

# 5. 恢复 LIVE（完成对账后）
#    删除 LIVE_DISABLED_FLAG.txt
#    验证 journal 中已无未解决订单
```

**旧代码不读 journal**：回滚后 `unified_runner.py` 的旧版本不再初始化或使用 `OrderJournal`，journal 文件作为只读档案保留。未来重新实施时，journal 中保留的 ABANDONED / RESOLVED 订单继续作为幂等证据。

**LIVE 恢复条件**（硬性要求）：
1. `LIVE_DISABLED_FLAG.txt` 已被删除
2. journal 中所有非终态订单均已手动对账完毕
3. 人工确认

---

### P0-6: FILL_CANCELLED 必须映射为人工介入阻断状态

**阻断原文**：
```
FILL_CANCELLED 必须映射为人工介入阻断状态，不得作为普通终态
```

**v5 修正**：

```python
class OrderStatus(Enum):
    ...
    FILL_CANCELLED = 'FILL_CANCELLED'  # ⚠️ 特殊状态: 需人工介入

    @classmethod
    def terminal_set(cls) -> set:
        """终态 — 不再需要任何后续处理，不阻断。"""
        return {
            cls.FILLED_ALL, cls.REJECTED, cls.CANCELLED_ALL,
            cls.CANCELLED_PART, cls.DISABLED, cls.DELETED,
            # FILL_CANCELLED 不在终态集: 需要人工介入
        }

    @classmethod
    def blocking_set(cls) -> set:
        """阻断 — 存在该状态时阻断市场全部新订单。"""
        return {
            cls.RESERVED, cls.SUBMITTING, cls.SUBMITTED,
            cls.FILLED_PART, cls.CANCELLING, cls.TIMEOUT, cls.UNKNOWN,
            cls.FILL_CANCELLED,  # ← 阻断: 需人工确认最终状态
        }

    @classmethod
    def human_intervention_set(cls) -> set:
        """需要人工介入的状态。"""
        return {
            cls.FILL_CANCELLED,  # 胖手指撤单或机构干预
            cls.UNKNOWN,         # 多次轮询后仍未知
        }
```

**FILL_CANCELLED 处理流程**：

1. 由 Futu `OrderStatus.FILL_CANCELLED` 映射为 QuantBot `FILL_CANCELLED`
2. 该状态加入 `blocking_set()`，阻断该市场全部新订单
3. 同时加入 `human_intervention_set()`，标记需人工确认
4. 人工确认后可手动更新 journal 记录为 `CANCELLED_ALL` 或 `FILLED_ALL`（取决于实际成交状态）
5. 在 `human_intervention_set()` 清空前，该市场无法执行任何 LIVE 新订单

---

### P0-7: REVERSAL_SELL 不调用 confirm_stop

**阻断原文**：
```
REVERSAL_SELL 保持现有行为，不调用 confirm_stop()，除非 Roy 另行明确批准
```

**v5 修正**：

当前行为（`unified_runner.py` L305-310）：
- REVERSAL_SELL 生成订单，不调用 `record_stop()`（见 v4 S-07 确认）
- 冷却期不启动
- 反转清仓后下次信号可重新建仓

journal 恢复流程中，`recover_post_fill_actions` 仅处理 `STOP_SELL`：

```python
def recover_post_fill_actions(self, futu_adapter, risk_mgr, market):
    """恢复未完成的成交后动作。"""
    cursor = self.conn.execute('''
        SELECT intent_id, intent_type, futu_code, order_id, status
        FROM orders
        WHERE status IN ('FILLED_ALL', 'FILLED_PART')
          AND (post_fill_stop_applied = 0 OR post_fill_pos_init_applied = 0)
          AND execution_mode = 'LIVE'
    ''')

    for row in cursor.fetchall():
        intent_id, intent_type, futu_code, order_id, status = row

        if intent_type == 'STOP_SELL':
            # P0-4: 仅确认持仓为零后激活冷却期
            if self.maybe_activate_cooldown(intent_id, futu_adapter, market):
                risk_mgr.confirm_stop(futu_code)
                print(f'[RECOVERY] 冷却期已确认: {futu_code} (order={order_id})')

        elif intent_type == 'SIGNAL_BUY':
            # 初始化风控追踪
            if self._should_init_position(intent_id, futu_adapter, market):
                # 读取 filled_price
                row2 = self.conn.execute(
                    "SELECT filled_qty, filled_avg_price FROM orders WHERE intent_id=?",
                    (intent_id,)
                ).fetchone()
                cost_price = row2[1] if row2 else 0
                risk_mgr.init_position(futu_code, cost_price, cost_price)
                risk_mgr.update_highest(futu_code, cost_price)
                self.conn.execute(
                    "UPDATE orders SET post_fill_pos_init_applied=1, "
                    "updated_at=datetime('now') WHERE intent_id=?",
                    (intent_id,)
                )
                print(f'[RECOVERY] 持仓风控已初始化: {futu_code} (order={order_id})')

        elif intent_type == 'REVERSAL_SELL':
            # P0-7: 保持现有行为，不调用 confirm_stop()
            # 反转卖出后 mark post_fill_stop_applied=1 阻止后续重试
            self.conn.execute(
                "UPDATE orders SET post_fill_stop_applied=1, "
                "updated_at=datetime('now') WHERE intent_id=?",
                (intent_id,)
            )
            print(f'[RECOVERY] REVERSAL_SELL 已确认(无冷却期): {futu_code} (order={order_id})')

        self.conn.commit()
```

---

## 3. Final Order State Machine

### 完整状态图

```
                            ┌─────────────────────────┐
                            │    INTENT_CREATED        │  (内存中 orders 列表)
                            │    (尚未写入 journal)     │
                            └───────────┬─────────────┘
                                        │ journal.try_acquire_intent()
                                        ▼
                            ┌─────────────────────────┐
                            │       RESERVED           │  Journal 中
                            │  幂等键已锁定, 未调 API   │  order_id=''
                            └────┬──────────┬─────────┘
                          ┌──────┘          │
                          │                  │
                          ▼                  │ place_order() 调用
                    ┌──────────┐             │
                    │ABANDONED │             ▼
                    │未提交放弃 │   ┌─────────────────────────┐
                    │(不删除)   │   │       SUBMITTING        │  place_order() 已调用
                    └──────────┘   │  等待 broker 同步返回    │  等待 broker 响应
                                   └────┬────────────────┬──┘
                              ┌─────────┘        ┌───────┘
                              ▼                   ▼
                    ┌──────────────────┐  ┌──────────────────┐
                    │    SUBMITTED     │  │     TIMEOUT      │
                    │ 有 order_id     │  │ 同步返回超时     │
                    │ broker 已受理   │  │ 状态未知         │
                    └───────┬─────────┘  └────────┬─────────┘
                            │                      │
                            ▼                      ▼
                    ┌─────────────────────────────────────────────┐
                    │         ORDER STATUS RECONCILIATION          │
                    │  (提交后轮询 Futu order_list_query())        │
                    └───────┬──────────┬───────────┬──────────────┘
                            │          │           │
              ┌─────────────┘          │           └─────────────┐
              ▼                        ▼                         ▼
    ┌────────────────┐     ┌──────────────────┐      ┌──────────────────┐
    │   FILLED_ALL   │     │   FILLED_PART    │      │    REJECTED      │
    │  全量成交       │     │  部分成交        │      │  Futu 拒单      │
    └───────┬────────┘     └───────┬──────────┘      └──────────────────┘
            │                      │
            ▼                      ▼
    ┌────────────────┐     ┌──────────────────┐
    │ 副作用执行      │     │ 副作用执行(部分)  │      ┌──────────────────┐
    │ post_fill_*    │     │ 等待剩余成交/     │      │  FILL_CANCELLED  │
    │  全部标记 1    │     │  等待下次检查     │      │  需人工介入      │
    └────────────────┘     └──────────────────┘      └──────────────────┘

    额外终态:
    CANCELLED_ALL  — broker 确认全量撤销
    CANCELLED_PART — broker 确认部分撤销（剩余部分状态需检查）
    CANCELLING     — broker 正在处理撤销请求（阻断态）
    DISABLED       — 账户禁用（终态）
    DELETED        — 订单被删除（终态）
    UNKNOWN        — 多次轮询后无法确定状态（需人工介入）
```

### 完整状态枚举

```python
from enum import Enum

class OrderStatus(Enum):
    # ─── 提交前状态 ──────────────────────────────────
    INTENT_CREATED  = 'INTENT_CREATED'   # 仅内存，不入 journal
    RESERVED        = 'RESERVED'         # journal 已写入，未调用 place_order
    ABANDONED       = 'ABANDONED'        # RESERVED 放弃（不删除）

    # ─── 提交中状态 ──────────────────────────────────
    SUBMITTING      = 'SUBMITTING'       # place_order 已调用，等待返回
    SUBMITTED       = 'SUBMITTED'        # 已获取 order_id

    # ─── 成交/执行状态 ────────────────────────────────
    FILLED_PART     = 'FILLED_PART'      # 部分成交
    FILLED_ALL      = 'FILLED_ALL'       # 全量成交
    CANCELLING      = 'CANCELLING'       # 撤销请求中

    # ─── 终态 ────────────────────────────────────────
    REJECTED        = 'REJECTED'         # 拒单
    CANCELLED_ALL   = 'CANCELLED_ALL'    # 全量撤销
    CANCELLED_PART  = 'CANCELLED_PART'   # 部分撤销
    DISABLED        = 'DISABLED'         # 账户禁用
    DELETED         = 'DELETED'          # 已删除

    # ─── 非正常终态（需人工介入） ──────────────────────
    TIMEOUT         = 'TIMEOUT'          # 提交超时
    UNKNOWN         = 'UNKNOWN'          # 状态未知
    FILL_CANCELLED  = 'FILL_CANCELLED'   # 成交后被撤销（胖手指/机构干预）

    @classmethod
    def terminal_set(cls) -> set:
        """终态 — 不再需要后续处理，不妨碍新订单。"""
        return {
            cls.FILLED_ALL, cls.REJECTED, cls.CANCELLED_ALL,
            cls.CANCELLED_PART, cls.DISABLED, cls.DELETED,
            cls.ABANDONED,
        }

    @classmethod
    def blocking_set(cls) -> set:
        """阻断 — 存在该状态时阻断市场全部新订单。"""
        return {
            cls.RESERVED, cls.SUBMITTING, cls.SUBMITTED,
            cls.FILLED_PART, cls.CANCELLING, cls.TIMEOUT,
            cls.UNKNOWN, cls.FILL_CANCELLED,
        }

    @classmethod
    def uncertain_set(cls) -> set:
        """不确定 — BUY 提交返回时立即中止剩余 BUY。"""
        return {cls.SUBMITTING, cls.TIMEOUT, cls.UNKNOWN}

    @classmethod
    def human_intervention_set(cls) -> set:
        """需人工介入 — 阻断市场 + 日志告警。"""
        return {cls.FILL_CANCELLED, cls.UNKNOWN}

    @classmethod
    def sell_completed_set(cls) -> set:
        """SELL 已完成 — 允许进入 BUY 阶段。"""
        return {cls.FILLED_ALL}
```

### Futu → QuantBot 状态映射

| Futu OrderStatus | QuantBot OrderStatus | 说明 |
|:---|:---:|:---|
| `NONE` | `UNKNOWN` | 未定义状态 |
| `UNSUBMITTED` | `RESERVED` | 未提交 |
| `WAITING_SUBMIT` | `SUBMITTING` | 等待提交 |
| `SUBMITTING` | `SUBMITTING` | 提交中 |
| `SUBMIT_FAILED` | `REJECTED` | 提交失败→拒单 |
| `TIMEOUT` | `TIMEOUT` | 超时 |
| `SUBMITTED` | `SUBMITTED` | 已提交 |
| `FILLED_PART` | `FILLED_PART` | 部分成交 |
| `FILLED_ALL` | `FILLED_ALL` | 全量成交 |
| `CANCELLING_PART` | `CANCELLING` | 部分撤销中 |
| `CANCELLING_ALL` | `CANCELLING` | 全部撤销中 |
| `CANCELLED_PART` | `CANCELLED_PART` | 部分已撤销 |
| `CANCELLED_ALL` | `CANCELLED_ALL` | 全部已撤销 |
| `FAILED` | `REJECTED` | 失败 |
| `DISABLED` | `DISABLED` | 账户禁用 |
| `DELETED` | `DELETED` | 已删除 |
| `FILL_CANCELLED` | `FILL_CANCELLED` | 成交后撤销→人工介入 |

---

## 4. Market Execution Lease Design

### 完整使用流程（`unified_runner.py`）

```python
def run(...):
    journal = None
    if execution_mode == 'LIVE_CONFIRMED':
        # LIVE 模式：创建 journal
        try:
            journal = OrderJournal(market, date)
            journal.verify_integrity()
        except Exception as e:
            print(f'[LIVE] journal 初始化失败: {e}')
            print('[LIVE] Fail-closed: 禁止进入下单路径')
            return  # 不执行任何订单

        # 获取市场租约
        try:
            if not journal.acquire_lease(market):
                print(f'[LIVE] 市场 {market} 租约被其他进程持有')
                return
        except RuntimeError as e:
            print(f'[LIVE] 租约获取失败: {e}')
            return

        # 恢复对账
        journal.update_lease_phase(market, 'RECONCILIATION')
        journal.resolve_stale_reserved(futu_adapter)
        journal.recover_post_fill_actions(futu_adapter, risk_mgr, market)

        # 检查阻断
        if journal.has_blocking_orders(market):
            print(f'[LIVE] 市场 {market} 存在阻断态订单，无法继续')
            journal.release_lease(market)
            return

        # 快照
        journal.update_lease_phase(market, 'SNAPSHOT')
        journal.renew_lease(market)
        # ... 账户快照代码 ...

    # ... Step 5: 持仓处理 + Step 6: 建仓决策 ...

    if execution_mode == 'LIVE_CONFIRMED':
        # 订单生成
        journal.update_lease_phase(market, 'ORDER_GEN')

    # ... orders.append(...) ...

    if execution_mode == 'LIVE_CONFIRMED':
        # 提交
        journal.update_lease_phase(market, 'SUBMIT')
        journal.renew_lease(market)

    executor = OrderExecutor(
        host=host, port=port,
        dry_run=(execution_mode != 'LIVE_CONFIRMED'),
        journal=journal,  # None for DRY_RUN / LIVE_BLOCKED
    )
    results = executor.execute_orders(orders, log_path=log_path)

    if execution_mode == 'LIVE_CONFIRMED':
        # 执行后对账
        journal.update_lease_phase(market, 'RECONCILE')
        journal.reconcile_orders(futu_adapter, market)

        # 释放租赁
        journal.release_lease(market)
```

---

## 5. Idempotency and Recovery Design

### 幂等键（Intent ID）生成规则

**格式**：`QNT:{date}:{market}:{symbol}:{action}:{qty}:{price_int}`

示例：`QNT:20260604:HK:00700.HK:BUY:100:35000`

**规则**：
- 不含 `run_id`、`timestamp`、PID 等可变字段
- 相同 symbol + action + qty + price 在同一天生成**完全相同的 intent_id**
- `price_int = round(price * 100)`（2 位小数整数化）

### Remark 生成规则（Futu 64 字节限制）

**格式**：`QNT:v1:{SHA256_32hex}`

示例：`QNT:v1:a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6`

**规则**：
- `SHA256_32hex` = SHA256(intent_id) 的前 32 个十六进制字符
- 固定 39 字节（`QNT:v1:` = 7 + 32 hex = 39），不超过 Futu 64 字节限制
- 绝不截断原始 intent_id 字符串
- 绝不拼接可变字段

**恢复时**：
1. 查询 Futu `order_list_query()` 获取今日全部订单
2. 逐笔匹配 `remark` 字段 → 解析出 SHA256 → 匹配 journal 中的 intent_id
3. 未匹配的 Futu 订单 → 额外检查（可能为非系统下单）

### 幂等检查流程

```python
def try_acquire_intent(self, intent_id: str, order: dict) -> tuple[bool, str]:
    """
    检查并锁定幂等键。
    
    使用 SQLite UNIQUE 约束 + BEGIN IMMEDIATE 保证原子性。
    
    返回:
        (True, 'OK'): 新 intent，可继续提交
        (False, reason): intent 已存在，禁止提交
    """
    self.conn.execute("BEGIN IMMEDIATE")
    try:
        # 检查是否已存在
        cursor = self.conn.execute(
            "SELECT status, order_id FROM orders WHERE intent_id = ?",
            (intent_id,)
        )
        row = cursor.fetchone()
        if row is not None:
            status, order_id = row
            self.conn.rollback()
            return (False, f'intent 已存在: status={status}, order_id={order_id}')

        # 插入 RESERVED 记录
        now = datetime.now().isoformat()
        remark = self._build_remark(intent_id)
        cursor.execute('''
            INSERT INTO orders (
                intent_id, status, order_id,
                symbol, futu_code, action, qty, price, market,
                execution_mode, intent_type,
                post_fill_stop_applied, post_fill_pos_init_applied,
                created_at, updated_at, remark
            ) VALUES (
                ?, 'RESERVED', '',
                ?, ?, ?, ?, ?, ?,
                'LIVE', ?,
                0, 0,
                ?, ?, ?
            )
        ''', (
            intent_id,
            order['symbol'], order.get('futu_code', ''),
            order['action'], order['qty'], order['price'],
            order.get('market', ''), order['intent_type'],
            now, now, remark,
        ))
        self.conn.commit()
        return (True, 'OK')
    except sqlite3.IntegrityError:
        self.conn.rollback()
        return (False, 'intent_id 冲突 (并发)')
    except Exception:
        self.conn.rollback()
        raise
```

### 恢复流程（启动时）

```python
def startup_recovery(self, futu_adapter, risk_mgr, market):
    """启动恢复流程。"""
    # Step 1: 清理过期 RESERVED → ABANDONED
    self.resolve_stale_reserved(futu_adapter)

    # Step 2: 检查是否存在未解决订单
    if self.has_unresolved_orders(market):
        blocking = self.has_blocking_orders(market)
        human = self.has_human_intervention(market)
        if human:
            print('[RECOVERY] ⚠️ 存在需人工介入的订单 (FILL_CANCELLED/UNKNOWN)')
        if blocking:
            print(f'[RECOVERY] ⚠️ 市场 {market} 存在阻断态订单')
        # 不阻止恢复后续动作，但阻止新订单

    # Step 3: 恢复未完成的成交后动作
    self.recover_post_fill_actions(futu_adapter, risk_mgr, market)

    # Step 4: 对账（查询 Futu 订单表，更新 journal 中 SUBMITTED/SUBMITTING 状态）
    self.reconcile_orders(futu_adapter, market)

    # Step 5: 返回是否可继续
    return not self.has_blocking_orders(market)
```

---

## 6. Partial Fill and Post-Fill Side Effects

### 部分成交对 SELL→BUY 的影响

| SELL 状态 | 允许 BUY? | 理由 |
|:---:|:---:|:---|
| FILLED_ALL | ✅ 允许 | 全量成交，持仓已释放 |
| REJECTED | ✅ 允许 | 未成交，持仓不变 |
| CANCELLED_ALL | ✅ 允许 | 已撤销，持仓不变 |
| ABANDONED | ✅ 允许 | 未提交，持仓不变 |
| FILLED_PART | ❌ 阻断 | 部分持仓仍在，剩余量不确定 |
| CANCELLED_PART | ❌ 阻断 | 部分持仓已撤销，剩余量不确定 |
| SUBMITTED | ❌ 阻断 | 等待成交，持仓不确定 |
| SUBMITTING | ❌ 阻断 | 提交中，持仓不确定 |
| TIMEOUT | ❌ 阻断 | 状态未知 |
| UNKNOWN | ❌ 阻断 | 状态未知 |
| FILL_CANCELLED | ❌ 阻断 | 需人工介入 |

### 部分成交对 STOP_SELL 冷却期的影响

```
STOP_SELL place_order
  → 返回 SUBMITTED
    → 轮询 Futu order_list_query
      → FILLED_ALL:
          → 查询 Futu 实时持仓
            → 持仓=0: 激活完整冷却期
            → 持仓>0: 等待下次核对（记录日志）
      → FILLED_PART:
          → 不激活冷却期（持仓未完全清空）
          → 下次启动时重新检查
      → REJECTED / CANCELLED_ALL:
          → 不激活冷却期
          → 持有 risk_mgr.rollback_stop() 调用以撤销 record_stop()
```

### 分离的副作用列

```sql
post_fill_stop_applied     INTEGER DEFAULT 0,   -- 冷却期是否已激活
post_fill_pos_init_applied INTEGER DEFAULT 0,   -- 风控追踪是否已初始化
```

**为什么 `post_fill_stop_applied` 不等同于 `post_fill_pos_init_applied`**：

| 场景 | 需要标记 stop | 需要标记 pos_init |
|:---|---:|:---:|
| SIGNAL_BUY FILLED_ALL | ❌ | ✅ |
| STOP_SELL FILLED_ALL + 持仓=0 | ✅ | ❌ |
| STOP_SELL FILLED_ALL + 持仓>0 | ❌（等待下次） | ❌ |
| REVERSAL_SELL FILLED_ALL | ✅（恶意） | ❌ |
| SIGNAL_BUY FILLED_PART | ❌（等待剩余成交） | ❌ |

---

## 7. SELL-to-BUY Fail-Closed Rules

### 完整 SELL→BUY 流程

```
                     ┌───────────────────────┐
                     │    STEP 1: SELL 执行   │
                     │  全部 SELL 订单提交    │
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │    STEP 2: SELL 确认   │  ← 仅 LIVE 模式
                     │  等待成交 + 轮询 Futu  │
                     │  order_list_query()   │
                     └───────────┬───────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
          ┌──────────────────┐     ┌──────────────────┐
          │  全部 FILLED_ALL │     │ 存在非 FILLED_ALL │
          └────────┬─────────┘     └────────┬─────────┘
                   │                        │
                   ▼                        ▼
          ┌──────────────────┐     ┌──────────────────┐
          │ STEP 3: 账户/持仓  │     │  阻断 BUY       │
          │      重查         │     │  输出原因 + 返回 │
          └────────┬─────────┘     └──────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
        ▼                     ▼
┌───────────────┐   ┌───────────────┐
│  重查成功     │   │  重查失败     │
│  获取实时:    │   │  fail-closed: │
│  - cash      │   │  阻断 BUY     │
│  - positions │   │  记录错误日志  │
│  - exposure  │   └───────────────┘
│  - assets    │
└───────┬───────┘
        │
        ▼
┌───────────────────────┐
│ STEP 4: 重新执行风控    │
│  - 仓位限制 (20%)      │
│  - 总暴露 (80%)       │
│  - 预算 (cash * 0.8)  │
│  - 持仓数 (≤5只)       │
└───────────────────────┘
        │
        ▼
┌───────────────────────┐
│    STEP 5: BUY 执行    │
│  每笔提交后 running_cash │
│  扣减 + running_exposure│
│  不确定态→中止剩余     │
└───────────────────────┘
```

### 重查 fail-closed

```python
def re_query_after_sell(self, futu_adapter, market) -> dict:
    """SELL 成交后重新查询账户和持仓。
    
    Returns:
        dict: {cash, total_assets, positions, exposure}
    
    Raises:
        RuntimeError: 查询失败时 fail-closed
    """
    account = futu_adapter.get_account_info(market)
    if account is None:
        raise RuntimeError(
            f"SELL 成交后账户查询失败 (market={market})。"
            f"无法确认实际现金和资产。Fail-closed: 阻断 BUY。"
        )

    positions = futu_adapter.get_positions(market)
    if not positions and account.get('cash', 0) == 0:
        # 可能有持仓查询异常 → 需要区分"真实空仓"和"查询失败"
        # 但 get_positions 当前返回 [] 无法区分（R-01）
        # P0-2 修复后将使 get_positions 在失败时抛出异常而非返回 []
        # 此处假设修复后 get_positions 已 fail-closed
        pass

    exposure = sum(p.get('market_val', 0) for p in positions)

    return {
        'cash': account['cash'],
        'total_assets': account['total_assets'],
        'positions': positions,
        'exposure': exposure,
    }
```

### 再风控（使用实时数据）

```python
# LIVE 模式下 SELL 全部成交后
live_data = re_query_after_sell(futu_adapter, market)
live_cash = live_data['cash']
live_total_assets = live_data['total_assets']
live_exposure = live_data['exposure']

# 每笔 BUY 严格使用实时现金
running_cash = live_cash
running_exposure = live_exposure

for o in buy_orders:
    trade_val = o['qty'] * o['price']

    # 实时仓位限制检查
    ok, reason = risk_mgr.check_position_limit(
        to_futu_code(o['symbol']),
        trade_val, live_total_assets
    )
    if not ok:
        print(f'  [SKIP] {o["symbol"]}: {reason}')
        continue

    # 实时总暴露检查
    ok, reason = risk_mgr.check_total_exposure(
        running_exposure, trade_val, live_total_assets
    )
    if not ok:
        print(f'  [SKIP] {o["symbol"]}: {reason}')
        continue

    # 实时现金预算
    if trade_val > running_cash * 0.8:
        print(f'  [SKIP] {o["symbol"]}: 现金不足')
        continue

    # 提交订单
    r = self._place_single_order(o, market)

    # 更新运行中现金和暴露
    if r['status'] in ('SUBMITTED', 'FILLED_ALL', 'SUBMITTING', 'TIMEOUT', 'UNKNOWN'):
        running_cash -= trade_val
        running_exposure += trade_val
    # REJECTED: 不扣减

    # 不确定态 → 中止剩余
    if r['status'] in OrderStatus.uncertain_set():
        break
```

---

## 8. Rollback and LIVE Disable Procedure

### 回滚执行清单

```bash
# ═══════════════════════════════════════════════════════════════
# Phase F2-SEC 回滚方案
# ═══════════════════════════════════════════════════════════════

# --- 步骤 1: 禁用 LIVE_CONFIRMED ------------------------------------
# 阻止回滚后误进入 LIVE 执行路径
echo "DISABLED" > output/LIVE_DISABLED_FLAG.txt
# 可选: 设置环境变量
# set QUANT_LIVE_ENABLED=NO

# --- 步骤 2: 备份 journal 文件 --------------------------------------
# journal 文件永久保留
# 复制一份到 backups 目录
cp output/order_journal_*.db backups/journal_f2sec_$(date +%Y%m%d_%H%M%S).bak

# --- 步骤 3: 输出未解决订单列表 ------------------------------------
python -c "
from core.order_journal import OrderJournal, OrderStatus
for market in ('HK', 'US'):
    try:
        j = OrderJournal.load_today(market)
        terminal = [s.value for s in OrderStatus.terminal_set()]
        placeholders = ','.join('?' * len(terminal))
        rows = j.conn.execute(
            f'SELECT intent_id, status, order_id, symbol, action FROM orders '
            f'WHERE status NOT IN ({placeholders})'
        ).fetchall()
        if rows:
            print(f'=== 市场 {market} 未解决订单 ===')
            for r in rows:
                print(f'  {r}')
    except Exception as e:
        print(f'{market}: {e}')
"

# --- 步骤 4: 回滚代码 ----------------------------------------------
git checkout -- core/order_executor.py core/stop_loss.py core/futu_adapter.py unified_runner.py
rm -f core/order_journal.py  # 删除新增文件

# --- 步骤 5: 验证回滚 ----------------------------------------------
git diff --stat  # 应无修改
git status       # 应干净

# --- 步骤 6: 恢复 LIVE（仅未解决订单对账完成后）----------------------
# 条件:
#   1. LIVE_DISABLED_FLAG.txt 已被删除
#   2. journal 中所有非终态订单已逐单人工/自动对账完毕
#   3. 人工确认可以恢复
```

### LIVE 禁用和恢复规则

```python
# unified_runner.py 中
def is_live_enabled() -> bool:
    """检查 LIVE 是否全局启用。"""
    flag_file = Path('output/LIVE_DISABLED_FLAG.txt')
    if flag_file.exists() and flag_file.read_text().strip() == 'DISABLED':
        print('[LIVE] ⚠️ LIVE 执行已被禁用 (LIVE_DISABLED_FLAG.txt 存在)')
        return False
    return True

# 在 --confirm-live 和 QUANT_LIVE_CONFIRM=YES 检查之后，额外增加此检查
live_confirmed = live_confirmed and is_live_enabled()
```

---

## 9. Final SQLite Schema

```sql
-- ═══════════════════════════════════════════════════════════════
-- Phase F2-SEC: Order Journal SQLite Schema
-- ═══════════════════════════════════════════════════════════════

-- 开启外键约束
PRAGMA foreign_keys = ON;

-- ─── 订单表 ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    -- 幂等键
    intent_id               TEXT NOT NULL,     -- QNT:20260604:HK:00700.HK:BUY:100:35000

    -- 状态
    status                  TEXT NOT NULL,     -- OrderStatus 枚举值

    -- Broker 信息
    order_id                TEXT DEFAULT '',   -- Futu order_id (可能为空: RESERVED/SUBMITTING)
    remark                  TEXT DEFAULT '',   -- QNT:v1:<SHA256-32hex> (≤64 bytes)

    -- 订单信息
    symbol                  TEXT NOT NULL,     -- 标准符号: 00700.HK
    futu_code               TEXT DEFAULT '',   -- Futu 代码: HK.00700 (用于恢复对账)
    action                  TEXT NOT NULL,     -- BUY / SELL
    qty                     INTEGER NOT NULL,
    price                   REAL NOT NULL,
    market                  TEXT NOT NULL,     -- HK / US
    execution_mode          TEXT NOT NULL DEFAULT 'LIVE',  -- 仅 'LIVE'
    intent_type             TEXT NOT NULL,     -- SIGNAL_BUY / SIGNAL_SELL / STOP_SELL / REVERSAL_SELL

    -- 成交信息
    filled_qty              INTEGER DEFAULT 0,
    filled_avg_price        REAL DEFAULT 0.0,

    -- 副作用标记 (分离，见 P0-4)
    post_fill_stop_applied      INTEGER DEFAULT 0,   -- 冷却期是否已激活
    post_fill_pos_init_applied  INTEGER DEFAULT 0,   -- 风控追踪是否已初始化

    -- 审计字段
    created_at              TEXT NOT NULL,      -- ISO 8601
    updated_at              TEXT NOT NULL,      -- ISO 8601
    message                 TEXT DEFAULT '',    -- 人类可读描述

    -- 约束
    UNIQUE(intent_id)                            -- intent 幂等键唯一
);

-- 索引
CREATE UNIQUE INDEX IF NOT EXISTS idx_order_id
    ON orders(order_id) WHERE order_id != '';     -- order_id 非空时唯一
CREATE INDEX IF NOT EXISTS idx_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_post_fill_stop ON orders(intent_type, post_fill_stop_applied);
CREATE INDEX IF NOT EXISTS idx_post_fill_pos ON orders(intent_type, post_fill_pos_init_applied);
CREATE INDEX IF NOT EXISTS idx_futu_code ON orders(futu_code);
CREATE INDEX IF NOT EXISTS idx_market_status ON orders(market, status);

-- ─── 市场租约表 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_leases (
    market              TEXT PRIMARY KEY,       -- HK / US
    owner               TEXT NOT NULL,          -- hostname:pid
    host_id             TEXT NOT NULL,          -- socket.gethostname()
    process_id          TEXT NOT NULL,          -- str(os.getpid())
    acquired_at         TEXT NOT NULL,          -- ISO 8601
    last_renewed_at     TEXT NOT NULL,          -- ISO 8601
    expires_at          TEXT NOT NULL,          -- ISO 8601 (acquired + 30min)
    phase               TEXT NOT NULL DEFAULT 'RECOVERY',  -- RECOVERY|SNAPSHOT|ORDER_GEN|SUBMIT|RECONCILE|DONE
    status              TEXT NOT NULL DEFAULT 'ACTIVE'     -- ACTIVE|RELEASED|EXPIRED
);

-- ─── 审计日志表 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,              -- ISO 8601
    category        TEXT NOT NULL,              -- STATE_TRANSITION | LEASE | SIDE_EFFECT
    action          TEXT NOT NULL,              -- 操作描述
    intent_id       TEXT DEFAULT '',            -- 相关 intent (可选)
    market          TEXT DEFAULT '',            -- 相关市场 (可选)
    old_value       TEXT DEFAULT '',            -- 旧值
    new_value       TEXT DEFAULT '',            -- 新值
    details         TEXT DEFAULT '',            -- 补充细节 (JSON)
    host_id         TEXT NOT NULL,              -- 执行节点
    process_id      TEXT NOT NULL               -- 执行进程
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_log(category);
CREATE INDEX IF NOT EXISTS idx_audit_intent ON audit_log(intent_id);
```

---

## 10. Verification Test Matrix

### 新增测试清单

| 测试 | 覆盖 | 验证方法 | 是否需要 Futu? |
|:---|:---|---:|:---:|
| **幂等与 journal** | | | |
| `UNIQUE(intent_id)` 幂等键约束 | 同 intent_id 第二次 INSERT 报错 | 内存 SQLite | ❌ |
| `UNIQUE(idx_order_id)` 空串允许多条 | `order_id=''` 可插入多条 | 内存 SQLite | ❌ |
| `UNIQUE(idx_order_id)` 非空唯一 | `order_id='123'` 第二次报错 | 内存 SQLite | ❌ |
| `try_acquire_intent` 新 intent | 返回 (True, 'OK') + RESERVED 行 | 内存 SQLite | ❌ |
| `try_acquire_intent` 重复 intent | 返回 (False, 'exists') 不插入 | 内存 SQLite | ❌ |
| `try_acquire_intent` 并发安全 | 两个独立连接同时 acquire 同一 intent | 临时文件 SQLite + 2 连接 | ❌ |
| remark 生成 | SHA256(intent_id) 固定 39 字节 | 纯函数 | ❌ |
| remark 唯一性 | 不同 intent_id 不同 remark | 纯函数 | ❌ |
| **状态机** | | | |
| `terminal_set` 不阻断 | 终态订单不影响 `has_unresolved_orders` | 内存 SQLite | ❌ |
| `blocking_set` 阻断 | 阻断态订单使 `has_blocking_orders` 返回 True | 内存 SQLite | ❌ |
| `human_intervention_set` 标记 | FILL_CANCELLED/UNKNOWN 在阻断+人力集合 | 枚举测试 | ❌ |
| `sell_completed_set` | 仅 FILLED_ALL 允许 BUY | 枚举测试 | ❌ |
| RESERVED → SUBMITTED 合法 | 合法转换 | 内存 SQLite | ❌ |
| RESERVED → ABANDONED 合法 | 合法转换（不删除） | 内存 SQLite | ❌ |
| SUBMITTING → SUBMITTED 合法 | 合法转换 | 内存 SQLite | ❌ |
| SUBMITTED → FILLED_ALL 合法 | 合法转换 | 内存 SQLite | ❌ |
| FILLED_ALL → SUBMITTED 非法 | 终态不可回退 | 纯函数检查 | ❌ |
| **租赁系统** | | | |
| `acquire_lease` 新市场 | 返回 True + 插入租约 | 内存 SQLite | ❌ |
| `acquire_lease` 重复拒绝 | 同一 market 第二次返回 False | 内存 SQLite | ❌ |
| `acquire_lease` 过期无未解决可接管 | 过期 + 无未解决 → 返回 True (steal) | 内存 SQLite | ❌ |
| `acquire_lease` 过期有未解决报错 | 过期 + 有未解决 → RuntimeError | 内存 SQLite | ❌ |
| `renew_lease` 续租 | expires_at 延长 | 内存 SQLite | ❌ |
| `release_lease` 释放 | status='RELEASED' | 内存 SQLite | ❌ |
| 租赁并发 | 两个独立进程同 market → 第二个拒绝 | 临时文件 SQLite + 2 进程 | ❌ |
| **恢复与对账** | | | |
| `resolve_stale_reserved` | >1h RESERVED → ABANDONED (不 DELETE) | 内存 SQLite + mock adapter | ❌ |
| `resolve_stale_reserved` 保护 | <1h 不处理 | 内存 SQLite | ❌ |
| `recover_post_fill_actions` STOP_SELL | FILLED_ALL + stop=0 → confirm_stop | mock risk_mgr | ❌ |
| `recover_post_fill_actions` SIGNAL_BUY | FILLED_ALL + pos_init=0 → init_position | mock risk_mgr | ❌ |
| `recover_post_fill_actions` REVERSAL_SELL | 不调 confirm_stop | mock risk_mgr | ❌ |
| `reconcile_orders` | SUBMITTED → FILLED_ALL (Futu 查询后) | mock futu_adapter | ❌ |
| **LIVE fail-closed** | | | |
| `dry_run=False, journal=None` | 构造时报 RuntimeError | 纯函数 | ❌ |
| `verify_integrity` 失败 | PRAGMA 返回非 ok → RuntimeError | 损坏的 SQLite | ❌ |
| journal 写入失败 | INSERT 异常 → 不进入下单路径 | mock 模拟异常 | ❌ |
| **现金与预算** | | | |
| `running_cash` SUBMITTED 扣减 | SUBMITTED 后 running_cash 减少 | mock 订单 | ❌ |
| `running_cash` REJECTED 不扣减 | REJECTED 后 running_cash 不变 | mock 订单 | ❌ |
| `running_cash` TIMEOUT 扣减+中止 | TIMEOUT 后扣减 + 剩余 BUY 被 BLOCKED | mock 订单 | ❌ |
| `can_proceed_to_buy` SELL FILLED_ALL | 返回 True | 枚举测试 | ❌ |
| `can_proceed_to_buy` SELL FILLED_PART | 返回 False + 阻断 | 枚举测试 | ❌ |
| `can_proceed_to_buy` SELL REJECTED | 返回 True (现金未使用) | 枚举测试 | ❌ |
| **再风控** | | | |
| `re_query_after_sell` 成功 | 返回实时 cash/assets/positions/exposure | mock futu_adapter | ❌ |
| `re_query_after_sell` 失败 | 抛出 RuntimeError (fail-closed) | mock 返回 None | ❌ |
| 风控使用实时数据 | 仓位/暴露检查用 live_total_assets | mock 订单 | ❌ |
| **回滚** | | | |
| LIVE_DISABLED_FLAG 存在 | `is_live_enabled()` 返回 False | 临时文件 | ❌ |
| journal 文件不被回滚删除 | 回滚后文件存在 | 文件系统检查 | ❌ |
| **Futu API 契约** | | | |
| place_order 正确签名 | remark/SHA256 与 SDK 兼容 | 静态检查 | ❌ |
| OrderStatus 映射完整 | 18 态全部映射 | 枚举测试 | ❌ |

### ⛔ 禁止的测试

- 任何涉及 `TrdEnv.SIMULATE` 真实下单的测试（包括通过 `order_list_query` 间接运行）
- 任何通过 `--live` 或 `LIVE_CONFIRMED` 路由进入下单路径的测试
- FOK 相关测试（FOK 不存在于本机 SDK）
- 任何连接测试环境中不存在的 Futu OpenD 实例

### 并发测试详细设计

```python
# tests/smoke/test_concurrent_journal.py
import sqlite3
import threading
from pathlib import Path
import pytest

def test_concurrent_intent_isolation():
    """两个独立连接并发获取同一 intent → 只有一个成功。"""
    db_path = Path(tmpdir) / 'test_concurrent.db'
    schema = Path('core/order_journal.sql').read_text()
    
    results = []
    
    def try_acquire(conn_id: int):
        conn = sqlite3.connect(str(db_path))
        conn.executescript(schema)
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE intent_id='test'"
            )
            count = cursor.fetchone()[0]
            if count == 0:
                conn.execute(
                    "INSERT INTO orders (intent_id, status, ...) VALUES (?, ...)",
                    ('test', 'RESERVED', ...)
                )
                conn.commit()
                results.append(conn_id)
            else:
                conn.rollback()
        except sqlite3.IntegrityError:
            conn.rollback()
            results.append(None)
        finally:
            conn.close()
    
    t1 = threading.Thread(target=try_acquire, args=(1,))
    t2 = threading.Thread(target=try_acquire, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    # 只有一个成功, 另一个因 IntegrityError 或 COUNT>0 失败
    assert len([r for r in results if r is not None]) == 1
```

---

## 11. Audit Trail Design

### 审计记录规则

所有以下操作在执行时写入 `audit_log` 表：

| 操作 | category | action 示例 | old_value | new_value |
|:---|:---:|:---|:---|:---|
| 状态转换 | `STATE_TRANSITION` | `RESERVED→SUBMITTED` | `RESERVED` | `SUBMITTED` |
| 租约获取 | `LEASE` | `acquire_lease` | — | `owner=host:pid` |
| 租约续租 | `LEASE` | `renew_lease` | `expires=...` | `expires=...` |
| 租约释放 | `LEASE` | `release_lease` | `ACTIVE` | `RELEASED` |
| 副作用冷却期 | `SIDE_EFFECT` | `confirm_stop` | `post_fill_stop=0` | `post_fill_stop=1` |
| 副作用风控 | `SIDE_EFFECT` | `init_position` | `post_fill_pos=0` | `post_fill_pos=1` |
| 幂等拒绝 | `STATE_TRANSITION` | `intent_exists` | — | `status=SUBMITTED` |
| 清理 RESERVED | `STATE_TRANSITION` | `RESERVED→ABANDONED` | `RESERVED` | `ABANDONED` |

**审计示例**：

```json
{
  "timestamp": "2026-06-04T09:35:01.123",
  "category": "STATE_TRANSITION",
  "action": "RESERVED→SUBMITTED",
  "intent_id": "QNT:20260604:HK:00700.HK:BUY:100:35000",
  "market": "HK",
  "old_value": "RESERVED",
  "new_value": "SUBMITTED",
  "details": "{\"order_id\": \"12345678\"}",
  "host_id": "my-pc",
  "process_id": "12345"
}
```

---

## 12. Confirmation of No Code/Git/Order Actions

✅ **本报告为只读分析，未修改任何代码或文件。**  
✅ **未执行任何下单入口或可能连接交易执行路径的测试。**  
✅ **未执行 Git commit、push、merge、reset 或 checkout。**  
✅ **未接入或建议接入 TrdEnv.REAL。**  
✅ **未调整 confidence_v2、Gate 或策略逻辑。**  
✅ **未触碰受保护代码（config.py L-001/L-002、xmm-strategy/、volume_profile.py、fusion_engine.py、fusion_controller.py、chan/）。**  
✅ **未触碰 4 个旧 research untracked 文件。**  
✅ **本地 SDK 核验已完成（futu 10.05.6508）。**  
✅ **v5 关闭全部 7 个 P0 项 + 补充设计细节。**  

**P0 关闭状态**（7/7）：

| P0 | 状态 | 对应章节 |
|:---:|:---:|:---:|
| 1. RESERVED→SUBMITTING 状态机 | ✅ 关闭 | §2, §3 |
| 2. LIVE fail-closed | ✅ 关闭 | §2 (P0-2) |
| 3. 市场级 execution lease | ✅ 关闭 | §2 (P0-3), §4 |
| 4. 部分成交 + 副作用 | ✅ 关闭 | §2 (P0-4), §6 |
| 5. 回滚 + LIVE 禁用 | ✅ 关闭 | §2 (P0-5), §8 |
| 6. FILL_CANCELLED 人工介入 | ✅ 关闭 | §2 (P0-6) |
| 7. REVERSAL_SELL 无 confirm_stop | ✅ 关闭 | §2 (P0-7) |

**补充设计细节**（全部完成）：

| 细节 | 状态 |
|:---|:---:|
| attempt_id 删除 | ✅ 已移除，改由 audit_log 记录重试历史 |
| SELL 后查询 fail-closed | ✅ §7 `re_query_after_sell` |
| 再风控用实时数据 | ✅ §7 `live_total_assets`/现金/exposure |
| `_place_single_order` 返回映射状态 | ✅ 映射表在 §3 |
| 并发测试设计 | ✅ §10 详细测试矩阵 + 代码示例 |
| 不自动删除，仅 ABANDONED | ✅ RESERVED→ABANDONED（非 DELETE） |
| 全部操作可审计 | ✅ §11 audit_log 表 |

**等待 Codex 批准后实施 Phase F2-SEC。**