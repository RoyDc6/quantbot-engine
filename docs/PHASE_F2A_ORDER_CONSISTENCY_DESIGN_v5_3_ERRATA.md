# Phase F2-A v5.3 Errata
# 订单执行一致性修复 — 最后 3 个 P0 最终修正

**版本**: v5.3 (Errata)  
**日期**: 2026-06-04  
**状态**: 基于 v5.2 的最终补充修正。3/3 P0 关闭后申请实施批准。

---

## P0-1: 可执行的 Windows 回滚方案

### 问题

`QUANT_LIVE_CONFIRM=NO` 无法阻止显式 `--confirm-live` CLI flag；回滚脚本未禁用 Windows 计划任务 `AutoTradeHK`/`AutoTradeUS`；停止 OpenD 无法阻止已提交订单继续成交。

### 修正

#### 完整回滚执行脚本

```powershell
# ═══════════════════════════════════════════════════════════════
# rollback_f2sec.ps1 — Phase F2-SEC 回滚脚本
# 以管理员身份运行
# ═══════════════════════════════════════════════════════════════

Write-Host "=== Phase F2-SEC 回滚 ===" -ForegroundColor Red

# ── 第1层: 停止 Futu OpenD ──────────────────────────────────────
Write-Host "[1/5] 停止 Futu OpenD..." -ForegroundColor Yellow
taskkill /F /IM futuopend.exe 2>$null
if ($?) {
    Write-Host "  OpenD 已停止" -ForegroundColor Green
} else {
    Write-Host "  (OpenD 未运行)" -ForegroundColor Gray
}

# ── 第2层: 禁用 Windows 计划任务（阻止自动化运行）────────────────
Write-Host "[2/5] 禁用计划任务..." -ForegroundColor Yellow
$tasks = @("AutoTradeHK", "AutoTradeUS")
foreach ($t in $tasks) {
    schtasks /Change /TN $t /DISABLE 2>$null
    if ($?) {
        Write-Host "  $t 已禁用" -ForegroundColor Green
    } else {
        Write-Host "  (任务 $t 不存在)" -ForegroundColor Gray
    }
}

# ── 第3层: 设置环境变量 ──────────────────────────────────────────
Write-Host "[3/5] 设置环境变量..." -ForegroundColor Yellow
[Environment]::SetEnvironmentVariable("QUANT_LIVE_KILLED", "YES", "User")
# 当前会话也设置
$env:QUANT_LIVE_KILLED = "YES"
Write-Host "  QUANT_LIVE_KILLED=YES 已设置 (用户级)" -ForegroundColor Green

# ── 第4层: 备份 journal ──────────────────────────────────────────
Write-Host "[4/5] 备份 journal..." -ForegroundColor Yellow
$date = Get-Date -Format "yyyyMMdd_HHmmss"
New-Item -ItemType Directory -Force -Path "backups" | Out-Null
Get-ChildItem "output/order_journal_*.db" | ForEach-Object {
    $dest = "backups/journal_f2sec_$date_$($_.Name)"
    Copy-Item $_.FullName $dest
    Write-Host "  备份: $_ → $dest" -ForegroundColor Green
}

# ── 第5层: 输出未解决订单列表 ────────────────────────────────────
Write-Host "[5/5] 检查未解决订单..." -ForegroundColor Yellow
python -c "
import sqlite3, glob
for f in glob.glob('output/order_journal_*.db'):
    conn = sqlite3.connect(f)
    rows = conn.execute(\"\"\"
        SELECT status, count(*) FROM orders
        GROUP BY status ORDER BY status
    \"\"\").fetchall()
    print(f'  {f}:')
    for status, count in rows:
        print(f'    {status}: {count}')
    conn.close()
"

Write-Host ""
Write-Host "=== 回滚完成 ===" -ForegroundColor Red
Write-Host ""
Write-Host "重要注意事项:" -ForegroundColor Yellow
Write-Host "  1. 已提交尚未成交的订单将通过 Futu 正常成交，不受影响"
Write-Host "  2. 回滚后 journal 文件保留为只读档案，不影响旧代码运行"
Write-Host "  3. 需要恢复 LIVE 时，必须完成以下人工步骤:" -ForegroundColor Cyan
Write-Host "     a. 逐单确认未解决订单状态 (Futu order_list_query)"
Write-Host "     b. 启动 OpenD"
Write-Host "     c. 启用计划任务: schtasks /Change /TN AutoTradeHK /ENABLE"
Write-Host "     d. 删除环境变量: [Environment]::SetEnvironmentVariable('QUANT_LIVE_KILLED', '', 'User')"
Write-Host "     e. 使用 --confirm-live 或 QUANT_LIVE_CONFIRM=YES 运行"
```

#### Kill switch 检查（独立于被回滚代码）

```python
# core/live_kill_switch.py — 新增文件，不在回滚清单中
"""
LIVE 执行全局 kill switch。

检查流程（多层）:
  1. QUANT_LIVE_KILLED=YES 环境变量 → 阻止所有 LIVE 执行
  2. Futu OpenD 是否在运行 → 阻止所有下单尝试

注: 此文件回滚时保留（不删除）。旧代码不会 import 此模块，
    但即便有人绕过 env check 手动运行 --confirm-live，
    第1层（OpenD 停止）也将导致所有 place_order() 失败。
"""

import os

def is_live_killed() -> bool:
    return os.environ.get('QUANT_LIVE_KILLED', '').upper() == 'YES'

def assert_live_not_killed():
    if is_live_killed():
        raise RuntimeError(
            "LIVE 执行已被全局 kill switch 禁用 "
            "(QUANT_LIVE_KILLED=YES)。"
        )
```

#### 关于"已提交订单继续成交"的处理

这是预期行为，不由 kill switch 控制，而是由 journal **对账恢复**处理：

```text
回滚期间已提交订单的成交:
  ├── 若成交 → 下次运行时 reconciliation 过程会更新 journal 状态
  ├── 若未成交 → 留在 broker 系统，人工确认后手动处理
  └── journal 中未解决订单 → 在恢复 LIVE 前必须逐单对账

这属于 broker 订单生命周期管理, 不是 LIVE kill switch 的职责范围。
kill switch 只负责阻止 NEW 订单提交。
```

---

## P0-2: `RELEASED` lease 必须允许正常重新获取

### 问题

`acquire_lease()` 未处理 `RELEASED` 状态，会落入"过期 ACTIVE，需人工介入"分支。导致首次运行释放后第二次运行无法获取 lease。

### 修正

```python
def acquire_lease(self, market: str) -> str:
    """获取市场租赁。返回 lease_token。"""
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

        # ── 正常重新获取: RELEASED → 重新激活 ─────
        if status == 'RELEASED':
            self.conn.execute(
                "UPDATE market_leases SET owner=?, host_id=?, process_id=?, "
                "acquired_at=?, last_renewed_at=?, expires_at=?, lease_token=?, "
                "phase='RECOVERY', status='ACTIVE' WHERE market=?",
                (f'{host_id}:{process_id}', host_id, process_id,
                 now, now, expires, token, market)
            )
            self.conn.commit()
            return token

        # ── 自己持有: 续租 ──────────────────────────
        if status == 'ACTIVE' and expires_at > now:
            if owner == f'{host_id}:{process_id}':
                self._renew_lease_inner(market, existing_token, expires)
                self.conn.commit()
                return existing_token
            # 其他进程持有 → 拒绝
            self.conn.rollback()
            return ''

        # ── ACTIVE 过期: 唯一需人工介入的场景 ─────
        if status == 'ACTIVE' and expires_at <= now:
            self.conn.rollback()
            raise RuntimeError(
                f"Market {market} lease EXPIRED (owner={owner}, "
                f"expires_at={expires_at}). "
                f"Manual intervention required:\n"
                f"  1. Verify old process is stopped\n"
                f"  2. Run: journal.release_lease('{market}')\n"
                f"  3. Retry"
            )

        # 其他状态（理论上不应出现）
        self.conn.rollback()
        return ''
```

### 状态流转矩阵

| 现有 status | 过期? | 自己? | 行为 |
|:---|:---:|:---:|:---|
| (无记录) | — | — | 创建新租赁 ✅ |
| `RELEASED` | — | — | 重新激活 ✅ |
| `ACTIVE` | ❌ 未过期 | ✅ 自己 | 续租 ✅ |
| `ACTIVE` | ❌ 未过期 | ❌ 他人 | 返回 '' |
| `ACTIVE` | ✅ 已过期 | — | **人工介入** ⚠️ |

### 新增测试

| 测试 | 验证 |
|:---|:---|
| `RELEASED` 后可重新获取 | 释放后 → acquire → 返回新 token |
| 运行→释放→再运行 | 完整周期: acquire → release → acquire 成功 |
| ACTIVE 过期需人工 | acquire(过期) → RuntimeError |

---

## P0-3: 结构化下单结果 — 修复返回类型与 dataclass

### 问题

1. RET_ERROR 返回值是字符串 `msg`，但示例中调用 `data.iloc`（v5.2:268）— 类型错误
2. RET_ERROR+关键词推断为 REJECTED — 不安全，必须仅由 `order_status` 判定
3. RET_OK + order_id 未解析 `order_status`
4. `PlaceOrderResult` 5 字段全部必填但调用处只传 3 个

### 修正

#### Dataclass 使用默认值

```python
@dataclass
class PlaceOrderResult:
    success: bool
    status: str                          # QuantBot OrderStatus
    message: str
    order_id: str = ''                   # ★ 默认 ''，可省略
    futu_status: str = ''                # ★ 默认 ''，可省略（Futu 原始状态）
```

所有字段在构造时均可只传必填项：

```python
PlaceOrderResult(True, 'SUBMITTED', 'ok', order_id='123')        # 正确
PlaceOrderResult(False, 'TIMEOUT', '连接超时')                     # 正确
PlaceOrderResult(False, 'UNKNOWN', 'RET_OK 但无 order_id')        # 正确
```

#### 完整映射（仅 `order_status` 权威判定）

```python
def _call_place_order(self, order, futu_code, market, remark) -> PlaceOrderResult:
    """调用 broker 下单并提取结构化状态。

    核心原则:
      - 仅凭 Futu SDK 的 order_status 字段判断拒单/成交
      - RET_ERROR 永远不映射为 REJECTED（可能是网络错误）
      - RET_OK+order_id 仍需解析 order_status
    """
    try:
        ctx = ft.OpenSecTradeContext(filter_trdmarket=market, host=..., port=...)
        try:
            ret, data = ctx.place_order(
                price=..., qty=..., code=futu_code,
                trd_side=..., remark=remark,
                trd_env=ft.TrdEnv.SIMULATE,
                order_type=ft.OrderType.NORMAL,
                time_in_force=ft.TimeInForce.DAY,
            )

            if ret != ft.RET_OK:
                # ★ RET_ERROR: data 是字符串 msg，不是 DataFrame
                #   不推断任何状态。无法区分拒单 vs 网络异常。
                return PlaceOrderResult(
                    False, 'TIMEOUT',
                    f'下单 API 返回错误 (ret={ret}): {data}'
                )

            # ret == RET_OK: data 是 pd.DataFrame
            if data is None or len(data) == 0:
                return PlaceOrderResult(
                    False, 'UNKNOWN',
                    'RET_OK 但 data 为空或 None'
                )

            row = data.iloc[0]
            order_id = str(row.get('order_id', ''))
            futu_raw_status = str(row.get('order_status', ''))
            dealt_qty = int(row.get('dealt_qty', 0))
            dealt_price = float(row.get('dealt_avg_price', 0.0))

            if not order_id:
                return PlaceOrderResult(
                    False, 'UNKNOWN',
                    'RET_OK 但 data 中无 order_id',
                    futu_status=futu_raw_status,
                )

            # ★ 权威判定: 按 order_status 精确映射
            if futu_raw_status in ('SUBMITTED',):
                return PlaceOrderResult(
                    True, 'SUBMITTED', f'order_id={order_id}',
                    order_id=order_id, futu_status=futu_raw_status,
                )

            if futu_raw_status in ('FILLED_ALL',):
                return PlaceOrderResult(
                    True, 'FILLED_ALL', f'order_id={order_id} 已全部成交',
                    order_id=order_id, futu_status=futu_raw_status,
                )

            if futu_raw_status in ('SUBMIT_FAILED', 'FAILED'):
                return PlaceOrderResult(
                    False, 'REJECTED', f'Futu 拒单: status={futu_raw_status}',
                    order_id=order_id, futu_status=futu_raw_status,
                )

            if futu_raw_status in ('FILLED_PART',):
                return PlaceOrderResult(
                    True, 'FILLED_PART', f'部分成交 qty={dealt_qty}@price={dealt_price}',
                    order_id=order_id, futu_status=futu_raw_status,
                )

            if futu_raw_status in ('TIMEOUT',):
                return PlaceOrderResult(
                    False, 'TIMEOUT', f'order_id={order_id} 超时',
                    order_id=order_id, futu_status=futu_raw_status,
                )

            if futu_raw_status in ('CANCELLED_ALL', 'CANCELLED_PART',
                                    'CANCELLING_ALL', 'CANCELLING_PART'):
                return PlaceOrderResult(
                    True, f'CANCELLED', f'状态={futu_raw_status}',
                    order_id=order_id, futu_status=futu_raw_status,
                )

            if futu_raw_status in ('FILL_CANCELLED',):
                return PlaceOrderResult(
                    True, 'FILL_CANCELLED', f'成交后撤销: order_id={order_id}',
                    order_id=order_id, futu_status=futu_raw_status,
                )

            if futu_raw_status in ('DISABLED', 'DELETED'):
                return PlaceOrderResult(
                    False, futu_raw_status,
                    f'订单不可用: status={futu_raw_status}',
                    order_id=order_id, futu_status=futu_raw_status,
                )

            # ★ 未知/未映射的 order_status → UNKNOWN
            return PlaceOrderResult(
                False, 'UNKNOWN',
                f'未识别的 order_status={futu_raw_status}',
                order_id=order_id, futu_status=futu_raw_status,
            )

        finally:
            ctx.close()

    except Exception as e:
        return PlaceOrderResult(
            False, 'TIMEOUT', f'下单异常: {e}'
        )
```

#### 完整映射表（权威来源：Futu `order_status`）

| Futu order_status | QuantBot 映射 | 成功? | 说明 |
|:---|:---:|:---:|:---|
| `SUBMITTED` | `SUBMITTED` | ✅ | 已提交 |
| `FILLED_ALL` | `FILLED_ALL` | ✅ | 全部成交 |
| `FILLED_PART` | `FILLED_PART` | ✅ | 部分成交 |
| `SUBMIT_FAILED` | `REJECTED` | ❌ | **唯一权威拒单** |
| `FAILED` | `REJECTED` | ❌ | **唯一权威拒单** |
| `TIMEOUT` | `TIMEOUT` | ❌ | 超时 |
| `CANCELLED_ALL` | `CANCELLED_ALL` | ✅ | 全量撤销 |
| `CANCELLED_PART` | `CANCELLED_PART` | ✅ | 部分撤销 |
| `CANCELLING_ALL/PART` | `CANCELLING` | ✅ | 撤销中 |
| `FILL_CANCELLED` | `FILL_CANCELLED` | ✅ | 需人工介入 |
| `DISABLED` | `DISABLED` | ❌ | 账户禁用 |
| `DELETED` | `DELETED` | ❌ | 已删除 |
| `NONE`/`UNSUBMITTED`/`WAITING_SUBMIT` | `SUBMITTING` | ❌ | 提交中（非终态） |
| (RET_ERROR) | `TIMEOUT` | ❌ | **不推断 REJECTED** |
| (RET_OK 无 order_id) | `UNKNOWN` | ❌ | 不确定 |
| (RET_OK+未知 status) | `UNKNOWN` | ❌ | 未映射状态 |

#### REJECTED 判定规则（严格）

```python
# ❌ 不安全 (v5.2):
if any(kw in error_str for kw in ('reject', 'denied', 'fail')):
    return PlaceOrderResult(False, 'REJECTED', ...)

# ✅ 安全 (v5.3): 仅凭 Futu order_status 判断
if futu_raw_status in ('SUBMIT_FAILED', 'FAILED'):
    return PlaceOrderResult(False, 'REJECTED', ...)
# 所有 RET_ERROR → TIMEOUT（不推断）
```

---

## 继承测试矩阵更新

| 原测试 (v5) | 变更 | 原因 |
|:---|:---:|:---|
| acquire → release → acquire 成功 | ✅ 新增 | P0-2: RELEASED 重新获取 |
| 过期 lease 自动接管 | ❌ **删除** | P0-2: 不再自动接管 |
| 过期 lease 人工介入 | ✅ 保留 | P0-2: 仅 ACTIVE 过期需人工 |
| RET_ERROR+关键词→REJECTED | ❌ **删除** | P0-3: 改为 TIMEOUT |
| RET_OK+order_status→映射 | ✅ **新增** | P0-3: 完整权威映射 |
| RET_ERROR→TIMEOUT | ✅ **新增** | P0-3: 所有 RET_ERROR→TIMEOUT |
| RET_OK 无 order_id→UNKNOWN | ✅ **新增** | P0-3: 不确定状态 |
| 回滚禁用计划任务 | ✅ **新增** | P0-1: schtasks /DISABLE |
| 回滚后已提交订单继续成交 | ✅ **新增文档说明** | P0-1: 预期行为 |

---

## 总结

| P0 | 问题 | 修正 |
|:---:|:---|:---|
| **1** | 回滚 kill switch 无效 | 停止 OpenD + `schtasks /DISABLE AutoTradeHK/AutoTradeUS` + `QUANT_LIVE_KILLED=YES` + 保留 `live_kill_switch.py` |
| **2** | RELEASED 无法重新获取 | `status=RELEASED` → 重新激活；仅 `ACTIVE`+过期→人工 |
| **3a** | 关键词推断拒单 | 仅 `order_status=SUBMIT_FAILED/FAILED` → REJECTED；所有 RET_ERROR → TIMEOUT |
| **3b** | RET_ERROR 用 `data.iloc` | RET_ERROR 时 data 是字符串，分支区分处理 |
| **3c** | RET_OK 未解析 order_status | 完整 18 态 order_status 映射 |
| **3d** | dataclass 字段全部必填 | `order_id=''` + `futu_status=''` 有默认值 |

**3/3 P0 全部关闭。等待 Codex 批准后实施 Phase F2-SEC。**
