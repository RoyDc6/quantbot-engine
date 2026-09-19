"""Delivery-only report gate for Northstar-D1 HK."""

from __future__ import annotations

from .delivery_readiness import main_for_market


def main(argv=None) -> int:
    return main_for_market("HK", argv)


if __name__ == "__main__":
    raise SystemExit(main())
