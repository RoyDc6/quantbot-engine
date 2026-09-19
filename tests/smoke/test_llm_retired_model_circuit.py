from types import SimpleNamespace
from market_state import event_detector as e


def test_retired_primary_without_fallback_is_requested_once_per_detector(monkeypatch):
    calls = []
    def chat(*args, **kwargs):
        calls.append(kwargs['model'])
        return '[ERROR 410] model reached end of life'
    monkeypatch.setattr(e, 'nvidia_llm', SimpleNamespace(chat=chat))
    detector = e.MarketEventDetector(model='retired', fallback_model='')
    for _ in range(3):
        reply, model, _ = detector._chat_with_fallback('sample')
        assert reply.startswith('[ERROR 410]')
        assert model == 'retired'
    assert calls == ['retired']
    e.MarketEventDetector(model='retired', fallback_model='')._chat_with_fallback('sample')
    assert calls == ['retired', 'retired']


def test_retired_primary_and_fallback_are_each_requested_once(monkeypatch):
    calls = []
    def chat(*args, **kwargs):
        calls.append(kwargs['model'])
        return '[ERROR 410] model reached end of life'
    monkeypatch.setattr(e, 'nvidia_llm', SimpleNamespace(chat=chat))
    detector = e.MarketEventDetector(model='primary', fallback_model='backup')
    for _ in range(3):
        reply, model, _ = detector._chat_with_fallback('sample')
        assert reply.startswith('[ERROR 410]')
        assert model == 'backup'
    assert calls == ['primary', 'backup']


def test_transient_error_without_fallback_is_not_permanently_cached(monkeypatch):
    replies = iter(['[ERROR 503] unavailable', '{"ok":true}'])
    monkeypatch.setattr(e, 'nvidia_llm', SimpleNamespace(chat=lambda *a, **k: next(replies)))
    detector = e.MarketEventDetector(model='primary', fallback_model='')
    assert detector._chat_with_fallback('sample')[0].startswith('[ERROR 503]')
    assert detector._chat_with_fallback('sample')[0] == '{"ok":true}'
