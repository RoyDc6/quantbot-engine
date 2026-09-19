from unified_runner import _fc_result_to_signal


def test_unified_signal_preserves_llm_audit_metadata():
    result = _fc_result_to_signal(
        {
            "ticker": "AAPL.US",
            "date": "2026-07-06",
            "close": 200.0,
            "sources": {
                "xmm": {},
                "vp": {},
                "llm": {
                    "sentiment_score": 40.0,
                    "event_type": "momentum_shift",
                    "event_summary": "technical audit",
                    "model": "fallback-model",
                },
            },
            "status": {"xmm": "OK", "vp": "OK", "llm": "OK"},
            "fusion": {
                "level": "HOLD",
                "score": 3.0,
                "confidence": 0.3,
                "weights_used": {"xmm": 0.6, "vp": 0.25, "llm": 0.0},
                "reserved_weights": {"llm": 0.15},
                "llm_alpha_mode": "audit_only",
                "llm_audit_score": 40.0,
                "normalization_denominator": 1.0,
                "raw_scores": {"xmm": 0.0, "vp": 20.0, "llm": 40.0},
            },
            "directive": {"level": "HOLD"},
            "gate": {"approved": True, "reject_reasons": []},
        },
        "US",
    )

    assert result["llm_alpha_mode"] == "audit_only"
    assert result["llm_audit_score"] == 40.0
    assert result["weights_used"]["llm"] == 0.0
    assert result["reserved_weights"]["llm"] == 0.15
    assert result["normalization_denominator"] == 1.0
    assert result["llm_model"] == "fallback-model"


def test_unified_signal_deduplicates_warnings_without_losing_order():
    result = _fc_result_to_signal(
        {
            "ticker": "00700.HK",
            "warnings": [
                "置信度 0.38 < 0.60",
                "综合置信度低",
                "LLM处于审计模式：原始分保留，alpha贡献为0",
            ],
            "fusion": {
                "level": "REDUCED",
                "warnings": [
                    "综合置信度低",
                    "LLM处于审计模式：原始分保留，alpha贡献为0",
                ],
            },
        },
        "HK",
    )

    assert result["warnings"] == [
        "置信度 0.38 < 0.60",
        "综合置信度低",
        "LLM处于审计模式：原始分保留，alpha贡献为0",
    ]
