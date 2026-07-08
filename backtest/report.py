"""
Renders backtest_results.json (written by backtest.runner) as a simple
HTML report — the "results view" for the backtesting pipeline. Plain
inline-styled HTML so it drops straight into a Flask
render_template_string() call without needing new static assets.
"""

def _metric_row(label, m):
    if m is None or m["trade_count"] == 0:
        return "<tr><td>{}</td><td colspan='5' style='color:#888'>no trades</td></tr>".format(label)
    return (
        "<tr><td>{}</td><td>{}</td><td>{}%</td><td>{}%</td><td>{}</td><td>${}</td></tr>"
    ).format(
        label,
        m["trade_count"],
        m["win_rate_pct"],
        m["max_drawdown_pct"],
        m["sharpe_ratio"],
        m["total_pnl_abs"],
    )


def render_results_html(results, generated_at=None):
    """
    results: the list written to backtest_results.json by
    backtest.runner (one dict per symbol/asset-class run).
    """
    sections = []
    for r in results:
        metrics = r["metrics"]
        rows = [_metric_row("Overall", metrics["overall"])]
        for regime_name in sorted(metrics["by_regime"].keys()):
            rows.append(_metric_row(regime_name.capitalize(), metrics["by_regime"][regime_name]))

        sections.append("""
        <div class="card">
          <h3>{symbol} <span class="tag">{asset_class} / {timeframe}</span></h3>
          <p class="muted">{bar_count} bars simulated</p>
          <table>
            <tr><th>Regime</th><th>Trades</th><th>Win rate</th><th>Max drawdown</th><th>Sharpe</th><th>Total P&amp;L</th></tr>
            {rows}
          </table>
        </div>
        """.format(
            symbol=r["symbol"],
            asset_class=r["asset_class"],
            timeframe=r["timeframe"],
            bar_count=r["bar_count"],
            rows="".join(rows),
        ))

    return """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Backtest Results</title>
      <style>
        body {{ font-family: -apple-system, Arial, sans-serif; background:#0e0e12; color:#eee; padding:24px; }}
        h1 {{ font-size:20px; }}
        .muted {{ color:#888; font-size:13px; }}
        .tag {{ font-size:13px; color:#7ab8ff; font-weight:normal; }}
        .card {{ background:#1a1a20; border-radius:8px; padding:16px 20px; margin-bottom:20px; }}
        table {{ border-collapse: collapse; width:100%; margin-top:8px; }}
        th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid #2a2a32; font-size:14px; }}
        th {{ color:#aaa; font-weight:normal; }}
        a {{ color:#7ab8ff; }}
      </style>
    </head>
    <body>
      <h1>Backtest Results</h1>
      <p class="muted">Generated {generated_at} &middot; <a href="/dashboard">&larr; back to dashboard</a></p>
      {sections}
    </body>
    </html>
    """.format(generated_at=generated_at or "unknown", sections="".join(sections))
