"""Independent Hong Kong deployment entry point."""

from .runner import main_for_market


if __name__ == "__main__":
    raise SystemExit(main_for_market("HK"))
