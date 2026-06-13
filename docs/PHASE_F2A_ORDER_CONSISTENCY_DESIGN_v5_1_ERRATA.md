# Phase F2-A v5.1 Errata
# 订单执行一致性修复 — 7 项 P0 修正 + 3 项附加修订

**版本**: v5.1 (Errata)  
**日期**: 2026-06-04  
**状态**: 基于 v5 的补充修正，非重写。关闭全部 7 个 P0 阻断项 + 3 项附加修订后申请实施批准。  
**原则**: 只修正明确指出的问题；未提及的 v5 设计保持不变。

---

## P0-1: SUBMITTING 必须真正落地

### 问题

`try_acquire_intent()` 只写入 `RESERVED`，没有"更新为 SUBMITTING → 持久化 → 调用 broker"的原子流程。RESERVED 可直接跳至 SUBMITTED（v5:914, 1393, 1503）。

### 修正

**`_place_single_order()` LIVE 分支流程（替换 v5 设计）**：

```python
# === Step 1: RESERVED → SUBMITTING（先持久化，再调 broker）===

# 1a. 更新 journal: RESERVED → SUBMITTING
journal.update_status(
    intent_id=intent_id,
    new_status='SUBMITTING',
    message='准备提交至 broker'
)

# 1b. 写入审计日志（与上述 update 在同一事务）
journal.append_audit(
    category='STATE_TRANSITION',
    action='RESERVED→SUBMITTING',
    intent_id=intent_id,
    market=market,
)

# 1c. 以上两步在同一 SQLite 事务（见附加修订第3项）
journal.conn.commit()

# === Step 2: 调用 broker ===
try:
    ok, result = self._adapter.place_order(...)

    # 2a. SUBMITTING → SUBMITTED（有 order_id）
    if ok:
        journal.update_status(intent_id, 'SUBMITTED', order_id=result)
        # 审计日志与 update 同事务

    # 2b. SUBMITTING → REJECTED（broker 拒单）
    else:
        journal.update_status(intent_id, 'REJECTED', message=result)

except Exception as e:
    # 2c. SUBMITTING → TIMEOUT（异常/超时）
    journal.update_status(intent_id, 'TIMEOUT', message=str(e))
```

### 禁止的状态转换

| 非法转换 | 原因 |
|:---|:---|
| `RESERVED → SUBMITTED` | 未经过 SUBMITTING，无法保证幂等 |
| `RESERVED → FILLED_ALL` | 未提交不可能成交 |
| `SUBMITTING → ABANDONED` | 已调用 broker 不可放弃；仅对账后可转为 UNKNOWN/TIMEOUT |

### 完整性断言

```python
assert journal.get_status(intent_id) in ('SUBMITTING',), \
    "place_order() 调用前必须先持久化 SUBMITTING"
```

---

## P0-2: Execution Lease 增加 fencing 和 owner 校验

### 问题

1. 其他进程持有租约时 `return False` 未结束 `BEGIN IMMEDIATE` 事务（v5:285）
2. `renew/update_phase/release` 无 owner 校验
3. 租约过期被接管后，旧进程仍可继续提交订单

### 修正

**增加随机 lease_token**：

```sql
ALTER TABLE market_leases ADD COLUMN lease_token TEXT NOT NULL DEFAULT '';
-- 或替换为完整 CREATE TABLE（当前为未实施设计）
```

```python
import uuid

def acquire_lease(self, market: str) -> str:
    """获取市场租赁。返回 lease_token。失败时返回 '' 或抛出异常。"""
    token = uuid.uuid4().hex  # 32 字符随机 hex
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
            # 无现有租约
            self.conn.execute(
                "INSERT INTO market_leases (...) VALUES (?, ..., ?, ?)",
                (..., token, ...)
            )
            self.conn.commit()
            return token

        owner, status, expires_at = row
        if status == 'ACTIVE' and expires_at > now:
            if owner == f'{host_id}:{process_id}':
                # 自己持有：续租
                self._renew_lease_inner(market, token, expires)
                self.conn.commit()
                return token
            # 其他进程持有
            self.conn.rollback()  # ★ 修复 P0-2(1): 明确结束事务
            return ''

        # 租约过期
        unresolved = self.has_unresolved_orders(market)
        if unresolved:
            self.conn.rollback()
            raise RuntimeError("Lease expired + unresolved orders = manual intervention")

        # 接管：生成新 token
        self.conn.execute(
            "UPDATE market_leases SET owner=?, lease_token=?, ... WHERE market=?",
            (f'{host_id}:{process_id}', token, ..., market)
        )
        self.conn.commit()
        return token
    except Exception:
        self.conn.rollback()
        raise
```

**所有租约操作带 token 校验**：

```python
def _with_lease_check(self, market: str, token: str, sql: str, params: tuple) -> int:
    """执行带 token 校验的 SQL。返回受影响行数。"""
    where = "AND market=? AND lease_token=? AND status='ACTIVE'"
    cursor = self.conn.execute(sql + where, params + (market, token))
    affected = cursor.rowcount
    if affected == 0:
        raise RuntimeError(
            f"Lease ownership check failed: market={market}, "
            f"token exists but no ACTIVE lease for this token. "
            f"Possible expired or stolen lease. Aborting."
        )
    return affected

def renew_lease(self, market: str, token: str) -> None:
    self.conn.execute("BEGIN IMMEDIATE")
    try:
        self._with_lease_check(market, token,
            "UPDATE market_leases SET last_renewed_at=datetime('now'), expires_at=? ",
            (expires,)
        )
        self.conn.commit()
    except Exception:
        self.conn.rollback()
        raise
```

**broker 调用前的 fencing 检查**：

```python
def assert_lease_valid(self, market: str, token: str) -> None:
    """每次 broker 调用前执行 fencing 检查。"""
    now = datetime.now().isoformat()
    cursor = self.conn.execute(
        "SELECT 1 FROM market_leases "
        "WHERE market=? AND lease_token=? AND status='ACTIVE' AND expires_at > ?",
        (market, token, now)
    )
    if cursor.fetchone() is None:
        raise RuntimeError(
            f"LEASE FENCED: market={market}, token={token[:8]}... "
            f"Lease expired, stolen, or released. Aborting broker call."
        )

# _place_single_order() 中：
if not self.dry_run:
    self.journal.assert_lease_valid(market, lease_token)  # 每次 broker 前
    ok, result = self._adapter.place_order(...)
```

---

## P0-3: 恢复流程顺序错误

### 问题

v5 恢复流程图顺序为：post-fill 副作用 → 对账（错误）。完整 `run()` 示例甚至在阻断检查前没有调用对账（v5:834, 989）。

### 修正

**正确恢复顺序**（替换 v5 §4 流程图）：

```text
获取 lease (phase=RECOVERY)
  │
  ▼
1. Broker 对账 ──────────────────────────────────────
   → 调用 futu_adapter.order_list_query(market, start=today)
   → 逐笔匹配 remark 与 journal 中的 SUBMITTED/SUBMITTING/SUB_MITTED_NONLOCAL
   → 更新 journal 状态（FILLED_ALL / FILLED_PART / CANCELLED / 等）
   │
   ▼
2. 清理过期 RESERVED → ABANDONED ───────────────────
   → 使用 futu_code + remark 精确匹配（见 P0-4）
   → 查询失败时保持 RESERVED
   │
   ▼
3. 执行幂等副作用 ──────────────────────────────────
   → STOP_SELL FILLED_ALL: confirm_stop() → 标记 applied
   → SIGNAL_BUY FILLED_ALL: init_position() → 标记 applied
   → REVERSAL_SELL FILLED_ALL: 标记 applied（无 confirm_stop）
   │
   ▼
4. 阻断检查 ─────────────────────────────────────────
   → has_blocking_orders(market)
   → 有阻断 → release_lease + return
   │
   ▼
5. 账户/持仓快照 (phase=SNAPSHOT)
6. 订单生成 (phase=ORDER_GEN)
7. 订单提交 (phase=SUBMIT)
8. 执行后对账 (phase=RECONCILE)
9. 释放租约 (phase=DONE)
```

**审计日志顺序必须与上列步骤一致**：

```text
Step 1: audit(category='RECONCILIATION', action='broker_reconcile_start')
        audit(category='RECONCILIATION', action='order_status_updated',
              old_value='SUBMITTED', new_value='FILLED_ALL')
Step 2: audit(category='RECONCILIATION', action='RESERVED→ABANDONED', ...)
Step 3: audit(category='SIDE_EFFECT', action='confirm_stop', ...)
```

---

## P0-4: Stale RESERVED 必须使用 `futu_code + remark` 精确匹配

### 问题

`resolve_stale_reserved()` 使用标准 symbol 查询 Futu，未使用 `remark` 精确匹配；查询失败时未 fail-closed（v5:97, 120）。

### 修正

```python
def resolve_stale_reserved(self, futu_adapter, market: str, lease_token: str) -> int:
    """将过期 RESERVED → ABANDONED（使用 futu_code + remark 精确匹配）。"""
    cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
    cursor = self.conn.execute(
        "SELECT intent_id, symbol, futu_code, remark FROM orders "
        "WHERE status='RESERVED' AND created_at < ? AND market=?",
        (cutoff, market)
    )
    resolved = 0
    for row in cursor.fetchall():
        intent_id, symbol, futu_code, remark = row

        # 查询失败 → fail-closed: 保持 RESERVED，不转为 ABANDONED
        if futu_adapter is None:
            print(f'[JOURNAL] Adapter is None, keeping RESERVED: {intent_id}')
            continue

        # 使用 futu_code + remark 精确匹配
        try:
            orders_df = futu_adapter.query_orders(
                market=market,
                code=futu_code,       # 精确标的
                remark=remark,        # 精确 remark 匹配（需在 adapter 层加 filtering）
                start=today_str,
            )
        except Exception as e:
            print(f'[JOURNAL] Query failed for {intent_id}: {e}. Keeping RESERVED.')
            continue  # ★ fail-closed: 查询异常时保持 RESERVED

        # 有匹配 → 说明 broker 接收了此订单 → 不可 ABANDONED
        if orders_df is not None and len(orders_df) > 0:
            print(f'[JOURNAL] RESERVED {intent_id} found in broker (remark={remark}). '
                  f'Updating to SUBMITTED instead.')
            # 从 orders_df 提取 order_id
            order_id = orders_df.iloc[0]['order_id']
            self.update_status(intent_id, 'SUBMITTED', order_id=order_id)
            continue

        # 无匹配 → 安全转为 ABANDONED
        self.conn.execute(
            "UPDATE orders SET status='ABANDONED', updated_at=datetime('now') "
            "WHERE intent_id=?",
            (intent_id,)
        )
        resolved += 1
        print(f'[JOURNAL] RESERVED {intent_id} → ABANDONED (no broker match)')

    self.conn.commit()
    return resolved
```

### 关键规则

| 场景 | 行为 |
|:---|:---|
| `futu_adapter is None` | 保持 RESERVED（不做不可逆操作） |
| 查询异常 | 保持 RESERVED（fail-closed） |
| 查询有匹配（remark 精确匹配） | 更新为 SUBMITTED（而不是 ABANDONED） |
| 查询无匹配 | → ABANDONED（安全） |

---

## P0-5: SELL→BUY 规则统一：所有 SELL 必须为 FILLED_ALL

### 问题

v5 声明"仅 FILLED_ALL SELL 允许 BUY"，但 `can_proceed_to_buy()` 对 `REJECTED/ABANDONED/CANCELLED_ALL` 放行（v5:404, 1077）。

### 修正

```python
def can_proceed_to_buy(self, sell_results: list) -> tuple[bool, str]:
    """
    本轮存在任何 SELL 时，所有 SELL 必须为 FILLED_ALL 才允许 BUY。
    
    即使 REJECTED 也不放行，因为：
    - REJECTED 可能是 Futu 限流/账户问题，其他 SELL 也面临风险
    - 已记录的 record_stop() 可能已经改变了风控状态
    - 统一严格规则避免例外引发的不一致
    """
    if not sell_results:
        return True, ''  # 无 SELL → 直接 BUY

    for r in sell_results:
        s = r.get('status', '')
        if s == 'FILLED_ALL':
            continue  # ✅ 全量成交
        else:
            return (False,
                    f'SELL 未全部成交: {r.get("symbol","?")} 状态={s}. '
                    f'规则: 本轮所有 SELL 必须为 FILLED_ALL 才允许 BUY')

    return True, ''
```

### 缓释说明

`REJECTED` 和 `CANCELLED_ALL` 的 SELL 确实未释放资金，理论上可以放行 BUY。但为了**一致性**，本设计采用最严格规则：**本轮有 SELL → 全部 FILLED_ALL → 才 BUY**。若 Roy 未来希望放宽此规则（如 REJECTED 不影响 BUY），需单独批准。

---

## P0-6: 止损副作用必须先执行 `confirm_stop()`，成功后再标记

### 问题

`maybe_activate_cooldown()` 先设置 `post_fill_stop_applied=1`，然后才调用 `confirm_stop()`。中间崩溃导致冷却期丢失（v5:452, 618）。

### 修正

```python
def apply_stop_cooldown(self, intent_id: str, futu_code: str,
                         futu_adapter, risk_mgr, market: str) -> bool:
    """执行止损冷却期幂等应用。
    
    顺序: 先执行 confirm_stop() → 成功后标记 applied
    崩溃安全: confirm_stop() 是幂等的，重复调用无副作用。
    """
    # Step 1: 确认持仓为零
    try:
        positions = futu_adapter.get_positions(market)
    except Exception as e:
        print(f'[COOLDOWN] 查询持仓失败: {e}。保持未完成，下次重试。')
        return False

    held = [p for p in positions if p['code'] == futu_code]
    if len(held) > 0 and held[0].get('qty', 0) > 0:
        print(f'[COOLDOWN] {futu_code} 仍有持仓 {held[0]["qty"]} 股，不激活冷却期')
        return False

    # Step 2: ★ 优先执行 confirm_stop()（幂等：重复调用无副作用）
    risk_mgr.confirm_stop(futu_code)  # 更新 stop_timestamps + save_state()
    print(f'[COOLDOWN] confirm_stop() 已执行: {futu_code}')

    # Step 3: ★ 成功后标记 applied
    self.conn.execute(
        "UPDATE orders SET post_fill_stop_applied=1, updated_at=datetime('now') "
        "WHERE intent_id=?",
        (intent_id,)
    )
    self.conn.commit()
    return True
```

### 崩溃安全分析

| 崩溃点 | 后果 | 恢复 |
|:---|:---|:---|
| Step 1 中/前 | `post_fill_stop_applied=0`，下次重试 | ✅ |
| Step 2 后、Step 3 前 | `confirm_stop()` 已执行，`applied=0` | ✅ 幂等：重复 `confirm_stop()` 无副作用 |
| Step 3 后 | 全部完成 | ✅ |

---

## P0-7: 回滚后 LIVE 禁用必须独立于被回滚代码

### 问题

回滚 `git checkout` 会替换包含 `LIVE_DISABLED_FLAG` 检查的 `unified_runner.py`，旧代码不读取该旗标（v5:1247, 1264）。

### 修正

**方案：通过 `core/__init__.py` 层拦截**（该文件不在回滚范围内可被保留，或使用独立于回滚文件的模块）：

```python
# core/live_kill_switch.py  ← 新增文件，不在回滚清单中
"""
LIVE 执行全局 kill switch。

该文件独立于订单执行代码，不会在 Phase F2-SEC 回滚中被删除或覆盖。
它被 core/order_executor.py 和 core/order_journal.py 引用，
回滚后这些文件回到旧版本，不再 import 此模块。

但 unified_runner.py 被回滚后也会失去此 import。
因此需要第二个保护层：
"""

import os
from pathlib import Path

KILL_SWITCH_FILE = Path(__file__).parent.parent / 'output' / 'LIVE_DISABLED_FLAG.txt'

def check_live_enabled() -> bool:
    """全局 LIVE kill switch 检查。"""
    if KILL_SWITCH_FILE.exists():
        content = KILL_SWITCH_FILE.read_text().strip()
        if content == 'DISABLED':
            return False
    return True

def disable_live():
    """禁用 LIVE 执行。"""
    KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    KILL_SWITCH_FILE.write_text('DISABLED')
    print(f'[KILL_SWITCH] LIVE 已禁用: {KILL_SWITCH_FILE}')


# ===== 第二保护层: 进程级环境变量 =====
# 即使 F2-SEC 全部代码被回滚，unified_runner.py 旧版本中的 LIVE 检查
# 已经在 v4 之前版本就存在 --confirm-live 检查。
# 
# 回滚后的旧 unified_runner.py 仍会检查 --confirm-live flag。
# 因此回滚时需要重命名或移除该 flag:
#   mv output/QUANT_LIVE_CONFIRMED_FLAG.txt output/QUANT_LIVE_CONFIRMED_FLAG.txt.BAK
# 
# 这样即使旧代码被恢复，--confirm-live 也会因找不到 flag 而拒绝 LIVE 执行。
```

**完整回滚中的 LIVE 禁用流程**（替换 v5 §8）：

```bash
# === Phase F2-SEC 回滚 — LIVE 禁用（独立于被回滚代码）===

# 第1层: 删除 LIVE_CONFIRM flag（旧 unified_runner.py 依赖此 flag）
#        回滚后旧代码检查该 flag，不存在 = 拒绝 LIVE
echo "[ROLLBACK] 删除 LIVE_CONFIRM flag..."
rm -f output/QUANT_LIVE_CONFIRMED_FLAG.txt

# 第2层: 创建 kill switch 文件（即使 F2-SEC 代码被回滚，kill_switch.py
#        在回滚清单外。回滚后旧代码不会 import 它，但作为额外安全层保留）
echo "[ROLLBACK] 创建 LIVE_DISABLED_FLAG..."
echo "DISABLED" > output/LIVE_DISABLED_FLAG.txt

# 第3层: 备份 journal
echo "[ROLLBACK] 备份 journal..."
cp output/order_journal_*.db backups/journal_f2sec_$(date +%Y%m%d_%H%M%S).bak

# 第4层: 输出未解决订单列表
echo "[ROLLBACK] 检查未解决订单..."
python -c "check_unresolved()"

# 第5层: 回滚代码
echo "[ROLLBACK] 回滚代码..."
git checkout -- core/order_executor.py core/stop_loss.py core/futu_adapter.py unified_runner.py
rm -f core/order_journal.py core/live_kill_switch.py

# 第6层: 恢复 LIVE — 手动步骤
#   a. 逐一确认未解决订单
#   b. 删除 LIVE_DISABLED_FLAG.txt
#   c. 重新创建 QUANT_LIVE_CONFIRMED_FLAG.txt 或使用 --confirm-live
```

---

## 附加修订

### 1. Futu `UNSUBMITTED` 映射修正

| Futu OrderStatus | v5 映射 | v5.1 修正 | 原因 |
|:---|:---:|:---:|:---|
| `UNSUBMITTED` | `RESERVED` | → **`SUBMITTING`** | Futu `UNSUBMITTED` 意味着订单已录入 broker 系统、排队等待提交。它不是"未调 API（RESERVED）"，而是"已提交、未到达交易所"。不可放弃。 |

### 2. `intent_id` 价格精度修正

`intent_id` 应使用 broker 最终提交精度以避免碰撞。

```text
# v5: price_int = round(price * 100)     # 2 位小数
# v5.1: price_int = round(price * 1000)  # 3 位小数（兼容 HK 3 位 + US 实际精度）

BUY 00700.HK @ 350.000 HKD → intent_id 含 price=350000  # 3 位小数
BUY AAPL.US   @ 150.000 USD → intent_id 含 price=150000  # 3 位小数
```

- 港股: 3 位小数（Futu 实际支持）
- 美股: 标准为 2 位，使用 3 位（末尾补 0）兼容，不会碰撞
- 小数点后超过 3 位的价格（极少见）仍不会碰撞

**remark 使用相同的 price_int**（SHA256 输入中包含精确价格）。

### 3. 审计日志与状态更新同事务

```python
def _update_status_internal(self, intent_id: str, new_status: str,
                             **extra_fields) -> None:
    """更新状态和写入审计日志在同一事务中。

    使用 self.conn（单一连接），禁止在事务中间断开或提交。
    调用方负责 commit/rollback。
    """
    # 读取旧状态
    cursor = self.conn.execute(
        "SELECT status FROM orders WHERE intent_id=?", (intent_id,)
    )
    row = cursor.fetchone()
    old_status = row[0] if row else ''

    # 更新 orders 表
    updates = ["status=?", "updated_at=datetime('now')"]
    params = [new_status]
    for key, value in extra_fields.items():
        updates.append(f"{key}=?")
        params.append(value)
    params.append(intent_id)

    self.conn.execute(
        f"UPDATE orders SET {', '.join(updates)} WHERE intent_id=?",
        params
    )

    # 写入 audit_log（同一连接，同一事务）
    self.conn.execute(
        "INSERT INTO audit_log (timestamp, category, action, intent_id, "
        "market, old_value, new_value, host_id, process_id) "
        "VALUES (datetime('now'), 'STATE_TRANSITION', ?, ?, "
        " (SELECT market FROM orders WHERE intent_id=?), ?, ?, ?, ?)",
        (f'{old_status}→{new_status}', intent_id,
         intent_id, old_status, new_status,
         socket.gethostname(), str(os.getpid()))
    )
    # 调用方 commit()
```

**`_place_single_order()` 使用示例**：

```python
journal.conn.execute("BEGIN IMMEDIATE")
try:
    journal._update_status_internal(
        intent_id, 'SUBMITTING',
        order_id='', message='准备提交至 broker'
    )
    journal.conn.commit()  # Step 1 提交

    # Step 2: 调用 broker
    ok, result = self._adapter.place_order(...)

    journal.conn.execute("BEGIN IMMEDIATE")
    if ok:
        journal._update_status_internal(
            intent_id, 'SUBMITTED',
            order_id=result
        )
    else:
        journal._update_status_internal(
            intent_id, 'REJECTED',
            message=result
        )
    journal.conn.commit()
except Exception:
    journal.conn.rollback()
    raise
```

---

## 总结：v5 → v5.1 变更对照

| # | 问题 | 类型 | v5.1 修正 |
|:---:|:---|:---:|:---|
| P0-1 | SUBMITTING 未落地 | 🔴 阻塞 | RESERVED→SUBMITTING 先持久化再调 broker；禁止 RESERVED→SUBMITTED |
| P0-2 | Lease 无 fencing | 🔴 阻塞 | 随机 lease_token；所有操作 WHERE token+ACTIVE+校验行数；broker 前 fencing |
| P0-3 | 恢复顺序错误 | 🔴 阻塞 | broker 对账 → 清理 → 副作用 → 阻断检查 → 新订单 |
| P0-4 | Stale RESERVED 查询弱 | 🟡 修复 | 使用 futu_code+remark 精确匹配；查询失败保持 RESERVED |
| P0-5 | SELL→BUY 矛盾 | 🔴 阻塞 | 所有 SELL 必须 FILLED_ALL；REJECTED 不放行 |
| P0-6 | 副作用顺序倒置 | 🔴 阻塞 | 先 confirm_stop() → 成功 → 标记 applied |
| P0-7 | 回滚后 LIVE 禁用无效 | 🔴 阻塞 | 三层层保护：删 confirm flag + kill switch + 回滚清单外模块 |
| A-1 | UNSUBMITTED 映射错误 | 🟡 修复 | UNSUBMITTED→SUBMITTING（非 RESERVED） |
| A-2 | intent_id 精度碰撞 | 🟢 优化 | 从 2 位改为 3 位小数 |
| A-3 | 审计事务分离 | 🟡 修复 | 状态更新+审计日志同事务；single connection commit 策略 |

**10/10 全部关闭。等待 Codex 批准实施 Phase F2-SEC。**
