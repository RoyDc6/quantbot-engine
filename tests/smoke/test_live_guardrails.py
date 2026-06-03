# -*- coding: utf-8 -*-
"""
Phase B — Live Guardrails 测试套件。

测试策略：
  1. 纯函数 → 通过 subprocess 运行独立测试脚本（不依赖 any quant 模块）
  2. --help → 子进程
  3. 执行路径 → inline mock run()
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from core.paths import PROJECT_ROOT as PRJ

PYTHON = sys.executable
UNIFIED_RUNNER = str(PRJ / 'unified_runner.py')
PURE_TEST_SCRIPT = str(PRJ / 'tests' / 'smoke' / 'guardrails_pure_standalone.py')


# ═══════════════════════════════════════════════════════════════════
# 1. 纯函数测试（通过独立脚本，零依赖 quant 模块）
# ═══════════════════════════════════════════════════════════════════

class TestPureFunctions:
    """通过 test_guardrails_pure.py 运行纯函数测试。"""

    def test_all_pure_functions(self):
        result = subprocess.run(
            [PYTHON, PURE_TEST_SCRIPT],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=30,
        )
        print(result.stdout)
        if result.stderr:
            print(f'STDERR: {result.stderr[:500]}')
        assert result.returncode == 0, (
            f"Pure function tests failed (rc={result.returncode})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr[:500]}"
        )
        assert '0 failures' in result.stdout or 'TOTAL:' in result.stdout


# ═══════════════════════════════════════════════════════════════════
# 2. --help 集成测试
# ═══════════════════════════════════════════════════════════════════

class TestHelp:
    def test_confirm_live_in_help(self):
        result = subprocess.run(
            [PYTHON, UNIFIED_RUNNER, '--help'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=30, cwd='C:\\',
        )
        output = result.stdout + result.stderr
        assert '--confirm-live' in output, (
            f"--confirm-live missing from --help\nstdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )


# ═══════════════════════════════════════════════════════════════════
# 3. 执行路径验证（mock OrderExecutor）
# ═══════════════════════════════════════════════════════════════════

def _make_buy_signal(symbol):
    """生成 BUY 级 FusionController 返回值，确保订单被生成。"""
    return {
        'ticker': symbol, 'date': '2026-06-03',
        'close': 100.0, 'data_source': 'Futu',
        'fusion': {
            'level': 'BUY', 'score': 50, 'confidence': 0.70,
            'position_pct': 0.10, 'risk_level': 'MEDIUM', 'reasoning': 'test',
            'raw_scores': {'xmm': 60, 'vp': 30, 'llm': 15},
            'weights_used': {},
            'warnings': [],
        },
        'directive': {'level': 'BUY', 'target_pct': 0.10},
        'sources': {
            'xmm': {'action': 'BUY', 'reason': 'test', 'trend': 'UP',
                    'position_size': 0.7, 'td_count': 0},
            'vp': {'state': 'above_box', 'direction': 'BUY',
                   'vah': 105, 'val': 95, 'poc': 100},
            'llm': {'sentiment_score': 15, 'event_summary': 'test',
                    'event_type': 'none'},
        },
        'status': {'xmm': 'OK', 'vp': 'OK', 'llm': 'OK'},
        'stale_days': 0,
        'gate': {'approved': True, 'reject_reasons': []},
        'rsi_daily': 55, 'rsi_weekly': 52,
        'market_state': 'CRAB',
    }


class TestExecutionPath:
    """验证不同 CLI 组合下 OrderExecutor.execute_orders() 的调用情况。"""

    def _run_with_mocks(self, market='HK', dry_run=True,
                         requested_live=False, live_confirmed=False):
        from unified_runner import run

        with patch('unified_runner.FutuAdapter') as MockFA:
            inst = MockFA.return_value
            inst.test_connection.return_value = (True, 'Mock OK')
            inst.fetch_vix_data.return_value = {'vix': 15.0}
            inst.get_account_info.return_value = {
                'total_assets': 1500000, 'cash': 1400000, 'market_val': 0,
            }
            inst.get_positions.return_value = []

            with patch('unified_runner.OrderExecutor') as MockOE:
                mock_exec = MagicMock()
                MockOE.return_value = mock_exec

                with patch('unified_runner.FusionController') as MockFC:
                    fc_inst = MockFC.return_value
                    fc_inst.analyze_ticker.return_value = _make_buy_signal('00700.HK')

                    run(market=market, dry_run=dry_run, signal_only=False,
                        no_stop=False,
                        requested_live=requested_live,
                        live_confirmed=live_confirmed,
                        )
                    return mock_exec, MockOE

    def test_dry_run_calls_executor(self):
        """普通 dry-run（无 --live）应调用 execute_orders。"""
        mock_exec, _MockOE = self._run_with_mocks(
            market='HK', dry_run=True,
            requested_live=False, live_confirmed=False)
        assert mock_exec.execute_orders.called, "dry-run 应调用 execute_orders"

    def test_live_no_confirm_blocked(self):
        """--live 但无确认不应调用 execute_orders，OrderExecutor 也不应实例化。"""
        mock_exec, MockOE = self._run_with_mocks(
            market='HK', dry_run=False,
            requested_live=True, live_confirmed=False)
        assert not mock_exec.execute_orders.called, (
            "--live without confirm 必须不调用 execute_orders"
        )
        MockOE.assert_not_called()

    def test_live_confirmed_calls_executor(self):
        """--live --confirm-live 应调用 execute_orders。"""
        mock_exec, _MockOE = self._run_with_mocks(
            market='HK', dry_run=False,
            requested_live=True, live_confirmed=True)
        assert mock_exec.execute_orders.called, (
            "--live --confirm-live 应调用 execute_orders"
        )


# ═══════════════════════════════════════════════════════════════════
# 4. 直接测试真实的 _build_pre_trade_summary（防双算漂移）
# ═══════════════════════════════════════════════════════════════════

class TestBuildPreTradeSummaryDirect:
    """直接调用 unified_runner._build_pre_trade_summary 验证 cash/exposure 推算正确。"""

    def test_cash_and_exposure_no_double_count(self):
        from unified_runner import _build_pre_trade_summary

        summary = _build_pre_trade_summary(
            market='HK',
            requested_live=True,
            confirmed_live=True,
            account={'market_val': 20000},
            positions=[],
            orders=[
                {'symbol': '00700.HK', 'action': 'BUY', 'qty': 100, 'price': 100.0, 'reason': 'test'},
                {'symbol': '00981.HK', 'action': 'SELL', 'qty': 100, 'price': 50.0, 'reason': 'test'},
            ],
            total_assets=150000,
            cash_before=100000,
            exposure_before=20000,
        )

        assert summary['account']['cash_before'] == 100000
        # cash = 100000 + sell(5000) - buy(10000) = 95000
        assert summary['account']['cash_after_orders'] == 95000, (
            f"cash_after_orders={summary['account']['cash_after_orders']}, 应为 95000"
        )
        # exposure = 20000 + buy(10000) - sell(5000) = 25000 → 25000/150000=16.666... → 16.7%
        assert summary['account']['exposure_after_pct'] == 16.7, (
            f"exposure_after_pct={summary['account']['exposure_after_pct']}, 应为 16.7"
        )
        # risk check 也应基于正确值
        assert summary['risk_checks']['cash_after_orders']['pass'] is True
        assert summary['risk_checks']['exposure_after_orders']['pass'] is True