"""Phase 6 — Self-contained HTML dashboard ("the CRM").

Turns a BacktestResult into a single theme-aware HTML page: KPI cards, an equity
curve and R-distribution drawn as inline SVG (crisp + light/dark aware, no baked
images), a per-session panel, and a sortable/filterable trades table where every
row carries its full decision reason.

The page is fully self-contained (inline CSS + JS, no external requests) so it
works opened as a local file *and* when published. ``build_html(standalone=True)``
wraps a complete document for local use; ``standalone=False`` emits body-only
content for embedding.
"""

from __future__ import annotations

import html
import json

import pandas as pd

from config import Config, DEFAULT_CONFIG
from arcanebot.backtest.engine import BacktestResult
from arcanebot.backtest.stats import per_session, summarize


# --------------------------------------------------------------------------- #
# Inline SVG charts (theme-aware via CSS custom properties)
# --------------------------------------------------------------------------- #
def _equity_svg(equity: pd.Series, start_equity: float) -> str:
    if equity.empty:
        return "<p class='empty'>No equity data.</p>"
    W, H, pad = 760, 240, 8
    vals = equity.to_numpy(dtype=float)
    n = len(vals)
    lo, hi = float(vals.min()), float(vals.max())
    lo = min(lo, start_equity)
    hi = max(hi, start_equity)
    span = (hi - lo) or 1.0

    def x(i):
        return pad + (W - 2 * pad) * (i / max(n - 1, 1))

    def y(v):
        return pad + (H - 2 * pad) * (1 - (v - lo) / span)

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    area = f"{pad},{y(lo):.1f} " + pts + f" {x(n-1):.1f},{y(lo):.1f}"
    base_y = y(start_equity)
    end_x, end_y = x(n - 1), y(vals[-1])
    up = vals[-1] >= start_equity
    cls = "up" if up else "down"
    return f"""
<svg viewBox="0 0 {W} {H}" class="chart equity {cls}" preserveAspectRatio="none" role="img" aria-label="Equity curve">
  <line x1="{pad}" y1="{base_y:.1f}" x2="{W-pad}" y2="{base_y:.1f}" class="baseline"/>
  <polygon points="{area}" class="area"/>
  <polyline points="{pts}" class="line"/>
  <circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="3.5" class="dot"/>
</svg>"""


def _r_hist_svg(rs: list[float]) -> str:
    if not rs:
        return "<p class='empty'>No trades.</p>"
    W, H, pad = 760, 200, 20
    lo = min(min(rs), -1.0)
    hi = max(max(rs), 1.0)
    bins = 24
    span = (hi - lo) or 1.0
    counts = [0] * bins
    for r in rs:
        b = min(int((r - lo) / span * bins), bins - 1)
        counts[b] += 1
    cmax = max(counts) or 1
    bw = (W - 2 * pad) / bins
    zero_x = pad + (W - 2 * pad) * (0 - lo) / span
    bars = []
    for i, c in enumerate(counts):
        if c == 0:
            continue
        bx = pad + i * bw
        bh = (H - 2 * pad) * (c / cmax)
        by = (H - pad) - bh
        bin_center = lo + (i + 0.5) / bins * span
        klass = "win" if bin_center > 0 else "loss"
        bars.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw-1.5:.1f}" height="{bh:.1f}" class="bar {klass}"/>'
        )
    return f"""
<svg viewBox="0 0 {W} {H}" class="chart hist" role="img" aria-label="Distribution of R multiples">
  {''.join(bars)}
  <line x1="{zero_x:.1f}" y1="{pad-6}" x2="{zero_x:.1f}" y2="{H-pad}" class="zero"/>
  <text x="{zero_x:.1f}" y="{pad-9}" class="axis-label" text-anchor="middle">0R</text>
</svg>"""


# --------------------------------------------------------------------------- #
# HTML assembly
# --------------------------------------------------------------------------- #
def _kpi_cards(s) -> str:
    def card(label, value, tone="", sub=""):
        sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
        return (
            f'<div class="kpi {tone}"><div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div>{sub_html}</div>'
        )

    if s.trades == 0:
        return card("Trades", "0", "", "no trades generated")

    exp_tone = "good" if s.expectancy_r > 0 else "bad"
    pf_tone = "good" if s.profit_factor >= 1 else "bad"
    pnl_tone = "good" if s.total_pnl >= 0 else "bad"
    flag = ""
    if s.expectancy_r > 2.0:
        flag = "&nbsp;⚠"
    shadow = ""
    if s.shadow_expectancy_r is not None:
        shadow = f"fixed-R: {s.shadow_expectancy_r:+.2f}R"
    return "".join([
        card("Trades", f"{s.trades}", "", f"{s.wins}W / {s.losses}L"),
        card("Win rate", f"{s.win_rate:.1%}"),
        card("Expectancy", f"{s.expectancy_r:+.2f}R{flag}", exp_tone, shadow),
        card("Profit factor", f"{s.profit_factor:.2f}", pf_tone),
        card("Total PnL", f"{s.total_pnl:+,.0f}", pnl_tone),
        card("Max drawdown", f"{s.max_drawdown_pct:.1%}", "bad" if s.max_drawdown < 0 else "",
             f"{s.max_drawdown:,.0f}"),
        card("Avg win / loss", f"{s.avg_win_r:+.2f} / {s.avg_loss_r:+.2f}R"),
    ])


def _session_rows(trades) -> str:
    ps = per_session(trades)
    if ps.empty:
        return '<tr><td colspan="4" class="empty">No trades.</td></tr>'
    rows = []
    for name, r in ps.iterrows():
        tone = "good" if r["expectancy_r"] > 0 else "bad"
        rows.append(
            f'<tr><td>{html.escape(str(name))}</td>'
            f'<td class="num">{int(r["trades"])}</td>'
            f'<td class="num">{r["win_rate"]:.0%}</td>'
            f'<td class="num {tone}">{r["expectancy_r"]:+.2f}R</td></tr>'
        )
    return "".join(rows)


def _config_chips(cfg: Config) -> str:
    chips = [
        ("risk", f"{cfg.risk.risk_per_trade_pct}%/trade"),
        ("daily stop", f"{cfg.risk.daily_loss_limit_pct}%"),
        ("spread", f"{cfg.costs.spread*100:.0f}c"),
        ("slippage", f"{cfg.costs.slippage*100:.0f}c"),
        ("TP", cfg.risk.tp_mode),
        ("swing N", str(cfg.structure.swing_n)),
        ("entry", cfg.data.entry_timeframe),
        ("structure", cfg.data.structure_timeframe),
    ]
    return "".join(
        f'<span class="chip"><span class="chip-k">{html.escape(k)}</span>'
        f'<span class="chip-v">{html.escape(v)}</span></span>'
        for k, v in chips
    )


def _trades_json(trades) -> str:
    rows = []
    for i, t in enumerate(trades, 1):
        rows.append({
            "n": i,
            "entry_time": str(t.entry_time),
            "exit_time": str(t.exit_time),
            "side": t.side,
            "session": t.session,
            "entry": round(t.entry_price, 2),
            "sl": round(t.stop_loss, 2),
            "tp": round(t.take_profit, 2),
            "exit": round(t.exit_price, 2),
            "lots": round(t.lots, 2),
            "r": round(t.r_multiple, 2),
            "pnl": round(t.pnl, 2),
            "result": t.exit_reason,
            "shadow_r": None if t.shadow_r_multiple is None else round(t.shadow_r_multiple, 2),
            "reason": t.reason,
        })
    return json.dumps(rows)


def build_html(result: BacktestResult, cfg: Config = DEFAULT_CONFIG,
               standalone: bool = True, title: str = "Arcanebot — backtest") -> str:
    s = summarize(result.trades, result.equity_curve)
    rs = [t.r_multiple for t in result.trades]
    start_equity = cfg.risk.starting_equity

    if len(result.equity_curve):
        d0 = str(result.equity_curve.index[0])[:16]
        d1 = str(result.equity_curve.index[-1])[:16]
        span = f"{d0} → {d1}"
    else:
        span = "—"

    body = f"""
<div class="wrap">
  <header class="topbar">
    <div class="brand">
      <span class="mark">◆</span>
      <div>
        <div class="brand-name">Arcanebot</div>
        <div class="brand-sub">{cfg.instrument.symbol} · ICT session-liquidity · backtest</div>
      </div>
    </div>
    <div class="range">{html.escape(span)}</div>
  </header>

  <section class="chips">{_config_chips(cfg)}</section>

  <section class="kpis">{_kpi_cards(s)}</section>

  <section class="panels">
    <div class="panel wide">
      <div class="panel-head"><h2>Equity curve</h2>
        <span class="panel-note">start {start_equity:,.0f}</span></div>
      {_equity_svg(result.equity_curve, start_equity)}
    </div>
    <div class="panel">
      <div class="panel-head"><h2>By session</h2></div>
      <table class="mini">
        <thead><tr><th>Session</th><th class="num">Trades</th>
          <th class="num">Win</th><th class="num">Exp</th></tr></thead>
        <tbody>{_session_rows(result.trades)}</tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <div class="panel-head"><h2>Distribution of R</h2>
      <span class="panel-note">green = winners · red = losers</span></div>
    {_r_hist_svg(rs)}
  </section>

  <section class="panel">
    <div class="panel-head">
      <h2>Trades</h2>
      <div class="filters">
        <input id="search" type="search" placeholder="search reason / level…" aria-label="Search trades"/>
        <select id="f-side" aria-label="Filter by side"><option value="">side: all</option>
          <option value="long">long</option><option value="short">short</option></select>
        <select id="f-session" aria-label="Filter by session"><option value="">session: all</option></select>
        <select id="f-result" aria-label="Filter by result"><option value="">result: all</option></select>
      </div>
    </div>
    <div class="table-scroll">
      <table id="trades" class="trades">
        <thead><tr>
          <th data-k="n" class="num">#</th>
          <th data-k="entry_time">Entry</th>
          <th data-k="side">Side</th>
          <th data-k="session">Session</th>
          <th data-k="entry" class="num">Entry</th>
          <th data-k="sl" class="num">SL</th>
          <th data-k="tp" class="num">TP</th>
          <th data-k="exit" class="num">Exit</th>
          <th data-k="lots" class="num">Lots</th>
          <th data-k="r" class="num">R</th>
          <th data-k="pnl" class="num">PnL</th>
          <th data-k="result">Result</th>
          <th data-k="reason">Reason</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div id="empty-note" class="empty" hidden>No trades match these filters.</div>
  </section>

  <footer class="foot">
    Generated by Arcanebot. Synthetic data has no edge — judge only on real
    IC Markets candles. Expectancy &gt; 2R is flagged as likely a bug.
  </footer>
</div>
<script>
const TRADES = {_trades_json(result.trades)};
</script>
<script>{_JS}</script>
"""

    if not standalone:
        return f"<style>{_CSS}</style>\n{body}"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


def write_dashboard(result: BacktestResult, path: str, cfg: Config = DEFAULT_CONFIG) -> str:
    with open(path, "w") as fh:
        fh.write(build_html(result, cfg, standalone=True))
    return path


# --------------------------------------------------------------------------- #
# CSS / JS (inlined)
# --------------------------------------------------------------------------- #
_CSS = """
:root{
  --bg:#f5f6f8; --panel:#ffffff; --panel-2:#f0f2f5; --border:#e3e6ec;
  --text:#18202a; --muted:#5b6674; --accent:#a9760a; --accent-soft:rgba(169,118,10,.12);
  --win:#1a7f37; --loss:#cf372e; --grid:rgba(20,30,45,.07);
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0e1319; --panel:#151c25; --panel-2:#1b232e; --border:#27313d;
  --text:#e6ebf1; --muted:#8b98a7; --accent:#e0b23c; --accent-soft:rgba(224,178,60,.14);
  --win:#3fb950; --loss:#f0574d; --grid:rgba(255,255,255,.07);
}}
:root[data-theme="dark"]{
  --bg:#0e1319; --panel:#151c25; --panel-2:#1b232e; --border:#27313d;
  --text:#e6ebf1; --muted:#8b98a7; --accent:#e0b23c; --accent-soft:rgba(224,178,60,.14);
  --win:#3fb950; --loss:#f0574d; --grid:rgba(255,255,255,.07);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right}
h2{margin:0;font-size:14px;font-weight:600;letter-spacing:.01em}
.topbar{display:flex;justify-content:space-between;align-items:center;
  padding-bottom:16px;border-bottom:1px solid var(--border);margin-bottom:16px;flex-wrap:wrap;gap:10px}
.brand{display:flex;align-items:center;gap:12px}
.mark{color:var(--accent);font-size:22px;line-height:1}
.brand-name{font-weight:700;font-size:18px;letter-spacing:.02em}
.brand-sub{color:var(--muted);font-size:12px}
.range{font-family:var(--mono);color:var(--muted);font-size:12.5px}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:18px}
.chip{display:inline-flex;gap:6px;align-items:baseline;background:var(--panel-2);
  border:1px solid var(--border);border-radius:6px;padding:3px 9px;font-size:12px}
.chip-k{color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-size:10px}
.chip-v{font-family:var(--mono)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:18px}
.kpi{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.kpi-label{color:var(--muted);text-transform:uppercase;letter-spacing:.07em;font-size:10.5px;margin-bottom:6px}
.kpi-value{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:22px;font-weight:600;letter-spacing:-.01em}
.kpi-sub{color:var(--muted);font-size:11px;font-family:var(--mono);margin-top:3px}
.kpi.good .kpi-value{color:var(--win)} .kpi.bad .kpi-value{color:var(--loss)}
.panels{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-bottom:14px}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin-bottom:14px}
.panel-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:10px;flex-wrap:wrap}
.panel-note{color:var(--muted);font-size:11.5px}
.chart{width:100%;height:auto;display:block}
.equity .area{fill:var(--accent-soft)}
.equity .line{fill:none;stroke:var(--accent);stroke-width:1.6;vector-effect:non-scaling-stroke}
.equity.down .line{stroke:var(--loss)} .equity.down .area{fill:rgba(240,87,77,.12)}
.equity .baseline{stroke:var(--grid);stroke-width:1;stroke-dasharray:3 3;vector-effect:non-scaling-stroke}
.equity .dot{fill:var(--accent)} .equity.down .dot{fill:var(--loss)}
.hist .bar.win{fill:var(--win)} .hist .bar.loss{fill:var(--loss)}
.hist .zero{stroke:var(--muted);stroke-width:1}
.axis-label{fill:var(--muted);font-family:var(--mono);font-size:10px}
table{border-collapse:collapse;width:100%}
.mini th,.mini td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border);font-size:13px}
.mini th{color:var(--muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.mini td.good{color:var(--win)} .mini td.bad{color:var(--loss)}
.filters{display:flex;gap:8px;flex-wrap:wrap}
.filters input,.filters select{background:var(--panel-2);border:1px solid var(--border);
  color:var(--text);border-radius:7px;padding:5px 9px;font-size:12.5px;font-family:var(--sans)}
.filters input{min-width:190px}
.table-scroll{overflow-x:auto;border:1px solid var(--border);border-radius:9px}
.trades{font-size:12.5px}
.trades th,.trades td{padding:7px 9px;border-bottom:1px solid var(--border);white-space:nowrap}
.trades th{position:sticky;top:0;background:var(--panel-2);color:var(--muted);font-weight:600;
  text-align:left;cursor:pointer;user-select:none;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.trades th.num{text-align:right}
.trades th:hover{color:var(--text)}
.trades tbody tr:hover{background:var(--panel-2)}
.trades td.reason{white-space:normal;min-width:280px;color:var(--muted);font-size:11.5px}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;font-weight:600;font-family:var(--mono)}
.pill.long{background:rgba(63,185,80,.15);color:var(--win)}
.pill.short{background:rgba(240,87,77,.15);color:var(--loss)}
.pill.res-TP{background:rgba(63,185,80,.15);color:var(--win)}
.pill.res-SL{background:rgba(240,87,77,.15);color:var(--loss)}
.pill.res-EOD{background:var(--panel-2);color:var(--muted)}
.r-pos{color:var(--win)} .r-neg{color:var(--loss)}
.sort-asc::after{content:" ▲";font-size:9px} .sort-desc::after{content:" ▼";font-size:9px}
.empty{color:var(--muted);text-align:center;padding:18px;font-size:13px}
.foot{color:var(--muted);font-size:11.5px;margin-top:24px;border-top:1px solid var(--border);padding-top:14px;line-height:1.6}
@media (max-width:820px){.panels{grid-template-columns:1fr}}
"""

_JS = """
(function(){
  const tbody=document.querySelector('#trades tbody');
  const emptyNote=document.getElementById('empty-note');
  const search=document.getElementById('search');
  const fSide=document.getElementById('f-side');
  const fSession=document.getElementById('f-session');
  const fResult=document.getElementById('f-result');
  let sortKey='n', sortDir=1;

  [...new Set(TRADES.map(t=>t.session))].sort().forEach(v=>{
    const o=document.createElement('option');o.value=v;o.textContent='session: '+v;fSession.appendChild(o);});
  [...new Set(TRADES.map(t=>t.result))].sort().forEach(v=>{
    const o=document.createElement('option');o.value=v;o.textContent='result: '+v;fResult.appendChild(o);});

  function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
  function fmt(v,d){return (v>=0?'':'')+v.toFixed(d);}

  function rowHTML(t){
    const rCls=t.r>=0?'r-pos':'r-neg';
    const pnlCls=t.pnl>=0?'r-pos':'r-neg';
    return `<tr>
      <td class="num">${t.n}</td>
      <td>${esc(t.entry_time.slice(5,16))}</td>
      <td><span class="pill ${t.side}">${t.side}</span></td>
      <td>${esc(t.session)}</td>
      <td class="num">${t.entry.toFixed(2)}</td>
      <td class="num">${t.sl.toFixed(2)}</td>
      <td class="num">${t.tp.toFixed(2)}</td>
      <td class="num">${t.exit.toFixed(2)}</td>
      <td class="num">${t.lots.toFixed(2)}</td>
      <td class="num ${rCls}">${fmt(t.r,2)}R</td>
      <td class="num ${pnlCls}">${fmt(t.pnl,2)}</td>
      <td><span class="pill res-${t.result}">${t.result}</span></td>
      <td class="reason" title="${esc(t.reason)}">${esc(t.reason)}</td>
    </tr>`;
  }

  function render(){
    const q=search.value.toLowerCase(), sd=fSide.value, ss=fSession.value, rs=fResult.value;
    let rows=TRADES.filter(t=>
      (!sd||t.side===sd)&&(!ss||t.session===ss)&&(!rs||t.result===rs)&&
      (!q||(t.reason.toLowerCase().includes(q)||t.side.includes(q)||t.session.toLowerCase().includes(q))));
    rows.sort((a,b)=>{const x=a[sortKey],y=b[sortKey];
      if(typeof x==='number'&&typeof y==='number')return (x-y)*sortDir;
      return String(x).localeCompare(String(y))*sortDir;});
    tbody.innerHTML=rows.map(rowHTML).join('');
    emptyNote.hidden=rows.length>0;
  }

  document.querySelectorAll('#trades th').forEach(th=>{
    th.addEventListener('click',()=>{
      const k=th.dataset.k;
      if(sortKey===k)sortDir*=-1;else{sortKey=k;sortDir=1;}
      document.querySelectorAll('#trades th').forEach(h=>h.classList.remove('sort-asc','sort-desc'));
      th.classList.add(sortDir>0?'sort-asc':'sort-desc');
      render();
    });
  });
  [search,fSide,fSession,fResult].forEach(el=>el.addEventListener('input',render));
  render();
})();
"""
