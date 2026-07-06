from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
import logging, time, datetime, math

import config
import state
from errors import InsufficientFundsError, MarketClosedError, InvalidSymbolError, BrokerConnectionError
from brokers.alpaca_broker import AlpacaBroker
from brokers.oanda_broker import OandaBroker
from risk.risk_manager import RiskManager

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
app.secret_key = config.FLASK_SECRET

WEBHOOK_SECRET = config.WEBHOOK_SECRET
DASHBOARD_PASSWORD = config.DASHBOARD_PASSWORD

# --- Broker + risk manager setup ---------------------------------------
alpaca_creds = config.get_broker_credentials("alpaca")
alpaca_broker = AlpacaBroker(
    api_key=alpaca_creds["api_key"],
    secret_key=alpaca_creds["api_secret"],
    base_url=alpaca_creds["base_url"],
)

oanda_creds = config.get_broker_credentials("oanda")
oanda_broker = OandaBroker(
    api_key=oanda_creds["api_key"],
    account_id=oanda_creds["account_id"],
    base_url=oanda_creds["base_url"],
)

BROKERS = {"stock": alpaca_broker, "forex": oanda_broker, "crypto": alpaca_broker}

risk_manager = RiskManager(config.get_risk_config())


def asset_class_for_symbol(symbol):
    """Crypto pairs use Alpaca's slash format, e.g. BTC/USD.
    Forex pairs use OANDA's underscore format, e.g. EUR_USD.
    Anything else (AAPL, TSLA, ...) is treated as a stock/Alpaca symbol."""
    if "/" in symbol:
        return "crypto"
    if "_" in symbol:
        return "forex"
    return "stock"


def get_combined_equity():
    """Best-effort combined equity across DISTINCT brokers. Stock and
    crypto share the same Alpaca account/equity, so we dedupe by broker
    identity here — otherwise Alpaca's balance would get counted twice
    and make the account-wide circuit breaker math wrong."""
    total = 0.0
    got_any = False
    seen_brokers = []
    for broker in BROKERS.values():
        if any(broker is seen for seen in seen_brokers):
            continue
        seen_brokers.append(broker)
        try:
            total += broker.get_account_info()["equity"]
            got_any = True
        except BrokerConnectionError as e:
            logging.warning("Could not fetch equity from a broker: {}".format(e))
    if not got_any:
        raise BrokerConnectionError("Could not reach either broker to compute combined equity")
    return total


def check_daily_rollover():
    """Reset daily counters (trades_today, risk_manager's daily P&L)
    the first time a request comes in on a new day."""
    today = datetime.date.today().isoformat()
    if state.current_day != today:
        state.current_day = today
        state.trades_today = {"stock": 0, "forex": 0, "crypto": 0}
        try:
            risk_manager.reset_daily(get_combined_equity())
        except BrokerConnectionError:
            risk_manager.reset_daily(None)


# --- HTML templates -------------------------------------------------------

LOGIN_HTML = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rent Generator</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#000;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",sans-serif}
.logo{color:#00d64f;font-size:32px;font-weight:700;letter-spacing:-1px;margin-bottom:8px}
.sub{color:#555;font-size:14px;margin-bottom:48px}
.box{width:320px}
input{width:100%;background:#111;border:1.5px solid #222;color:#fff;padding:16px;border-radius:14px;margin-bottom:12px;font-size:16px;outline:none;transition:border .2s}
input:focus{border-color:#00d64f}
button{width:100%;background:#00d64f;color:#000;border:none;padding:16px;border-radius:14px;font-size:16px;font-weight:700;cursor:pointer;transition:opacity .2s}
button:hover{opacity:.9}
.error{color:#ff4444;font-size:13px;text-align:center;margin-bottom:12px}
</style></head>
<body>
<div class="logo">Rent Generator</div>
<div class="sub">Trading Bot Dashboard</div>
<div class="box">
{% if error %}<div class="error">Wrong password. Try again.</div>{% endif %}
<form method="POST">
<input type="password" name="password" placeholder="Password" autofocus>
<button type="submit">Sign in</button>
</form>
</div>
</body></html>'''

DASHBOARD_HTML = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rent Generator</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#000;color:#fff;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",sans-serif;padding:24px;max-width:900px;margin:0 auto}
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px}
.logo{color:#00d64f;font-size:22px;font-weight:700;letter-spacing:-0.5px}
.mode-pill{display:inline-block;background:#222;color:#aaa;font-size:11px;padding:3px 8px;border-radius:10px;margin-left:8px;vertical-align:middle;text-transform:uppercase}
.mode-pill.live{background:#ff444422;color:#ff4444}
.status-pill{display:flex;align-items:center;gap:6px;background:#111;padding:8px 14px;border-radius:20px;font-size:13px}
.dot{width:8px;height:8px;border-radius:50%;background:#00d64f}
.dot.off{background:#ff4444}
.balance-section{text-align:center;margin-bottom:20px}
.balance-label{color:#555;font-size:14px;margin-bottom:6px}
.balance-amount{font-size:52px;font-weight:700;letter-spacing:-2px;color:#fff}
.balance-pnl{font-size:18px;margin-top:4px}
.balance-pnl.up{color:#00d64f}
.balance-pnl.down{color:#ff4444}
.asset-split{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:24px}
.asset-card{background:#111;border-radius:16px;padding:16px;text-align:center}
.asset-card .name{color:#555;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.asset-card .val{font-size:20px;font-weight:700}
.asset-card .halted{color:#ff4444;font-size:11px;margin-top:4px;font-weight:600}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:16px}
.stats-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}
.card{background:#111;border-radius:20px;padding:20px}
.card-label{color:#555;font-size:12px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
.card-value{font-size:24px;font-weight:700}
.card-value.green{color:#00d64f}
.card-value.red{color:#ff4444}
.card-value.white{color:#fff}
.section-title{color:#555;font-size:12px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px;margin-top:28px}
.position-card{background:#111;border-radius:20px;padding:20px;margin-bottom:16px;border:1.5px solid #00d64f22}
.position-empty{border-color:#222;color:#444;font-size:14px}
.position-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.pos-label{color:#555;font-size:13px}
.pos-val{font-size:16px;font-weight:600}
.chart-card{background:#111;border-radius:20px;padding:20px;margin-bottom:16px}
.trade-list{background:#111;border-radius:20px;overflow:hidden;margin-bottom:16px}
.trade-row{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid #1a1a1a}
.trade-row:last-child{border-bottom:none}
.trade-left{display:flex;align-items:center;gap:12px}
.badge{padding:4px 10px;border-radius:20px;font-size:11px;font-weight:700;text-transform:uppercase}
.badge.buy{background:#00d64f22;color:#00d64f}
.badge.sell{background:#ff444422;color:#ff4444}
.asset-badge{padding:3px 8px;border-radius:20px;font-size:10px;font-weight:700;text-transform:uppercase;background:#2a2a2a;color:#999}
.trade-sym{font-size:15px;font-weight:600}
.trade-detail{color:#555;font-size:12px;margin-top:2px}
.trade-pnl{font-size:15px;font-weight:700}
.trade-pnl.gain{color:#00d64f}
.trade-pnl.loss{color:#ff4444}
.trade-pnl.neutral{color:#555}
.controls{background:#111;border-radius:20px;padding:20px;margin-bottom:16px}
.control-row{display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid #1a1a1a}
.control-row:last-child{border-bottom:none}
.control-label{font-size:14px;color:#ccc}
.control-sub{font-size:12px;color:#555;margin-top:2px}
input[type=range]{-webkit-appearance:none;width:120px;height:4px;background:#222;border-radius:2px;outline:none}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;background:#00d64f;border-radius:50%;cursor:pointer}
.range-val{color:#00d64f;font-size:14px;font-weight:600;min-width:40px;text-align:right}
.kill-btn{background:#ff444420;color:#ff4444;border:1.5px solid #ff444440;padding:10px 20px;border-radius:12px;font-size:14px;font-weight:600;cursor:pointer;width:100%;margin-top:12px;transition:background .2s}
.kill-btn:hover{background:#ff444440}
.kill-btn.active{background:#00d64f20;color:#00d64f;border-color:#00d64f40}
.watchlist{background:#111;border-radius:20px;padding:20px;margin-bottom:16px}
.watch-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid #1a1a1a}
.watch-row:last-child{border-bottom:none}
.watch-sym{font-size:15px;font-weight:600}
.watch-price{color:#00d64f;font-size:15px;font-weight:600}
.add-sym{display:flex;gap:8px;margin-top:12px}
.add-sym input{flex:1;background:#1a1a1a;border:1.5px solid #222;color:#fff;padding:10px 14px;border-radius:12px;font-size:14px;outline:none}
.add-sym input:focus{border-color:#00d64f}
.add-sym button{background:#00d64f;color:#000;border:none;padding:10px 16px;border-radius:12px;font-size:14px;font-weight:700;cursor:pointer}
.manual-btns{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
.buy-btn{background:#00d64f20;color:#00d64f;border:1.5px solid #00d64f40;padding:12px;border-radius:14px;font-size:14px;font-weight:700;cursor:pointer;transition:background .2s}
.buy-btn:hover{background:#00d64f40}
.sell-btn{background:#ff444420;color:#ff4444;border:1.5px solid #ff444440;padding:12px;border-radius:14px;font-size:14px;font-weight:700;cursor:pointer;transition:background .2s}
.sell-btn:hover{background:#ff444440}
.sym-input{width:100%;background:#1a1a1a;border:1.5px solid #222;color:#fff;padding:10px 14px;border-radius:12px;font-size:14px;outline:none;margin-bottom:8px}
.sym-input:focus{border-color:#00d64f}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#00d64f;color:#000;padding:12px 24px;border-radius:14px;font-weight:700;font-size:14px;opacity:0;transition:opacity .3s;z-index:999}
.empty{color:#444;font-size:14px;padding:12px 0;text-align:center}
</style>
</head>
<body>
<div class="header">
  <div class="logo">Rent Generator <span class="mode-pill {{ 'live' if trading_mode == 'live' else '' }}">{{ trading_mode }}</span></div>
  <div class="status-pill">
    <div class="dot {{ '' if bot_enabled else 'off' }}"></div>
    <span>{{ 'Bot running' if bot_enabled else 'Bot paused' }}</span>
  </div>
</div>

<div class="balance-section">
  <div class="balance-label">Combined portfolio value</div>
  <div class="balance-amount">${{ combined_equity }}</div>
</div>

<div class="asset-split">
  <div class="asset-card">
    <div class="name">Stocks + Crypto (Alpaca)</div>
    <div class="val">${{ stock_account.equity }}</div>
    {% if stock_halted %}<div class="halted">⛔ stocks halted</div>{% endif %}
    {% if crypto_halted %}<div class="halted">⛔ crypto halted</div>{% endif %}
  </div>
  <div class="asset-card">
    <div class="name">Forex (OANDA)</div>
    <div class="val">${{ forex_account.equity }}</div>
    {% if forex_halted %}<div class="halted">⛔ halted today</div>{% endif %}
  </div>
</div>
<div style="text-align:center;color:#444;font-size:11px;margin-top:-16px;margin-bottom:20px">Stocks and crypto share one Alpaca balance — not two separate pools</div>

{% if account_halted %}
<div class="card" style="border:1.5px solid #ff444460;margin-bottom:16px;text-align:center;color:#ff4444;font-weight:700">
  🚨 ACCOUNT-WIDE HALT ACTIVE — all trading stopped for today
</div>
{% endif %}

<div class="grid3">
  <div class="card">
    <div class="card-label">Trades today</div>
    <div class="card-value white">{{ trades_today.stock + trades_today.forex + trades_today.crypto }}</div>
  </div>
  <div class="card">
    <div class="card-label">Win rate</div>
    <div class="card-value {{ 'green' if win_rate >= 50 else 'red' }}">{{ win_rate }}%</div>
  </div>
  <div class="card">
    <div class="card-label">Open positions</div>
    <div class="card-value white">{{ positions|length }}</div>
  </div>
</div>

<div class="stats-grid">
  <div class="card">
    <div class="card-label">Avg gain</div>
    <div class="card-value green">${{ avg_gain }}</div>
  </div>
  <div class="card">
    <div class="card-label">Avg loss</div>
    <div class="card-value red">-${{ avg_loss }}</div>
  </div>
  <div class="card">
    <div class="card-label">Best trade</div>
    <div class="card-value green">${{ best_trade }}</div>
  </div>
  <div class="card">
    <div class="card-label">Worst trade</div>
    <div class="card-value red">-${{ worst_trade }}</div>
  </div>
</div>

<div class="section-title">Open positions</div>
{% if positions %}
  {% for position in positions %}
  <div class="position-card">
    <div class="position-row"><span class="pos-label">Symbol</span><span class="pos-val" style="color:#00d64f">{{ position.symbol }} <span class="asset-badge">{{ position.asset_class }}</span></span></div>
    <div class="position-row"><span class="pos-label">Size</span><span class="pos-val">{{ position.qty }}</span></div>
    <div class="position-row"><span class="pos-label">Avg entry</span><span class="pos-val">${{ position.avg_entry }}</span></div>
    <div class="position-row"><span class="pos-label">Current price</span><span class="pos-val">${{ position.current_price }}</span></div>
    <div class="position-row">
      <span class="pos-label">Unrealized P&L</span>
      <span class="pos-val" style="color:{{ '#00d64f' if position.unrealized_pl >= 0 else '#ff4444' }}">${{ position.unrealized_pl }}</span>
    </div>
  </div>
  {% endfor %}
{% else %}
<div class="position-card position-empty">No open positions</div>
{% endif %}

<div class="section-title">Equity curve (combined)</div>
<div class="chart-card">
  <canvas id="equityChart" height="80"></canvas>
</div>

<div class="section-title">Watchlist — stocks</div>
<div class="watchlist">
  {% for sym in watched_symbols.stock %}
  <div class="watch-row"><span class="watch-sym">{{ sym }}</span><span class="watch-price" id="price-{{ sym }}">—</span></div>
  {% endfor %}
  {% if not watched_symbols.stock %}<div class="empty">No symbols added</div>{% endif %}
  <div class="add-sym">
    <input type="text" id="newStockSym" placeholder="Add stock symbol (e.g. TSLA)" maxlength="8">
    <button onclick="addSymbol('stock')">Add</button>
  </div>
</div>

<div class="section-title">Watchlist — forex</div>
<div class="watchlist">
  {% for sym in watched_symbols.forex %}
  <div class="watch-row"><span class="watch-sym">{{ sym }}</span><span class="watch-price" id="price-{{ sym }}">—</span></div>
  {% endfor %}
  {% if not watched_symbols.forex %}<div class="empty">No symbols added</div>{% endif %}
  <div class="add-sym">
    <input type="text" id="newForexSym" placeholder="Add forex pair (e.g. GBP_USD)" maxlength="8">
    <button onclick="addSymbol('forex')">Add</button>
  </div>
</div>

<div class="section-title">Watchlist — crypto</div>
<div class="watchlist">
  {% for sym in watched_symbols.crypto %}
  <div class="watch-row"><span class="watch-sym">{{ sym }}</span><span class="watch-price" id="price-{{ sym }}">—</span></div>
  {% endfor %}
  {% if not watched_symbols.crypto %}<div class="empty">No symbols added</div>{% endif %}
  <div class="add-sym">
    <input type="text" id="newCryptoSym" placeholder="Add crypto pair (e.g. ETH/USD)" maxlength="10">
    <button onclick="addSymbol('crypto')">Add</button>
  </div>
</div>

<div class="section-title">Manual trade</div>
<div class="card">
  <input type="text" class="sym-input" id="manualSym" placeholder="Symbol (e.g. AAPL, EUR_USD, or BTC/USD)">
  <div class="manual-btns">
    <button class="buy-btn" onclick="manualTrade('buy')">Buy</button>
    <button class="sell-btn" onclick="manualTrade('sell')">Sell</button>
  </div>
</div>

<div class="section-title">Bot controls</div>
<div class="controls">
  <div class="control-row">
    <div>
      <div class="control-label">Stock risk per trade</div>
      <div class="control-sub">% of combined equity per signal</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <input type="range" min="1" max="50" value="{{ risk_percent.stock }}" id="stockRiskSlider" oninput="updateRisk('stock', this.value)">
      <span class="range-val" id="stockRiskVal">{{ risk_percent.stock }}%</span>
    </div>
  </div>
  <div class="control-row">
    <div>
      <div class="control-label">Forex risk per trade</div>
      <div class="control-sub">% of combined equity per signal</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <input type="range" min="1" max="50" value="{{ risk_percent.forex }}" id="forexRiskSlider" oninput="updateRisk('forex', this.value)">
      <span class="range-val" id="forexRiskVal">{{ risk_percent.forex }}%</span>
    </div>
  </div>
  <div class="control-row">
    <div>
      <div class="control-label">Max stock trades/day</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <input type="range" min="1" max="50" value="{{ max_trades_per_day.stock }}" id="stockMaxSlider" oninput="updateMaxTrades('stock', this.value)">
      <span class="range-val" id="stockMaxVal">{{ max_trades_per_day.stock }}</span>
    </div>
  </div>
  <div class="control-row">
    <div>
      <div class="control-label">Max forex trades/day</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <input type="range" min="1" max="50" value="{{ max_trades_per_day.forex }}" id="forexMaxSlider" oninput="updateMaxTrades('forex', this.value)">
      <span class="range-val" id="forexMaxVal">{{ max_trades_per_day.forex }}</span>
    </div>
  </div>
  <div class="control-row">
    <div>
      <div class="control-label">Crypto risk per trade</div>
      <div class="control-sub">% of combined equity per signal (kept low — crypto is volatile)</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <input type="range" min="1" max="50" value="{{ risk_percent.crypto }}" id="cryptoRiskSlider" oninput="updateRisk('crypto', this.value)">
      <span class="range-val" id="cryptoRiskVal">{{ risk_percent.crypto }}%</span>
    </div>
  </div>
  <div class="control-row">
    <div>
      <div class="control-label">Max crypto trades/day</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <input type="range" min="1" max="50" value="{{ max_trades_per_day.crypto }}" id="cryptoMaxSlider" oninput="updateMaxTrades('crypto', this.value)">
      <span class="range-val" id="cryptoMaxVal">{{ max_trades_per_day.crypto }}</span>
    </div>
  </div>
  <button class="kill-btn {{ 'active' if not bot_enabled else '' }}" onclick="toggleBot()">
    {{ 'Resume bot' if not bot_enabled else 'Kill switch — pause all trading' }}
  </button>
</div>

<div class="section-title">Trade history</div>
<div class="trade-list">
  {% if trades %}
    {% for trade in trades|reverse %}
    <div class="trade-row">
      <div class="trade-left">
        <span class="badge {{ trade.action }}">{{ trade.action }}</span>
        <div>
          <div class="trade-sym">{{ trade.symbol }} <span class="asset-badge">{{ trade.asset_class }}</span></div>
          <div class="trade-detail">{{ trade.qty }} @ ${{ trade.price }} · {{ trade.time }}</div>
        </div>
      </div>
      <div class="trade-pnl {% if trade.pnl is not none %}{% if trade.pnl > 0 %}gain{% elif trade.pnl < 0 %}loss{% else %}neutral{% endif %}{% else %}neutral{% endif %}">
        {% if trade.pnl is not none %}
          {% if trade.pnl > 0 %}+${{ "%.2f" % trade.pnl }}{% elif trade.pnl < 0 %}-${{ "%.2f" % (trade.pnl * -1) }}{% else %}$0.00{% endif %}
        {% else %}—{% endif %}
      </div>
    </div>
    {% endfor %}
  {% else %}
  <div class="empty" style="padding:20px">No trades yet</div>
  {% endif %}
</div>

<div id="toast" class="toast"></div>

<script>
const labels = {{ eq_times|tojson }};
const data = {{ eq_values|tojson }};

if(data.length > 1){
  new Chart(document.getElementById('equityChart'), {
    type:'line',
    data:{labels:labels,datasets:[{label:'Equity',data:data,borderColor:'#00d64f',backgroundColor:'rgba(0,214,79,0.05)',borderWidth:2,pointRadius:2,tension:0.4,fill:true}]},
    options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#444',font:{size:11}},grid:{color:'#1a1a1a'}},y:{ticks:{color:'#444',font:{size:11},callback:v=>'$'+v.toFixed(0)},grid:{color:'#1a1a1a'}}}}
  });
} else {
  document.getElementById('equityChart').parentElement.innerHTML = '<div class="empty" style="padding:20px">Chart builds as trades come in</div>';
}

function showToast(msg, color){
  color = color || '#00d64f';
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = color;
  t.style.color = color === '#00d64f' ? '#000' : '#fff';
  t.style.opacity = '1';
  setTimeout(function(){t.style.opacity='0';}, 3000);
}

function updateRisk(assetClass, v){
  document.getElementById(assetClass+'RiskVal').textContent = v+'%';
  fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_class:assetClass, risk_percent:parseInt(v)})});
}

function updateMaxTrades(assetClass, v){
  document.getElementById(assetClass+'MaxVal').textContent = v;
  fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset_class:assetClass, max_trades_per_day:parseInt(v)})});
}

function toggleBot(){
  fetch('/toggle_bot',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    showToast(d.enabled ? 'Bot resumed' : 'Bot paused', d.enabled ? '#00d64f' : '#ff4444');
    setTimeout(function(){location.reload();}, 1000);
  });
}

function addSymbol(assetClass){
  const inputMap = {stock: 'newStockSym', forex: 'newForexSym', crypto: 'newCryptoSym'};
  const sym = document.getElementById(inputMap[assetClass]).value.toUpperCase().trim();
  if(!sym) return;
  fetch('/watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:sym, asset_class:assetClass})})
    .then(function(r){return r.json();}).then(function(d){
      showToast(d.status === 'added' ? sym+' added' : 'Already in watchlist');
      setTimeout(function(){location.reload();}, 1000);
    });
}

function manualTrade(action){
  const sym = document.getElementById('manualSym').value.toUpperCase().trim();
  if(!sym){showToast('Enter a symbol first','#ff4444');return;}
  if(!confirm('Place manual '+action.toUpperCase()+' for '+sym+'?')) return;
  fetch('/webhook',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({secret:'{{ ws }}',action:action,symbol:sym,manual:true})})
    .then(function(r){return r.json();}).then(function(d){
      if(d.status === 'order placed') showToast(action.toUpperCase()+' '+d.qty+' '+sym);
      else showToast(d.error || 'Error','#ff4444');
      setTimeout(function(){location.reload();}, 2000);
    });
}

setTimeout(function(){location.reload();}, 30000);
</script>
</body></html>'''


# --- Routes -----------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == DASHBOARD_PASSWORD:
            session['auth'] = True
            return redirect(url_for('dashboard'))
        return render_template_string(LOGIN_HTML, error=True)
    return render_template_string(LOGIN_HTML, error=False)


@app.route('/')
def index():
    return redirect(url_for('login'))


@app.route('/dashboard')
def dashboard():
    if not session.get('auth'):
        return redirect(url_for('login'))

    check_daily_rollover()

    try:
        stock_acct = alpaca_broker.get_account_info()
    except BrokerConnectionError as e:
        logging.error("Alpaca account fetch failed: {}".format(e))
        stock_acct = {"equity": 0.0, "buying_power": 0.0, "last_equity": 0.0}

    try:
        forex_acct = oanda_broker.get_account_info()
    except BrokerConnectionError as e:
        logging.error("OANDA account fetch failed: {}".format(e))
        forex_acct = {"equity": 0.0, "buying_power": 0.0, "last_equity": 0.0}

    combined_equity = stock_acct["equity"] + forex_acct["equity"]

    now = time.strftime('%H:%M')
    if not state.equity_history['times'] or state.equity_history['times'][-1] != now:
        state.equity_history['times'].append(now)
        state.equity_history['values'].append(round(combined_equity, 2))
        if len(state.equity_history['times']) > 100:
            state.equity_history['times'].pop(0)
            state.equity_history['values'].pop(0)

    positions = []
    try:
        for p in alpaca_broker.get_positions():
            # alpaca_broker.get_positions() returns BOTH stocks and crypto
            # (same account) — split them back out by symbol format so the
            # dashboard tags each correctly instead of lumping crypto in as 'stock'.
            ac = asset_class_for_symbol(p.symbol)
            price_fmt = '{:.4f}' if ac == 'crypto' else '{:.2f}'
            positions.append({
                'symbol': p.symbol, 'qty': p.qty, 'asset_class': ac,
                'avg_entry': price_fmt.format(float(p.avg_entry_price)),
                'current_price': price_fmt.format(float(p.current_price)),
                'unrealized_pl': round(float(p.unrealized_pl), 2),
            })
    except BrokerConnectionError:
        pass

    try:
        for p in oanda_broker.get_positions():
            long_units = float(p.get('long', {}).get('units', 0))
            short_units = float(p.get('short', {}).get('units', 0))
            units = long_units if long_units != 0 else short_units
            avg_price = p.get('long', {}).get('averagePrice') if long_units != 0 else p.get('short', {}).get('averagePrice')
            unrealized = float(p.get('long', {}).get('unrealizedPL', 0)) + float(p.get('short', {}).get('unrealizedPL', 0))
            positions.append({
                'symbol': p['instrument'], 'qty': units, 'asset_class': 'forex',
                'avg_entry': '{:.5f}'.format(float(avg_price or 0)),
                'current_price': '—',
                'unrealized_pl': round(unrealized, 2),
            })
    except BrokerConnectionError:
        pass

    completed = [t for t in state.trade_log if t.get('pnl') is not None]
    wins = [t for t in completed if t['pnl'] > 0]
    losses = [t for t in completed if t['pnl'] < 0]
    win_rate = round(len(wins) / len(completed) * 100) if completed else 0
    avg_gain = round(sum(t['pnl'] for t in wins) / len(wins), 2) if wins else 0
    avg_loss = round(abs(sum(t['pnl'] for t in losses) / len(losses)), 2) if losses else 0
    best_trade = round(max([t['pnl'] for t in wins] or [0]), 2)
    worst_trade = round(abs(min([t['pnl'] for t in losses] or [0])), 2)

    return render_template_string(
        DASHBOARD_HTML,
        trading_mode=config.TRADING_MODE,
        combined_equity='{:,.2f}'.format(combined_equity),
        stock_account={'equity': '{:,.2f}'.format(stock_acct['equity'])},
        forex_account={'equity': '{:,.2f}'.format(forex_acct['equity'])},
        stock_halted=risk_manager.trading_halted['stock'],
        forex_halted=risk_manager.trading_halted['forex'],
        crypto_halted=risk_manager.trading_halted['crypto'],
        account_halted=risk_manager.account_halted,
        positions=positions,
        trades=state.trade_log,
        eq_times=state.equity_history['times'], eq_values=state.equity_history['values'],
        watched_symbols=state.watched_symbols, bot_enabled=state.bot_enabled,
        risk_percent=state.risk_percent, max_trades_per_day=state.max_trades_per_day,
        trades_today=state.trades_today,
        win_rate=win_rate, avg_gain=avg_gain, avg_loss=avg_loss,
        best_trade=best_trade, worst_trade=worst_trade, ws=WEBHOOK_SECRET,
    )


@app.route('/toggle_bot', methods=['POST'])
def toggle_bot():
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    state.bot_enabled = not state.bot_enabled
    return jsonify({'enabled': state.bot_enabled})


@app.route('/settings', methods=['POST'])
def settings():
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    data = request.json or {}
    asset_class = data.get('asset_class')
    if asset_class not in ('stock', 'forex', 'crypto'):
        return jsonify({'error': 'asset_class must be stock, forex, or crypto'}), 400
    if 'risk_percent' in data:
        state.risk_percent[asset_class] = int(data['risk_percent'])
    if 'max_trades_per_day' in data:
        state.max_trades_per_day[asset_class] = int(data['max_trades_per_day'])
    return jsonify({'status': 'updated'})


@app.route('/watchlist', methods=['POST'])
def add_watchlist():
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    data = request.json or {}
    sym = data.get('symbol', '').upper()
    asset_class = data.get('asset_class') or asset_class_for_symbol(sym)
    if asset_class not in ('stock', 'forex', 'crypto'):
        return jsonify({'error': 'invalid asset_class'}), 400
    if sym and sym not in state.watched_symbols[asset_class]:
        state.watched_symbols[asset_class].append(sym)
        return jsonify({'status': 'added'})
    return jsonify({'status': 'exists'})


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return jsonify({'error': 'no data'}), 415
    if data.get('secret') != WEBHOOK_SECRET:
        return jsonify({'error': 'unauthorized'}), 401

    check_daily_rollover()

    action = data.get('action')
    symbol = data.get('symbol')
    if not action or not symbol:
        return jsonify({'error': 'missing fields'}), 400
    if symbol in ('{{TICKER}}', '{{ticker}}'):
        return jsonify({'error': 'invalid symbol'}), 400

    is_manual = data.get('manual', False)
    asset_class = asset_class_for_symbol(symbol)
    broker = BROKERS[asset_class]

    if not state.bot_enabled and not is_manual:
        return jsonify({'error': 'bot paused'}), 400
    if state.trades_today[asset_class] >= state.max_trades_per_day[asset_class] and not is_manual:
        return jsonify({'error': 'max {} trades reached for today'.format(asset_class)}), 400

    signal_key = '{0}_{1}'.format(symbol, action)
    now = time.time()
    if not is_manual:
        if signal_key in state.last_signal_time:
            if now - state.last_signal_time[signal_key] < 60:
                return jsonify({'status': 'duplicate ignored'}), 200
    state.last_signal_time[signal_key] = now

    try:
        account = broker.get_account_info()
        price = broker.get_price(symbol)
        risk_amount = account['equity'] * (state.risk_percent[asset_class] / 100.0)

        if asset_class == 'stock':
            size = int(risk_amount / price)
            if size < 1:
                return jsonify({'error': 'position too small'}), 400
        elif asset_class == 'crypto':
            # Crypto supports fractional quantities (Alpaca allows down to
            # 1e-6). Round DOWN (floor), not to-nearest — rounding up even
            # a fraction of a unit can push position_value a cent or two
            # over the risk manager's cap, causing a correctly-sized trade
            # to get rejected right at the boundary.
            size = math.floor((risk_amount / price) * 1_000_000) / 1_000_000
            if size <= 0:
                return jsonify({'error': 'position too small'}), 400
        else:
            # Simplified forex sizing: treat risk_amount as notional units.
            # This does NOT account for pip value or lot conventions properly yet —
            # revisit before trading real size on forex.
            size = int(risk_amount)
            if size < 1:
                return jsonify({'error': 'position too small'}), 400

        approved, reason = risk_manager.check_trade(broker, symbol, action, size, asset_class, price=price)
        if not approved:
            return jsonify({'error': reason}), 400

        pnl = None
        if action == 'sell':
            last_buy = next(
                (t for t in reversed(state.trade_log)
                 if t['action'] == 'buy' and t['symbol'] == symbol and t['asset_class'] == asset_class),
                None
            )
            if last_buy:
                pnl = round((price - float(last_buy['price'])) * size, 2)

        broker.place_order(symbol, action, size)
        logging.info('{} {} {} of {} ({})'.format(action.upper(), size, asset_class, symbol, config.TRADING_MODE))

        state.trades_today[asset_class] += 1
        state.trade_log.append({
            'time': time.strftime('%H:%M:%S'),
            'action': action,
            'symbol': symbol,
            'asset_class': asset_class,
            'qty': size,
            'price': (
                '{:.5f}'.format(price) if asset_class == 'forex'
                else '{:.4f}'.format(price) if asset_class == 'crypto'
                else '{:.2f}'.format(price)
            ),
            'pnl': pnl
        })

        if pnl is not None:
            try:
                risk_manager.record_trade_result(asset_class, pnl, get_combined_equity())
            except BrokerConnectionError:
                pass

        return jsonify({'status': 'order placed', 'qty': size, 'symbol': symbol, 'asset_class': asset_class})

    except InsufficientFundsError as e:
        return jsonify({'error': str(e)}), 400
    except MarketClosedError as e:
        return jsonify({'error': str(e)}), 400
    except InvalidSymbolError as e:
        return jsonify({'error': str(e)}), 400
    except BrokerConnectionError as e:
        logging.error('Broker error: {}'.format(e))
        return jsonify({'error': str(e)}), 502


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'running', 'mode': config.TRADING_MODE})


if __name__ == '__main__':
    import os
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
