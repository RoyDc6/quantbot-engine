from __future__ import annotations

import pytest

from hk_daily_selection.config import StrategyConfig
from hk_daily_selection.selector import run_selection
from hk_daily_selection.synthetic import make_synthetic_bundle


@pytest.fixture(scope="session")
def synthetic_bundle():
    return make_synthetic_bundle(StrategyConfig(), periods=180)


@pytest.fixture(scope="session")
def synthetic_selection(synthetic_bundle):
    bars, membership = synthetic_bundle
    return run_selection(bars, membership, StrategyConfig())
