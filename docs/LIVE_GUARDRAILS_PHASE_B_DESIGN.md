# Phase B — Live Guardrails 实盘门禁设计

> 设计日期: 2026-06-03
> 范围: `unified_runner.py` / `core/order_executor.py`
> 状态: 设计草案 | 未修改代码 | 未 Git 操作

---

## 一、当前调用链

### 定时任务路径（方案 A 已实施）

```
AutoTradeHK (09:35, Enabled)
  └─ scheduled_hk.bat
       └─ python unified_runner.py --market HK
            └─ run(market='HK', dry_run=True)       ← --live 已移除，强制 dry-run

AutoTradeUS (21:35, Enabled)
  └─ scheduled_us.bat
       └─ python unified_runner.py --market US
            └─ run(market='US', dry_run=True)        ← --live 已移除，强制 dry-run
```

### 手动 Live 路径（方案 B 设计目标）

```
手动 CLI:
  └─ python unified_runner.py --market HK --live
       └─ run(market='HK', dry_run=False)
            └─ OrderExecutor(dry_run=False)
                 └─ FutuAdapter.place_order()
                      └─ ft.TrdEnv.SIMULATE          ← 当前仍为模拟环境
```

### 参数传递关系（方案 B 设计目标）

| 断言 | 值 |
|------|----|
| `args.live` | 用户是否请求 live |
| `live_confirmed = _is_live_confirmed(args)` | 用户是否确认 live |
| `execute_live = args.live and live_confirmed and not should_block` | **唯一决定是否调用 OrderExecutor(dry_run=False)** |
| `FutuAdapter.place_order()` | 硬编码 `TrdEnv.SIMULATE` |

---

## 二、新 CLI 行为矩阵

### 核心原则

- `--live` 保留为语义参数："用户想执行交易"
- `--confirm-live` 新增为安全参数："用户确认执行交易"
- 二者必须同时出现，或 `QUANT_LIVE_CONFIRM=YES` 环境变量等效替代

### 行为矩阵

| # | `--live` | `--confirm-live` | `QUANT_LIVE_CONFIRM=YES` | 行为 | `execution_mode` |
|---|----------|-----------------|--------------------------|------|-----------------|
| 1 | ❌ | ❌ | ❌ | 完整流程，dry-run 执行订单 | `DRY_RUN` |
| 2 | ❌ | ✅ | ❌ | 报 warning"`--confirm-live` 需配合 `--live`"，仍 dry-run | `DRY_RUN` |
| 3 | ❌ | ❌ | ✅ | 忽略环境变量（无 `--live` 时无效），dry-run | `DRY_RUN` |
| 4 | ✅ | ❌ | ❌ | 完整流程，生成订单 + pre-trade summary，**阻止执行**，日志 `LIVE_BLOCKED_BY_CONFIRM` | `LIVE_BLOCKED_BY_CONFIRM` |
| 5 | ✅ | ✅ | ❌ | 完整流程，允许进入 `OrderExecutor(dry_run=False)` | `LIVE_CONFIRMED` |
| 6 | ✅ | ❌ | ✅ | 完整流程，环境变量视为确认，允许进入 live | `LIVE_CONFIRMED` |
| 7 | ✅ | ✅ | ✅ | 重复确认，正常允许 live | `LIVE_CONFIRMED` |

### 决策函数：`_is_live_confirmed(args)`

```python
def _is_live_confirmed(args) -> bool:
    """判断是否获得了 live 确认。
    
    - --live 是必要条件（没有 --live 则无需确认）
    - --confirm-live CLI flag 或 QUANT_LIVE_CONFIRM=YES 均可
    - 同时设置时以 --confirm-live 为准（显式 > 环境变量）
    """
    if not args.live:
        return False                          # --live 都没有，不需要确认
    if args.confirm_live:
        return True                           # CLI 显式确认
    if os.environ.get('QUANT_LIVE_CONFIRM', '').upper() == 'YES':
        return True                           # 环境变量确认（定时任务用）
    return False                              # 需要确认但未提供
```

---

## 三、Pre-Trade Summary 格式

### 输出位置

在 Step 4（建仓决策）完成后、Step 5（订单执行）之前插入。此时 `orders` 列表已完整生成。

### 控制台输出格式

```text
╔═══════════════════════════════════════════════════════════════╗
║                    PRE-TRADE SUMMARY                         ║
╠═══════════════════════════════════════════════════════════════╣
║ Mode:           LIVE_BLOCKED_BY_CONFIRM                      ║
║ Market:         HK                                           ║
║ Timestamp:      2026-06-03T20:53:33                          ║
╠═══════════════════════════════════════════════════════════════╣
║ Account Snapshot                                             ║
║   Total Assets:     1,506,681                                ║
║   Cash Before:      1,460,041                                ║
║   Cash After:       1,399,441                                ║
║   Market Value:        46,640                                ║
║   Exposure Before:     3.1%                                 ║
║   Exposure After:      7.1%                                 ║
╠═══════════════════════════════════════════════════════════════╣
║ Orders Summary                                               ║
║   Total Orders:   3                                          ║
║   BUY:            2   GROSS:        79,600                   ║
║   SELL:           1   GROSS:        19,000                   ║
║   Net Cash Impact:   +60,600                                 ║
║   Largest Order:   00700.HK BUY 100股 @ 520.00 = 52,000     ║
╠═══════════════════════════════════════════════════════════════╣
║ Per-Order Details                                            ║
║ # │ Symbol      │ Action │  Qty │  Price  │  Notional │ Reason            ║
║ 1 │ 00700.HK    │ BUY    │  100 │ 520.00  │   52,000  │ xmm_bottom        ║
║ 2 │ 09988.HK    │ BUY    │  200 │ 138.00  │   27,600  │ vp_breakout       ║
║ 3 │ 00388.HK    │ SELL   │   50 │ 380.00  │   19,000  │ stop_loss         ║
╠═══════════════════════════════════════════════════════════════╣
║ Risk Warnings (hardF拦截检查)                                 ║
║ ✅ Order Count > 0          PASS  (3 orders)                 ║
║ ✅ Cash After Orders        PASS  (1,399,441 >= 0)           ║
║ ✅ Largest Order %          PASS  (3.5% <= 20.0%)            ║
║ ✅ Exposure After Orders    PASS  (7.1% <= 80.0%)            ║
║ ❌ LIVE_CONFIRM_REQUIRED: --confirm-live missing             ║
╚═══════════════════════════════════════════════════════════════╝
```

### JSON 结构（供 guardrail 日志使用）

```json
{
  "timestamp": "2026-06-03T16:26:54",
  "market": "HK",
  "requested_live": true,
  "confirmed_live": false,
  "execution_mode": "LIVE_BLOCKED_BY_CONFIRM",
  "account": {
    "total_assets": 1506681,
    "cash_before": 1460041,
    "cash_after_orders": 1399441,
    "market_val": 46640,
    "exposure_before_pct": 3.1,
    "exposure_after_pct": 7.1,
    "exposure_before_value": 46640,
    "exposure_after_value": 107240
  },
  "orders_summary": {
    "total": 3,
    "buy_count": 2,
    "sell_count": 1,
    "gross_buy": 79600,
    "gross_sell": 19000,
    "net_cash_impact": 60600,
    "largest_order": {
      "symbol": "00700.HK",
      "action": "BUY",
      "qty": 100,
      "price": 520.00,
      "notional": 52000
    }
  },
  "per_order_details": [
    { "symbol": "00700.HK", "action": "BUY",  "qty": 100, "price": 520.00, "notional": 52000, "reason": "xmm_bottom" },
    { "symbol": "09988.HK", "action": "BUY",  "qty": 200, "price": 138.00, "notional": 27600, "reason": "vp_breakout" },
    { "symbol": "00388.HK", "action": "SELL", "qty": 50,  "price": 380.00, "notional": 19000, "reason": "stop_loss" }
  ],
  "risk_checks": {
    "order_count": { "pass": true,  "detail": "3 orders" },
    "cash_after_orders": { "pass": true,  "detail": "1,399,441 >= 0" },
    "largest_order_pct": { "pass": true,  "detail": "3.5% <= 20.0%" },
    "exposure_after_orders": { "pass": true,  "detail": "7.1% <= 80.0%" },
    "confirm_live": { "pass": false, "detail": "--confirm-live missing" }
  },
  "block_reason": "LIVE_CONFIRM_REQUIRED: --confirm-live not provided"
}
```

---

## 四、硬性拦截规则设计

### 规则表

| # | 规则 | 条件 | 拦截级别 | 说明 |
|---|------|------|---------|------|
| 1 | **确认缺失** | `args.live=True` 但 `_is_live_confirmed()=False` | BLOCK | 必须提供 `--confirm-live` 或 `QUANT_LIVE_CONFIRM=YES` |
| 2 | **无订单** | `len(orders) == 0` | SKIP | 无需执行，不触发任何交易路径 |
| 3 | **现金不足** | 买入后现金 `available_cash - sum(buy_orders.notional) < 0` | BLOCK_LIVE | 拦截 live 模式；不调用 OrderExecutor，不执行订单 |
| 4 | **单只超限** | `max(order.notional) / total_assets > config.MAX_POSITION_PCT` (0.20) | BLOCK_LIVE | 同上 |
| 5 | **总暴露超限** | `(current_exposure + sum(buy_orders.notional) - sum(sell_orders.notional)) / total_assets > config.MAX_TOTAL_PCT` (0.80) | BLOCK_LIVE | 同上 |
| 6 | **符号格式无效** | `symbol` 不以 `.HK` 或 `.US` 结尾 | BLOCK | 防止路由错误 |
| 7 | **数量不为正** | `qty <= 0` | SKIP | 跳过该笔，不影响其他订单 |
| 8 | **风控已触发** | 同一标的在冷却期内 | SKIP | 由 `risk_mgr.is_in_cooldown()` 决定 |

### 拦截分级

| 级别 | 行为 |
|------|------|
| `BLOCK` | **硬性拦截所有场景** — 无论 dry-run 还是 live，都不应执行 |
| `BLOCK_LIVE` | **仅拦截 live 模式** — 不调用 `OrderExecutor`，不执行订单，仅保留 summary/log/report |
| `SKIP` | **跳过单笔订单** — 不影响其他订单执行 |
| `PASS` | **放行** — 所有检查通过 |

### 建议实现方式

```python
def _should_block_live(summary: dict) -> tuple[bool, list[str]]:
    """
    检查是否应拦截 live 执行。
    
    Returns:
        (block, reasons): block=True 表示应拦截，reasons 为原因列表
    """
    reasons = []
    
    # 规则 1: 确认缺失（仅在 requested_live=True 时检查）
    if summary.get('requested_live') and not summary.get('confirmed_live'):
        reasons.append("LIVE_CONFIRM_REQUIRED")
    
    # 规则 3: 现金不足
    cash_after = summary.get('risk_checks', {}).get('cash_after_orders', {}).get('pass', True)
    if not cash_after:
        reasons.append("INSUFFICIENT_CASH")
    
    # 规则 4: 单只超限（直接取 dict value，非遍历）
    largest_check = summary.get('risk_checks', {}).get('largest_order_pct', {})
    if not largest_check.get('pass', True):
        reasons.append(largest_check.get('detail', 'POSITION_LIMIT_EXCEEDED'))
    
    # 规则 5: 总暴露超限
    exposure_check = summary.get('risk_checks', {}).get('exposure_after_orders', {})
    if not exposure_check.get('pass', True):
        reasons.append(exposure_check.get('detail', 'EXPOSURE_LIMIT_EXCEEDED'))
    
    return len(reasons) > 0, reasons
```

---

## 五、日志设计

### 日志文件

```
output/live_guardrails_{market}_{YYYYMMDD}_{HHMMSS}.json
```

### 日志完整字段

```json
{
  "meta": {
    "timestamp": "2026-06-03T16:26:54.123",
    "market": "HK",
    "source": "unified_runner"
  },
  "session": {
    "requested_live": true,
    "confirmed_live": false,
    "confirmation_method": "none",
    "execution_mode": "LIVE_BLOCKED_BY_CONFIRM",
    "block_reason": "LIVE_CONFIRM_REQUIRED: --confirm-live not provided"
  },
  "account_snapshot": {
    "total_assets": 1506681,
    "cash_before": 1460041,
    "cash_after_orders": 1399441,
    "market_val": 46640,
    "exposure_before_pct": 3.1,
    "exposure_after_pct": 7.1,
    "exposure_before_value": 46640,
    "exposure_after_value": 107240
  },
  "orders_summary": {
    "total": 3,
    "buy_count": 2,
    "sell_count": 1,
    "gross_buy": 79600,
    "gross_sell": 19000,
    "net_cash_impact": 60600
  },
  "per_order_details": [
    { "symbol": "00700.HK", "action": "BUY",  "qty": 100, "price": 520.00, "notional": 52000, "reason": "xmm_bottom" },
    { "symbol": "09988.HK", "action": "BUY",  "qty": 200, "price": 138.00, "notional": 27600, "reason": "vp_breakout" },
    { "symbol": "00388.HK", "action": "SELL", "qty": 50,  "price": 380.00, "notional": 19000, "reason": "stop_loss" }
  ],
  "risk_checks": {
    "order_count":      { "pass": true,  "detail": "3 orders" },
    "cash_after_orders": { "pass": true,  "detail": "1,399,441 >= 0" },
    "largest_order_pct": { "pass": true,  "detail": "3.5% <= 20.0%" },
    "exposure_after_orders": { "pass": true,  "detail": "7.1% <= 80.0%" }
  },
  "positions_held": [
    { "symbol": "00700.HK", "qty": 200, "cost": 456.00, "cur": 520.00, "pnl_pct": 14.0 },
    { "symbol": "00388.HK", "qty": 100, "cost": 152.00, "cur": 166.40, "pnl_pct": 9.5 }
  ]
}
```

### 日志写入时机

1. **每次进入订单生成流程后写入**（Step 4 完成后、pre-trade summary 输出后立即写入）— 保证在订单执行前就有记录
2. 如果 `execution_mode == 'LIVE_CONFIRMED'`，在订单执行后再追加执行结果
3. **`--signal-only` 不写 guardrail log**（无账户/订单数据），或写轻量 `signal_scan_log`

---

## 六、最小实现方案

### 改动点概要

| 文件 | 改动 | 类型 |
|------|------|------|
| `unified_runner.py` | `run()` 增加 `requested_live=False, live_confirmed=False` 参数 | 修改函数签名 |
| `unified_runner.py` | CLI 增加 `--confirm-live` | 新增参数 |
| `unified_runner.py` | 新增 `_is_live_confirmed(args)` | 新增纯函数 |
| `unified_runner.py` | 新增 `_build_pre_trade_summary(...)` | 新增纯函数 |
| `unified_runner.py` | 新增 `_print_pre_trade_summary(summary)` | 新增纯函数 |
| `unified_runner.py` | 新增 `_save_guardrail_log(summary, path)` | 新增纯函数 |
| `unified_runner.py` | 新增 `_should_block_live(summary)` | 新增纯函数 |
| `unified_runner.py` | Step 5 前插入 pre-trade summary + live gate | 修改流程 |
| `unified_runner.py` | `execute_live = args.live and live_confirmed and not should_block` → `OrderExecutor(dry_run=not execute_live)` | 修改调用（见下方核心逻辑） |
| `core/order_executor.py` | 无需改动 | — |
| `core/futu_adapter.py` | 保留 `TrdEnv.SIMULATE`，仅接口风险评估 | 不改代码 |
| `scheduled_hk/us.bat` | 保留方案 A 改动，不新增 | 不改 |

### 详细改动说明

#### 1. `run()` 函数签名

```python
def run(market='HK', dry_run=True, signal_only=False, no_stop=False,
        requested_live=False, live_confirmed=False):
    """
    Parameters:
        market: 'HK' / 'US'
        dry_run: True=执行模拟订单, False=执行真实订单（仅当 requested_live+confirmed 时才传 False）
        signal_only: 仅信号扫描
        no_stop: 跳过止损检查
        requested_live: 用户是否请求了 --live（CLI 显式传入，不在 run 内部反推）
        live_confirmed: 用户是否确认了 live（CLI + env）
    """
```

#### 2. CLI 参数

```python
parser.add_argument('--confirm-live', action='store_true',
                    help='确认实盘交易（必须与 --live 同时使用）')
```

#### 3. CLI 入口逻辑（`if __name__ == '__main__'` 段）

```python
requested_live = bool(args.live)
live_confirmed = _is_live_confirmed(args)

# 显式传递 requested_live，不在 run() 内部靠 dry_run 反推
dry_run = not requested_live  # 初始 dry_run 来自用户请求
                                # 执行阶段再根据 execute_live 最终决定
```

```python
# 警告：--confirm-live 无 --live
if args.confirm_live and not args.live:
    print('[WARN] --confirm-live 需配合 --live 使用，当前仍为 dry-run')

# 警告：--live 无确认
if args.live and not live_confirmed:
    print('[WARN] --live 但未提供 --confirm-live 或 QUANT_LIVE_CONFIRM=YES')
    print('       订单将生成但不执行。使用 --confirm-live 确认执行。')
```

#### 4. Step 5 插入点

在 Step 4（建仓决策）与 Step 5（订单执行）之间插入：

```python
# Step 4.5: Pre-Trade Summary + Live Gate
summary = _build_pre_trade_summary(
    market=market,
    requested_live=requested_live,
    confirmed_live=live_confirmed,
    account=account,
    positions=positions,
    orders=orders,
    total_assets=total_assets,
    cash_before=cash_before,
    exposure_before=exposure_before,
)
_print_pre_trade_summary(summary)
_save_guardrail_log(summary, log_dir)

# 是否应拦截 live
should_block, block_reasons = _should_block_live(summary)

# 核心决策逻辑（三种路径，互斥）
execute_live = requested_live and live_confirmed and not should_block

if execute_live:
    # ── 路径 A: LIVE_CONFIRMED — 允许调用 OrderExecutor(dry_run=False)
    pass  # 进入 Step 5
elif requested_live and not live_confirmed:
    # ── 路径 B: LIVE_BLOCKED_BY_CONFIRM
    #     订单已生成 + summary 已输出 + guardrail log 已写入
    #     不调用 OrderExecutor（即使是 dry_run 也不调用）
    #     后续信号 JSON 和报告正常生成
    print(f'\n  [GUARDRAIL] Live 执行被拦截 (LIVE_BLOCKED_BY_CONFIRM)')
    print(f'  [GUARDRAIL] 订单已生成但不执行。使用 --confirm-live 确认后重试。')
elif requested_live and live_confirmed and should_block:
    # ── 路径 C: LIVE_BLOCKED_BY_RULES
    #     有确认但风控规则拦截，同样不调用 OrderExecutor
    print(f'\n  [GUARDRAIL] Live 执行被风控规则拦截: {"; ".join(block_reasons)}')
    print(f'  [GUARDRAIL] 请检查风险提示后重试。')
else:
    # ── 路径 D: DRY_RUN — 正常 dry-run，允许 OrderExecutor(dry_run=True)
    pass  # 进入 Step 5（dry_run=True）
```

#### 5. OrderExecutor 调用（核心决策）

```python
# 核心逻辑：根据 execute_live 决定是否调用 / 以什么模式调用 OrderExecutor
# execute_live = requested_live and live_confirmed and not should_block

if execute_live:
    executor = OrderExecutor(host=..., port=..., dry_run=False)
elif not requested_live:
    # 普通 dry-run（未请求 live）
    executor = OrderExecutor(host=..., port=..., dry_run=True)
else:
    # LIVE_BLOCKED_BY_CONFIRM 或 LIVE_BLOCKED_BY_RULES
    # 不调用 OrderExecutor 的任何方法（包括 execute_orders）
    # 原始 orders 保留不变，供后续 signals/report/guardrail log 使用
    executor = None
```

> ⚠️ **关键约束**：Paths B/C（LIVE_BLOCKED）不应调用 `OrderExecutor.execute_orders()`，即使以 `dry_run=True`。需要将 `run()` 中的执行流程提前用条件分支隔开。

---

## 七、测试计划

### 测试清单

| # | 测试用例 | 预期 | 验证方法 |
|---|---------|------|---------|
| 1 | `python unified_runner.py --help` | 显示 `--confirm-live` | 手动运行 |
| 2 | `python unified_runner.py --market HK --signal-only` | 不受影响，正常 dry-run | 手动运行 + pytest |
| 3 | `_build_pre_trade_summary()` 纯函数测试 | 返回正确结构 + 数值 | pytest |
| 4 | `_is_live_confirmed()` 纯函数测试 | 4 种组合正确 | pytest |
| 5 | `_should_block_live()` 纯函数测试 | 5 种规则正确拦截 | pytest |
| 6 | 普通 dry-run（无 `--live`）允许 `OrderExecutor(dry_run=True)` | `execute_orders()` 被调用一次 | 集成，mock OrderExecutor |
| 7 | `--live` 无 `--confirm-live` **不调用 `OrderExecutor.execute_orders()`** | 输出 `LIVE_BLOCKED_BY_CONFIRM`；断言 `execute_orders` 未被调用 | 集成，mock OrderExecutor ⭐ |
| 8 | `--live --confirm-live` 允许 `OrderExecutor(dry_run=False)` | `execute_orders()` 以 `dry_run=False` 被调用 | 集成（当前仍为 SIMULATE） |
| 9 | `QUANT_LIVE_CONFIRM=YES` + `--live` 等价于 `--confirm-live` | 允许 live | 集成 |
| 10 | `--confirm-live` 无 `--live` 报 warning | 打印 warning，仍 dry-run | 手动 |
| 11 | pytest smoke 全通过 | 11 passed | `pytest` |

> ⚠️ **#7 是关键防护测试**：当 `--live` 无 `--confirm-live` 时，必须在 mock 层面断言 `OrderExecutor.execute_orders()` 未被调用。仅检查 stdout 输出 blocked 不足以保证安全 —— 必须从调用链层面验证 OrderExecutor 没有被实例化和调用。

### 纯函数单元测试示例

```python
# tests/test_live_guardrails.py （新增）

class TestIsLiveConfirmed:
    def test_no_live_no_confirm(self):
        args = argparse.Namespace(live=False, confirm_live=False)
        assert _is_live_confirmed(args) == False

    def test_live_no_confirm(self):
        args = argparse.Namespace(live=True, confirm_live=False)
        with patch.dict(os.environ, {}, clear=True):
            assert _is_live_confirmed(args) == False

    def test_live_with_confirm(self):
        args = argparse.Namespace(live=True, confirm_live=True)
        assert _is_live_confirmed(args) == True

    def test_live_with_env(self):
        args = argparse.Namespace(live=True, confirm_live=False)
        with patch.dict(os.environ, {'QUANT_LIVE_CONFIRM': 'YES'}):
            assert _is_live_confirmed(args) == True

    def test_confirm_no_live(self):
        args = argparse.Namespace(live=False, confirm_live=True)
        assert _is_live_confirmed(args) == False


class TestBuildPreTradeSummary:
    def test_basic_structure(self):
        summary = _build_pre_trade_summary(...)
        assert 'account' in summary
        assert 'orders_summary' in summary
        assert 'per_order_details' in summary
        assert 'risk_checks' in summary

    def test_no_orders(self):
        summary = _build_pre_trade_summary(orders=[])
        assert summary['orders_summary']['total'] == 0
        assert summary['orders_summary']['gross_buy'] == 0


class TestShouldBlockLive:
    def test_missing_confirm(self):
        summary = gen_summary(requested_live=True, confirmed_live=False)
        block, reasons = _should_block_live(summary)
        assert block == True
        assert 'LIVE_CONFIRM_REQUIRED' in reasons

    def test_zero_orders_no_block(self):
        summary = gen_summary(orders=[], requested_live=False)
        block, reasons = _should_block_live(summary)
        assert block == False
```

---

## 八、明确不做

以下不在 Phase B 范围：

| 条目 | 原因 |
|------|------|
| ❌ 不改 `TrdEnv.SIMULATE` | 仍是唯一保护层，单独评估 |
| ❌ 不接 `TrdEnv.REAL` | 不在本阶段目标 |
| ❌ 不改 `scheduled_hk.bat` / `scheduled_us.bat` | 方案 A 已完成，两文件已不带 `--live` |
| ❌ 不改 Gate 阈值 | 独立于 guardrails |
| ❌ 不改 `fusion_confidence` | 独立于 guardrails |
| ❌ 不改策略信号 | 不涉及信号生成逻辑 |
| ❌ 不改 `core/order_executor.py` | 仅通过 `dry_run` 参数控制 |
| ❌ 不改 `core/futu_adapter.py` | 仅接口风险评估，不改代码 |
| ❌ 不提交代码 | 本阶段为设计 + Codex 审查 |

---

## 附录：CLI 使用示例

```bash
# Dry-run（默认）
python unified_runner.py --market HK

# Dry-run 信号模式（不受影响）
python unified_runner.py --market HK --signal-only

# 生成完整画像和订单，但不执行
python unified_runner.py --market HK --live
# 输出: pre-trade summary + LIVE_BLOCKED_BY_CONFIRM

# 允许执行（CLI 确认）
python unified_runner.py --market HK --live --confirm-live

# 允许执行（环境变量确认，适用于定时任务）
set QUANT_LIVE_CONFIRM=YES
python unified_runner.py --market HK --live

# 一次性
QUANT_LIVE_CONFIRM=YES python unified_runner.py --market HK --live

# 错误用法：仅 --confirm-live 无 --live
python unified_runner.py --confirm-live
# 输出: [WARN] --confirm-live 需配合 --live 使用，当前仍为 dry-run
```
