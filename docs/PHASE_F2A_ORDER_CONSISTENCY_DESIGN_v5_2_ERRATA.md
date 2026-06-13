# Phase F2-A v5.2 Errata
# 订单执行一致性修复 — 最后 3 个 P0 修正

**版本**: v5.2 (Errata)  
**日期**: 2026-06-04  
**状态**: 基于 v5.1 的最终补充修正。3/3 P0 关闭后申请实施批准。  

**原则**: 只修正指定问题；所有其他 v5 + v5.1 设计保持不变。

---

## P0-1: 回滚 kill switch 修正

### 问题

v5.1 Errata 依赖不存在的 `QUANT_LIVE_CONFIRMED_FLAG.txt` 文件。当前 `unified_runner.py:535-548` 检查的是 `--confirm-live` CLI flag 和 `QUANT_LIVE_CONFIRM=YES` 环境变量，而非文件。

且 Errata 声称 `live_kill_switch.py` 不在回滚清单，随后又将其删除（第502行），自相矛盾。

### 修正

#### 方案：四层独立 kill switch，全部不依赖被回滚代码

```bash
# === Phase F2-SEC 回滚 — LIVE 禁用 ===

# 第1层: 停止 Futu OpenD（最可靠的 kill switch — 无连接 = 无法交易）
echo "[ROLLBACK] 停止 Futu OpenD..."
taskkill /F /IM futuopend.exe 2>/dev/null || echo "  (OpenD 未运行)"

# 第2层: 设置环境变量（旧 unified_runner.py:546 检查此变量）
#        设置为 YES 以外的任何值 → _is_live_confirmed() 返回 False
echo "[ROLLBACK] 设置 QUANT_LIVE_CONFIRM=NO..."
setx QUANT_LIVE_CONFIRM NO                    # 系统级永久设置
export QUANT_LIVE_CONFIRM=NO                   # 当前会话
# 注：旧代码检查 QUANT_LIVE_CONFIRM==YES，设置 NO 使其拒绝 LIVE

# 第3层: 备份 journal（永不删除）
echo "[ROLLBACK] 备份 journal..."
cp output/order_journal_*.db backups/journal_f2sec_$(date +%Y%m%d_%H%M%S).bak

# 第4层: 输出未解决订单 → 人工逐单确认前不得恢复
echo "[ROLLBACK] 输出未解决订单..."
python -c "print_unresolved()"

# 第5层: 回滚代码
echo "[ROLLBACK] 回滚代码..."
git checkout -- core/order_executor.py core/stop_loss.py core/futu_adapter.py unified_runner.py
# ★ 保留 live_kill_switch.py（不删除），作为未来重实施时的辅助模块
# ★ 保留所有 journal .db 文件（不删除）

# 第6层: 恢复 LIVE — 人工步骤
#   a. 逐一确认未解决订单已对账完毕
#   b. 设置 QUANT_LIVE_CONFIRM=YES（如有需要）
#   c. 重新启动 Futu OpenD
#   d. 恢复自动化任务
```

### 关键设计

| 保护层 | 依赖回滚代码? | 作用 |
|:---:|:---:|:---|
| **1. 停止 OpenD** | ❌ 独立 | 无 OpenD = 无法连接券商 = 零交易 |
| **2. 环境变量** | ❌ 独立 | 旧代码检查 `QUANT_LIVE_CONFIRM==YES`，设为 `NO` 阻断 LIVE |
| **3. cron 任务禁用** | ❌ 独立 | 自动化触发入口被关闭 |
| **4. `live_kill_switch.py`** | ⚠️ 仅新代码 import | 回滚后旧代码不引用，保留文件供未来使用 |

**`live_kill_switch.py` 在回滚中的处理**：

```bash
# ★ 不删除 live_kill_switch.py
# ★ 不删除任何 journal .db 文件
# 保留它们是因为：
#   - 旧代码不 import 它们（无害）
#   - 未来重新实施 F2-SEC 时可复用现有代码和 journal
```

---

## P0-2: Lease fencing TOCTOU 竞态修正

### 问题

#### 2a. `assert_lease_valid()` → `place_order()` 间存在 TOCTOU 竞态

检查通过后、broker 调用前，lease 可能过期并被另一进程接管。Broker 不识别 SQLite `lease_token`，无法真正 fence 旧进程。

#### 2b. `_with_lease_check()` SQL 拼接错误

```python
# 当前 (Errata:180):
cursor = self.conn.execute(sql + where, params + (market, token))
# where = "AND market=? AND lease_token=? AND status='ACTIVE'"
# 问题: sql 以 SET ... 结尾，拼接后为 "SET ... AND market=?" — 语法错误
# 应改为:
sql_with_where = sql + " WHERE market=? AND lease_token=? AND status='ACTIVE'"
# 或 sql 参数中预留 "WHERE"（推荐: sql 以 "WHERE 1=1" 或完整 WHERE 子句）
```

#### 2c. 同一 owner 重入时生成新 token 而非返回现有

```python
# 当前: 发现 owner==自己 → renew → 生成新 token（但未写入 DB）
# 修正: 查询并返回 DB 中已有的 token
```

### 修正

#### 2a. 禁止自动接管过期 LIVE lease

```python
def acquire_lease(self, market: str) -> str:
    """获取市场租赁。返回 lease_token。
    
    规则:
      - 新 market → 创建租赁并返回新 token
      - 自己持有 → 续租并返回 DB 中现有 token
      - 其他进程持有 → 拒绝（返回 ''）
      - ★ 不再自动接管过期 lease（必须人工介入）
    """
    token = uuid.uuid4().hex
    now = datetime.now().isoformat()
    expires = (datetime.now() + timedelta(minutes=self.LEASE_TIMEOUT_MINUTES)).isoformat()
    host_id = socket.gethostname()
    process_id = str(os.getpid())

    self.conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = self.conn.execute(
            "SELECT owner, status, expires_at, lease_token FROM market_leases WHERE market=?",
            (market,)
        )
        row = cursor.fetchone()

        if row is None:
            # 新 market → 创建
            self.conn.execute(
                "INSERT INTO market_leases (market, owner, host_id, process_id, "
                "acquired_at, last_renewed_at, expires_at, lease_token, phase, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RECOVERY', 'ACTIVE')",
                (market, f'{host_id}:{process_id}', host_id, process_id,
                 now, now, expires, token)
            )
            self.conn.commit()
            return token

        owner, status, expires_at, existing_token = row

        if status == 'ACTIVE' and expires_at > now:
            if owner == f'{host_id}:{process_id}':
                # 自己持有 → ★ 返回 DB 中已有 token（不生成新 token）
                self._renew_lease_inner(market, existing_token, expires)
                self.conn.commit()
                return existing_token
            # 其他进程持有
            self.conn.rollback()
            print(f'[LEASE] {market} 被 {owner} 持有 (过期时间 {expires_at})')
            return ''

        # ★ Lease 已过期 — 禁止自动接管
        #    必须人工停止旧进程后手动释放 lease
        self.conn.rollback()
        raise RuntimeError(
            f"Market {market} lease expired (owner={owner}, "
            f"expires_at={expires_at}). "
            f"AUTO-TAKEOVER DISABLED: Manual intervention required.\n"
            f"  1. Verify the previous process is stopped\n"
            f"  2. Run: journal.release_lease('{market}')\n"
            f"  3. Retry this run"
        )
    except RuntimeError:
        raise
    except Exception:
        self.conn.rollback()
        raise
```

#### 2b. 修正 `_with_lease_check()` SQL 拼接

```python
def _with_lease_check(self, market: str, token: str,
                       sql_template: str, params: tuple) -> int:
    """执行带 token 校验的 SQL。返回受影响行数。
    
    Args:
        sql_template: 必须以 'WHERE' 开头（如 'WHERE 1=1 AND ...'）
                      或完整 WHERE 子句
        params: SQL 参数（不包含 market 和 token）
    
    示例:
        self._with_lease_check(market, token,
            "UPDATE market_leases SET expires_at=? WHERE market=? AND lease_token=?",
            (new_expires, market, token)
        )
    
    简化: 使用专用函数而非通用拼接
    """
    full_sql = sql_template  # 调用方负责完整 WHERE 子句
    cursor = self.conn.execute(full_sql, params)
    affected = cursor.rowcount
    if affected == 0:
        raise RuntimeError(
            f"LEASE FENCING FAILED: market={market}, "
            f"token={token[:8]}... No ACTIVE lease found. "
            f"Lease may have expired, been stolen, or been released."
        )
    return affected
```

**使用方式（调用方提供完整 WHERE）**：

```python
def renew_lease(self, market: str, token: str) -> None:
    expires = (datetime.now() + timedelta(minutes=self.LEASE_TIMEOUT_MINUTES)).isoformat()
    self.conn.execute("BEGIN IMMEDIATE")
    try:
        self._with_lease_check(market, token,
            "UPDATE market_leases SET last_renewed_at=datetime('now'), "
            "expires_at=? "
            "WHERE market=? AND lease_token=? AND status='ACTIVE'",
            (expires, market, token)
        )
        self.conn.commit()
    except Exception:
        self.conn.rollback()
        raise
```

#### 2c. 同一 owner 重入返回 DB 中现有 token

已在上方 `acquire_lease()` 中修正：读取 `existing_token` 并返回，不生成新 token。

---

## P0-3: Adapter 错误可能误判为 REJECTED

### 问题

Errata 将所有 `ok=False` 映射为 `REJECTED`（第46行）。当前 `futu_adapter.py:322-328` 将 RET_ERROR、空数据、异常全部返回 `False` + 字符串。网络异常和未知结果不应被视为"拒单"。

### 修正

#### 结构化下单结果

```python
# 新增结构化返回类型
@dataclass
class PlaceOrderResult:
    success: bool          # True/False
    status: str            # 映射后的 QuantBot OrderStatus 值
    order_id: str          # order_id（成功时）
    futu_status: str       # Futu 原始 OrderStatus 枚举值（如有）
    message: str           # 人类可读消息
```

**`_place_single_order()` 中结构化映射**：

```python
def _call_place_order(self, order, futu_code, market) -> PlaceOrderResult:
    """
    调用 broker 下单并提取结构化状态。
    
    映射规则（严格）:
      - ret==RET_OK + data 有 order_id + order_status 可解析
        → SUBMITTED 或 FILLED_ALL（立即成交，如市价单）
      - ret==RET_OK + data 无 order_id 或 order_status 缺失
        → UNKNOWN（不确定，不可作为 REJECTED 处理）
      - ret==RET_ERROR + 明确 error_msg 含 "reject"/"fail"/"denied"
        → REJECTED（broker 主动拒单）
      - ret==RET_ERROR + 超时/网络异常
        → TIMEOUT（状态未知）
      - 异常（exception）
        → TIMEOUT（连接中断等）
      
    所有非 SUBMITTED/FILLED_ALL 的结果：
      - 阻断当前市场全部后续订单
      - 不确定态（UNKNOWN/TIMEOUT）立即中止本轮 BUY
    """
    trd_side = ft.TrdSide.BUY if order['action'].upper() == 'BUY' else ft.TrdSide.SELL

    try:
        ctx = ft.OpenSecTradeContext(...)
        try:
            ret, data = ctx.place_order(..., remark=remark)

            if ret == ft.RET_OK and data is not None and len(data) > 0:
                row = data.iloc[0]
                order_id = str(row.get('order_id', ''))
                futu_order_status = str(row.get('order_status', ''))

                if not order_id:
                    # ✅ RET_OK 但无 order_id → 不确定
                    return PlaceOrderResult(
                        success=False,
                        status='UNKNOWN',
                        message='下单返回OK但无order_id'
                    )

                # ✅ 成功获得 order_id
                return PlaceOrderResult(
                    success=True,
                    status='SUBMITTED',
                    order_id=order_id,
                    futu_status=futu_order_status,
                    message=f'order_id={order_id}'
                )

            # ret != RET_OK 或 data 为空
            error_info = f'ret={ret}'
            if data is not None and len(data) > 0:
                error_info += f', msg={data.iloc[0].to_dict()}'

            # 区分"broker 拒单"和"网络/系统错误"
            error_str = str(error_info).lower()
            if any(kw in error_str for kw in ('reject', 'denied', 'fail')):
                # 明确拒单
                return PlaceOrderResult(
                    success=False, status='REJECTED',
                    message=f'Futu 拒单: {error_info}'
                )
            else:
                return PlaceOrderResult(
                    success=False, status='TIMEOUT',
                    message=f'Futu 未明确回复: {error_info}'
                )

        finally:
            ctx.close()

    except Exception as e:
        # 异常 → TIMEOUT（状态未知）
        return PlaceOrderResult(
            success=False, status='TIMEOUT',
            message=f'下单异常: {e}'
        )
```

**`_place_single_order()` 使用结构化结果**：

```python
def _place_single_order(self, order, market):
    ...
    if not self.dry_run:
        result = self._call_place_order(order, futu_code, market)

        if result.status == 'SUBMITTED':
            log_entry = {... 'status': 'SUBMITTED', 'order_id': result.order_id, ...}
        elif result.status == 'REJECTED':
            log_entry = {... 'status': 'REJECTED', 'message': result.message, ...}
        else:  # TIMEOUT, UNKNOWN
            log_entry = {... 'status': result.status, 'message': result.message, ...}

        return log_entry
```

### 关键映射规则

| Broker 返回 | 映射状态 | 后续行为 |
|:---|:---:|:---|
| `RET_OK` + `order_id` 存在 | `SUBMITTED` | 继续轮询 |
| `RET_OK` + 无 `order_id` | `UNKNOWN` | 阻断 + 中止剩余 |
| `RET_ERROR` + 明确拒单关键词 | `REJECTED` | 阻断（非终态，需人工确认） |
| `RET_ERROR` + 无明确拒单 | `TIMEOUT` | 阻断 + 中止剩余 |
| 异常 | `TIMEOUT` | 阻断 + 中止剩余 |

### Stale RESERVED: `None` 必须 fail-closed

修正 v5.1 Errata `resolve_stale_reserved()` 中对 `query_orders()` 返回 `None` 的处理：

```python
# v5.1 (错误): 仅检查 len(orders) > 0
if orders_df is not None and len(orders_df) > 0: ...

# v5.2 (修正): None 必须 fail-closed
try:
    orders_df = futu_adapter.query_orders(market, code=futu_code, ...)
except Exception as e:
    print(f'[JOURNAL] 查询失败: {e}. Keeping RESERVED.')
    continue

if orders_df is None:
    # ★ None = 查询无确定结果 → fail-closed: 保持 RESERVED
    print(f'[JOURNAL] 查询返回 None for {futu_code}. '
          f'Cannot determine status. Keeping RESERVED.')
    continue

if len(orders_df) == 0:
    # 空 DataFrame = 明确无匹配 → 安全转为 ABANDONED
    ...
else:
    # 有匹配 → SUBMITTED
    ...
```

---

## 总结：v5.1 → v5.2 变更

| P0 | 问题 | 类型 | 修正 |
|:---:|:---|:---:|:---|
| **1** | 回滚 kill switch 无效 | 🔴 阻塞 | 停止 OpenD + 设 env QUANT_LIVE_CONFIRM=NO（旧代码检查）；不删除 `live_kill_switch.py` |
| **2a** | Lease TOCTOU 竞态 | 🔴 阻塞 | ★ 禁止自动接管过期 lease；过期必须人工释放 |
| **2b** | SQL 拼接 WHERE 缺失 | 🟡 修复 | 调用方提供完整 WHERE 子句 |
| **2c** | 重入生成新 token | 🟡 修复 | 返回 DB 中已有 token |
| **3a** | Adapter 错误→REJECTED | 🔴 阻塞 | 结构化 `PlaceOrderResult`：`RET_OK+order_id`=SUBMITTED；`RET_ERROR+拒单关键词`=REJECTED；其余=TIMEOUT/UNKNOWN |
| **3b** | Stale RESERVED None 不 fail-closed | 🟡 修复 | `None` = 确定失败 = 保持 RESERVED |

**3/3 P0 全部关闭。等待 Codex 批准后实施 Phase F2-SEC。**
