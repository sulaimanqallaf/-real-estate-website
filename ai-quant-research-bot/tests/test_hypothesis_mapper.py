"""src/research/hypothesis_mapper.py - maps a Paper into falsifiable
ResearchHypothesis objects (Sprint 4, Priority 2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.research import hypothesis_mapper as hm
from src.research.papers import Paper


def _paper(**overrides):
    defaults = dict(
        title="Momentum Anomaly in Equity Returns", authors=("A. Author",), year=2020, venue="JF", citation_count=10,
        abstract="We find a momentum breakout effect.", url="https://example.com/1", external_ids={"doi": "10.1/abc"}, source="openalex",
    )
    defaults.update(overrides)
    return Paper(**defaults)


def test_generate_hypotheses_returns_empty_for_unrelated_paper():
    paper = _paper(title="A Survey of Macroeconomic Models", abstract="Inflation and interest rates.")
    assert hm.generate_hypotheses(paper, ["SPY"]) == []


def test_generate_hypotheses_returns_empty_without_symbols():
    paper = _paper()
    assert hm.generate_hypotheses(paper, []) == []


def test_generate_hypotheses_one_per_matched_strategy_and_symbol():
    paper = _paper(title="Momentum and Breakout Effects", abstract="A study of price momentum.")
    hypotheses = hm.generate_hypotheses(paper, ["SPY", "QQQ"])

    assert len(hypotheses) == 2
    assert {h.symbol for h in hypotheses} == {"SPY", "QQQ"}
    assert all(h.strategy_name == "Momentum Breakout" for h in hypotheses)
    assert all(h.data_provenance == "real_market_data" for h in hypotheses)


def test_generate_hypotheses_includes_a_falsifiable_description():
    paper = _paper(title="Momentum Breakout Effects")
    hypothesis = hm.generate_hypotheses(paper, ["SPY"])[0]
    assert "Falsified if" in hypothesis.description
    assert "SPY" in hypothesis.description
    assert paper.title in hypothesis.description


def test_generate_hypotheses_extracts_a_window_for_mean_reversion():
    paper = _paper(title="Mean Reversion", abstract="Using a 10-day moving average filter.")
    hypothesis = hm.generate_hypotheses(paper, ["SPY"])[0]
    assert hypothesis.config_overrides == {"strategies": {"mean_reversion": {"ma_window": 10}}}


def test_generate_hypotheses_extracts_a_window_for_momentum_breakout():
    paper = _paper(title="Momentum Breakout", abstract="A 30-day breakout signal.")
    hypothesis = hm.generate_hypotheses(paper, ["SPY"])[0]
    assert hypothesis.config_overrides == {"strategies": {"momentum_breakout": {"breakout_window": 30}}}


def test_generate_hypotheses_no_override_without_a_window_mention():
    paper = _paper(title="Mean Reversion", abstract="A general study of reversal effects.")
    hypothesis = hm.generate_hypotheses(paper, ["SPY"])[0]
    assert hypothesis.config_overrides == {}


def test_generate_hypotheses_ignores_an_out_of_range_window():
    paper = _paper(title="Mean Reversion", abstract="A 1-day effect and a 999-day effect.")
    hypothesis = hm.generate_hypotheses(paper, ["SPY"])[0]
    assert hypothesis.config_overrides == {}


def test_generate_hypotheses_no_override_for_trend_following_even_with_a_window():
    paper = _paper(title="Trend Following", abstract="A 50-day trend-following strategy.")
    hypotheses = hm.generate_hypotheses(paper, ["SPY"])
    trend_hyps = [h for h in hypotheses if h.strategy_name == "Trend Following"]
    assert trend_hyps
    assert all(h.config_overrides == {} for h in trend_hyps)


def test_generate_hypotheses_ids_are_deterministic_and_traceable():
    paper = _paper(title="Momentum Breakout", external_ids={"doi": "10.1/XYZ"})
    h1 = hm.generate_hypotheses(paper, ["SPY"])[0]
    h2 = hm.generate_hypotheses(paper, ["SPY"])[0]
    assert h1.hypothesis_id == h2.hypothesis_id
    assert "spy" in h1.hypothesis_id.lower()


def test_generate_hypotheses_ids_differ_by_symbol():
    paper = _paper(title="Momentum Breakout")
    hypotheses = hm.generate_hypotheses(paper, ["SPY", "QQQ"])
    assert hypotheses[0].hypothesis_id != hypotheses[1].hypothesis_id


def test_paper_hypothesis_key_is_filesystem_and_sql_safe():
    paper = _paper(external_ids={"doi": "10.1/ABC-123"})
    key = hm.paper_hypothesis_key(paper)
    assert key == key.lower()
    assert all(c.isalnum() or c == "_" for c in key)
