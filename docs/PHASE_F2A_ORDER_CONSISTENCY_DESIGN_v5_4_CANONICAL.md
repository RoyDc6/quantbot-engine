# Phase F2-A v5.4 Canonical Implementation Contract
# 订单执行一致性修复 — 最终实施契约

**版本**: v5.4 (Canonical — 取代 v1–v5.3 全部版本)  
**日期**: 2026-06-04  
**状态**: 本文件是 Phase F2-SEC 的唯一实施来源。所有早前版本 (v1–v5.3) 中的矛盾陈述已废除。  
**原则**: 仅包含可验证的唯一映射、精确契约和 fail-closed 规则。不得从本文档之外的任何早前版本推断设计。

---

## 1. 可验证且 fail-closed 的 Windows 回滚流程

### 1.1 回滚执行脚本

```powershell
# ═════════════════════════════════════════════════════════════════
# rollback_f2sec.ps1 — Phase F2-SEC 回滚
# 要求: 以管理员身份运行
# 行为: 每一步失败都导致回滚中止 (fail-closed)
# ═════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"

Write-Host "=== Phase F2-SEC 回滚 ===" -ForegroundColor Red

# ── Step 1: 验证并停止 Futu OpenD ────────────────────────────
Write-Host "[1/7] 停止 Futu OpenD..." -ForegroundColor Yellow
$opend = Get-Process "futuopend" -ErrorAction SilentlyContinue
if ($opend) {
    taskkill /F /IM futuopend.exe 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    $stillRunning = Get-Process "futuopend" -ErrorAction SilentlyContinue
    if ($stillRunning) { throw "OpenD 无法停止" }
    Write-Host "  ✓ OpenD 已停止" -ForegroundColor Green
} else {
    Write-Host "  ✓ (OpenD 未运行)" -ForegroundColor Gray
}

# ── Step 2: 停止当前正在运行的 unified_runner.py ──────────────
Write-Host "[2/7] 停止运行中的 unified_runner..." -ForegroundColor Yellow
$runners = Get-Process | Where-Object {
    $_.ProcessName -eq "python" -and $_.CommandLine -match "unified_runner"
}
if ($runners) {
    $runners | Stop-Process -Force
    Write-Host "  ✓ 已停止 $($runners.Count) 个 runner 进程" -ForegroundColor Green
} else {
    Write-Host "  ✓ (无运行中的 runner)" -ForegroundColor Gray
}

# ── Step 3: 禁用 Windows 计划任务 ─────────────────────────────
Write-Host "[3/7] 禁用计划任务..." -ForegroundColor Yellow
$tasks = @("AutoTradeHK", "AutoTradeUS")
foreach ($t in $tasks) {
    $task = schtasks /Query /TN $t 2>&1
    if ($LASTEXITCODE -eq 0) {
        schtasks /Change /TN $t /DISABLE 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "无法禁用计划任务 $t" }
        Write-Host "  ✓ $t 已禁用" -ForegroundColor Green
    } else {
        Write-Host "  ✓ (任务 $t 不存在)" -ForegroundColor Gray
    }
}

# ── Step 4: 设置环境变量 kill switch ─────────────────────────
Write-Host "[4/7] 设置 QUANT_LIVE_KILLED..." -ForegroundColor Yellow
[Environment]::SetEnvironmentVariable("QUANT_LIVE_KILLED", "YES", "User")
$env:QUANT_LIVE_KILLED = "YES"

# 验证: 读取确认
$check = [Environment]::GetEnvironmentVariable("QUANT_LIVE_KILLED", "User")
if ($check -ne "YES") { throw "环境变量设置失败" }
Write-Host "  ✓ QUANT_LIVE_KILLED=YES (用户级)" -ForegroundColor Green

# ── Step 5: 备份 journal ───────────────────────────────────────
Write-Host "[5/7] 备份 journal..." -ForegroundColor Yellow
$date = Get-Date -Format "yyyyMMdd_HHmmss"
New-Item -ItemType Directory -Force -Path "backups" | Out-Null
$journalFiles = Get-ChildItem "output/order_journal_*.db" -ErrorAction SilentlyContinue
if ($journalFiles) {
    foreach ($f in $journalFiles) {
        $dest = "backups/journal_f2sec_$date`_$($f.Name)"
        Copy-Item $f.FullName $dest -Force
        Write-Host "  ✓ $($f.Name) → $dest" -ForegroundColor Green
    }
} else {
    Write-Host "  ✓ (无 journal 文件)" -ForegroundColor Gray
}

# ── Step 6: 回滚代码 ───────────────────────────────────────────
Write-Host "[6/7] 回滚代码..." -ForegroundColor Yellow
git checkout -- core/order_executor.py core/stop_loss.py core/futu_adapter.py unified_runner.py
if ($LASTEXITCODE -ne 0) { throw "Git checkout 失败" }
Write-Host "  ✓ 代码已回滚" -ForegroundColor Green
# ★ 保留 core/live_kill_switch.py 和 core/order_journal.py
#   (旧版代码不 import 它们，保留无害)

# ── Step 7: 验证回滚 ──────────────────────────────────────────
Write-Host "[7/7] 验证..." -ForegroundColor Yellow
$diff = git diff --stat
if ($diff) { throw "回滚后仍有未回滚的修改" }
Write-Host "  ✓ git status clean" -ForegroundColor Green

# 输出未解决订单信息
Write-Host ""
python -c "
import sqlite3, glob
for f in glob.glob('output/order_journal_*.db'):
    conn = sqlite3.connect(f)
    rows = conn.execute('''
        SELECT status, count(*) FROM orders
        GROUP BY status ORDER BY status
    ''').fetchall()
    if rows:
        print(f'  {f}:')
        for status, count in rows:
            print(f'    {status}: {count}')
    conn.close()
"

Write-Host ""
Write-Host "=== 回滚成功 ===" -ForegroundColor Green
Write-Host ""
Write-Host "恢复 LIVE 的条件（逐一手动确认）:" -ForegroundColor Yellow
Write-Host "  1. journal 中已无未解决订单" -ForegroundColor Cyan
Write-Host "  2. 启动 Futu OpenD" -ForegroundColor Cyan
Write-Host "  3. schtasks /Change /TN AutoTradeHK /ENABLE" -ForegroundColor Cyan
Write-Host "  4. 删除 QUANT_LIVE_KILLED: Remove-Item Env:QUANT_LIVE_KILLED" -ForegroundColor Cyan
Write-Host "  5. 使用 --confirm-live 或 QUANT_LIVE_CONFIRM=YES 运行" -ForegroundColor Cyan
```

### 1.2 Kill switch 的硬约束保证

回滚后旧 `unified_runner.py` 的 LIVE 检查链：

```text
LIVE_CHECK:
  1. --confirm-live 手动 flag → 放行（人工操作，无法机械禁止）
  2. QUANT_LIVE_CONFIRM=YES env → 放行

但无论哪种放行，最终下单路径阻挡：
  → core/futu_adapter.py 中 ctx = ft.OpenSecTradeContext(host, port)
  → OpenD 已停止 → socket 连接失败 → 抛出异常
  → _place_single_order() 捕获异常 → 返回 TIMEOUT
  → 订单永不触达 broker
```

**硬约束**：停止 OpenD = 无法连接券商 = 零订单。此约束**不依赖** `live_kill_switch.py` 或任何新增代码。即使旧代码被恢复，OpenD 停止后所有订单提交都会连接失败。

### 1.3 关于已提交订单继续成交

回滚不撤销 broker 中已受理的订单。这些订单由 journal reconciliation 追踪：

```text
已提交但未成交的订单:
  → 在 broker 中正常执行（无法取消）
  → 重新运行时 reconciliation 流程会通过 order_list_query() 确认状态
  → FAILED/REJECTED → journal 更新
  → FILLED_ALL → journal 更新 + 标记副作用
  → UNKNOWN → 人工介入
```

---

## 2. 完整 Lease API

### 2.1 核心规则

| 规则 | 说明 |
|:---|:---|
| **ACTIVE 不可复用** | `acquire_lease()` 遇到任何 `ACTIVE` 记录均返回 `''`，不管 owner 是否匹配 |
| **续租必须持 token** | `renew_lease(market, token)` 是延长租赁的唯一方式 |
| **强制释放必须审计** | `force_release_expired_lease()` 必须确认已过期 + 人工确认 token |
| **RELEASED 可重获** | `status='RELEASED'` 时允许正常重新获取 |

### 2.2 API 契约

```python
def acquire_lease(self, market: str) -> str:
    """
    获取市场租赁。
    
    返回: lease_token (32 hex)，或 '' 表示拒绝。
    
    规则:
      - 无记录 → 创建 ACTIVE，返回新 token
      - status='RELEASED' → 重新激活，返回新 token
      - status='ACTIVE' → 拒绝，返回 ''（OWNER 校验的续租请用 renew_lease）
      - 其他 → 拒绝，返回 ''
    
    审计: 每次 acquire 写入 audit_log(category='LEASE', action='acquire_lease')
    """

def renew_lease(self, market: str, token: str) -> None:
    """
    续租（延长 expires_at）。
    
    校验: WHERE market=? AND lease_token=? AND status='ACTIVE'
    行数=0 → raise RuntimeError("Lease fencing failed")
    
    审计: 写入 audit_log(category='LEASE', action='renew_lease')
    """

def update_lease_phase(self, market: str, token: str, phase: str) -> None:
    """
    更新租约阶段。所有阶段同上校验。
    审计: 写入 audit_log。
    """

def release_lease(self, market: str, token: str) -> None:
    """
    正常释放租赁。status='RELEASED'。
    校验: WHERE market=? AND lease_token=? AND status='ACTIVE'
    审计: 写入 audit_log。
    """

def force_release_expired_lease(self, market: str, expected_owner: str,
                                  expected_token: str) -> None:
    """
    强制释放过期租赁（人工操作）。
    
    校验:
      WHERE market=? AND lease_token=? AND owner=? 
      AND status='ACTIVE' AND expires_at < datetime('now')
    
    只有全部条件满足时才 UPDATE status='RELEASED'。
    任一条件不满足→raise RuntimeError("条件不匹配，强制释放被拒绝")
    
    审计: 写入 audit_log(category='LEASE', action='force_release_expired',
                          details=json.dumps({expected_owner, expected_token}))
    
    注意: 此函数不校验电话/密码，由调用方确认人工授权。
    """

def assert_lease_valid(self, market: str, token: str) -> None:
    """
    每次 broker 调用前的 fencing 检查。
    
    校验: WHERE market=? AND lease_token=? AND status='ACTIVE' AND expires_at > now()
    行数=0 → raise RuntimeError("LEASE FENCING FAILED")
    """

def get_lease_info(self, market: str) -> dict:
    """返回当前租约信息（只读，用于诊断）。"""
```

### 2.3 测试

| 测试 | 预期 |
|:---|:---|
| `acquire_lease` 新市场 | 返回 token，记录 ACTIVE |
| `acquire_lease` 已 ACTIVE | 返回 ''（不论 owner 是否匹配） |
| `acquire_lease` RELEASED | 重新激活，返回新 token |
| `renew_lease` 正确 token | expires_at 延长 |
| `renew_lease` 错误 token | RuntimeError |
| `renew_lease` 过期后 | RuntimeError |
| `release_lease` 正确 token | status → RELEASED |
| `release_lease` 错误 token | RuntimeError |
| `force_release_expired_lease` 全部匹配 | status → RELEASED |
| `force_release_expired_lease` owner 不匹配 | RuntimeError |
| `force_release_expired_lease` 未过期 | RuntimeError |
| `assert_lease_valid` 有效 | 无异常 |
| `assert_lease_valid` 过期 token | RuntimeError |
| `assert_lease_valid` 释放后 | RuntimeError |

---

## 3. 基于本机 SDK 17 态的唯一映射字典

### 3.1 `FUTU_TO_QUANTBOT` 映射（权威来源）

```python
# 本机 SDK: futu 10.05.6508
# 定义位置: futu/common/constant.py:1299-1350
# 共 17 种 OrderStatus 枚举

FUTU_TO_QUANTBOT: dict[str, str] = {
    # ── 提交前/提交中 → SUBMITTING ──────────────
    'NONE':             'UNKNOWN',        # N/A → 未知（非 SUBMITTING）
    'UNSUBMITTED':      'SUBMITTING',     # 已录入 broker 系统，不可放弃
    'WAITING_SUBMIT':   'SUBMITTING',     # 等待提交至交易所
    'SUBMITTING':       'SUBMITTING',     # 提交中

    # ── 提交结果 ────────────────────────────────
    'SUBMIT_FAILED':    'REJECTED',       # 提交失败 → 唯一权威"拒单"
    'SUBMITTED':        'SUBMITTED',      # 已提交，等待成交
    'TIMEOUT':          'TIMEOUT',        # 处理超时（Futu 服务器超时）

    # ── 成交 ────────────────────────────────────
    'FILLED_PART':      'FILLED_PART',    # 部分成交
    'FILLED_ALL':       'FILLED_ALL',     # 全部成交

    # ── 撤销 ────────────────────────────────────
    'CANCELLING_PART':  'CANCELLING',     # 部分撤销中（非终态）
    'CANCELLING_ALL':   'CANCELLING',     # 全部撤销中（非终态）
    'CANCELLED_PART':   'CANCELLED_PART', # 已部分撤销
    'CANCELLED_ALL':    'CANCELLED_ALL',  # 已全部撤销

    # ── 异常终态 ────────────────────────────────
    'FAILED':           'REJECTED',       # 服务拒绝 → 唯一权威"拒单"
    'DISABLED':         'DISABLED',       # 已失效
    'DELETED':          'DELETED',        # 已删除
    'FILL_CANCELLED':   'FILL_CANCELLED', # 成交后撤销 → 需人工介入
}
```

### 3.2 `QuantBot.OrderStatus` 枚举（唯一真实来源）

```python
from enum import Enum

class OrderStatus(Enum):
    # 提交前
    RESERVED        = 'RESERVED'          # journal 已写入，未调 broker
    ABANDONED       = 'ABANDONED'         # RESERVED 放弃（不删除）

    # 提交中
    SUBMITTING      = 'SUBMITTING'        # broker 已录入，等待处理
    SUBMITTED       = 'SUBMITTED'         # 已提交，等待成交

    # 成交
    FILLED_PART     = 'FILLED_PART'       # 部分成交
    FILLED_ALL      = 'FILLED_ALL'        # 全部成交

    # 撤销
    CANCELLING      = 'CANCELLING'        # 撤销请求中
    CANCELLED_PART  = 'CANCELLED_PART'    # 已部分撤销
    CANCELLED_ALL   = 'CANCELLED_ALL'     # 已全部撤销

    # 异常
    REJECTED        = 'REJECTED'          # 拒单（权威: SUBMIT_FAILED/FAILED）
    TIMEOUT         = 'TIMEOUT'           # 超时
    UNKNOWN         = 'UNKNOWN'           # 状态无法确定
    FILL_CANCELLED  = 'FILL_CANCELLED'    # 成交后撤销（人工介入）
    DISABLED        = 'DISABLED'          # 账户禁用
    DELETED         = 'DELETED'           # 订单被删除

    # ── 集合 ────────────────────────────────────
    @classmethod
    def terminal_set(cls) -> set:
        """终态 — 不再需要处理，不阻断。"""
        return {
            cls.FILLED_ALL, cls.REJECTED,
            cls.CANCELLED_ALL, cls.CANCELLED_PART,
            cls.DISABLED, cls.DELETED,
            cls.ABANDONED,
        }

    @classmethod
    def blocking_set(cls) -> set:
        """阻断 — 存在该状态时阻断该市场全部新订单。"""
        return {
            cls.RESERVED, cls.SUBMITTING, cls.SUBMITTED,
            cls.FILLED_PART, cls.CANCELLING,
            cls.TIMEOUT, cls.UNKNOWN, cls.FILL_CANCELLED,
        }

    @classmethod
    def uncertain_set(cls) -> set:
        """不确定状态 — SUBMITTING 返回时中止本轮剩余 BUY。"""
        return {cls.TIMEOUT, cls.UNKNOWN}

    @classmethod
    def human_intervention_set(cls) -> set:
        """需人工介入的状态。"""
        return {cls.FILL_CANCELLED, cls.UNKNOWN}

    @classmethod
    def sell_completed_set(cls) -> set:
        """SELL 已完成（允许进入 BUY）。"""
        return {cls.FILLED_ALL}

    @classmethod
    def from_futu(cls, futu_status: str) -> 'OrderStatus':
        """将 Futu OrderStatus 字符串映射为 QuantBot OrderStatus。"""
        qb_status = FUTU_TO_QUANTBOT.get(futu_status, 'UNKNOWN')
        try:
            return cls(qb_status)
        except ValueError:
            return cls.UNKNOWN
```

### 3.3 控制流规则（禁止使用 `success` 布尔值）

```python
# ❌ 禁止: 用 success 布尔值控制流程
if result.success:
    proceed_to_next_buy()  # 错误

# ✅ 正确: 用 OrderStatus 枚举判断
status = OrderStatus.from_futu(futu_raw_status)

if status == OrderStatus.SUBMITTED:
    # 正常提交
elif status in OrderStatus.uncertain_set():
    # 不确定 → 中止
elif status in OrderStatus.sell_completed_set():
    # SELL 完成
elif status in OrderStatus.blocking_set():
    # 阻断
```

`PlaceOrderResult` 中 `success` 字段**仅用于日志显示**，不驱动控制流：

```python
@dataclass
class PlaceOrderResult:
    status: OrderStatus    # QuantBot OrderStatus (驱动控制流的唯一来源)
    message: str
    order_id: str = ''
    futu_status: str = ''

    @property
    def success(self) -> bool:
        """仅用于日志，不驱动控制流。"""
        return self.status in (OrderStatus.SUBMITTED, OrderStatus.FILLED_ALL,
                               OrderStatus.FILLED_PART, OrderStatus.CANCELLED_ALL,
                               OrderStatus.CANCELLED_PART, OrderStatus.CANCELLING)
```

---

## 4. 最终测试矩阵（废除 v1–v5 全部旧冲突测试）

### 4.1 废除声明

以下 v5 测试被本契约**明确废除**，不得存在于最终代码中：

| 来源 | 测试 | 废除原因 |
|:---|:---|:---|
| v5:1393 | `RESERVED → SUBMITTED` 合法 | 必须经过 SUBMITTING |
| v5:1423 | `REJECTED` SELL 后允许 BUY | 所有 SELL 必须 FILLED_ALL |
| v5:1433 | 状态总数为 18 | 本机 SDK 17 态 |
| v5: | FILL_CANCELLED 有 success=True | FILL_CANCELLED 是阻断态 |

### 4.2 最终测试清单

```python
# ═══════════════════════════════════════════════════════════════
# Phase F2-SEC 最终测试契约
# ═══════════════════════════════════════════════════════════════

# ─── State Machine ──────────────────────────────────────────
# 合法转换
transition_RESERVED_to_ABANDONED:            "RESERVED → ABANDONED（UPDATE，非 DELETE）"
transition_RESERVED_to_SUBMITTING:           "RESERVED → SUBMITTING（先持久化再调 broker）"
transition_SUBMITTING_to_SUBMITTED:          "SUBMITTING → SUBMITTED（有 order_id）"
transition_SUBMITTING_to_REJECTED:           "SUBMITTING → REJECTED（权威 order_status）"
transition_SUBMITTING_to_TIMEOUT:            "SUBMITTING → TIMEOUT（异常/超时）"
transition_SUBMITTED_to_FILLED_ALL:          "SUBMITTED → FILLED_ALL（对账）"
transition_SUBMITTED_to_FILLED_PART:         "SUBMITTED → FILLED_PART（对账）"

# 非法转换（必须抛出异常/断言失败）
illegal_RESERVED_to_SUBMITTED:               "RESERVED → SUBMITTED 禁止"
illegal_SUBMITTING_to_ABANDONED:             "SUBMITTING → ABANDONED 禁止"
illegal_FILLED_ALL_to_anything:              "终态不可回退"

# ─── terminal_set / blocking_set ───────────────────────────
terminal_set_contains_FILLED_ALL:            "FILLED_ALL ∈ terminal_set"
terminal_set_not_contains_FILL_CANCELLED:    "FILL_CANCELLED ∉ terminal_set"
blocking_set_contains_FILL_CANCELLED:        "FILL_CANCELLED ∈ blocking_set"
blocking_set_contains_TIMEOUT:               "TIMEOUT ∈ blocking_set"
blocking_set_NOT_overlap_terminal:           "terminal_set ∩ blocking_set = ∅"

# ─── Futu 映射 ───────────────────────────────────────────────
mapping_NONE_to_UNKNOWN:                     "NONE → UNKNOWN（不是 SUBMITTING）"
mapping_UNSUBMITTED_to_SUBMITTING:           "UNSUBMITTED → SUBMITTING"
mapping_SUBMIT_FAILED_to_REJECTED:           "SUBMIT_FAILED → REJECTED（唯一拒单）"
mapping_FAILED_to_REJECTED:                  "FAILED → REJECTED（唯一拒单）"
mapping_FILL_CANCELLED_to_FILL_CANCELLED:   "FILL_CANCELLED → FILL_CANCELLED"
mapping_TIMEOUT_to_TIMEOUT:                  "TIMEOUT → TIMEOUT"
mapping_count_17:                            "FUTU_TO_QUANTBOT 有 17 个条目"
mapping_each_one_to_one:                     "Futu 每个状态唯一映射到一个 QuantBot 状态"

# ─── Lease ────────────────────────────────────────────────────
acquire_new_market:                          "无记录 → 返回 token, status=ACTIVE"
acquire_while_ACTIVE:                        "ACTIVE → 返回 ''（不论 owner 是否匹配）"
acquire_while_RELEASED:                      "RELEASED → 重新激活, 返回新 token"
renew_with_valid_token:                      "正确 token → expires_at 延长"
renew_with_invalid_token:                    "错误 token → RuntimeError"
renew_after_expiry:                          "过期后 renew → RuntimeError"
release_with_valid_token:                    "正确 token → status=RELEASED"
release_with_invalid_token:                  "错误 token → RuntimeError"
force_release_expired:                       "已过期+owner+token 匹配 → RELEASED"
force_release_not_expired:                   "未过期 → RuntimeError"
force_release_wrong_owner:                   "owner 不匹配 → RuntimeError"
fencing_valid:                               "有效 lease → 无异常"
fencing_expired:                             "过期 lease → RuntimeError"
fencing_released:                            "释放后 → RuntimeError"

# ─── LIVE fail-closed ─────────────────────────────────────────
OrderExecutor_dry_run_None_journal:          "dry_run=False, journal=None → RuntimeError"
OrderJournal_verify_integrity_fail:          "损坏 DB → RuntimeError"
OrderJournal_verify_integrity_ok:            "正常 DB → 无异常"

# ─── SELL→BUY ─────────────────────────────────────────────────
all_sell_FILLED_ALL_allows_Buy:              "[SELL] → [FILLED_ALL, FILLED_ALL] → BUY 允许"
any_sell_not_FILLED_ALL_blocks_Buy:          "[SELL] → [FILLED_ALL, FILLED_PART] → BUY 阻断"
all_sell_not_FILLED_ALL_blocks_Buy:          "[SELL] → [FILLED_PART, FILLED_PART] → BUY 阻断"
sell_REJECTED_blocks_Buy:                    "[SELL] → [REJECTED] → BUY 阻断（严格规则）"
no_sell_allows_Buy:                          "[] → BUY 允许"

# ─── Recovery ──────────────────────────────────────────────────
recover_reserved_stale:                      ">1h RESERVED + broker 无匹配 → ABANDONED"
recover_reserved_fresh:                      "<1h RESERVED → 不做操作"
recover_reserved_query_fail:                 "查询异常 → 保持 RESERVED（fail-closed）"
recover_reserved_query_None:                 "查询返回 None → 保持 RESERVED（fail-closed）"
recover_reserved_found_in_broker:            "查询匹配 → SUBMITTED"
recover_STOP_SELL_FILLED_ALL:                "FILLED_ALL STOP_SELL → confirm_stop + applied"
recover_SIGNAL_BUY_FILLED_ALL:               "FILLED_ALL SIGNAL_BUY → init_position + applied"
recover_REVERSAL_SELL:                       "FILLED_ALL → 标记 applied（无 confirm_stop）"

# ─── Broker 调用 ──────────────────────────────────────────────
place_order_RET_OK_and_order_id:             "RET_OK + order_id → SUBMITTED（解析 order_status）"
place_order_RET_OK_no_order_id:              "RET_OK + 无 order_id → UNKNOWN"
place_order_RET_OK_order_status_FILLED_ALL:  "order_status=FILLED_ALL → FILLED_ALL"
place_order_RET_OK_order_status_SUBMIT_FAILED: "order_status=SUBMIT_FAILED → REJECTED"
place_order_RET_ERROR:                       "RET_ERROR → TIMEOUT（不推断 REJECTED）"
place_order_exception:                       "异常 → TIMEOUT"

# ─── Concurrency ──────────────────────────────────────────────
concurrent_acquire_same_intent:              "2 连接同 intent → 1 成功 1 失败"
concurrent_acquire_same_market:              "2 连接同 market → 1 acquire 1 拒绝"

# ─── Rollback ──────────────────────────────────────────────────
rollback_stops_OpenD:                        "回滚脚本验证 OpenD 已停止"
rollback_disables_scheduled_tasks:           "回滚脚本禁用 AutoTradeHK/US"
rollback_kills_running_runner:               "回滚脚本停止运行中的 unified_runner"
rollback_fails_if_OpenD_not_stoppable:       "OpenD 无法停止 → throw"
rollback_preserves_journal:                  "回滚后 journal 文件存在"

# ⛔ 禁止的测试
# 任何 TrdEnv.SIMULATE 真实下单
# 任何 --live 或 LIVE_CONFIRMED 路由
# FOK 相关测试（本机 SDK 不支持）
```

---

## 5. 交付确认

✅ **本文件取代 v1–v5.3 全部版本，是 Phase F2-SEC 的唯一实施契约。**  
✅ **未修改任何代码或现有文件。**  
✅ **未执行任何下单入口或可能连接交易执行路径的测试。**  
✅ **未执行 Git commit、push、merge、reset 或 checkout。**  
✅ **未接入 TrdEnv.REAL。**  
✅ **未调整策略、Gate 或 confidence_v2。**  
✅ **未触碰受保护代码或 4 个旧 research untracked 文件。**  
✅ **本机 SDK 17 态 Mapping 已精确验证。**  
✅ **所有 v5 及此前版本中与本文档冲突的内容已废除。**  

**等待 Codex 批准后实施 Phase F2-SEC。**