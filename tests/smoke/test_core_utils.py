from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.utils import calc_atr, dict_json_safe, to_futu_code, to_standard_symbol


def test_symbol_conversion_round_trip():
    assert to_futu_code("00700.HK") == "HK.00700"
    assert to_futu_code("SPY.US") == "US.SPY"
    assert to_standard_symbol("HK.00700") == "00700.HK"
    assert to_standard_symbol("US.SPY") == "SPY.US"


def test_dict_json_safe_converts_numpy_scalars_and_arrays():
    raw = {
        "shares": np.int64(100),
        "score": np.float64(12.5),
        "passed": np.bool_(True),
        "series": np.array([1, 2, 3]),
    }

    safe = dict_json_safe(raw)

    assert safe == {
        "shares": 100,
        "score": 12.5,
        "passed": True,
        "series": [1, 2, 3],
    }


def test_calc_atr_returns_latest_atr_ratio():
    high = np.array([10, 11, 12, 13, 14, 15], dtype=float)
    low = np.array([9, 9.5, 10.5, 11.5, 12.5, 13.5], dtype=float)
    close = np.array([9.5, 10.5, 11.5, 12.5, 13.5, 14.5], dtype=float)

    atr_ratio = calc_atr(high, low, close, n=3)

    assert atr_ratio > 0
    assert atr_ratio < 1
