from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
import alpaca_trade_api as tradeapi
import os, logging, time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

API_KEY = os.environ.get('ALPACA_API_KEY')
SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY')
BASE_URL = os.environ.get('ALPACA_BASE_URL')
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET')
DASHBOARD_PASSWORD = os.environ.get('DASHBOARD_PASSWORD', 'Rentmachine123')
app.secret_key = os.environ.get('FLASK_SECRET', 'rentgenerator_secret_2024')

api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')

last_signal_time = {}
trade_log = []
equity_history = {'times': [], 'values': []}
watched_symbols = ['AAPL']
bot_enabled = True
max_trades_per_day = 20
daily_loss_limit = 500
risk_percent = 10
trades_today = 0

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
.status-pill{display:flex;align-items:center;gap:6px;background:#111;padding:8px 14px;border-radius:20px;font-size:13px}
.dot{width:8px;height:8px;border-radius:50%;background:#00d64f}
.dot.off{background:#ff4444}
.balance-section{text-align:center;margin-bottom:36px}
.balance-label{color:#555;font-size:14px;margin-bottom:6px}
.balance-amount{font-size:52px;font-weight:700;letter-spacing:-2px;color:#fff}
.balance-pnl{font-size:18px;margin-top:4px}
.balance-pnl.up{color:#00d64f}
.balance-pnl.down{color:#ff4444}
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
  <div class="logo">Rent Generator</div>
  <div class="status-pill">
    <div class="dot {{ '' if bot_enabled else 'off' }}"></div>
    <span>{{ 'Bot running' if bot_enabled else 'Bot paused' }}</span>
  </div>
</div>

<div class="balance-section">
  <div class="balance-label">Portfolio value</div>
  <div class="balance-amount">${{ account.equity }}</div>
  <div class="balance-pnl {{ 'up' if account.pnl_raw >= 0 else 'down' }}">
    {{ '+' if account.pnl_raw >= 0 else '' }}${{ account.pnl }} today
  </div>
</div>

<div class="grid3">
  <div class="card">
    <div class="card-label">Buying power</div>
    <div class="card-value white">${{ account.buying_power }}</div>
  </div>
  <div class="card">
    <div class="card-label">Trades today</div>
    <div class="card-value white">{{ trades_today }}</div>
  </div>
  <div class="card">
    <div class="card-label">Win rate</div>
    <div class="card-value {{ 'green' if win_rate >= 50 else 'red' }}">{{ win_rate }}%</div>
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

<div class="section-title">Current position</div>
{% if position %}
<div class="position-card">
  <div class="position-row"><span class="pos-label">Symbol</span><span class="pos-val" style="color:#00d64f">{{ position.symbol }}</span></div>
  <div class="position-row"><span class="pos-label">Shares</span><span class="pos-val">{{ position.qty }}</span></div>
  <div class="position-row"><span class="pos-label">Avg entry</span><span class="pos-val">${{ position.avg_entry }}</span></div>
  <div class="position-row"><span class="pos-label">Current price</span><span class="pos-val">${{ position.current_price }}</span></div>
  <div class="position-row">
    <span class="pos-label">Unrealized P&L</span>
    <span class="pos-val" style="color:{{ '#00d64f' if position.unrealized_pl >= 0 else '#ff4444' }}">${{ position.unrealized_pl }}</span>
  </div>
</div>
{% else %}
<div class="position-card position-empty">No open position</div>
{% endif %}

<div class="section-title">Equity curve</div>
<div class="chart-card">
  <canvas id="equityChart" height="80"></canvas>
</div>

<div class="section-title">Watchlist</div>
<div class="watchlist">
  {% for sym in watched_symbols %}
  <div class="watch-row">
    <span class="watch-sym">{{ sym }}</span>
    <span class="watch-price" id="price-{{ sym }}">—</span>
  </div>
  {% endfor %}
  {% if not watched_symbols %}
  <div class="empty">No symbols added</div>
  {% endif %}
  <div class="add-sym">
    <input type="text" id="newSym" placeholder="Add symbol (e.g. TSLA)" maxlength="5">
    <button onclick="addSymbol()">Add</button>
  </div>
</div>

<div class="section-title">Manual trade</div>
<div class="card">
  <input type="text" class="sym-input" id="manualSym" placeholder="Symbol (e.g. AAPL)">
  <div class="manual-btns">
    <button class="buy-btn" onclick="manualTrade('buy')">Buy</button>
    <button class="sell-btn" onclick="manualTrade('sell')">Sell</button>
  </div>
</div>

<div class="section-title">Bot controls</div>
<div class="controls">
  <div class="control-row">
    <div>
      <div class="control-label">Risk per trade</div>
      <div class="control-sub">% of account per signal</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <input type="range" min="1" max="50" value="{{ risk_percent }}" id="riskSlider" oninput="updateRisk(this.value)">
      <span class="range-val" id="riskVal">{{ risk_percent }}%</span>
    </div>
  </div>
  <div class="control-row">
    <div>
      <div class="control-label">Max trades per day</div>
      <div class="control-sub">Bot stops after this many</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <input type="range" min="1" max="50" value="{{ max_trades_per_day }}" id="maxTradesSlider" oninput="updateMaxTrades(this.value)">
      <span class="range-val" id="maxTradesVal">{{ max_trades_per_day }}</span>
    </div>
  </div>
  <div class="control-row">
    <div>
      <div class="control-label">Daily loss limit</div>
      <div class="control-sub">Bot stops if exceeded</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <input type="range" min="50" max="2000" step="50" value="{{ daily_loss_limit }}" id="lossSlider" oninput="updateLossLimit(this.value)">
      <span class="range-val" id="lossVal">${{ daily_loss_limit }}</span>
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
          <div class="trade-sym">{{ trade.symbol }}</div>
          <div class="trade-detail">{{ trade.qty }} shares @ ${{ trade.price }} · {{ trade.time }}</div>
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

function updateRisk(v){
  document.getElementById('riskVal').textContent = v+'%';
  fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({risk_percent:parseInt(v)})});
}

function updateMaxTrades(v){
  document.getElementById('maxTradesVal').textContent = v;
  fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_trades_per_day:parseInt(v)})});
}

function updateLossLimit(v){
  document.getElementById('lossVal').textContent = '$'+v;
  fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({daily_loss_limit:parseInt(v)})});
}

function toggleBot(){
  fetch('/toggle_bot',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    showToast(d.enabled ? 'Bot resumed' : 'Bot paused', d.enabled ? '#00d64f' : '#ff4444');
    setTimeout(function(){location.reload();}, 1000);
  });
}

function addSymbol(){
  const sym = document.getElementById('newSym').value.toUpperCase().trim();
  if(!sym) return;
  fetch('/watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:sym})})
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

@app.route('/login', methods=['GET','POST'])
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
    try:
        acct = api.get_account()
        equity = float(acct.equity)
        buying_power = float(acct.buying_power)
        last_equity = float(acct.last_equity)
        pnl_raw = round(equity - last_equity, 2)

        now = time.strftime('%H:%M')
        if not equity_history['times'] or equity_history['times'][-1] != now:
            equity_history['times'].append(now)
            equity_history['values'].append(round(equity, 2))
            if len(equity_history['times']) > 100:
                equity_history['times'].pop(0)
                equity_history['values'].pop(0)

        account_data = {
            'equity': '{:,.2f}'.format(equity),
            'buying_power': '{:,.2f}'.format(buying_power),
            'pnl': '{:,.2f}'.format(abs(pnl_raw)),
            'pnl_raw': pnl_raw
        }

        position = None
        try:
            positions = api.list_positions()
            if positions:
                p = positions[0]
                upl = float(p.unrealized_pl)
                position = {
                    'symbol': p.symbol,
                    'qty': p.qty,
                    'avg_entry': '{:.2f}'.format(float(p.avg_entry_price)),
                    'current_price': '{:.2f}'.format(float(p.current_price)),
                    'unrealized_pl': round(upl, 2)
                }
        except:
            pass

        completed = [t for t in trade_log if t.get('pnl') is not None]
        wins = [t for t in completed if t['pnl'] > 0]
        losses = [t for t in completed if t['pnl'] < 0]
        win_rate = round(len(wins) / len(completed) * 100) if completed else 0
        avg_gain = round(sum(t['pnl'] for t in wins) / len(wins), 2) if wins else 0
        avg_loss = round(abs(sum(t['pnl'] for t in losses) / len(losses)), 2) if losses else 0
        best_trade = round(max([t['pnl'] for t in wins] or [0]), 2)
        worst_trade = round(abs(min([t['pnl'] for t in losses] or [0])), 2)

        return render_template_string(DASHBOARD_HTML,
            account=account_data, position=position, trades=trade_log,
            eq_times=equity_history['times'], eq_values=equity_history['values'],
            watched_symbols=watched_symbols, bot_enabled=bot_enabled,
            risk_percent=risk_percent, max_trades_per_day=max_trades_per_day,
            daily_loss_limit=daily_loss_limit, trades_today=trades_today,
            win_rate=win_rate, avg_gain=avg_gain, avg_loss=avg_loss,
            best_trade=best_trade, worst_trade=worst_trade, ws=WEBHOOK_SECRET)
    except Exception as e:
        logging.error('Dashboard error: {}'.format(e))
        return 'Error: {}'.format(e), 500

@app.route('/toggle_bot', methods=['POST'])
def toggle_bot():
    global bot_enabled
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    bot_enabled = not bot_enabled
    return jsonify({'enabled': bot_enabled})

@app.route('/settings', methods=['POST'])
def settings():
    global risk_percent, max_trades_per_day, daily_loss_limit
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    data = request.json
    if 'risk_percent' in data:
        risk_percent = int(data['risk_percent'])
    if 'max_trades_per_day' in data:
        max_trades_per_day = int(data['max_trades_per_day'])
    if 'daily_loss_limit' in data:
        daily_loss_limit = int(data['daily_loss_limit'])
    return jsonify({'status': 'updated'})

@app.route('/watchlist', methods=['POST'])
def add_watchlist():
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    sym = request.json.get('symbol', '').upper()
    if sym and sym not in watched_symbols:
        watched_symbols.append(sym)
        return jsonify({'status': 'added'})
    return jsonify({'status': 'exists'})

@app.route('/webhook', methods=['POST'])
def webhook():
    global trades_today
    data = request.json
    if not data:
        return jsonify({'error': 'no data'}), 415
    if data.get('secret') != WEBHOOK_SECRET:
        return jsonify({'error': 'unauthorized'}), 401

    action = data.get('action')
    symbol = data.get('symbol')
    if not action or not symbol:
        return jsonify({'error': 'missing fields'}), 400
    if symbol in ('{{TICKER}}', '{{ticker}}'):
        return jsonify({'error': 'invalid symbol'}), 400

    is_manual = data.get('manual', False)

    if not bot_enabled and not is_manual:
        return jsonify({'error': 'bot paused'}), 400
    if trades_today >= max_trades_per_day and not is_manual:
        return jsonify({'error': 'max trades reached'}), 400

    daily_pnl = sum(t['pnl'] for t in trade_log if t.get('pnl') is not None)
    if daily_pnl <= -daily_loss_limit and not is_manual:
        return jsonify({'error': 'daily loss limit hit'}), 400

    signal_key = '{0}_{1}'.format(symbol, action)
    now = time.time()
    if not is_manual:
        if signal_key in last_signal_time:
            if now - last_signal_time[signal_key] < 60:
                return jsonify({'status': 'duplicate ignored'}), 200
    last_signal_time[signal_key] = now

    try:
        acct = api.get_account()
        equity = float(acct.equity)
        risk_amount = equity * (risk_percent / 100.0)
        price = float(api.get_latest_trade(symbol).price)
        qty = int(risk_amount / price)
        if qty < 1:
            return jsonify({'error': 'position too small'}), 400

        pnl = None
        if action == 'sell':
            last_buy = next((t for t in reversed(trade_log) if t['action'] == 'buy' and t['symbol'] == symbol), None)
            if last_buy:
                pnl = round((price - float(last_buy['price'])) * qty, 2)

        if action == 'buy':
            api.submit_order(symbol=symbol, qty=qty, side='buy', type='market', time_in_force='day')
            logging.info('BUY {} shares of {}'.format(qty, symbol))
        elif action == 'sell':
            api.submit_order(symbol=symbol, qty=qty, side='sell', type='market', time_in_force='day')
            logging.info('SELL {} shares of {}'.format(qty, symbol))

        trades_today += 1
        trade_log.append({
            'time': time.strftime('%H:%M:%S'),
            'action': action,
            'symbol': symbol,
            'qty': qty,
            'price': '{:.2f}'.format(price),
            'pnl': pnl
        })
        return jsonify({'status': 'order placed', 'qty': qty, 'symbol': symbol})
    except Exception as e:
        logging.error('Error: {}'.format(e))
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'running'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
