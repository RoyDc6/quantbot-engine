#!/usr/bin/env python3
"""
research/scripts/run_factor_study.py — 因子研究环境验证脚本

验证整个研究环境的 import 链、数据层、因子注册系统是否正常工作。
运行方式: python -m research.scripts.run_factor_study
"""

import sys
from pathlib import Path

# 确保 research 在路径中
RESEARCH_ROOT = Path(__file__).resolve().parent.parent  # E:\quant\research
QUANT_ROOT = RESEARCH_ROOT.parent  # E:\quant
for p in [str(RESEARCH_ROOT), str(QUANT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def verify_imports():
    """验证所有模块可导入"""
    print("=" * 60)
    print("📦 验证模块导入")
    print("=" * 60)

    modules = [
        "research.config",
        "research.data.base",
        "research.data.symbol_registry",
        "research.data.cache",
        "research.data.futu_provider",
        "research.data.okx_provider",
        "research.factors.base",
        "research.factors.registry",
    ]

    all_ok = True
    for mod_name in modules:
        try:
            __import__(mod_name)
            print(f"  ✅ {mod_name}")
        except Exception as e:
            print(f"  ❌ {mod_name}: {e}")
            all_ok = False

    return all_ok


def verify_data_layer():
    """验证数据层"""
    print("\n" + "=" * 60)
    print("📊 验证数据层")
    print("=" * 60)

    from research.data.symbol_registry import SymbolRegistry

    reg = SymbolRegistry()
    print(f"  符号总数: {len(reg.all)}")
    for m in reg.list_markets():
        syms = reg.get_symbols(m)
        print(f"  {m}: {len(syms)} 个标的")
        for s in syms[:3]:
            print(f"    - {s.symbol} ({s.name})")
        if len(syms) > 3:
            print(f"    ... 还有 {len(syms)-3} 个")

    # 验证 KLineData
    from research.data.base import KLineData
    import pandas as pd

    df = pd.DataFrame({
        "open": [100, 101], "high": [102, 103],
        "low": [99, 100], "close": [101, 102],
        "volume": [1000, 1200],
    })
    kdata = KLineData(symbol="SPY", market="US",
                      timeframe="1d", df=df)
    print(f"\n  KLineData 验证:")
    print(f"    symbol={kdata.symbol}, market={kdata.market}")
    print(f"    bars={kdata.n_bars}")

    return True


def verify_factor_registry():
    """验证因子注册系统"""
    print("\n" + "=" * 60)
    print("🧩 验证因子注册系统")
    print("=" * 60)

    from research.factors.registry import FactorRegistry, FactorMeta

    # 注册一个测试因子
    from research.factors.base import BaseFactor
    from research.data.base import KLineData
    import pandas as pd

    class TestMomentum(BaseFactor):
        @classmethod
        def meta(cls):
            return FactorMeta(
                name="test_momentum",
                category="technical",
                markets=["US", "HK", "CN", "CRYPTO"],
                frequencies=["1d"],
                description="测试动量因子",
                default_params={"window": 10},
            )

        def compute(self, data: KLineData, **params) -> pd.Series:
            window = params.get("window", 10)
            ret = data.df["close"].pct_change(window)
            return ret

    FactorRegistry.register(TestMomentum)
    print(f"  注册后因子总数: {FactorRegistry.count()}")

    # 列出因子
    all_factors = FactorRegistry.list_factors()
    print(f"  所有因子: {[m.name for m in all_factors]}")

    us_factors = FactorRegistry.list_factors(market="US")
    print(f"  美股可用: {[m.name for m in us_factors]}")

    tech_factors = FactorRegistry.list_factors(category="technical")
    print(f"  技术因子: {[m.name for m in tech_factors]}")

    # 计算测试
    df = pd.DataFrame({
        "open": range(100, 120), "high": range(101, 121),
        "low": range(99, 119), "close": range(100, 120),
        "volume": [1000] * 20,
    })
    kdata = KLineData(symbol="TEST", market="US",
                      timeframe="1d", df=df)

    result = FactorRegistry.compute("test_momentum", kdata, window=5)
    print(f"\n  计算 test_momentum(window=5):")
    print(f"    结果长度: {len(result) if result is not None else 0}")
    print(f"    最后 3 个值: {result.tail(3).tolist() if result is not None else 'N/A'}")

    return True


def verify_separation():
    """验证与执行层的分离"""
    print("\n" + "=" * 60)
    print("🔒 验证执行层分离")
    print("=" * 60)

    # 检查是否引用了执行层模块
    forbidden_imports = [
        "paper_trading", "unified_runner", "auto_trade",
        "fusion_framework", "signal_engine",
    ]

    all_clean = True
    for name in forbidden_imports:
        if name in sys.modules:
            print(f"  ⚠️  发现执行层模块: {name}")
            all_clean = False

    if all_clean:
        print("  ✅ 未发现执行层模块引用 — 研究层与执行层完全分离")

    return all_clean


def main():
    print("\n" + "=" * 60)
    print("🔬 QuantBot 因子研究环境 — 验证报告")
    print("=" * 60)

    results = {}

    results["imports"] = verify_imports()
    results["data"] = verify_data_layer()
    results["registry"] = verify_factor_registry()
    results["separation"] = verify_separation()

    # 汇总
    print("\n" + "=" * 60)
    print("📋 验证汇总")
    print("=" * 60)
    all_pass = True
    for name, ok in results.items():
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")
        if not ok:
            all_pass = False

    print()
    if all_pass:
        print("  🎉 所有验证通过！研究环境骨架搭建完成。")
    else:
        print("  ⚠️  部分验证失败，请检查上述错误。")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())