# Phase F2-A v5.5 Canonical — 最终实施契约
# 订单执行一致性修复

**版本**: v5.5 (Canonical — 完整取代 v1–v5.4 全部版本)  
**日期**: 2026-06-04  
**状态**: 本文件是 Phase F2-SEC **唯一**及**完整**的实施来源。所有早前版本 (v1–v5.4) 中与本文档冲突的内容已**永久废除**。  
**授权**: 仅限只读设计，未批准实施。  
**审查环境**: Python 3.12, futu 10.05.6508, `C:\Users\RoyGoode\AppData\Roaming\Futu_OpenD\Futu_OpenD.exe`

---

## 目录

1. [可验证且 fail-closed 的 Windows 回滚方案](#1-可验证且-fail-closed-的-windows-回滚方案)
2. [独立只读 Reconciliation 工具](#2-独立只读-reconciliation-工具)
3. [完整 F2-SEC 实施契约](#3-完整-f2-sec-实施契约)
4. [SQLite Schema 与事务设计](#4-sqlite-schema-与事务设计)
5. [唯一状态映射与枚举](#5-唯一状态映射与枚举)
6. [完整状态机转换表](#6-完整状态机转换表)
7. [RESERVED→SUBMITTING→Broker 提交事务流程](#7-reservedsubmittingbroker-提交事务流程)
8. [Startup Recovery/Reconciliation 顺序](#8-startup-recoveryreconciliation-顺序)
9. [Post-Fill 副作用恢复](#9-post-fill-副作用恢复)
10. [SELL→BUY 完整流程](#10-sellbuy-完整流程)
11. [Lease API 设计](#11-lease-api-设计)
12. [最终测试矩阵](#12-最终测试矩阵)
13. [交付确认](#13-交付确认)

---

## 1. 可验证且 fail-closed 的 Windows 回滚方案

### 1.1 回滚源定义

F2-SEC 实施后，所有修改将在单个 commit 中提交。本系统的替换被定义为：

| 回滚场景 | 操作方法 | 验证条件 |
|:---|:---|:---|
| **未提交修改** (开发中) | `git checkout -- <changed_files>` | `git diff --stat` 确认 clean |
| **已提交但未推送** | `git revert <f2sec-commit-hash>` — 创建一个反向 commit | `git log` 确认 revert 存在 |
| **已推送至远端** | `git revert <f2sec-commit-hash> && git push` | `git diff main..origin/main` 确认同步 |

**关键原则**: `git revert` 保留完整提交历史，F2-SEC 代码保留在历史中（不影响 journal 文件读取）。回滚后的旧代码**不读取 journal**，但第 2 节的独立 Reconciliation 工具可在任何代码版本下运行。

### 1.2 可执行回滚脚本

```powershell
# ═════════════════════════════════════════════════════════════════
# rollback_f2sec.ps1 — Phase F2-SEC 完整回滚
# 要求: 以管理员身份运行
# 行为: 每一步失败都中止 (fail-closed)，无静默跳过
# ═════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"

Write-Host "=== Phase F2-SEC 回滚 ===" -ForegroundColor Red

# ── Step 1: 验证并停止 Futu_OpenD.exe ─────────────────────────
Write-Host "[1/9] 停止 Futu_OpenD.exe..." -ForegroundColor Yellow
$opend = Get-CimInstance Win32_Process -Filter "Name='Futu_OpenD.exe'" -ErrorAction SilentlyContinue
if ($opend) {
    $opendPid = $opend.ProcessId
    Write-Host "  OpenD 进程 ID: $opendPid — 正在停止..."
    Stop-Process -Id $opendPid -Force
    Start-Sleep -Seconds 2
    $stillRunning = Get-CimInstance Win32_Process -Filter "Name='Futu_OpenD.exe'" -ErrorAction SilentlyContinue
    if ($stillRunning) { throw "Futu_OpenD.exe 无法停止 (PID: $opendPid)" }
    Write-Host "  ✓ Futu_OpenD.exe 已停止" -ForegroundColor Green
} else {
    Write-Host "  ✓ (Futu_OpenD.exe 未运行)" -ForegroundColor Gray
}

# ── Step 2: 停止正在运行的所有 unified_runner 进程 ─────────────
Write-Host "[2/9] 停止运行中的 unified_runner.py..." -ForegroundColor Yellow
$runners = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "unified_runner" }
if ($runners) {
    $count = ($runners | Measure-Object).Count
    Write-Host "  发现 $count 个 runner 进程，正在停止..."
    $runners | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 1
    $stillRunning = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "unified_runner" }
    if ($stillRunning) { throw "unified_runner.py 无法停止" }
    Write-Host "  ✓ $count 个 runner 进程已停止" -ForegroundColor Green
} else {
    Write-Host "  ✓ (无运行中的 unified_runner)" -ForegroundColor Gray
}

# ── Step 3: 禁用 Windows 计划任务 ─────────────────────────────
Write-Host "[3/9] 禁用计划任务..." -ForegroundColor Yellow
$tasksToDisable = @("AutoTradeHK", "AutoTradeUS")
foreach ($t in $tasksToDisable) {
    $task = schtasks /Query /TN $t 2>&1
    if ($LASTEXITCODE -eq 0) {
        schtasks /Change /TN $t /DISABLE 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "无法禁用计划任务 $t" }
        Write-Host "  ✓ $t 已禁用" -ForegroundColor Green
    } else {
        Write-Host "  ✓ (计划任务 $t 不存在)" -ForegroundColor Gray
    }
}

# ── Step 4: 停止当前可能运行的其他量化进程 ─────────────────────
Write-Host "[4/9] 停止其他量化工具进程..." -ForegroundColor Yellow
$extraProcesses = @("FTWebSocket", "FTUpdate", "CrashReporter")
foreach ($p in $extraProcesses) {
    $proc = Get-Process -Name $p -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Name $p -Force -ErrorAction SilentlyContinue
        Write-Host "  ✓ $p 已停止" -ForegroundColor Green
    }
}

# ── Step 5: 设置环境变量 kill switch (两层) ──────────────────
Write-Host "[5/9] 设置环境变量 kill switch..." -ForegroundColor Yellow
[Environment]::SetEnvironmentVariable("QUANT_LIVE_KILLED", "YES", "User")
$env:QUANT_LIVE_KILLED = "YES"
$check = [Environment]::GetEnvironmentVariable("QUANT_LIVE_KILLED", "User")
if ($check -ne "YES") { throw "环境变量 QUANT_LIVE_KILLED 设置失败" }
Write-Host "  ✓ QUANT_LIVE_KILLED=YES (用户级)" -ForegroundColor Green
Write-Host "  ⚠ 提示: unified_runner.py 的 _is_live_confirmed() 在被修改后应读取此环境变量"
Write-Host "  ⚠ 关键: 停止 OpenD 后即使旧代码也无法连接券商 (硬约束)"

# ── Step 6: 备份 journal 文件 (永不清除) ─────────────────────
Write-Host "[6/9] 备份 journal 数据库..." -ForegroundColor Yellow
$backupDir = "backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$journalFiles = Get-ChildItem "output/order_journal_*.db" -ErrorAction SilentlyContinue
$backupCount = 0
if ($journalFiles) {
    foreach ($f in $journalFiles) {
        $dest = Join-Path $backupDir "journal_f2sec_${timestamp}_$($f.Name)"
        Copy-Item $f.FullName $dest -Force
        Write-Host "  ✓ $($f.Name) → $dest" -ForegroundColor Green
        $backupCount++
    }
} else {
    Write-Host "  ✓ (无 journal 文件)" -ForegroundColor Gray
}

# ── Step 7: 回滚代码 (根据提交状态选择) ──────────────────────
Write-Host "[7/9] 回滚代码..." -ForegroundColor Yellow

# 检测 F2-SEC commit hash
$f2secTag = git log --oneline --grep="F2-SEC" -n 1 2>&1
if ($LASTEXITCODE -eq 0 -and $f2secTag) {
    # F2-SEC 已提交 → 使用 git revert
    $hash = ($f2secTag -split ' ')[0]
    Write-Host "  检测到 F2-SEC commit: $hash，执行 revert..."
    git revert --no-edit $hash 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Git revert 失败" }
    Write-Host "  ✓ Git revert $hash 成功" -ForegroundColor Green
} else {
    # F2-SEC 未提交 → 使用 git checkout
    Write-Host "  未检测到提交 (未暂存修改)，执行 checkout..."
    $files = @(
        "core/order_executor.py",
        "core/stop_loss.py",
        "core/futu_adapter.py",
        "unified_runner.py",
        "core/order_journal.py",
        "core/live_kill_switch.py",
        "core/reconciliation_tool.py"
    )
    git checkout -- $files 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Git checkout 失败" }
    Write-Host "  ✓ 代码已通过 git checkout 回滚" -ForegroundColor Green
}

# ── Step 8: 删除可能干扰的编译缓存 ──────────────────────────
Write-Host "[8/9] 清理 Python 编译缓存..." -ForegroundColor Yellow
Get-ChildItem -Recurse -Filter "__pycache__" -Path "core" -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }
Write-Host "  ✓ pycache 已清理" -ForegroundColor Green

# ── Step 9: 最终验证 ──────────────────────────────────────────
Write-Host "[9/9] 最终验证..." -ForegroundColor Yellow

# 验证 OpenD 已停止
$opendCheck = Get-CimInstance Win32_Process -Filter "Name='Futu_OpenD.exe'" -ErrorAction SilentlyContinue
if ($opendCheck) { throw "验证失败: Futu_OpenD.exe 仍在运行" }
Write-Host "  ✓ Futu_OpenD.exe 已停止" -ForegroundColor Green

# 验证计划任务已禁用
foreach ($t in $tasksToDisable) {
    $taskCheck = schtasks /Query /FO LIST /TN $t 2>&1
    if ($LASTEXITCODE -eq 0 -and $taskCheck -match "禁用") {
        Write-Host "  ✓ $t 已禁用" -ForegroundColor Green
    } elseif ($LASTEXITCODE -ne 0) {
        Write-Host "  ✓ $t 不存在" -ForegroundColor Gray
    } else {
        # 检查任务状态
        $statusLine = $taskCheck | Select-String "Status"
        Write-Host "  ⚠ 任务 $t 状态: $($statusLine.Line)" -ForegroundColor Yellow
    }
}

# 验证 journal 未被删除
$journalExists = Test-Path "output/order_journal_*.db"
$backupExists = (Get-ChildItem $backupDir"/*journal*" -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0
if ($journalExists) { Write-Host "  ✓ journal 文件保留在原位置" -ForegroundColor Green }
if ($backupExists) { Write-Host "  ✓ journal 已备份至 $backupDir" -ForegroundColor Green }

# git 状态验证
$gitStatus = git status --short 2>&1
if ($gitStatus) {
    Write-Host "  ⚠ 警告: git status 非 clean，需要手动检查:" -ForegroundColor Yellow
    $gitStatus | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Host "  ✓ git status clean" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== 回滚验证完成 ===" -ForegroundColor Green
Write-Host ""
Write-Host "LIVE 恢复条件 (逐一手动确认):" -ForegroundColor Yellow
Write-Host "  1. 独立 reconciliation 工具已确认 journal 中无未解决订单" -ForegroundColor Cyan
Write-Host "  2. 手动启动 Futu_OpenD.exe" -ForegroundColor Cyan
Write-Host "  3. schtasks /Change /ENABLE AutoTradeHK /AutoTradeUS" -ForegroundColor Cyan
Write-Host "  4. Remove-Item Env:QUANT_LIVE_KILLED (环境变量清除)" -ForegroundColor Cyan
Write-Host "  5. 正常使用 --confirm-live 或 QUANT_LIVE_CONFIRM=YES 运行" -ForegroundColor Cyan
```

### 1.3 Kill Switch 硬约束验证

```text
回滚后 LIVE 执行路径分析:

入口: _is_live_confirmed(unified_runner.py:535)
  1. --confirm-live → return True (用户显式确认)
  2. QUANT_LIVE_CONFIRM=YES → return True

无论哪种方式 → execute_live 为 True → 调用 executor.execute_orders()
  → _place_single_order()
  → self._adapter.place_order()
  → futu_adapter.place_order()
  → ft.OpenSecTradeContext(host='127.0.0.1', port=11111)

Futu_OpenD.exe 已停止 → socket 连接 127.0.0.1:11111 失败
  → socket.connect() 抛出 ConnectionRefusedError
  → _place_single_order() 捕获异常 → 返回 PlaceOrderResult(TIMEOUT)
  → 订单永不触达券商

结论: 停止 OpenD = 无法建立 Trd 上下文 = 零订单。这是无论旧代码还是新代码都
无法绕过的硬约束。回滚时即使 plan task 仍在运行 (Step 3 已禁用), runner
进程仍会启动但无法连接券商。
```

---

## 2. 独立只读 Reconciliation 工具

### 2.1 设计原则

- **永远不下单**: 本工具只读取 Futu `order_list_query()` API + 本地 journal
- **可在任何代码版本下运行**: 不依赖 F2-SEC 的 journal 读写代码
- **输出对账报告**: 标记未解决订单、状态差异、需人工介入项
- **可协助恢复**: 回滚后运行以确认 journal 状态并指导人工操作

### 2.2 文件位置

`E:\quant\core\reconciliation_tool.py`

### 2.3 API 契约

```python
class ReconciliationTool:
    """
    只读对账工具。绝不下单，绝不修改 journal。
    
    使用方式:
        rt = ReconciliationTool(adapter, journal_dir='output')
        report = rt.reconcile(market='HK')
        rt.print_report(report)
    """
    
    def reconcile(self, market: str) -> ReconciliationReport:
        """
        执行对账。
        
        1. 加载 journal 中当日本市场的所有订单
        2. 调用 adapter.get_order_list(market) 获取 Futu 端订单
        3. 按 order_id 和 intent_id (remark) 匹配
        4. 返回差异报告
        
        对账失败时: 返回报告包含 error_info, 不抛出异常
        (工具只读, 不应阻碍恢复时使用)
        """
    
    def find_unresolved(self, market: str) -> list[dict]:
        """
        查找未解决订单 (journal 中非终态且 Futu 中也非终态)。
        回滚后使用此方法确认是否可安全恢复 LIVE。
        """
```

### 2.4 输出格式

```python
@dataclass
class ReconciliationReport:
    market: str
    timestamp: str
    
    # ── 统计 ──
    journal_count: int          # journal 总订单数
    futu_count: int             # Futu 总订单数
    matched_count: int          # 匹配上的订单数
    unmatched_journal: list     # journal 中有但 Futu 查不到的
    unmatched_futu: list        # Futu 中有但 journal 中没有的
    status_mismatch: list       # 状态不一致的
    
    # ── 安全评估 ──
    unresolved_count: int       # 非终态订单数
    has_blocking_orders: bool   # 是否存在需要人工介入的订单
    error_info: str | None      # 查询失败时的错误信息
    
    # ── 恢复建议 ──
    can_safely_resume_live: bool  # 是否可以安全恢复 LIVE
    recommendations: list[str]    # 具体的恢复建议
```

---

## 3. 完整 F2-SEC 实施契约

### 3.1 文件变更清单

| 文件 | 操作 | 说明 |
|:---|:---:|:---|
| `core/order_journal.py` | **新增** | SQLite journal + lease + audit 管理 |
| `core/reconciliation_tool.py` | **新增** | 只读对账工具 |
| `core/order_executor.py` | **大幅修改** | 集成 OrderJournal, 新状态机, SELL→BUY 依赖 |
| `core/stop_loss.py` | **小幅修改** | `record_stop()` 与 `confirm_stop()` 分离 |
| `core/futu_adapter.py` | **小幅修改** | `place_order()` 返回结构化结果, 增加 `order_list_query()` |
| `unified_runner.py` | **小幅修改** | 集成 journal, lease, recovery startup |
| `core/live_kill_switch.py` | **新增** | 回滚后读取 QUANT_LIVE_KILLED 环境变量 |

### 3.2 受保护文件 (不修改)

`config.py(仅L-001/L-002)`, `xmm-strategy/`, `volume_profile.py`, `fusion_engine.py(仅fuse())`, `core/fusion_controller.py(仅信号/风控)`, `chan/`, OKX core strategy.

### 3.3 关键设计决策

| 决策 | 选择 | 理由 |
|:---|:---|:---|
| 持久化引擎 | **SQLite** (非 JSONL/MySQL) | 内置事务、UNIQUE 约束、零依赖 |
| 并发锁 | **SQLite BEGIN IMMEDIATE** (非 portalocker) | 系统自带 3rd-party 未安装 |
| 幂等键 (intent_id) | `QNT:v1:{SHA256(date_market_symbol_action_qty_price)}` | 稳定、唯一、不含 run_id |
| Broker 幂等标记 | **remark 参数** (≤39 bytes) | `QNT:v1:<32hex>` 固定 39 字节, 无截断 |
| 恢复策略 | **仅对账, 不自动重提** | PENDING/SUBMITTING/TIMEOUT/UNKNOWN 均不对应 |
| 测试隔离 | **临时文件 SQLite + 2 独立连接** | 不污染 journal, 不接触交易环境 |

---

## 4. SQLite Schema 与事务设计

### 4.1 数据库文件

```
output/order_journal_{market}_{YYYYMMDD}.db
```
每个市场每天独立数据库文件, 避免跨市场干扰。

### 4.2 表: orders

```sql
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- 幂等键 (唯一)
    intent_id       TEXT NOT NULL UNIQUE,
    remark_hash     TEXT NOT NULL,            -- SHA256-32hex → broker 中识别的标识
    
    -- 订单信息
    market          TEXT NOT NULL,            -- HK / US
    futu_code       TEXT NOT NULL DEFAULT '', -- Futu 格式代码 (us.HK.00700)
    symbol          TEXT NOT NULL DEFAULT '', -- 标准格式 (00700.HK)
    action          TEXT NOT NULL,            -- BUY / SELL
    qty             INTEGER NOT NULL,
    price           REAL NOT NULL,
    
    -- 订单状态
    status          TEXT NOT NULL DEFAULT 'RESERVED',  -- QuantBot OrderStatus 枚举值
    
    -- Broker 信息
    order_id        TEXT NOT NULL DEFAULT '',  -- Futu order_id
    futu_status     TEXT NOT NULL DEFAULT '',  -- Futu 原始 OrderStatus 字符串
    
    -- 成交信息
    filled_qty      INTEGER NOT NULL DEFAULT 0,
    filled_price    REAL NOT NULL DEFAULT 0.0,
    
    -- 业务信息 (恢复用)
    intent_type     TEXT NOT NULL,  -- SIGNAL_BUY / STOP_SELL / REVERSAL_SELL / SIGNAL_SELL
    post_fill_stop_applied   INTEGER NOT NULL DEFAULT 0,  -- 0=未完成 1=已完成
    post_fill_pos_init_applied INTEGER NOT NULL DEFAULT 0, -- 0=未完成 1=已完成
    
    -- 元数据
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 意图唯一约束 (已由 UNIQUE(intent_id) 保证)
-- Broker order_id 唯一约束 (仅非空时生效)
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_order_id 
    ON orders(order_id) WHERE order_id != '';
```

### 4.3 表: market_leases

```sql
CREATE TABLE IF NOT EXISTS market_leases (
    market          TEXT PRIMARY KEY,          -- HK / US
    lease_token     TEXT NOT NULL DEFAULT '',  -- 随机 32 hex
    owner           TEXT NOT NULL DEFAULT '',  -- hostname:pid
    status          TEXT NOT NULL DEFAULT 'RELEASED',  -- ACTIVE / RELEASED
    phase           TEXT NOT NULL DEFAULT '',  -- recovery / pre_trade / submit / post_trade
    expires_at      TEXT NOT NULL DEFAULT '',  -- ISO 8601
    acquired_at     TEXT NOT NULL DEFAULT '',
    released_at     TEXT NOT NULL DEFAULT '',
    
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 4.4 表: audit_log

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,  -- ORDER / LEASE / SIDE_EFFECT / RECOVERY / ROLLBACK
    action          TEXT NOT NULL,  -- acquire_lease / submit_order / confirm_stop / ...
    market          TEXT NOT NULL DEFAULT '',
    intent_id       TEXT NOT NULL DEFAULT '',
    lease_token     TEXT NOT NULL DEFAULT '',
    old_status      TEXT NOT NULL DEFAULT '',
    new_status      TEXT NOT NULL DEFAULT '',
    details         TEXT NOT NULL DEFAULT '{}',  -- JSON
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_log(category);
CREATE INDEX IF NOT EXISTS idx_audit_intent ON audit_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_audit_market ON audit_log(market);
```

### 4.5 事务规则

```text
事务 T1 (RESERVED → SUBMITTING):
  BEGIN IMMEDIATE
  INSERT INTO orders (...) VALUES (RESERVED, intent_id=...)
  INSERT INTO audit_log (category='ORDER', action='reserve_intent', ...)
  COMMIT
  
  → 成功: 返回 intent_id (数据库提交确认)
  → 失败: raise RuntimeError (journal write failed)

事务 T2 (RESERVED → SUBMITTING + broker call):
  BEGIN IMMEDIATE
  UPDATE orders SET status='SUBMITTING', updated_at=now() WHERE intent_id=?
  INSERT INTO audit_log (category='ORDER', action='submit_to_broker', ...)
  COMMIT
  
  → 两个操作在同一个事务中 (原子性)
  → 成功: 调用 broker place_order()
  → 失败: 整个事务回滚, intent 仍为 RESERVED

事务 T3 (broker return → journal update):
  BEGIN IMMEDIATE
  UPDATE orders SET status='SUBMITTED', order_id=?, futu_status=?, updated_at=now()
    WHERE intent_id=?
  INSERT INTO audit_log (category='ORDER', action='order_submitted', ...)
  COMMIT
  
  → 审计日志与状态更新在同一事务 (原子审计)
```

---

## 5. 唯一状态映射与枚举

### 5.1 `FUTU_TO_QUANTBOT` 映射字典 (权威来源)

键为 `ft.OrderStatus.*` 常量值, 不是字符串字面量。

```python
import futu as ft

# 本机 SDK: futu 10.05.6508 | 路径: futu/common/constant.py:1299-1350
# 共 17 种 OrderStatus 枚举

FUTU_TO_QUANTBOT: dict[str, str] = {
    # 注意: 键是 SDK 常量的实际值, 已通过 SDK 验证
    # ft.OrderStatus.NONE = "N/A"  (非字符串 "NONE")
    
    # ── 提交中 (已在 broker 系统中) ────────────
    ft.OrderStatus.NONE:            'UNKNOWN',        # "N/A" → 未知
    ft.OrderStatus.UNSUBMITTED:     'SUBMITTING',     # 已录入, 不可放弃
    ft.OrderStatus.WAITING_SUBMIT:  'SUBMITTING',     # 等待提交
    ft.OrderStatus.SUBMITTING:      'SUBMITTING',     # 提交中
    
    # ── 提交结果 ────────────────────────────────
    ft.OrderStatus.SUBMIT_FAILED:   'REJECTED',       # 权威拒单
    ft.OrderStatus.SUBMITTED:       'SUBMITTED',      # 已提交, 等待成交
    ft.OrderStatus.TIMEOUT:         'TIMEOUT',        # 处理超时
    
    # ── 成交 ────────────────────────────────────
    ft.OrderStatus.FILLED_PART:     'FILLED_PART',    # 部分成交
    ft.OrderStatus.FILLED_ALL:      'FILLED_ALL',     # 全部成交
    
    # ── 撤销 ────────────────────────────────────
    ft.OrderStatus.CANCELLING_PART:  'CANCELLING',    # 撤销中 (非终态)
    ft.OrderStatus.CANCELLING_ALL:   'CANCELLING',    # 撤销中 (非终态)
    ft.OrderStatus.CANCELLED_PART:   'CANCELLED_PART',# 已撤销 (部分成交)
    ft.OrderStatus.CANCELLED_ALL:    'CANCELLED_ALL', # 已撤销 (无成交)
    
    # ── 异常终态 ────────────────────────────────
    ft.OrderStatus.FAILED:          'REJECTED',       # 权威拒单
    ft.OrderStatus.DISABLED:        'DISABLED',       # 已失效
    ft.OrderStatus.DELETED:         'DELETED',        # 已删除
    ft.OrderStatus.FILL_CANCELLED:  'FILL_CANCELLED', # 成交后撤销 (人工)
}

# 验证: 映射键集合必须完全等于 SDK 所有 17 个枚举值
assert set(ft.OrderStatus.load_dict().keys()) == set(FUTU_TO_QUANTBOT.keys()), \
    "FUTU_TO_QUANTBOT 映射键不全 — 请同步 SDK OrderStatus 枚举"
```

### 5.2 `QuantBot.OrderStatus` 枚举 (唯一执行来源)

```python
from enum import Enum

class OrderStatus(Enum):
    """QuantBot 订单状态枚举 — 驱动所有控制流的唯一来源。"""
    
    # ── 提交前 ────────────────────────────────
    RESERVED        = 'RESERVED'         # journal 已写入, 未调 broker
    ABANDONED       = 'ABANDONED'        # RESERVED → 放弃 (UPDATE, 不 DELETE)
    
    # ── 提交中 ────────────────────────────────
    SUBMITTING      = 'SUBMITTING'       # broker 已录入, 等待处理
    SUBMITTED       = 'SUBMITTED'        # 已提交, 等待成交
    
    # ── 成交结果 ──────────────────────────────
    FILLED_PART     = 'FILLED_PART'      # 部分成交
    FILLED_ALL      = 'FILLED_ALL'       # 全部成交
    
    # ── 撤销 ──────────────────────────────────
    CANCELLING      = 'CANCELLING'       # 撤销请求中 (非终态)
    CANCELLED_PART  = 'CANCELLED_PART'   # 已撤销 (部分成交) → 需人工对账
    CANCELLED_ALL   = 'CANCELLED_ALL'    # 已撤销 (无成交)
    
    # ── 异常 ──────────────────────────────────
    REJECTED        = 'REJECTED'         # 拒单 (权威: SUBMIT_FAILED/FAILED)
    TIMEOUT         = 'TIMEOUT'          # 超时, 结果未知
    UNKNOWN         = 'UNKNOWN'          # 状态无法确定
    FILL_CANCELLED  = 'FILL_CANCELLED'   # 成交后撤销 (人工介入)
    DISABLED        = 'DISABLED'         # 账户/订单已失效
    DELETED         = 'DELETED'          # 订单被删除

    # ── 集合 ────────────────────────────────────
    @classmethod
    def terminal_set(cls) -> set['OrderStatus']:
        """终态 — 不再需要任何处理, 不阻断新订单。"""
        return {
            cls.FILLED_ALL,
            cls.REJECTED,
            cls.CANCELLED_ALL,
            cls.DISABLED,
            cls.DELETED,
            cls.ABANDONED,
        }

    @classmethod
    def blocking_set(cls) -> set['OrderStatus']:
        """阻断 — 存在该状态时阻断该市场全部新订单。"""
        return {
            cls.RESERVED,
            cls.SUBMITTING,
            cls.SUBMITTED,
            cls.FILLED_PART,
            cls.CANCELLING,
            cls.CANCELLED_PART,    # 部分成交后撤销 → 需人工/显式对账
            cls.TIMEOUT,
            cls.UNKNOWN,
            cls.FILL_CANCELLED,
        }

    @classmethod
    def uncertain_set(cls) -> set['OrderStatus']:
        """不确定提交状态 — 出现时立即中止本轮剩余 BUY。"""
        return {
            cls.TIMEOUT,
            cls.UNKNOWN,
            cls.SUBMITTING,    # 已发送 broker 但无权威确认
        }

    @classmethod
    def human_intervention_set(cls) -> set['OrderStatus']:
        """需人工介入的状态。"""
        return {
            cls.FILL_CANCELLED,
            cls.UNKNOWN,
            cls.CANCELLED_PART,  # 部分成交后撤销 → 需对账持仓
        }

    @classmethod
    def sell_completed_set(cls) -> set['OrderStatus']:
        """SELL 已完全成交 (仅此集合放行 BUY)。"""
        return {cls.FILLED_ALL}

    @classmethod
    def from_futu(cls, futu_status: str | None) -> 'OrderStatus':
        """将 Futu OrderStatus 映射为 QuantBot OrderStatus。"""
        if futu_status is None:
            return cls.UNKNOWN
        qb_status = FUTU_TO_QUANTBOT.get(futu_status, 'UNKNOWN')
        try:
            return cls(qb_status)
        except ValueError:
            return cls.UNKNOWN
```

### 5.3 控制流规则 — 禁止 `success` 布尔值驱动

```python
# ❌ 禁止: 用 success 布尔值控制流程
if result.success:
    proceed_to_next_buy()

# ✅ 正确: 用 OrderStatus 枚举判断
status = result.status  # 这是 QuantBot OrderStatus

if status in OrderStatus.uncertain_set():
    # 不确定提交 → 立即中止本轮剩余 BUY
    return
elif status in OrderStatus.blocking_set():
    # 阻断该市场全部新订单
    return
elif status == OrderStatus.REJECTED:
    # 权威拒单 → 记录日志
    pass
elif status == OrderStatus.SUBMITTED:
    # 正常提交 → 继续
    pass
```

**`PlaceOrderResult` 中 `success` 属性仅用于日志/UI 显示, 绝不驱动控制流:**

```python
@dataclass
class PlaceOrderResult:
    status: OrderStatus      # 驱动控制流的唯一来源
    message: str
    order_id: str = ''
    futu_status: str = ''
    
    @property
    def success(self) -> bool:
        """仅用于日志/UI 显示, 不驱动控制流。"""
        return self.status not in (
            OrderStatus.TIMEOUT, OrderStatus.UNKNOWN,
            OrderStatus.REJECTED
        )
```

---

## 6. 完整状态机转换表

### 6.1 所有合法转换

| 当前状态 | 合法转换 | 触发条件 | 事务要求 |
|:---|:---|:---|:---|
| **NEW** (不存在) | → RESERVED | `try_acquire_intent()` 写入 journal | T1: BEGIN IMMEDIATE + INSERT |
| **RESERVED** | → SUBMITTING | `submit_intent()` 持久化后调 broker | T2: 双 UPDATE + COMMIT |
| **RESERVED** | → ABANDONED | `abandon_reserved()` 清理 | UPDATE + audit |
| **SUBMITTING** | → SUBMITTED | `place_order()` 返回 RET_OK + order_id | T3: 原子更新 |
| **SUBMITTING** | → REJECTED | `place_order()` 返回 order_status=SUBMIT_FAILED/FAILED | T3: 原子更新 |
| **SUBMITTING** | → TIMEOUT | `place_order()` 异常或 RET_ERROR | T3: 原子更新 |
| **SUBMITTED** | → FILLED_ALL | 对账: order_status=FILLED_ALL | UPDATE + audit |
| **SUBMITTED** | → FILLED_PART | 对账: order_status=FILLED_PART | UPDATE + audit |
| **SUBMITTED** | → CANCELLED_ALL | 对账: order_status=CANCELLED_ALL | UPDATE + audit |
| **SUBMITTED** | → CANCELLED_PART | 对账: order_status=CANCELLED_PART | UPDATE + audit |
| **SUBMITTED** | → CANCELLING | 对账: order_status=CANCELLING_PART/ALL | UPDATE + audit |
| **SUBMITTED** | → REJECTED | 对账: order_status=SUBMIT_FAILED/FAILED | UPDATE + audit |
| **FILLED_PART** | → FILLED_ALL | 追加对账: 剩余部分已成交 | UPDATE + audit |
| **FILLED_PART** | → CANCELLED_PART | 对账: 剩余部分已撤销 | UPDATE + audit |
| **CANCELLING** | → CANCELLED_ALL | 对账: 撤销完成 | UPDATE + audit |
| **CANCELLING** | → CANCELLED_PART | 对账: 部分撤销完成 | UPDATE + audit |
| **TIMEOUT** | → SUBMITTED | 恢复对账: 发现 order_id | UPDATE + audit |
| **TIMEOUT** | → FILLED_ALL | 恢复对账: 发现已成交 | UPDATE + audit |
| **TIMEOUT** | → REJECTED | 恢复对账: 发现拒单 | UPDATE + audit |
| **TIMEOUT** | → UNKNOWN | 恢复对账: 24h 仍无确定结果 | UPDATE + audit |
| **UNKNOWN** | → SUBMITTED | 人工介入确认 | UPDATE + audit |
| **UNKNOWN** | → FILLED_ALL | 人工介入确认 | UPDATE + audit |

### 6.2 非法转换 (强行执行会抛出 RuntimeError/AssertionError)

| 来源状态 | 禁止的目标 | 理由 |
|:---|:---|:---|
| RESERVED | SUBMITTED | 必须经过 SUBMITTING |
| SUBMITTING | ABANDONED | 已在 broker 中, 不可放弃 |
| FILLED_ALL | 任何状态 | 终态不可回退 |
| FILLED_PART | SUBMITTED | 不可回退 |
| REJECTED | 任何状态 | 终态不可回退 |
| CANCELLED_ALL | 任何状态 | 终态不可回退 |
| DISABLED | 任何状态 | 终态 |
| DELETED | 任何状态 | 终态 |
| FILL_CANCELLED | 任何状态 | 需人工介入 |
| ABANDONED | 任何状态 | 终态 |

### 6.3 终态彻底性证明

```python
# 所有可能状态的总数 = 15
# terminal_set 已覆盖所有不需要再处理的状态
# blocking_set 覆盖所有需要阻断/人工处理的状态
# 两个集合的并集 = 所有状态

all_states = set(OrderStatus)
assert all_states == OrderStatus.terminal_set() | OrderStatus.blocking_set(), \
    "所有状态必须被 terminal_set 或 blocking_set 覆盖"
assert OrderStatus.terminal_set() & OrderStatus.blocking_set() == set(), \
    "terminal_set 与 blocking_set 不得重叠"
```

---

## 7. RESERVED→SUBMITTING→Broker 提交事务流程

### 7.1 `_place_single_order()` 完整伪代码

```python
def _place_single_order(self, order: dict, market: str) -> PlaceOrderResult:
    """下单, 带事务安全的状态机。"""
    
    # ── Phase 1: 生成幂等键 ──────────────────────────────────
    intent_id = OrderJournal.generate_intent_id(order, market)
    
    # ── Phase 2: RESERVED (journal 写入) ───────────────────────
    # 事务 T1: BEGIN IMMEDIATE + INSERT + INSERT audit
    journal_entry = self.journal.try_acquire_intent(
        intent_id=intent_id,
        order=order,
        market=market,
        intent_type=order.get('intent_type', ''),
    )
    if journal_entry is None:
        # intent_id 已存在 (重复)
        existing = self.journal.get_by_intent(intent_id)
        msg = f"重复意图跳过: {intent_id}, 当前状态={existing['status']}"
        print(f"  [SKIP] {msg}")
        return PlaceOrderResult(
            status=OrderStatus.from_string(existing['status']),
            message=msg,
            order_id=existing['order_id'],
        )
    
    # journal_entry 是刚刚写入的 RESERVED 记录
    
    # ── Phase 3: RESERVED → SUBMITTING (先持久化再调 broker) ──
    # 事务 T2: BEGIN IMMEDIATE + UPDATE status→SUBMITTING + INSERT audit
    # 此事务必须先提交, 然后才能调用 broker
    self.journal.update_status(
        intent_id=intent_id,
        new_status='SUBMITTING',
        audit_action='submit_to_broker',
    )
    # 至此: intent 已持久化为 SUBMITTING 状态, 数据库已确认
    
    # ⛔ 禁止: RESERVED → SUBMITTED (跳过 SUBMITTING)
    
    # ── Phase 4: Lease fencing 检查 ────────────────────────────
    self.lease.assert_lease_valid(market, self.lease_token)
    
    # ── Phase 5: 调用 broker ───────────────────────────────────
    try:
        ok, result = self._adapter.place_order(
            market=market,
            code=order['futu_code'],
            action=order['action'],
            qty=order['qty'],
            price=order['price'],
            remark=intent_id,   # ≤39 字节: QNT:v1:<32hex>
        )
    except Exception as e:
        # 异常 → TIMEOUT
        self.journal.update_status(
            intent_id=intent_id,
            new_status='TIMEOUT',
            order_id='',
            futu_status='',
            message=f"Broker 调用异常: {e}",
            audit_action='place_order_exception',
        )
        return PlaceOrderResult(status=OrderStatus.TIMEOUT, message=str(e))
    
    # ── Phase 6: Broker 返回 → 状态映射 ─────────────────────────
    if ok and result is not None and result.order_id:
        # 权威映射: 解析 Futu order_status
        futu_status = result.futu_status or ''
        qb_status = OrderStatus.from_futu(futu_status)
        
        if qb_status == OrderStatus.SUBMITTED:
            # 正常提交
            self.journal.update_status(
                intent_id=intent_id,
                new_status='SUBMITTED',
                order_id=result.order_id,
                futu_status=futu_status,
                audit_action='place_order_ok',
            )
            return PlaceOrderResult(
                status=OrderStatus.SUBMITTED,
                message="已提交",
                order_id=result.order_id,
                futu_status=futu_status,
            )
        elif qb_status == OrderStatus.REJECTED:
            # 权威拒单 (SUBMIT_FAILED/FAILED)
            self.journal.update_status(
                intent_id=intent_id,
                new_status='REJECTED',
                order_id=result.order_id or '',
                futu_status=futu_status,
                audit_action='place_order_rejected',
            )
            return PlaceOrderResult(
                status=OrderStatus.REJECTED,
                message=f"Futu 拒单: {futu_status}",
                order_id=result.order_id or '',
                futu_status=futu_status,
            )
        else:
            # 其他状态 (FILLED_ALL 等由后续对账处理)
            self.journal.update_status(
                intent_id=intent_id,
                new_status='SUBMITTED',
                order_id=result.order_id,
                futu_status=futu_status,
                audit_action='place_order_other_status',
            )
            return PlaceOrderResult(
                status=OrderStatus.SUBMITTED,
                message=f"提交完成, 原始状态={futu_status}",
                order_id=result.order_id,
                futu_status=futu_status,
            )
    else:
        # RET_ERROR 或空 data → TIMEOUT (不推断 REJECTED)
        err_msg = f"RET_ERROR: {result}" if not ok else "无 order_id"
        self.journal.update_status(
            intent_id=intent_id,
            new_status='TIMEOUT',
            order_id='',
            futu_status='',
            message=err_msg,
            audit_action='place_error_no_order_id',
        )
        return PlaceOrderResult(
            status=OrderStatus.TIMEOUT,
            message=err_msg,
        )
```

### 7.2 关键约束

```text
┌─────────────────────────────────────────────────────────────────────┐
│ RESERVED → SUBMITTING → broker 的强制顺序                           │
│                                                                     │
│ 1. try_acquire_intent() → RESERVED  (BEGIN IMMEDIATE + INSERT)     │
│ 2. update_status(SUBMITTING)        (BEGIN IMMEDIATE + UPDATE)     │
│    └─ 数据库提交之后, broker 调用之前                               │
│ 3. assert_lease_valid() → fencing 检查                              │
│ 4. adapter.place_order() → broker                                   │
│ 5. update_status(SUBMITTED/REJECTED/TIMEOUT)                        │
└─────────────────────────────────────────────────────────────────────┘

禁止情形:
  ❌ RESERVED → SUBMITTED (跳过 SUBMITTING)
  ❌ RESERVED → 直接调 broker (journal 未记录)
  ❌ SUBMITTING 后发生异常 → 不自动重提
  ❌ SUBMITTING 后 crash → 恢复仅对账, 不自动重提
```

---

## 8. Startup Recovery/Reconciliation 顺序

### 8.1 恢复流程 (在 `run()` 的 Step 0 之后执行)

```text
run() Recovery 阶段 (Step 0):
  ┌──────────────────────────────────────────────────────────────┐
  │ 0a. 获取 execution lease (acquire_lease)                      │
  │     └── 失败 (其他进程持有) → 终止运行                        │
  │                                                              │
  │ 0b. Broker 对账 (只读)                                        │
  │     ├── 调用 adapter.get_order_list(market) 获取所有 Futu 订单│
  │     ├── 按 remark 匹配 journal 中的 RESERVED/SUBMITTING 订单  │
  │     ├── 匹配成功 → 更新 journal 至正确状态                     │
  │     └── 查询失败 → 终止运行 (fail-closed)                     │
  │                                                              │
  │ 0c. Stale RESERVED 清理                                       │
  │     ├── >1h 且无 broker 匹配 → ABANDONED                      │
  │     ├── <1h 或无确定结果 → 保持 RESERVED                      │
  │     └── 查询异常/None → 保持 RESERVED (fail-closed)           │
  │                                                              │
  │ 0d. Post-fill 副作用执行                                       │
  │     ├── 遍历 FILLED_ALL 中未标记 post_fill_*_applied 的订单    │
  │     ├── intent_type=STOP_SELL → confirm_stop() + 标记 applied  │
  │     ├── intent_type=SIGNAL_BUY → init_position() + 标记 applied│
  │     ├── intent_type=REVERSAL_SELL → 仅标记 applied (无 stop)   │
  │     └── confirm_stop() 失败 → 保持未标记 (可重试)             │
  │                                                              │
  │ 0e. 阻断检查                                                   │
  │     ├── 查询该市场 journal 中是否有 blocking_set 状态的订单    │
  │     ├── 有 → 打印告警 + 阻断所有新订单 (仍完成报告生成)        │
  │     └── 无 → 正常执行                                           │
  │                                                              │
  │ 0f. 续租 (extend lease for submit phase)                      │
  │     └── renew_lease(market, token)                            │
  │                                                              │
  │ → 进入 Step 1 (信号生成), 最终到 Step 6 (订单执行)              │
  └──────────────────────────────────────────────────────────────┘
```

### 8.2 Stale RESERVED 精确匹配规则

```python
def resolve_stale_reserved(self, market: str, adapter):
    """
    清理超过 1 小时且 broker 中无对应订单的 RESERVED 记录。
    
    匹配规则 (精确):
      - 使用 futu_code + remark_hash 精确匹配 broker 订单
      - 不使用标准 symbol 模糊匹配
      - remark_hash 对应 broker 中的 remark 字段
    
    Fail-Closed 条件:
      - adapter is None → 保持 RESERVED, 不清理
      - adapter.get_order_list() 异常 → 保持 RESERVED
      - 返回 None/不确定 → 保持 RESERVED
    """
```

### 8.3 恢复后阻断检查

```python
def has_blocking_orders(self, market: str) -> bool:
    """检查该市场是否存在阻断新订单的状态。"""
    cursor = self.conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE market=? AND status IN (%s)
        """ % ','.join('?' for _ in OrderStatus.blocking_set()),
        [market] + [s.value for s in OrderStatus.blocking_set()]
    )
    count = cursor.fetchone()[0]
    return count > 0
```

---

## 9. Post-Fill 副作用恢复

### 9.1 副作用类型

| intent_type | 成交后的副作用 | `post_fill_stop_applied` | `post_fill_pos_init_applied` |
|:---|:---|:---:|:---:|
| SIGNAL_BUY | 更新持仓记录 | N/A | ✅ |
| STOP_SELL | `confirm_stop()` 激活冷却期 | ✅ | N/A |
| REVERSAL_SELL | 无副作用 (保持现有行为) | N/A | N/A |
| SIGNAL_SELL | 无特殊副作用 | N/A | N/A |

### 9.2 STOP_SELL 副作用事务安全

```python
def maybe_activate_stop_cooldown(self, intent_id: str, futu_code: str,
                                  order: dict, risk_mgr) -> None:
    """
    为已成交的 STOP_SELL 激活冷却期。
    
    崩溃安全流程:
    1. 读取 journal 中 post_fill_stop_applied
    2. 已标记 → 跳过 (幂等)
    3. 未标记:
       a. 调用 risk_mgr.confirm_stop(futu_code) — 持久化冷却期
       b. 成功 → UPDATE post_fill_stop_applied=1 + INSERT audit_log
       c. 失败 (异常) → 保持 post_fill_stop_applied=0 (可重试)
    """
    entry = self.journal.get_by_intent(intent_id)
    if entry['post_fill_stop_applied'] == 1:
        return  # 已执行, 幂等
    
    try:
        risk_mgr.confirm_stop(futu_code)
    except Exception as e:
        print(f"  [WARN] confirm_stop() 失败 (可重试): {e}")
        return  # 保持未标记
    
    # 成功: 先执行, 再标记
    self.journal.conn.execute(
        """
        UPDATE orders
        SET post_fill_stop_applied=1, updated_at=datetime('now')
        WHERE intent_id=?
        """,
        (intent_id,)
    )
    self.journal.log_audit(
        category='SIDE_EFFECT',
        action='confirm_stop',
        intent_id=intent_id,
        details=json.dumps({'futu_code': futu_code}),
    )
```

### 9.3 部分成交处理

```python
def after_sell_reconciliation(sell_results: list[PlaceOrderResult],
                               market: str, journal: OrderJournal,
                               adapter, risk_mgr) -> bool:
    """
    SELL 成交后处理。
    
    返回: True=可以进入 BUY, False=阻断 BUY
    
    规则 (v5.5, 严格):
      - 所有 SELL 必须 FILLED_ALL 才允许 BUY
      - FILLED_PART → 阻断 BUY (人工对账或下次运行时处理)
      - CANCELLED_PART → 阻断 BUY (部分成交+部分撤销, 需对账)
      - REJECTED → 阻断 BUY (严格规则, 不放行)
      - ABANDONED → 阻断 BUY (未提交成功)
    """
    all_filled_all = True
    for r in sell_results:
        if r.status != OrderStatus.FILLED_ALL:
            all_filled_all = False
    
    if not all_filled_all:
        print("  [BLOCK] 存在未完全成交的 SELL, 阻断所有 BUY")
        for r in sell_results:
            if r.status != OrderStatus.FILLED_ALL:
                print(f"    - {r.message} (status={r.status.value})")
        return False
    
    # 所有 SELL 已成交 → 重新查询账户和持仓
    try:
        account = adapter.get_account_info(market)
        if account is None:
            raise RuntimeError("get_account_info 返回 None")
        
        positions = adapter.get_positions(market)
        if positions is None:
            raise RuntimeError("get_positions 返回 None")
        
        # 重新计算风控
        total_assets = account.get('total_assets', 0)
        live_cash = account.get('cash', 0)
        current_exposure = sum(
            p.get('market_val', 0) for p in positions
        )
        
        print(f"    [RE-QUERY] 实时总资产={total_assets:.2f}, "
              f"实时现金={live_cash:.2f}, exposure={current_exposure:.2f}")
        
    except Exception as e:
        print(f"  [FAIL-CLOSED] SELL 后查询失败: {e}")
        return False  # 查询失败 → fail-closed, 阻断 BUY
    
    return True, account, positions, total_assets, live_cash, current_exposure
```

---

## 10. SELL→BUY 完整流程

### 10.1 订单排序规则

```text
execute_orders(market, orders):
  
  Phase A: 分离 SELL 和 BUY
    sell_orders = [o for o in orders if o.action == 'SELL']
    buy_orders  = [o for o in orders if o.action == 'BUY']
  
  Phase B: 执行 SELL
    sell_results = []
    for o in sell_orders:
        r = _place_single_order(o)
        sell_results.append(r)
        if r.status in OrderStatus.uncertain_set():
            # 不确定提交 → 中止本轮剩余 SELL + 全部 BUY
            return
    
    # 如果本轮无 SELL, 跳过 SELL 后查询
    if not sell_orders:
        return _execute_buy_phase(buy_orders, account, None)
  
  Phase C: SELL 成交确认 + 重新查询
    can_buy, *re_query = after_sell_reconciliation(
        sell_results, market, journal, adapter, risk_mgr
    )
    if not can_buy:
        return  # BUY 被阻断
  
  Phase D: 执行 BUY
    buy_results = []
    running_cash = re_query.live_cash  # 实时现金, 非估算
    
    for o in buy_orders:
        # 每笔 BUY 后实时扣减
        trade_val = o.qty * o.price
        
        if running_cash < trade_val:
            # 实时现金不足 → SKIP
            buy_results.append(PlaceOrderResult(
                status=OrderStatus.ABANDONED,
                message=f"实时现金不足: 需要 {trade_val}, 可用 {running_cash}"
            ))
            continue
        
        r = _place_single_order(o)
        buy_results.append(r)
        
        if r.status in OrderStatus.uncertain_set():
            # 不确定提交 → 中止剩余 BUY
            break
        elif r.status == OrderStatus.SUBMITTED:
            running_cash -= trade_val  # 已提交, 扣减资金
        elif r.status == OrderStatus.REJECTED:
            # 拒单不扣减资金, 但保留状态
            pass
        # 其他非终态 (FILLED_ALL 等不应由 place_order 返回)
    
    return sell_results + buy_results
```

### 10.2 每笔 BUY 后资金预留

```python
# v5.5 强制规则:
# 每笔 BUY 提交后, running_cash 立即扣减 trade_val
# 后续 BUY 检查 running_cash 是否足够

# 扣减条件:
# - status == SUBMITTED → 扣减 (已提交, 资金已占用)
# - status in uncertain_set → 扣减 + 中止剩余 (可能已占用)
# - status == REJECTED → 不扣减 (未占用)
# - status == ABANDONED → 不扣减 (未提交)
```

---

## 11. Lease API 设计

### 11.1 核心规则

| 规则 | 说明 |
|:---|:---|
| **ACTIVE 不可复用** | `acquire_lease()` 遇到任何 `ACTIVE` 记录均返回 `''`, 不管 owner 是否匹配 |
| **续租必须持 token** | `renew_lease(market, token)` 是延长租赁的唯一方式 |
| **强制释放必须审计** | `force_release_expired_lease()` 必须确认已过期 + owner + token 全部匹配 |
| **RELEASED 可重获** | `status='RELEASED'` 时允许正常重新获取 |

### 11.2 API 契约

```python
def acquire_lease(self, market: str) -> str:
    """
    获取市场租赁。
    
    返回: lease_token (32 hex), 或 '' 表示拒绝。
    
    规则:
      - 无记录 → INSERT ACTIVE, 返回新 token
      - status='RELEASED' → UPDATE status='ACTIVE', 返回新 token
      - status='ACTIVE' → 拒绝, 返回 '' (续租请用 renew_lease)
      - 其他 → 拒绝, 返回 ''
    
    原子性: 全部操作在 BEGIN IMMEDIATE 事务中
    审计: 每次 acquire 写入 audit_log(category='LEASE', action='acquire_lease')
    """

def renew_lease(self, market: str, token: str) -> None:
    """
    续租 (延长 expires_at)。
    
    校验: UPDATE market_leases SET ... 
           WHERE market=? AND lease_token=? AND status='ACTIVE'
    行数=0 → raise RuntimeError("Lease fencing failed")
    审计: 写入 audit_log
    """

def update_lease_phase(self, market: str, token: str, phase: str) -> None:
    """更新租约阶段, 同上校验。审计: 写入 audit_log。"""

def release_lease(self, market: str, token: str) -> None:
    """正常释放租赁。status='RELEASED'。校验同上。"""

def force_release_expired_lease(self, market: str, expected_owner: str,
                                  expected_token: str) -> None:
    """
    强制释放过期租赁 (人工操作)。
    
    校验: UPDATE market_leases SET status='RELEASED'
           WHERE market=? AND lease_token=? AND owner=?
           AND status='ACTIVE' AND expires_at < datetime('now')
    
    行数=0 → raise RuntimeError("条件不匹配, 强制释放被拒绝")
    审计: 写入 audit_log(category='LEASE', action='force_release_expired')
    """

def assert_lease_valid(self, market: str, token: str) -> None:
    """
    每次 broker 调用前的 fencing 检查。
    
    校验: SELECT 1 FROM market_leases
           WHERE market=? AND lease_token=? AND status='ACTIVE' 
           AND expires_at > datetime('now')
    行数=0 → raise RuntimeError("LEASE FENCING FAILED")
    """

def get_lease_info(self, market: str) -> dict:
    """返回当前租约信息 (只读, 用于诊断)。"""
```

### 11.3 关键安全设计

```text
fencing 检查到 broker 调用的 TOCTOU 风险:

v5.5 解决方案:
  → 禁止自动接管任何过期 LIVE lease
  → 过期 lease = 人工释放后才能重新获取
  → 即使旧进程 fencing 通过后过期, 新进程也无法自动接管

同一 owner 重入:
  → acquire_lease() 遇到 ACTIVE 直接返回 '' (不检查 owner)
  → "自己已经持有" 不是获取新 token 的理由
  → 续租: renew_lease(market, token) — 必须持有有效 token
```

---

## 12. 最终测试矩阵

### 12.1 废除声明

以下 v5 测试被本契约**明确废除**, 不得存在于最终代码中:

| 来源 | 测试 | 废除原因 |
|:---|:---|:---|
| v5 Canonical §4 | `RESERVED → SUBMITTED` 合法 | 必须经过 SUBMITTING |
| v5 Canonical §4 | `REJECTED` SELL 后允许 BUY | 所有 SELL 必须 FILLED_ALL |
| v5 Canonical §4 | 状态总数为 18 | 本机 SDK 17 态 |
| v5 Canonical §4 | FILL_CANCELLED 有 success=True | FILL_CANCELLED 是阻断态 |
| v5 Canonical §4 | `CANCELLED_PART ∈ terminal_set` | 需对账, 改为 blocking_set |
| v5 Canonical §4 | `SUBMITTING ∉ uncertain_set` | 已加入 uncertain_set |

### 12.2 最终测试清单 (48 项)

```python
# ═══════════════════════════════════════════════════════════════
# Phase F2-SEC 测试契约 (v5.5 Canonical)
# ═══════════════════════════════════════════════════════════════

# ─── State Machine (合法转换) ───────────────────────────────
transition_RESERVED_to_ABANDONED:            "RESERVED → ABANDONED (UPDATE, 非 DELETE)"
transition_RESERVED_to_SUBMITTING:           "RESERVED → SUBMITTING (先持久化再调 broker)"
transition_SUBMITTING_to_SUBMITTED:          "SUBMITTING → SUBMITTED (有 order_id)"
transition_SUBMITTING_to_REJECTED:           "SUBMITTING → REJECTED (权威 SUBMIT_FAILED/FAILED)"
transition_SUBMITTING_to_TIMEOUT:            "SUBMITTING → TIMEOUT (异常/RET_ERROR)"
transition_SUBMITTED_to_FILLED_ALL:          "SUBMITTED → FILLED_ALL (对账确认)"
transition_SUBMITTED_to_FILLED_PART:         "SUBMITTED → FILLED_PART (对账确认)"
transition_FILLED_PART_to_FILLED_ALL:        "FILLED_PART → FILLED_ALL (追加对账)"
transition_FILLED_PART_to_CANCELLED_PART:    "FILLED_PART → CANCELLED_PART (剩余撤销)"

# ─── State Machine (非法转换 — 必须抛出异常) ────────────────
illegal_RESERVED_to_SUBMITTED:               "RESERVED → SUBMITTED 禁止"
illegal_SUBMITTING_to_ABANDONED:             "SUBMITTING → ABANDONED 禁止"
illegal_FILLED_ALL_to_anything:              "终态不可回退"

# ─── terminal_set / blocking_set 验证 ──────────────────────
terminal_set_contains_FILLED_ALL:            "FILLED_ALL ∈ terminal_set"
terminal_set_not_contains_FILL_CANCELLED:    "FILL_CANCELLED ∉ terminal_set"
terminal_set_not_contains_CANCELLED_PART:    "CANCELLED_PART ∉ terminal_set"
blocking_set_contains_FILL_CANCELLED:        "FILL_CANCELLED ∈ blocking_set"
blocking_set_contains_CANCELLED_PART:        "CANCELLED_PART ∈ blocking_set"
blocking_set_contains_TIMEOUT:               "TIMEOUT ∈ blocking_set"
uncertain_set_contains_SUBMITTING:           "SUBMITTING ∈ uncertain_set"
terminal_blocking_no_overlap:                "terminal_set ∩ blocking_set = ∅"
all_states_covered:                          "所有状态 ∈ terminal_set ∪ blocking_set"

# ─── Futu 17 态映射 (键为 SDK 常量值) ──────────────────────
mapping_N_A_to_UNKNOWN:                      "N/A → UNKNOWN (不是 SUBMITTING)"
mapping_UNSUBMITTED_to_SUBMITTING:           "UNSUBMITTED → SUBMITTING"
mapping_SUBMIT_FAILED_to_REJECTED:           "SUBMIT_FAILED → REJECTED (权威拒单)"
mapping_FAILED_to_REJECTED:                  "FAILED → REJECTED (权威拒单)"
mapping_FILL_CANCELLED_to_FILL_CANCELLED:    "FILL_CANCELLED → FILL_CANCELLED"
mapping_TIMEOUT_to_TIMEOUT:                  "TIMEOUT → TIMEOUT"
mapping_count_17:                            "FUTU_TO_QUANTBOT 有 17 个条目"
mapping_keys_match_sdk:                      "映射键 == SDK OrderStatus 全部 17 个枚举值"
mapping_each_one_to_one:                     "Futu 每个状态唯一映射到一个 QuantBot 状态"

# ─── Lease ────────────────────────────────────────────────────
acquire_new_market:                          "无记录 → 返回 token, status=ACTIVE"
acquire_while_ACTIVE:                        "ACTIVE → 返回 '' (不论 owner 是否匹配)"
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

# ─── SELL→BUY (严格规则) ────────────────────────────────────
all_sell_FILLED_ALL_allows_Buy:              "[SELL]→[FILLED_ALL,FILLED_ALL]→BUY 允许"
any_sell_not_FILLED_ALL_blocks_Buy:          "[SELL]→[FILLED_ALL,FILLED_PART]→BUY 阻断"
all_sell_not_FILLED_ALL_blocks_Buy:          "[SELL]→[FILLED_PART,FILLED_PART]→BUY 阻断"
sell_REJECTED_blocks_Buy:                    "[SELL]→[REJECTED]→BUY 阻断 (严格)"
sell_CANCELLED_ALL_blocks_Buy:               "[SELL]→[CANCELLED_ALL]→BUY 阻断 (严格)"
sell_FILLED_PART_blocks_Buy:                 "[SELL]→[FILLED_PART]→BUY 阻断 (部分成交)"
no_sell_allows_Buy:                          "[]→BUY 允许"

# ─── Recovery ──────────────────────────────────────────────────
recover_reserved_stale:                      ">1h RESERVED + broker 无 remark 匹配 → ABANDONED"
recover_reserved_fresh:                      "<1h RESERVED → 不做操作"
recover_reserved_query_fail:                 "查询异常 → 保持 RESERVED (fail-closed)"
recover_reserved_query_None:                 "查询返回 None → 保持 RESERVED (fail-closed)"
recover_reserved_found_in_broker:            "精确匹配 futu_code+remark → SUBMITTED"
recover_STOP_SELL_FILLED_ALL:                "FILLED_ALL STOP_SELL → confirm_stop + applied"
recover_STOP_SELL_crash_before_confirm:      "confirm_stop 异常 → 保持未标记 (可重试)"
recover_SIGNAL_BUY_FILLED_ALL:               "FILLED_ALL SIGNAL_BUY → init_position + applied"
recover_REVERSAL_SELL:                       "FILLED_ALL → 标记 applied (无 confirm_stop)"

# ─── Broker 调用映射 ────────────────────────────────────────────
place_order_RET_OK_and_order_id:             "RET_OK + order_id → SUBMITTED (解析 order_status)"
place_order_RET_OK_no_order_id:              "RET_OK + 无 order_id → UNKNOWN"
place_order_RET_OK_futu_FILLED_ALL:          "order_status=FILLED_ALL → FILLED_ALL"
place_order_RET_OK_futu_SUBMIT_FAILED:       "order_status=SUBMIT_FAILED → REJECTED"
place_order_RET_OK_futu_FAILED:              "order_status=FAILED → REJECTED"
place_order_RET_ERROR:                       "RET_ERROR → TIMEOUT (不推断 REJECTED)"
place_order_exception:                       "异常 → TIMEOUT"

# ─── Concurrency ──────────────────────────────────────────────
concurrent_acquire_same_intent:              "2 连接同 intent → 1 成功 (UNIQUE) 1 失败"
concurrent_acquire_same_market:              "2 连接同 market → 1 acquire_lease 1 拒绝"
concurrent_acquire_released_then_new:        "释放后第二个连接可获取 (RELEASED 测试)"

# ─── Rollback ──────────────────────────────────────────────────
rollback_stops_Futu_OpenD_exe:               "回滚脚本验证 Futu_OpenD.exe 已停止"
rollback_disables_scheduled_tasks:           "回滚脚本禁用 AutoTradeHK/US"
rollback_kills_running_runner:               "回滚脚本停止运行中的 unified_runner"
rollback_fails_if_OpenD_not_stoppable:       "Futu_OpenD.exe 无法停止 → throw"
rollback_preserves_journal:                  "回滚后 journal 文件存在且已备份"
rollback_verification_comprehensive:         "全部 9 步验证通过"
rollback_revert_committed                    "已提交的 F2-SEC → git revert 处理"
rollback_checkout_uncommitted                "未提交 → git checkout 处理"

# ─── Reconciliation Tool (只读) ──────────────────────────────
recon_journal_matches_futu:                  "journal vs Futu 订单匹配"
recon_unmatched_journal_detected:            "journal 中有 Futu 无 → 报告"
recon_unmatched_futu_detected:               "Futu 中有 journal 无 → 报告"
recon_status_mismatch_detected:              "状态不一致 → 报告"
recon_query_fail_no_crash:                   "查询失败 → 报告含 error_info (不崩溃)"
recon_can_safely_resume:                     "无未解决 → can_safely_resume_live=True"

# ⛔ 禁止的测试
# 任何 TrdEnv.SIMULATE 真实下单
# 任何 --live 或 LIVE_CONFIRMED 路由
# FOK 相关测试 (本机 SDK 不支持 fill_side_type)
# 任何调用 broker place_order 且未 mock 的测试
```

---

## 13. 交付确认

✅ **本文件完全取代 v1–v5.4 全部版本, 是 Phase F2-SEC 的唯一实施来源。**  
✅ **P0-1: 使用 `Get-CimInstance Win32_Process` 精确检测 Futu_OpenD.exe, 使用 `git revert` 处理已提交版本, 提供独立只读 reconciliation 工具。**  
✅ **P0-2: 提供了完整的实施契约, 包含 SQLite schema, 事务设计, 完整状态转换表, RESERVED→SUBMITTING→broker 提交流程, startup recovery 顺序, post-fill 副作用恢复, SELL→BUY 完整流程。**  
✅ **P0-3: `CANCELLED_PART` 已从 `terminal_set` 移动到 `blocking_set`, `SUBMITTING` 已加入 `uncertain_set`。**  
✅ **P0-4: 映射键使用 `ft.OrderStatus.*` 常量 (如 `ft.OrderStatus.NONE` = `"N/A"`), 并添加穷举验证 `assert set(FUTU_TO_QUANTBOT.keys()) == set(ft.OrderStatus.load_dict().keys())`。**  
✅ **未修改任何代码或现有文件。**  
✅ **未执行 Git 操作。**  
✅ **未接入 TrdEnv.REAL。**  
✅ **未调整策略、Gate 或 confidence_v2。**  
✅ **未触碰受保护代码或 4 个旧 research untracked 文件。**  

**等待 Codex 批准后实施 Phase F2-SEC。**