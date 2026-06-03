#!/usr/bin/env python3
"""Diagnose LLM factor scoring: fetch real data and run full pipeline."""
import sys, os
from pathlib import Path
_BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(_BASE / 'paper_trading'))

# Force reload the NVIDIA API key
os.environ['NVIDIA_API_KEY'] = 'REDACTED_NVIDIA_API_KEY'

from llm_factor_factory.factor_scorer import score_factors, factor_to_signal_score, get_factor_summary, FACTORS

# === Step 1: Get live data from daily_runner's fetch function ===
sys.path.insert(0, str(_BASE / 'paper_trading'))
from daily_runner import fetch_klines_futu

print("=== Fetching SPY data via Futu ===")
raw = fetch_klines_futu('SPY.US', 252)
if raw is None:
    print("❌ Futu fetch returned None")
    raw = fetch_klines_futu('SPY', 252)

import pandas as pd
import numpy as np

if raw is not None:
    print(f"✅ Futu data received. Keys: {list(raw.keys())}")
    df = pd.DataFrame(raw)
    # Rename timestamp → date for factor_scorer compatibility
    if 'timestamp' in df.columns:
        df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.drop(columns=['timestamp'], inplace=True)
else:
    print("⚠️ Using synthetic SPY-like data")
    np.random.seed(42)
    n = 252
    dates = pd.date_range(end='2026-05-18', periods=n, freq='D')
    close = 500 + np.cumsum(np.random.randn(n) * 2)
    df = pd.DataFrame({
        'date': dates,
        'close': close,
        'high': close * 1.02,
        'low': close * 0.98,
        'open': close * 0.99,
        'volume': np.random.randint(50_000_000, 100_000_000, n),
        'symbol': 'SPY',
    })

print(f"\nData shape: {df.shape}")
print(f"Date range: {df['date'].iloc[0]} → {df['date'].iloc[-1]}")
print(f"Close range: {df['close'].min():.2f} → {df['close'].max():.2f}")
print(f"Data columns: {list(df.columns)}")
print()

# === Step 2: Run factor scoring ===
print("=== Running factor scoring ===")
try:
    scores = score_factors(df, 'SPY')
    print(f"\nScore dict keys: {list(scores.keys())}")
    print(f"Number of factors scored: {len(scores)}")
    
    for fname, result in sorted(scores.items()):
        raw_val = result.get('raw', 'N/A')
        pct = result.get('percentile', 'N/A')
        note = result.get('note', '')
        print(f"  {fname:35s} raw={str(raw_val):>10s}  pct={str(pct):>4s}  note={note}")

    full_score = factor_to_signal_score(scores)
    summary = get_factor_summary(scores)
    
    print(f"\n=== Final Score: {full_score:.4f} ===")
    print(f"Summary: {summary}")
    
    # Interpret
    if abs(full_score) < 0.5:
        print("\n🔴 SCORE NEAR ZERO: Factors are all near-neutral (pct≈50)")
        print("   Technical reason: percentile-based scoring, most factors near 50th percentile in normal conditions")
    elif abs(full_score) < 5:
        print(f"\n🟡 MODERATE SCORE: {full_score:.2f} — some extreme factors detected")
    else:
        print(f"\n🟢 STRONG SCORE: {full_score:.2f} — extreme market conditions detected")
        
except Exception as e:
    import traceback
    print(f"\n❌ Error: {e}")
    traceback.print_exc()