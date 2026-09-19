"""Use pinned Northstar factor/decision rules under a separate Crypto contract."""
from .io_utils import digest, verify_vendor
from .market import DAY_MS
from .vendor.config import NorthstarD1Config
from .vendor.model import NorthstarD1Model
from .vendor import factors as f

VARIANTS = ('trend_only', 'trend_structure', 'northstar_full')


class Strategy:
    def __init__(self, config):
        self.vendor_hash = verify_vendor()
        self.config = config
        self.model = NorthstarD1Model(NorthstarD1Config())
        self.version = 'NORTHSTAR_CRYPTO_0.1.0:' + self.vendor_hash[:12]

    def frames(self, bars):
        cfg = self.model.config
        return (f.calc_dual_trend(bars, cfg.short_period, cfg.long_period),
                f.calc_structure_layer(bars, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal, cfg.structure_threshold),
                f.calc_td_sequence(bars['close'], cfg.td_period))

    def signal(self, bars, symbol, variant='northstar_full', frames=None, index=None):
        if variant not in VARIANTS:
            raise ValueError('Unknown strategy variant')
        end = len(bars) if index is None else index + 1
        if end < self.config.warmup:
            raise ValueError('Insufficient warmup')
        trend_frame, struct_frame, td_frame = frames if frames is not None else self.frames(bars.iloc[:end])
        trend = f.get_trend_state(trend_frame.iloc[:end])
        struct = f.get_structure_state(struct_frame.iloc[:end])
        td = f.get_td_state(td_frame.iloc[:end])
        if variant == 'trend_only':
            struct = {key: False if isinstance(value, bool) else 0 for key, value in struct.items()}
        if variant != 'northstar_full':
            td = {'td_count': 0, 'td_near': False, 'td_reached': False,
                  'is_buy_seq': False, 'is_sell_seq': False}
        candidate = self.model.decide(trend, struct, td)
        close_ms = int(bars.iloc[end - 1]['ts']) + DAY_MS
        event_id = digest([self.version, self.config.fingerprint(), variant, symbol, close_ms])
        return {'schema_version': 1, 'strategy_version': self.version, 'variant': variant,
                'event_id': event_id, 'symbol': symbol, 'signal_close_ms': close_ms,
                'data_status': 'VALID', 'signal_status': 'ELIGIBLE',
                'action': candidate['action'], 'raw_fraction': candidate['signal_fraction'],
                'confidence': candidate['confidence'], 'confidence_semantics': 'evidence_strength_not_probability',
                'reason': candidate['reason'], 'reason_codes': candidate['reason_codes'],
                'audit': candidate['decision_audit'],
                'llm': {'status': 'NOT_EVALUATED', 'position_effect': 0},
                'vp_enabled': False, 'execution_mode': 'LOCAL_PAPER_ONLY'}
