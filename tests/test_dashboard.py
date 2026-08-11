"""Phase 6 tests — HTML dashboard generation."""

from config import DEFAULT_CONFIG
from arcanebot.backtest.engine import run_backtest
from arcanebot.data import resample
from arcanebot.reporting.dashboard import build_html
from tests.test_signal_engine import _fixture


def _result(make_frame):
    df = _fixture(make_frame)
    return run_backtest(df, resample(df, "15min"), DEFAULT_CONFIG), df


def test_dashboard_is_self_contained_and_populated(make_frame):
    result, _ = _result(make_frame)
    html = build_html(result, DEFAULT_CONFIG, standalone=True)

    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    # Self-contained: no external resource requests.
    assert "http://" not in html.replace("http://localhost", "") or "src=\"http" not in html
    assert "cdn" not in html.lower()
    # The one trade's reason is embedded in the trades JSON.
    assert result.trades[0].reason[:12] in html
    # KPI + chart scaffolding present.
    assert "kpi-value" in html and "<svg" in html


def test_body_only_variant_has_no_doctype(make_frame):
    result, _ = _result(make_frame)
    frag = build_html(result, DEFAULT_CONFIG, standalone=False)
    assert not frag.lstrip().startswith("<!doctype")
    assert "<style>" in frag and "<script>" in frag
