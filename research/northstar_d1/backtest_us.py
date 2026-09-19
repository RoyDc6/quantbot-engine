"""Independent United States backtest entry point."""

from .backtest import main_for_market


if __name__ == "__main__":
    raise SystemExit(main_for_market("US"))
