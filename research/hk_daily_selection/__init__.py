"""Hong Kong daily stock-selection research prototype.

This package is intentionally isolated from QuantBot's production signal,
account, order, and scheduler paths.
"""

from .config import StrategyConfig

__all__ = ["StrategyConfig"]
__version__ = "0.1.0"
