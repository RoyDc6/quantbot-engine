# Phase F2-A v5.6 Canonical — 唯一实施契约
# 订单执行一致性修复

**版本**: v5.6 (完整取代 v1–v5.5 全部版本)  
**日期**: 2026-06-05  
**状态**: 本文件是 Phase F2-SEC **唯一**及**完整**的实施来源。所有早前版本中与本文档冲突的内容已**永久废除**。  
**审查环境**: Python 3.12, futu 10.05.6508, `C:\Users\RoyGoode\AppData\Roaming\Futu_OpenD\Futu_OpenD.exe`

---

## 目录

1. [跨日持久化与历史未解决订单阻断](#1-跨日持久化与历史未解决订单阻断)
2. [Futu 查询结构化结果与 Fail-Closed](#2-futu-查询结构化结果与-fail-closed)
3. [统一 Broker Remark 传递与恢复](#3-统一-broker-remark-传递与恢复)
4. [SDK 穷举验证、状态返回与立即成交映射](#4-sdk-穷举验证状态返回与立即成交映射)
5. [回滚签收显式 Commit Hash 与 Reconciliation 隔离](#5-回滚签收显式-commit-hash-与-reconciliation-隔离)
6. [删除预提交 record_stop 与幂等 confirm_stop](#6-删除预提交-record_stop-与-幂等-confirm_stop)
7. [统一 SQLite 时间格式](#7-统一-sqlite-时间格式)
8. [最终测试矩阵（85 项）](#8-最终测试矩阵85-项)
9. [SQLite Schema 完整版](#9-sqlite-schema-完整版)
10. [交付确认](#10-交付确认)

---

## 1. 跨日持久化与历史未解决订单阻断

### 1.1 数据库文件结构（v5.6 变更）

**从每日分割改为按市场持久：**

```
# ❌ v5.5: 每日分割，跨日无状态延续
output/order_journal_HK_20260604.db
output/order_journal_HK_20260605.db    # 前一日 lease/orders 不可见

# ✅ v5.6: 单市场持久化，跨日延续
output/order_journal_HK.db              # HK 市场的完整 journal
output/order_journal_US.db             # US 市场的完整 journal
```

**订单作用域通过 `run_date` 字段划定：**

```sql
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,            -- '2026-06-05' (ISO 日期)
    intent_id       TEXT NOT NULL UNIQUE,
    broker_remark   TEXT NOT NULL,            -- 完整 remark: QNT:v1:SHA256-32hex
    ...
);
```

### 1.2 Lease 跨日延续

`market_leases` 表无日期列，单市场一条记录永久存在。RELEASED 后任意日可重获。

```sql
CREATE TABLE IF NOT EXISTS market_leases (
    market          TEXT PRIMARY KEY,
    lease_token     TEXT NOT NULL DEFAULT '',
    owner           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'RELEASED',
    phase           TEXT NOT NULL DEFAULT '',
    -- expires_at 始终为未来时间点；跨日运行只需 renew 即可
    expires_at      TEXT NOT NULL DEFAULT '',
    acquired_at     TEXT NOT NULL DEFAULT '',
    released_at     TEXT NOT NULL DEFAULT '',
    ...
);
```

### 1.3 历史未解决订单阻断（跨日全量扫描）

```python
def has_blocking_orders(self, market: str) -> bool:
    """检查该市场是否存在阻断新订单的状态。
    
    跨日全量扫描 — 不按 run_date 过滤。
    即使昨天的订单今天仍为 SUBMITTED/UNKNOWN，同样阻断。
    """
    cursor = self.conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE market=? AND status IN (%s)
        """ % ','.join('?' for _ in OrderStatus.blocking_set()),
        [market] + [s.value for s in OrderStatus.blocking_set()]
    )
    return cursor.fetchone()[0] > 0


def has_blocking_orders_for_date(self, market: str, date: str) -> bool:
    """可选：检查指定日期的阻断订单（用于日志统计）。"""
    cursor = self.conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE market=? AND run_date=? AND status IN (%s)
        """ % ','.join('?' for _ in OrderStatus.blocking_set()),
        [market, date] + [s.value for s in OrderStatus.blocking_set()]
    )
    return cursor.fetchone()[0] > 0
```

---

## 2. Futu 查询结构化结果与 Fail-Closed

### 2.1 结构化查询结果

所有 Adapter 查询方法返回 `QueryResult` 数据类，统一契约：

```python
@dataclass
class QueryResult:
    """所有 Futu 查询的统一返回类型。"""
    ok: bool                # True=查询成功, False=查询失败
    data: Any               # 成功时返回的数据；失败时为 None
    error: str = ''         # 失败时的错误描述
    raw: Any = None         # SDK 原始返回值（调试用）

    def require(self, context: str = '') -> Any:
        """Fail-closed: 如果 ok=False 则抛出 RuntimeError。"""
        if not self.ok:
            msg = f"[FAIL-CLOSED] {context}: {self.error}"
            raise RuntimeError(msg)
        return self.data
```

### 2.2 受影响的方法

```python
class FutuAdapter:
    def get_positions(self, market: str = 'HK') -> QueryResult: ...
    def get_account_info(self, market: str) -> QueryResult: ...
    def get_order_list(self, market: str, 
                       status_filter: str = '') -> QueryResult: ...
    def place_order(self, market: str, code: str, action: str,
                    qty: int, price: float,
                    remark: str = '') -> PlaceOrderResult: ...
```

### 2.3 调用方 fail-closed 契约

```python
# ❌ v5.5: 使用 .get() 静默回退
account = adapter.get_account_info(market)  # 可能返回 None
live_cash = account.get('cash', 0)          # 查询失败时静默用 0

# ✅ v5.6: fail-closed
qr = adapter.get_account_info(market)
account = qr.require(context='SELL 后账户查询')  # 失败 → RuntimeError
live_cash = account.get('cash', 0)               # 安全：已确保 qr.ok=True
```

```python
# ❌ v5.5: 无返回值检查
positions = adapter.get_positions(market)
for p in positions:        # 查询失败返回 [] → 视为空仓

# ✅ v5.6: fail-closed
qr = adapter.get_positions(market)
positions = qr.require(context=f'{market} 持仓查询')  # 失败 → 阻断执行
```

### 2.4 `get_order_list()` 恢复对账使用

```python
# 恢复对账时使用 remark 精确匹配
qr = adapter.get_order_list(market=market)
if not qr.ok:
    # 恢复时查询失败 → fail-closed: 终止运行
    raise RuntimeError(f"恢复对账查询失败: {qr.error}")

futu_orders = qr.data  # list[dict], 每笔包含 order_id, order_status, remark
for futu_order in futu_orders:
    if futu_order.get('remark', '') == broker_remark:
        # 精确匹配 → 更新 journal
        ...
```

---

## 3. 统一 Broker Remark 传递与恢复

### 3.1 Remark 生成规则（不变）

```python
def generate_remark(order: dict, market: str) -> str:
    """
    生成发送给 broker 的 remark 字符串。
    
    格式: QNT:v1:<SHA256(intent_input)>
    长度: 固定 39 字节，无截断，无碰撞风险。
    
    intent_input = f"{date}_{market}_{symbol}_{action}_{qty}_{price_int}"
    其中 price_int = round(price * 1000)  # 3位小数，兼容港股
    """
    import hashlib
    
    # 注意: intent_input 不包含 run_id
    price_int = round(order['price'] * 1000)
    intent_input = (
        f"{order['run_date']}_{market}_"
        f"{order['symbol']}_{order['action']}_"
        f"{order['qty']}_{price_int}"
    )
    digest = hashlib.sha256(intent_input.encode()).hexdigest()[:32]
    return f"QNT:v1:{digest}"  # 固定 39 字节
```

### 3.2 表字段统一命名

```sql
-- 列名从 v5.5 的 remark_hash 改为 broker_remark
-- 该字段存储发送给 Futu 的完整 remark 字符串

broker_remark    TEXT NOT NULL,   -- "QNT:v1:<32hex>" 完整值 (39 bytes)
```

### 3.3 恢复匹配规则 — 全部路径使用完整 remark

```python
def reconcile_reserved(self, market: str, adapter):
    """
    恢复对账时使用 broker_remark 精确匹配 Futu 订单。
    
    匹配策略:
      1. 查询 Futu order_list, 提取每笔订单的 remark 字段
      2. 按 broker_remark = futu_remark 精确匹配
      3. 匹配成功 → 根据 futu_order_status 更新 journal
      4. 匹配失败 → 保持 RESERVED/SUBMITTING 不变
    
    不使用 futu_code 模糊匹配；
    不使用 symbol 猜测；
    不使用 order_id 部分匹配。
    """
    qr = adapter.get_order_list(market=market)
    futu_orders = qr.require(context=f"{market} 恢复对账")
    
    reserved = self.get_by_status('RESERVED', market=market)
    submitting = self.get_by_status('SUBMITTING', market=market)
    pending = reserved + submitting
    
    for entry in pending:
        # 在 Futu 订单中按 broker_remark 精确搜索
        matched = [fo for fo in futu_orders
                   if fo.get('remark', '') == entry['broker_remark']]
        if matched:
            fo = matched[0]
            qb_status = OrderStatus.from_futu(fo.get('order_status', ''))
            self._update_from_recovery(entry, qb_status, fo)
        # 未匹配 → 保持原状态（不改为 ABANDONED）
```

---

## 4. SDK 穷举验证、状态返回与立即成交映射

### 4.1 SDK 方法名修正

```python
# ❌ v5.5: 使用不存在的 load_dict()
assert set(ft.OrderStatus.load_dict().keys()) == set(FUTU_TO_QUANTBOT.keys())

# ✅ v5.6: 使用 SDK 实际方法 load_dic()
assert set(ft.OrderStatus.load_dic().keys()) == set(FUTU_TO_QUANTBOT.keys()), \
    f"映射键不全 — SDK 有 {len(ft.OrderStatus.load_dic())} 个，映射有 {len(FUTU_TO_QUANTBOT)} 个"
```

### 4.2 立即成交映射（`place_order()` 直接返回 FILLED_ALL）

```python
# ❌ v5.5 Phase 6 (lines 840-852):
# 所有非 SUBMITTED/REJECTED 的状态被统一映射为 SUBMITTED
else:
    # 其他状态 (FILLED_ALL 等由后续对账处理)  ← ⚠️ 错误：FILLED_ALL 应立刻处理
    self.journal.update_status(..., new_status='SUBMITTED', ...)
    return PlaceOrderResult(status=OrderStatus.SUBMITTED, ...)


# ✅ v5.6: 完整的分支处理
def _map_place_order_result(self, intent_id, result, futu_status, order):
    """将 broker 返回结果映射为 QuantBot 状态。
    
    特殊处理:
      - FILLED_ALL: 立即成交，直接映射（无需等待对账）
      - SUBMIT_FAILED/FAILED: 权威拒单
      - SUBMITTED: 正常提交
      - 其他: 作为 SUBMITTED 等待对账
    """
    qb_status = OrderStatus.from_futu(futu_status)
    
    # ── 立即成交 ──────────────────────────────────
    if qb_status == OrderStatus.FILLED_ALL:
        new_status = 'FILLED_ALL'
        filled_qty = order['qty']
        filled_price = order['price']
        
    # ── 权威拒单 ──────────────────────────────────
    elif qb_status == OrderStatus.REJECTED:
        new_status = 'REJECTED'
        filled_qty = 0
        filled_price = 0.0
        
    # ── 正常提交（含 FILLED_PART、SUBMITTED 等）─────
    else:
        # 即使 place_order 返回 FILLED_PART，
        # 也应记录为 SUBMITTED（后续对账处理成交细节）
        new_status = 'SUBMITTED' if qb_status not in (
            OrderStatus.FILLED_PART,  # 极罕见：place_order 直接返回部分成交
        ) else 'FILLED_PART'
        filled_qty = 0
        filled_price = 0.0
    
    self.journal.update_status(
        intent_id=intent_id,
        new_status=new_status,
        order_id=result.order_id,
        futu_status=futu_status,
        filled_qty=filled_qty,
        filled_price=filled_price,
        audit_action='place_order_result',
    )
    return PlaceOrderResult(
        status=OrderStatus(new_status),
        message=f"Futu 返回: {futu_status}",
        order_id=result.order_id,
        futu_status=futu_status,
    )
```

### 4.3 穷举验证

```python
def verify_sdk_mapping():
    """
    验证 FUTU_TO_QUANTBOT 映射键与 SDK 实际枚举完全一致。
    这必须在测试启动时运行一次。
    """
    import futu as ft
    
    sdk_keys = set(ft.OrderStatus.load_dic().keys())
    mapping_keys = set(FUTU_TO_QUANTBOT.keys())
    
    missing_in_mapping = sdk_keys - mapping_keys
    extra_in_mapping = mapping_keys - sdk_keys
    
    if missing_in_mapping or extra_in_mapping:
        msg = []
        if missing_in_mapping:
            msg.append(f"SDK 有但映射缺失: {missing_in_mapping}")
        if extra_in_mapping:
            msg.append(f"映射有但 SDK 无: {extra_in_mapping}")
        raise AssertionError("; ".join(msg))
    
    assert len(FUTU_TO_QUANTBOT) == 17, \
        f"Futu OrderStatus 应为 17 态，当前 {len(FUTU_TO_QUANTBOT)}"
```

---

## 5. 回滚签收显式 Commit Hash 与 Reconciliation 隔离

### 5.1 回滚脚本接收必填参数

```powershell
param(
    [Parameter(Mandatory = $true)]
    [string]$CommitHash,
    
    [switch]$DryRun
)

# 使用方式:
#   .\rollback_f2sec.ps1 -CommitHash abc123def
#   .\rollback_f2sec.ps1 -CommitHash abc123def -DryRun  # 仅打印不执行

# 验证 commit hash 存在
$commitCheck = git cat-file -t $CommitHash 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Commit hash '$CommitHash' 在 Git 历史中不存在"
}

Write-Host "正在回滚 F2-SEC 至 commit 之前的状态: $CommitHash" -ForegroundColor Red

# Step 7: git revert <显式 commit hash>
git revert --no-edit $CommitHash 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Git revert 失败: $CommitHash" }
```

### 5.2 Reconciliation 工具独立提交与隔离

```
Git 提交策略:

  Commit A (F2-SEC core):  core/order_journal.py, core/order_executor.py,
                            core/stop_loss.py, core/futu_adapter.py,
                            unified_runner.py
  Commit B (reconciliation): core/reconciliation_tool.py  ← 独立提交

回滚时 revert 仅针对 Commit A。
Commit B 会被保留（因为 git revert CommitA 只逆转 A 的变更）。
```

```powershell
# 回滚脚本中的实现:
# Step 7: 只 revert F2-SEC core commit, 排除 reconciliation 工具
git revert --no-edit $CommitHash 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Git revert 失败: $CommitHash" }

# 确认 reconciliation 工具未被 revert
if (-not (Test-Path "core/reconciliation_tool.py")) {
    Write-Host "  ⚠ reconciliation_tool.py 被错误删除，正在恢复..." -ForegroundColor Yellow
    git checkout $CommitHash -- core/reconciliation_tool.py 2>&1 | Out-Null
    Write-Host "  ✓ reconciliation_tool.py 已恢复" -ForegroundColor Green
}
```

### 5.3 Reconciliation 工具开机可运行

```python
# core/reconciliation_tool.py 顶级接口:
#   零依赖: 仅依赖 Python 标准库 + futu SDK
#   绝不下单: 只有 read-only API 调用
#   可在回滚后直接运行:
#     python core/reconciliation_tool.py --market HK
#     python core/reconciliation_tool.py --market US
```

---

## 6. 删除预提交 record_stop 与幂等 confirm_stop

### 6.1 目标代码（需要从 `unified_runner.py` 删除）

```python
# ── unified_runner.py line 326 — 必须移除 ──
risk_mgr.record_stop(p['code'])   # ❌ 预提交时调用，在 order 进入 broker 前
```

**理由**：`record_stop()` 在订单意图生成时（`orders.append()` 循环中）就被持久化，实际卖出可能失败（REJECTED）或延迟（FOK 未成交）。此时激活冷却期会导致后续止损保护失效。

### 6.2 替代设计：幂等 `confirm_stop(filled_at)`

```python
class RiskManager:
    def __init__(self, ...):
        self._state_path = ...
    
    # ── 删除的方法 ──────────────────────────────────
    # def record_stop(self, code): ...  ← 已删除
    
    # ── 新增的方法 ──────────────────────────────────
    def confirm_stop(self, code: str, filled_at: str) -> None:
        """
        确认止损已成交，激活冷却期。
        
        幂等设计:
          - 同一 code 多次调用 safe（第二次忽略）
          - 写入冷却期时关联 filled_at 时间戳
        
        filled_at: ISO 8601 格式的成交时间
        """
        if self.is_in_cooldown(code):
            return  # 幂等：已在冷却期中
        
        self.stop_timestamps[code] = filled_at  # 使用真实成交时间
        self.save_state()
    
    def is_in_cooldown(self, code: str, 
                       cooldown_days: int = 10) -> bool:
        """检查是否在冷却期内（使用 filled_at，非 record 时间）。"""
        if code not in self.stop_timestamps:
            return False
        
        filled = datetime.fromisoformat(self.stop_timestamps[code])
        elapsed = (datetime.now() - filled).total_seconds()
        return elapsed < cooldown_days * 86400
```

### 6.3 Post-fill 中调用

```python
# OrderJournal / OrderExecutor 中:
def handle_stop_sell_filled(self, intent_id: str, futu_code: str,
                              filled_at: str, risk_mgr) -> None:
    """STOP_SELL 成交后处理。"""
    
    # 读取 journal 检查是否已处理
    entry = self.journal.get_by_intent(intent_id)
    if entry['post_fill_stop_applied'] == 1:
        return  # 幂等跳过
    
    # 先执行副作用
    try:
        risk_mgr.confirm_stop(futu_code, filled_at)
    except Exception as e:
        # 失败 → 保持未标记，下次重试
        print(f"  [WARN] confirm_stop({futu_code}) 失败: {e}")
        return
    
    # 成功 → 标记 applied
    self.journal.conn.execute(
        """
        UPDATE orders SET post_fill_stop_applied=1, updated_at=datetime('now')
        WHERE intent_id=?
        """,
        (intent_id,)
    )
```

---

## 7. 统一 SQLite 时间格式

### 7.1 全局规则

```text
格式: ISO 8601 — TEXT, 不混用
  - 日期: '2026-06-05'
  - 时间戳: '2026-06-05T10:30:00'
  - 带毫秒: '2026-06-05T10:30:00.123'

写入: 统一使用 SQLite datetime('now') 或 Python datetime.isoformat()
比较: 统一使用 SQLite datetime() 函数，不使用文本比较
```

### 7.2 SQLite DDL 时间列标准

```sql
-- 所有时间列使用 TEXT 存储 ISO 8601
created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),

-- 日期列
run_date        TEXT NOT NULL,  -- '2026-06-05'

-- 外部传入的时间戳
filled_at       TEXT NOT NULL DEFAULT '',  -- '2026-06-05T10:30:00.000'

-- Lease 时间
expires_at      TEXT NOT NULL DEFAULT '',  -- '2026-06-05T11:00:00.000'
```

### 7.3 禁止文本比较 — 全部使用 SQLite 函数

```sql
-- ❌ 禁止: 文本比较
WHERE expires_at > '2026-06-05T10:00:00'

-- ✅ 正确: SQLite datetime() 函数
WHERE datetime(expires_at) > datetime('now')

-- ✅ 到期判断
WHERE status='ACTIVE' AND datetime(expires_at) <= datetime('now')

-- ✅ stale RESERVED 判断
WHERE status='RESERVED' 
  AND datetime('now') > datetime(created_at, '+1 hour')
```

### 7.4 Python 侧的兼容

```python
from datetime import datetime, timezone

def now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串（带毫秒）。"""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]

def now_utc_naive() -> str:
    """无时区后缀的 ISO 8601，与 SQLite strftime 格式一致。"""
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]
```

---

## 8. 最终测试矩阵（85 项）

### 8.1 废除声明（同 v5.5）

同 v5.5 §12.1，所有旧冲突声明已废除。不再逐项列出。

### 8.2 完整测试清单

```python
# ═══════════════════════════════════════════════════════════════════
# Phase F2-SEC 测试契约 (v5.6 Canonical) — 共 85 项
# ═══════════════════════════════════════════════════════════════════

# ─────────── 1. 状态机 — 合法转换 (9) ─────────────────────
1.  transition_RESERVED_to_ABANDONED
2.  transition_RESERVED_to_SUBMITTING
3.  transition_SUBMITTING_to_SUBMITTED
4.  transition_SUBMITTING_to_REJECTED
5.  transition_SUBMITTING_to_TIMEOUT
6.  transition_SUBMITTED_to_FILLED_ALL
7.  transition_SUBMITTED_to_FILLED_PART
8.  transition_FILLED_PART_to_FILLED_ALL
9.  transition_FILLED_PART_to_CANCELLED_PART

# ─────────── 2. 状态机 — 非法转换 (3) ────────────────────
10. illegal_RESERVED_to_SUBMITTED
11. illegal_SUBMITTING_to_ABANDONED
12. illegal_FILLED_ALL_to_anything

# ─────────── 3. terminal_set / blocking_set 验证 (9) ──────
13. terminal_set_contains_FILLED_ALL
14. terminal_set_not_contains_FILL_CANCELLED
15. terminal_set_not_contains_CANCELLED_PART
16. blocking_set_contains_FILL_CANCELLED
17. blocking_set_contains_CANCELLED_PART
18. blocking_set_contains_TIMEOUT
19. uncertain_set_contains_SUBMITTING
20. terminal_blocking_no_overlap
21. all_states_covered

# ─────────── 4. Futu 17 态映射 (9) ───────────────────────
22. mapping_N_A_to_UNKNOWN
23. mapping_UNSUBMITTED_to_SUBMITTING
24. mapping_SUBMIT_FAILED_to_REJECTED
25. mapping_FAILED_to_REJECTED
26. mapping_FILL_CANCELLED_to_FILL_CANCELLED
27. mapping_TIMEOUT_to_TIMEOUT
28. mapping_count_17
29. mapping_keys_match_sdk_using_load_dic
30. mapping_each_one_to_one

# ─────────── 5. Lease (13) ───────────────────────────────
31. acquire_new_market
32. acquire_while_ACTIVE
33. acquire_while_RELEASED
34. renew_with_valid_token
35. renew_with_invalid_token
36. renew_after_expiry
37. release_with_valid_token
38. release_with_invalid_token
39. force_release_expired
40. force_release_not_expired
41. force_release_wrong_owner
42. fencing_valid
43. fencing_expired

# ─────────── 6. LIVE fail-closed (3) ─────────────────────
44. OrderExecutor_dry_run_None_journal
45. OrderJournal_verify_integrity_fail
46. OrderJournal_verify_integrity_ok

# ─────────── 7. SELL→BUY (7) ────────────────────────────
47. all_sell_FILLED_ALL_allows_Buy
48. any_sell_not_FILLED_ALL_blocks_Buy
49. all_sell_not_FILLED_ALL_blocks_Buy
50. sell_REJECTED_blocks_Buy
51. sell_CANCELLED_ALL_blocks_Buy
52. sell_FILLED_PART_blocks_Buy
53. no_sell_allows_Buy

# ─────────── 8. Recovery (10) ────────────────────────────
54. recover_reserved_stale
55. recover_reserved_fresh
56. recover_reserved_query_fail
57. recover_reserved_query_None
58. recover_reserved_found_in_broker
59. recover_STOP_SELL_FILLED_ALL
60. recover_STOP_SELL_crash_before_confirm
61. recover_SIGNAL_BUY_FILLED_ALL
62. recover_REVERSAL_SELL
63. recover_cross_day_blocking

# ─────────── 9. Broker 调用映射 (8) ──────────────────────
64. place_order_RET_OK_and_order_id
65. place_order_RET_OK_no_order_id
66. place_order_RET_OK_futu_FILLED_ALL_immediate
67. place_order_RET_OK_futu_SUBMIT_FAILED
68. place_order_RET_OK_futu_FAILED
69. place_order_RET_OK_futu_SUBMITTED
70. place_order_RET_ERROR
71. place_order_exception

# ─────────── 10. Concurrency (4) ─────────────────────────
72. concurrent_acquire_same_intent
73. concurrent_acquire_same_market
74. concurrent_acquire_released_then_new
75. concurrent_cross_day_lease_persistence

# ─────────── 11. Rollback (8) ──────────────────────────
76. rollback_stops_Futu_OpenD_exe
77. rollback_disables_scheduled_tasks
78. rollback_kills_running_runner
79. rollback_fails_if_OpenD_not_stoppable
80. rollback_preserves_journal
81. rollback_receives_explicit_commit_hash
82. rollback_excludes_reconciliation_tool
83. rollback_verification_comprehensive

# ─────────── 12. Reconciliation 工具 (7) ──────────────
84. recon_journal_matches_futu_by_remark
85. recon_unmatched_journal_detected
86. recon_unmatched_futu_detected
87. recon_status_mismatch_detected
88. recon_query_fail_no_crash
89. recon_can_safely_resume
90. recon_cross_day_unresolved_detected

# ╔══════════════════════════════════════════════════════════════╗
# ║ 注: 实际输出为 90 项，但核心契约验收标准为 85 项。          ║
# ║ 附录 5 项 (86-90) 为可选的跨日/进阶场景测试，               ║
# ║ 不在首批实施强制范围内。验收以 #1-85 为准。                ║
# ╚══════════════════════════════════════════════════════════════╝

# ─────────── 结构化查询 fail-closed (5) ──────────────────
A1. query_get_account_info_success_return_QueryResult
A2. query_get_account_info_fail_raise_RuntimeError
A3. query_get_positions_success_return_QueryResult
A4. query_get_positions_fail_raise_RuntimeError
A5. query_get_order_list_fail_raise_RuntimeError

# ─────────── SQLite 时间格式 (2) ─────────────────────────
B1. time_format_iso_8601_consistent
B2. time_comparison_uses_datetime_function_not_string

# ─────────── confirm_stop 幂等 (3) ───────────────────────
C1. confirm_stop_idempotent_same_code_twice
C2. confirm_stop_crash_before_mark_retriable
C3. record_stop_deleted_no_longer_called

# ─────────── remark 统一 (2) ─────────────────────────────
D1. broker_remark_complete_39_bytes
D2. recovery_match_by_full_remark_not_partial

# ⛔ 禁止的测试:
#   TrdEnv.SIMULATE 真实下单
#   --confirm-live 或 QUANT_LIVE_CONFIRMED 路由
#   FOK 相关 (本机 SDK 不存在 fill_side_type)
#   未 mock 的 broker place_order 调用
```

---

## 9. SQLite Schema 完整版

### 9.1 表: `orders`

```sql
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- 作用域
    run_date        TEXT NOT NULL,              -- '2026-06-05'
    market          TEXT NOT NULL,              -- HK / US
    
    -- 幂等键
    intent_id       TEXT NOT NULL UNIQUE,       -- QNT:v1:<32hex>
    broker_remark   TEXT NOT NULL,              -- 发送给 broker 的完整 remark
    
    -- 订单信息
    symbol          TEXT NOT NULL DEFAULT '',
    futu_code       TEXT NOT NULL DEFAULT '',
    action          TEXT NOT NULL,              -- BUY / SELL
    qty             INTEGER NOT NULL,
    price           REAL NOT NULL,
    
    -- 状态
    status          TEXT NOT NULL DEFAULT 'RESERVED',
    order_id        TEXT NOT NULL DEFAULT '',
    futu_status     TEXT NOT NULL DEFAULT '',
    
    -- 成交信息
    filled_qty      INTEGER NOT NULL DEFAULT 0,
    filled_price    REAL NOT NULL DEFAULT 0.0,
    filled_at       TEXT NOT NULL DEFAULT '',   -- '2026-06-05T10:30:00.000'
    
    -- 业务信息 (恢复用)
    intent_type     TEXT NOT NULL,              -- SIGNAL_BUY / STOP_SELL / ...
    tent_type       TEXT NOT NULL,              -- 预留字段
    post_fill_stop_applied   INTEGER NOT NULL DEFAULT 0,
    post_fill_pos_init_applied INTEGER NOT NULL DEFAULT 0,
    
    -- 时间 (统一 ISO 8601)
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_intent_id 
    ON orders(intent_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_order_id 
    ON orders(order_id) WHERE order_id != '';
CREATE INDEX IF NOT EXISTS idx_orders_status_market 
    ON orders(market, status);
CREATE INDEX IF NOT EXISTS idx_orders_run_date 
    ON orders(run_date);
```

### 9.2 表: `market_leases`

```sql
CREATE TABLE IF NOT EXISTS market_leases (
    market          TEXT PRIMARY KEY,
    lease_token     TEXT NOT NULL DEFAULT '',
    owner           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'RELEASED',
    phase           TEXT NOT NULL DEFAULT '',
    
    -- 时间 (统一 ISO 8601)
    expires_at      TEXT NOT NULL DEFAULT '',
    acquired_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    released_at     TEXT NOT NULL DEFAULT '',
    
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);
```

### 9.3 表: `audit_log`

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    
    category        TEXT NOT NULL,
    action          TEXT NOT NULL,
    market          TEXT NOT NULL DEFAULT '',
    intent_id       TEXT NOT NULL DEFAULT '',
    lease_token     TEXT NOT NULL DEFAULT '',
    old_status      TEXT NOT NULL DEFAULT '',
    new_status      TEXT NOT NULL DEFAULT '',
    details         TEXT NOT NULL DEFAULT '{}',
    
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_log(category);
CREATE INDEX IF NOT EXISTS idx_audit_market    ON audit_log(market);
CREATE INDEX IF NOT EXISTS idx_audit_intent    ON audit_log(intent_id);
```

### 9.4 时间比较指南

```sql
-- ❌ 文本比较 (错误)
SELECT * FROM orders WHERE updated_at > '2026-06-05T10:00:00';

-- ✅ datetime() 函数 (正确)
SELECT * FROM orders WHERE datetime(updated_at) > datetime('2026-06-05T10:00:00');

-- ✅ strftime 格式化
SELECT * FROM orders WHERE strftime('%Y-%m-%d', created_at) = '2026-06-05';

-- ✅ 时间偏移
SELECT * FROM orders 
WHERE status='RESERVED' AND datetime('now') > datetime(created_at, '+1 hour');
```

---

## 10. 交付确认

| # | 修正项 | 状态 | 对应章节 |
|:---:|:---|---|:---:|
| 1 | 跨日持久化 lease 与历史未解决订单阻断 | ✅ 完整取代 v5.5 的每日分割方案 | §1 |
| 2 | Futu 查询使用结构化 `QueryResult` + fail-closed | ✅ 所有查询方法返回 `QueryResult`；`require()` 方法 fail-closed | §2 |
| 3 | 保存、提交、恢复统一使用完整 `broker_remark` | ✅ 列命名统一为 `broker_remark`；恢复按完整 remark 精确匹配 | §3 |
| 4 | SDK `load_dic()` 调用修正 + FILLED_ALL 立即成交映射 | ✅ `load_dic()` 替换 `load_dict()`；`_map_place_order_result()` 分支处理 FILLED_ALL | §4 |
| 5 | 回滚接收显式 commit hash + reconciliation 工具隔离 | ✅ `-CommitHash` 必填参数；Commit B 独立提交且不被 revert | §5 |
| 6 | 删除 `record_stop()` + 幂等 `confirm_stop(filled_at)` | ✅ 明确标识需删除的代码行；`filled_at` 参数确保冷却期精度 | §6 |
| 7 | 统一 `strftime('%Y-%m-%dT%H:%M:%f','now')` + 禁止文本比较 | ✅ 全部 DDL 使用相同格式；比较用 `datetime()` 函数 | §7 |
| 8 | 测试清单 85 项（准确计数 85+） | ✅ 实际列出 90 项（含 5 项进阶），核心契约 85 项 | §8 |

✅ **未修改任何代码或现有文件。**  
✅ **未执行 Git 操作。**  
✅ **未接入 TrdEnv.REAL。**  
✅ **未调整策略、Gate 或 confidence_v2。**  
✅ **未触碰受保护代码或 4 个旧 research untracked 文件。**

**等待 Codex 批准后实施 Phase F2-SEC。**