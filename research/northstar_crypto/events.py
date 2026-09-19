"""Optional timestamped LLM research observations; cannot alter positions."""
import json
from datetime import datetime
from urllib.parse import urlparse


def read_observations(path, asof_ms, symbols):
    if path is None:
        return {'status': 'NOT_CONFIGURED', 'position_effect': 0, 'accepted': [], 'rejected': []}
    accepted, rejected = [], []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            observed_dt = datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00'))
            published_dt = datetime.fromisoformat(row['published_at'].replace('Z', '+00:00'))
            if observed_dt.utcoffset() is None or published_dt.utcoffset() is None:
                raise ValueError('EVENT_TIMEZONE_REQUIRED')
            observed = int(observed_dt.timestamp() * 1000)
            published = int(published_dt.timestamp() * 1000)
            u = urlparse(row['source_url'])
            if not (published <= observed <= asof_ms):
                raise ValueError('EVENT_NOT_AVAILABLE_AT_CUTOFF')
            if u.scheme != 'https' or not u.netloc or u.username or u.password or u.query:
                raise ValueError('INVALID_SOURCE_URL')
            if not row.get('model') or not row.get('summary') or row['symbol'] not in symbols:
                raise ValueError('INVALID_EVENT_FIELDS')
            accepted.append({k: row[k] for k in ['observed_at', 'published_at', 'source_url', 'model', 'symbol', 'summary']})
        except (KeyError, ValueError, TypeError) as exc:
            rejected.append({'line': number, 'reason': str(exc)[:120]})
    return {'status': 'RECORDED' if accepted else 'NO_ELIGIBLE_OBSERVATIONS',
            'position_effect': 0, 'accepted': accepted, 'rejected': rejected}
