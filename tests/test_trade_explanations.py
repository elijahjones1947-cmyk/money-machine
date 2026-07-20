"""
Tests for trade_explanations.py's explain_entry() -- pure function, no
Flask/DB/broker dependency, same reasoning as test_alerts.py for why
this imports and exercises the module directly.
"""

from backtest.strategy import DEFAULT_PARAMS
import trade_explanations as te


def _full_signal(**overrides):
    """A signal dict where every entry condition is True -- matches
    backtest.strategy.compute_signals()'s shape after the sub-condition
    enrichment."""
    base = {
        "ema_fast": 211.02, "ema_slow": 209.88, "rsi": 58.2,
        "recent_high": 210.50, "recent_low": 208.10,
        "breakout_price": 210.60,
        "buy_condition": True, "sell_signal": False,
        "trend_bullish": True, "trend_bearish": False,
        "higher_high_breakout": True, "higher_low": True, "rsi_ok": True,
    }
    base.update(overrides)
    return base


def test_full_long_entry_mentions_all_four_confirmed_conditions():
    text = te.explain_entry("buy", "AAPL", "stock", 212.34, _full_signal(), DEFAULT_PARAMS)
    assert text.startswith("Entered long:")
    assert "broke above" in text
    assert "confirms uptrend" in text
    assert "higher low" in text
    assert "supports momentum" in text


def test_long_entry_uses_the_actual_params_passed_in_not_defaults():
    custom_params = dict(DEFAULT_PARAMS)
    custom_params["lookback"] = 12
    custom_params["breakout_buffer_pct"] = 0.9
    text = te.explain_entry("buy", "AAPL", "stock", 212.34, _full_signal(), custom_params)
    assert "12-bar high" in text
    assert "0.9% buffer" in text


def test_long_entry_reports_unconfirmed_conditions_honestly():
    """A manual entry doesn't require compute_signals to agree -- if the
    breakout condition wasn't actually met, the explanation must say so,
    not silently omit it or claim it passed."""
    signal = _full_signal(higher_high_breakout=False)
    text = te.explain_entry("buy", "AAPL", "stock", 212.34, signal, DEFAULT_PARAMS, is_manual=True)
    assert "did NOT confirm a break" in text
    assert "(manual)" in text


def test_rsi_clause_omitted_when_rsi_filter_disabled():
    params = dict(DEFAULT_PARAMS)
    params["use_rsi_filter"] = False
    text = te.explain_entry("buy", "AAPL", "stock", 212.34, _full_signal(), params)
    assert "RSI" not in text


def test_entry_with_no_signal_data_returns_a_clear_fallback():
    text = te.explain_entry("buy", "AAPL", "stock", 212.34, None, DEFAULT_PARAMS)
    assert "no rationale generated" in text
    assert "212.34" in text


def test_short_entry_with_signal_is_a_factual_snapshot_not_a_rationale():
    text = te.explain_entry("sell", "EUR_USD", "forex", 1.0855, _full_signal(), DEFAULT_PARAMS, is_short=True)
    assert text.startswith("Entered short:")
    assert "aren't modeled by the Python strategy port" in text
    assert "EMA" in text


def test_short_entry_with_no_signal_still_notes_the_modeling_gap():
    text = te.explain_entry("sell", "EUR_USD", "forex", 1.0855, None, DEFAULT_PARAMS, is_short=True)
    assert "aren't modeled by the Python strategy port" in text


def test_price_formatting_matches_asset_class_precision():
    stock_text = te.explain_entry("buy", "AAPL", "stock", 212.3, None, DEFAULT_PARAMS)
    forex_text = te.explain_entry("buy", "EUR_USD", "forex", 1.0855321, None, DEFAULT_PARAMS)
    crypto_text = te.explain_entry("buy", "BTC/USD", "crypto", 61234.5, None, DEFAULT_PARAMS)
    assert "212.30" in stock_text
    assert "1.08553" in forex_text
    assert "61234.5000" in crypto_text


# --- detected_patterns enrichment (Phase 3) ------------------------------

def test_detected_candlestick_pattern_folded_into_the_explanation():
    patterns = {"candlestick_patterns": {"engulfing": "bullish"}}
    text = te.explain_entry("buy", "AAPL", "stock", 212.34, _full_signal(), DEFAULT_PARAMS,
                             detected_patterns=patterns)
    assert "bullish engulfing candle" in text


def test_detected_fibonacci_level_folded_into_the_explanation():
    patterns = {"fibonacci_level": {"name": "61.8%", "price": 208.5}}
    text = te.explain_entry("buy", "AAPL", "stock", 212.34, _full_signal(), DEFAULT_PARAMS,
                             detected_patterns=patterns)
    assert "61.8% Fibonacci retracement level" in text


def test_no_patterns_detected_does_not_add_a_clause():
    text_without = te.explain_entry("buy", "AAPL", "stock", 212.34, _full_signal(), DEFAULT_PARAMS)
    text_with_empty = te.explain_entry("buy", "AAPL", "stock", 212.34, _full_signal(), DEFAULT_PARAMS,
                                        detected_patterns={})
    assert text_without == text_with_empty


def test_patterns_still_shown_even_when_signal_is_none():
    patterns = {"candlestick_patterns": {"doji": "neutral"}}
    text = te.explain_entry("buy", "AAPL", "stock", 212.34, None, DEFAULT_PARAMS, detected_patterns=patterns)
    assert "no rationale generated" in text
    assert "doji candle" in text


def test_patterns_shown_on_short_entries_too():
    patterns = {"candlestick_patterns": {"pin_bar": "bearish"}}
    text = te.explain_entry("sell", "EUR_USD", "forex", 1.0855, _full_signal(), DEFAULT_PARAMS,
                             is_short=True, detected_patterns=patterns)
    assert "shooting star" in text
