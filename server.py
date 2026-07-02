from flask import Flask, request, jsonify
import alpaca_trade_api as tradeapi
import os
import logging
import time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

API_KEY = os.environ.get('ALPACA_API_KEY')
SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY')
BASE_URL = os.environ.get('ALPACA_BASE_URL')
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET')

api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')

last_signal_time = {}

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    
    if not data:
        return jsonify({'error': 'no data received'}), 415
    
    if data.get('secret') != WEBHOOK_SECRET:
        return jsonify({'error': 'unauthorized'}), 401
    
    action = data.get('action')
    symbol = data.get('symbol')
    
    if not action or not symbol:
        return jsonify({'error': 'missing fields'}), 400
    
    if symbol == '{{TICKER}}' or symbol == '{{ticker}}':
        return jsonify({'error': 'invalid symbol placeholder'}), 400
    
    # Deduplication — ignore same action on same symbol within 60 seconds
    signal_key = f"{symbol}_{action}"
    now = time.time()
    if signal_key in last_signal_time:
        if now - last_signal_time[signal_key] < 60:
            logging.info(f"Duplicate signal ignored: {action} {symbol}")
            return jsonify({'status': 'duplicate ignored'}), 200
    
    last_signal_time[signal_key] = now
    
    try:
        account = api.get_account()
        equity = float(account.equity)
        risk_amount = equity * 0.10
        
        price = float(api.get_latest_trade(symbol).price)
        qty = int(risk_amount / price)
        
        if qty < 1:
            return jsonify({'error': 'position size too small'}), 400
        
        if action == 'buy':
            api.submit_order(symbol=symbol, qty=qty, side='buy',
                           type='market', time_in_force='day')
            logging.info(f"BUY {qty} shares of {symbol}")
            
        elif action == 'sell':
            api.submit_order(symbol=symbol, qty=qty, side='sell',
                           type='market', time_in_force='day')
            logging.info(f"SELL {qty} shares of {symbol}")
            
        return jsonify({'status': 'order placed', 'qty': qty, 'symbol': symbol})
    
    except Exception as e:
        logging.error(f"Error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'running'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
