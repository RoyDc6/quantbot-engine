from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math


@dataclass(frozen=True)
class Config:
    symbols: tuple = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "NEAR-USDT")
    bar: str = "1Dutc"
    history_limit: int = 300
    warmup: int = 120
    initial_cash: float | None = None  # Resolved from the full OKX demo account; no default cash fallback.
    single_cap: float = 0.20
    gross_cap: float = 0.60
    fee_bps: float = 10.0  # Research assumption, not the user's account fee tier.
    slippage_bps: float = 5.0
    quote_max_age_seconds: int = 60
    max_clock_skew_seconds: int = 120

    def __post_init__(self):
        values = (self.single_cap, self.gross_cap, self.fee_bps, self.slippage_bps)
        if self.initial_cash is not None:
            values += (self.initial_cash,)
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Nonfinite research configuration')
        if not (0 < self.single_cap <= self.gross_cap <= 1):
            raise ValueError("Invalid long-only exposure limits")
        if (self.initial_cash is not None and self.initial_cash <= 0) or not (120 <= self.history_limit <= 300):
            raise ValueError("Invalid initial cash or history limit")
        if self.fee_bps < 0 or self.slippage_bps < 0:
            raise ValueError("Negative costs")
        if self.bar != "1Dutc" or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Only unique UTC daily spot instruments are supported")
        if any(not s.endswith('-USDT') or s.endswith('-SWAP') for s in self.symbols):
            raise ValueError("Only USDT spot instruments are supported")

    def payload(self):
        return asdict(self)

    def fingerprint(self):
        return sha256(json.dumps(self.payload(), sort_keys=True).encode()).hexdigest()
