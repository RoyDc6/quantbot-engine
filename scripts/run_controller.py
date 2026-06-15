# -*- coding: utf-8 -*-
"""
scripts/run_controller.py — FusionController CLI 入口

用法:
    python run_controller.py --market HK
    python run_controller.py --market US  
    python run_controller.py --single 00700.HK
    python run_controller.py --report
    python run_controller.py --market HK --live
    python run_controller.py --status
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core.fusion_controller import FusionController
from core.universe_manager import UniverseManager


def setup_argparse():
    parser = argparse.ArgumentParser(
        description='FusionController 战术中控台 — 三驾马车缝合管线',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    # 运行模式
    parser.add_argument('--market', choices=['HK', 'US', 'Crypto', 'all'], default=None,
                       help='市场扫描')
    parser.add_argument('--single', type=str, default=None,
                       help='单个标的分析')
    parser.add_argument('--report', action='store_true',
                       help='生成日报')
    parser.add_argument('--status', action='store_true',
                       help='系统状态检查')
    
    # 参数
    parser.add_argument('--market-state', type=str, default=None,
                       choices=['BULL', 'BEAR', 'CRAB', 'RECOVERY', 'CORRECTION'],
                       help='强制指定市场状态（默认自动检测）')
    parser.add_argument('--vix-regime', type=str, default=None,
                       choices=['SUPPRESSED', 'CANDIDATE', 'RELEASED'],
                       help='强制指定 VIX regime（默认自动检测）')
    parser.add_argument('--live', action='store_true',
                       help='启用实盘模式（当前为 dry-run）')
    parser.add_argument('--force-llm', action='store_true',
                       help='强制重跑 LLM 因子（跳过缓存）')
    parser.add_argument('--no-xmm', action='store_true',
                       help='禁用 XMM 信号源')
    parser.add_argument('--no-vp', action='store_true',
                       help='禁用 VP 信号源')
    parser.add_argument('--no-llm', action='store_true',
                       help='禁用 LLM 信号源')
    parser.add_argument('--output', type=str, default=None,
                       help='输出 JSON 文件路径')
    parser.add_argument('--max-tickers', type=int, default=None,
                       help='最多扫描标的数')
    parser.add_argument('--pretty', action='store_true',
                       help='JSON 格式化输出')
    
    return parser


def print_header():
    print('╔═══════════════════════════════════════════════╗')
    print('║   FusionController 战术中控台                  ║')
    print('║   三驾马车: XMM + VP + LLMBiasModel           ║')
    print(f'║   {datetime.now().strftime("%Y-%m-%d %H:%M")}                         ║')
    print('╚═══════════════════════════════════════════════╝')
    print()


def print_ticker_summary(result: dict):
    """打印单个标的的分析摘要。"""
    ticker = result.get('ticker', '?')
    close = result.get('close', 0)
    source = result.get('data_source', '?')
    directive = result.get('directive', {})
    fusion = result.get('fusion', {})
    gate = result.get('gate', {})
    status = result.get('status', {})

    # 信号源状态
    src_icons = {
        'xmm': '🟢' if status.get('xmm') == 'OK' else '⚪',
        'vp': '🟢' if status.get('vp') == 'OK' else '⚪',
        'llm': '🟢' if status.get('llm') == 'OK' else '⚪',
    }

    # 最终动作
    act = directive.get('action', 'HOLD')
    act_icon = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪', 'BLOCKED': '🚫'}.get(act, '⚪')

    print(f'  {act_icon} {ticker:<10}  ${close:<8.1f}  '
          f'{src_icons["xmm"]}XMM {src_icons["vp"]}VP {src_icons["llm"]}LLM  '
          f'→ {act:<7}  {fusion.get("level", "?"):<10} '
          f'(score={fusion.get("score", 0):+.1f} '
          f'conf={fusion.get("confidence", 0):.2f})  [{source}]')

    # 详细
    if act == 'BLOCKED':
        reasons = gate.get('reject_reasons', [])
        for r in reasons:
            print(f'    🚫 {r}')
    if fusion.get('warnings'):
        for w in fusion.get('warnings', []):
            print(f'    ⚠️  {w}')
    if fusion.get('reasoning'):
        print(f'    📝 {fusion["reasoning"]}')


def cmd_status(fc: FusionController):
    """系统状态。"""
    report = fc.status_report()
    print('=== 系统状态 ===')
    
    # 适配器状态
    adapters = report.get('adapters', {})
    for atype, info in adapters.items():
        icon = '✅' if info.get('available') else ('⚠️' if info.get('registered') else '❌')
        reg = '已注册' if info.get('registered') else '未注册'
        avail = '在线' if info.get('available') else '离线'
        msg = info.get('message') or ''
        suffix = f' | {msg}' if msg else ''
        print(f'  {atype:<15}: {icon} {reg} | {avail}{suffix}')
    
    print()
    for comp, status in report['components'].items():
        if comp in ('futu_adapter', 'crypto_adapter'):
            continue  # 已在上方展示
        print(f'  {comp:<15}: {"✅" if status else "❌"}')
    print()
    print(f'  资产池:   {fc.universe.total_count} 只')
    print(f'  港股:     {fc.universe.hk_count} 只')
    for s in fc.universe.get_hk_symbols():
        print(f'    {s} ({fc.universe.get_name(s)})')
    print(f'  美股:     {fc.universe.us_count} 只')
    for s in fc.universe.get_us_symbols():
        print(f'    {s} ({fc.universe.get_name(s)})')
    if fc.universe.crypto_count > 0:
        print(f'  Crypto:   {fc.universe.crypto_count} 只')
        for s in fc.universe.get_crypto_symbols():
            print(f'    {s} ({fc.universe.get_name(s)})')
    print()
    print(f'  权重配置:')
    from core.fusion_controller import BASE_WEIGHTS
    print(f'    XMM={BASE_WEIGHTS["xmm"]:.0%} VP={BASE_WEIGHTS["vp"]:.0%} LLM={BASE_WEIGHTS["llm"]:.0%}')
    print(f'    （静态权重，不随市场状态变化 | 断流时存活源重新归一化）')


def cmd_single(fc: FusionController, ticker: str, args):
    """单个标的分析。"""
    print(f'\n=== 单标的分: {ticker} ===\n')
    result = fc.analyze_ticker(
        ticker=ticker,
        market_state=args.market_state or 'CRAB',
        vix_regime=args.vix_regime or 'CANDIDATE',
        force_llm=args.force_llm,
        run_xmm=not args.no_xmm,
        run_vp=not args.no_vp,
        run_llm=not args.no_llm,
    )
    print_ticker_summary(result)
    return result


def cmd_scan(fc: FusionController, market: str, args):
    """市场扫描。"""
    markets = ['HK', 'US', 'Crypto'] if market == 'all' else [market]
    all_results = {}

    for m in markets:
        print(f'\n\n=== {m} 市场扫描 ===\n')
        state = args.market_state or ('CRAB' if m == 'HK' else 'BULL')
        vix = args.vix_regime or 'CANDIDATE'
        # Crypto 不适用 VIX 标记为 N/A
        if m == 'Crypto':
            vix = 'N/A'

        results = fc.scan_market(
            market=m,
            market_state=state,
            vix_regime=vix,
            max_tickers=args.max_tickers,
            force_llm=args.force_llm,
        )
        all_results[m] = results

        print()
        # 统计
        buy = sum(1 for r in results if r.get('directive', {}).get('action') == 'BUY')
        sell = sum(1 for r in results if r.get('directive', {}).get('action') == 'SELL')
        hold = sum(1 for r in results if r.get('directive', {}).get('action') == 'HOLD')
        blocked = sum(1 for r in results if r.get('directive', {}).get('action') == 'BLOCKED')
        print(f'  统计: BUY={buy} SELL={sell} HOLD={hold} BLOCKED={blocked}')
        print()

        for r in results:
            print_ticker_summary(r)

    return all_results


def cmd_report(fc: FusionController):
    """生成日报。"""
    print('\n=== 生成日报 ===\n')
    report = fc.daily_report(market='all')
    
    for m, data in report['markets'].items():
        print(f'\n【{m} 市场】 状态={data["market_state"]} VIX={data["vix_regime"]}')
        print(f'  总计: {data["total"]} | BUY: {data["buy"]} | SELL: {data["sell"]} | '
              f'HOLD: {data["hold"]} | BLOCKED: {data["blocked"]}')
        for r in data['results']:
            print_ticker_summary(r)
    
    print(f'\n【汇总】总BUY={report["summary"]["total_buy"]} '
          f'总SELL={report["summary"]["total_sell"]} '
          f'总BLOCKED={report["summary"]["total_blocked"]}')
    return report


def main():
    parser = setup_argparse()
    args = parser.parse_args()
    
    print_header()
    
    # 初始化
    fc = FusionController()
    
    # 确定命令
    if args.status:
        cmd_status(fc)
        return
    
    if args.single:
        result = cmd_single(fc, args.single, args)
        if args.output:
            _save_json(args.output, result, args.pretty)
        return
    
    if args.report:
        report = cmd_report(fc)
        if args.output:
            _save_json(args.output, report, args.pretty)
        return
    
    if args.market:
        results = cmd_scan(fc, args.market, args)
        if args.output:
            _save_json(args.output, results, args.pretty)
        return
    
    # 默认：显示帮助
    parser.print_help()
    print()
    print('提示: 可使用 --status 查看系统状态，或 --market HK 进行扫描')


def _save_json(path: str, data, pretty=False):
    """保存结果到 JSON 文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None,
                  default=str)
    print(f'\n💾 结果已保存: {path.resolve()}')


if __name__ == '__main__':
    main()
