# PolyTrade/app.py

import os
import json
import time
import threading
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from functools import wraps
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from utils.db import init_db, connect, now, to_dict
from utils.crypto import enc_str, dec_str
from utils.binance import BinanceUM
from cryptography.fernet import InvalidToken
import websocket

# --- Initialization ---
load_dotenv()
init_db()
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24))
# --- FIX: Removed async_mode='threading' to allow eventlet ---
socketio = SocketIO(app, cors_allowed_origins='*')

# --- Global WebSocket Worker Management ---
worker_thread = None
worker_stop_event = threading.Event()

# --- Upgraded Password Protection with Hashing ---
APP_PASSWORD_HASH = generate_password_hash(os.environ.get('APP_PASSWORD', 'tradebot'))

# Helper function to stop/start the ROI worker and refresh positions (Fixes Bug 1 & 2)
def _update_positions_and_worker(account_id, wait_time=1.0):
    """
    Stops the existing ROI worker, waits, starts a new worker for new symbols,
    and requests initial positions for UI refresh.
    """
    global worker_thread, worker_stop_event
    
    # 1. Stop the current worker
    if worker_thread and worker_thread.is_alive():
        worker_stop_event.set()
        worker_thread.join()
        
    # Wait for the external API (Binance) to update position status
    if wait_time > 0:
        time.sleep(wait_time)
        
    # 2. Restart the worker
    worker_stop_event.clear()
    worker_thread = threading.Thread(target=roi_websocket_worker, args=(account_id,))
    worker_thread.daemon = True
    worker_thread.start()
    
    # 3. Request initial positions for immediate UI update
    handle_initial_positions({'account_id': account_id})

# Helper function to update all active accounts' balances (Part of Bug 3 Fix)
def _update_account_balances():
    """Fetches and updates the balance for all active accounts."""
    accounts = list_accounts()
    updated_list = []
    for acc in accounts:
        # Only try to update active accounts
        if acc['active'] and acc['id']:
            try:
                bn = safe_get_client(acc)
                latest_balance = bn.futures_balance()
                with connect() as con:
                    con.cursor().execute('UPDATE accounts SET futures_balance=?, updated_at=? WHERE id=?', 
                                         (latest_balance, now(), acc['id']))
                    con.commit()
                # Update the in-memory dictionary for immediate return
                acc['futures_balance'] = latest_balance 
            except Exception as e:
                print(f"Error updating balance for account {acc.get('name')} ({acc['id']}): {e}")
        updated_list.append(acc)
        
    return updated_list


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if check_password_hash(APP_PASSWORD_HASH, password):
            session['logged_in'] = True
            session.permanent = True
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid password!', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

#<editor-fold desc="Helper Functions">
def get_account(acc_id):
    with connect() as con:
        return to_dict(con.cursor().execute('SELECT * FROM accounts WHERE id=?', (acc_id,)).fetchone())

def safe_get_client(acc):
    try:
        api_key = dec_str(acc['api_key_enc'])
        api_secret = dec_str(acc['api_secret_enc'])
    except InvalidToken:
        raise RuntimeError("Encryption key mismatch.")
    return BinanceUM(api_key, api_secret, bool(acc['testnet']))

def list_accounts():
    with connect() as con:
        return [to_dict(r) for r in con.cursor().execute('SELECT * FROM accounts ORDER BY id DESC').fetchall()]
#</editor-fold>

#<editor-fold desc="UI Routes">
@app.route('/')
@login_required
def home(): return redirect(url_for('dashboard'))

@app.route('/account')
@login_required
def account():
    return render_template('account.html', accounts_json=json.dumps(list_accounts()))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', accounts=list_accounts())
#</editor-fold>

#<editor-fold desc="API Routes">
@app.route('/accounts/add', methods=['POST'])
def accounts_add():
    data = request.get_json(force=True)
    name, api_key, api_secret = data.get('name','').strip(), data.get('api_key','').strip(), data.get('api_secret','').strip()
    testnet = 1 if data.get('testnet') else 0
    if not all([name, api_key, api_secret]): return jsonify({'error': 'Missing fields'}), 400
    try:
        bn = BinanceUM(api_key, api_secret, bool(testnet))
        balance = bn.futures_balance()
        bn.set_hedge_mode() # Always enable hedge mode
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    with connect() as con:
        cur = con.cursor()
        cur.execute('INSERT INTO accounts (name,exchange,api_key_enc,api_secret_enc,testnet,active,futures_balance,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (name, 'BINANCE_UM', enc_str(api_key), enc_str(api_secret), testnet, 1, balance, now(), now()))
        con.commit()
    return jsonify({'ok': True, 'accounts': list_accounts()})

@app.route('/accounts/delete/<int:acc_id>', methods=['POST'])
def accounts_delete(acc_id):
    with connect() as con:
        con.cursor().execute('DELETE FROM accounts WHERE id=?', (acc_id,))
        con.commit()
    return jsonify({'ok': True, 'accounts': list_accounts()})

# NEW ROUTE (Used by account.js for toggle functionality)
@app.route('/accounts/toggle/<int:acc_id>', methods=['POST'])
@login_required
def accounts_toggle(acc_id):
    with connect() as con:
        cur = con.cursor()
        r = cur.execute('SELECT active FROM accounts WHERE id=?', (acc_id,)).fetchone()
        if r:
            new_status = 1 if r['active'] == 0 else 0
            cur.execute('UPDATE accounts SET active=?, updated_at=? WHERE id=?', (new_status, now(), acc_id))
            con.commit()
            return jsonify({'ok': True, 'status': new_status})
        return jsonify({'error': 'Account not found'}), 404

# NEW ROUTE (Fixes Bug 3)
@app.route('/accounts/update_balances', methods=['POST'])
@login_required
def accounts_update_balances():
    try:
        updated_accounts = _update_account_balances()
        return jsonify({'ok': True, 'accounts': updated_accounts})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/symbol-info')
def symbol_info():
    symbol = (request.args.get('symbol') or '').upper().strip()
    if not symbol: return jsonify({'error': 'symbol required'}), 400
    try:
        bn = BinanceUM('', '', False)
        lot, min_notional = bn.symbol_filters(symbol)
        return jsonify({'symbol': symbol, 'min_notional': min_notional or 0, 'lot': lot or {}})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/futures/symbols')
def futures_symbols():
    try:
        bn = BinanceUM('', '', False)
        info = bn.exchange_info()
        symbols = [s['symbol'] for s in info.get('symbols', []) if s.get('quoteAsset') == 'USDT' and s.get('status') == 'TRADING']
        return jsonify({'symbols': symbols})
    except Exception as e: return jsonify({'symbols': [], 'error': str(e)}), 500

@app.route('/api/price')
def get_price():
    symbol = (request.args.get('symbol') or '').upper().strip()
    if not symbol: return jsonify({'error': 'symbol required'}), 400
    try:
        bn = BinanceUM('', '', False)
        return jsonify(bn.price(symbol))
    except Exception as e: return jsonify({'error': str(e)}), 500

# Template APIs
@app.route('/api/templates/save', methods=['POST'])
def tpl_save():
    data = request.get_json(force=True)
    name, settings = data.get('name', '').strip(), data.get('settings', {})
    if not name or not settings: return jsonify({'error': 'Template name and settings are required'}), 400
    with connect() as con:
        con.cursor().execute('INSERT INTO templates (name, settings_json, created_at) VALUES (?,?,?)', (name, json.dumps(settings), now()))
        con.commit()
    return jsonify({'ok': True})

@app.route('/api/templates/list')
def tpl_list():
    with connect() as con:
        templates = [to_dict(r) for r in con.cursor().execute('SELECT id, name, created_at FROM templates ORDER BY created_at DESC').fetchall()]
    return jsonify({'items': templates})

@app.route('/api/templates/get/<int:tpl_id>')
def tpl_get(tpl_id):
    with connect() as con:
        r = con.cursor().execute('SELECT settings_json FROM templates WHERE id=?', (tpl_id,)).fetchone()
    if not r: return jsonify({'error': 'Template not found'}), 404
    return jsonify(json.loads(r['settings_json']))

@app.route('/api/templates/delete/<int:tpl_id>', methods=['POST'])
def tpl_delete(tpl_id):
    with connect() as con:
        con.cursor().execute('DELETE FROM templates WHERE id=?', (tpl_id,))
        con.commit()
    return jsonify({'ok': True})

# Trade Execution APIs
@app.route('/api/trades/submit', methods=['POST'])
@login_required
def trades_submit():
    data = request.get_json(force=True)
    account_id, bot_name, coins = data.get('account_id'), data.get('bot_name', '').strip(), data.get('coins', [])
    if not all([account_id, bot_name, coins]): return jsonify({'error': 'Missing required fields.'}), 400
    
    acc = get_account(account_id)
    if not acc or not acc['active']: return jsonify({'error': 'Account is not active or not found'}), 400
    
    try:
        bn = safe_get_client(acc)
    except RuntimeError as e: return jsonify({'error': str(e)}), 400

    successful_trades = []
    try:
        for coin in coins:
            symbol, side, leverage, amount_usdt, margin_type = coin['symbol'], coin['side'].upper(), int(coin['leverage']), float(coin['margin']), coin['margin_mode'].upper()
            bn.set_leverage(symbol, leverage)
            bn.set_margin_type(symbol, margin_type)
            price = float(bn.price(symbol)['price'])
            qty = bn.round_lot_size(symbol, amount_usdt / price)
            order_side = 'BUY' if side == 'LONG' else 'SELL'
            bn.order_market(symbol, order_side, qty, position_side=side)
            successful_trades.append({'symbol': symbol, 'side': side})
            time.sleep(0.1)
    except Exception as e:
        if successful_trades:
            print(f"Rolling back {len(successful_trades)} trades...")
            for trade in successful_trades:
                try:
                    cleanup_side = 'SELL' if trade['side'] == 'LONG' else 'BUY'
                    pos_risk = bn.position_risk(trade['symbol'])
                    for p in pos_risk:
                        if p.get('positionSide') == trade['side']:
                            amt = abs(float(p.get('positionAmt', 0)))
                            if amt > 0: bn.order_market(trade['symbol'], cleanup_side, amt, position_side=trade['side'])
                            break
                except Exception as cleanup_e: print(f"Failed to rollback {trade['symbol']}: {cleanup_e}")
        return jsonify({'error': f"Failed to place order: {str(e)}"}), 500
    
    # FIX for Bug 1 & 2: Restart the ROI worker for new symbols & refresh UI.
    _update_positions_and_worker(account_id, wait_time=1)
    
    return jsonify({'ok': True, 'message': f"{len(successful_trades)} trades submitted."})

@app.route('/api/trades/close', methods=['POST'])
@login_required
def trades_close():
    data, closed_count = request.get_json(force=True), 0
    account_id, trades_to_close = data.get('account_id'), data.get('trades', [])
    if not account_id or not trades_to_close: return jsonify({'error': 'Account and trades list required'}), 400

    acc = get_account(account_id)
    try:
        bn = safe_get_client(acc)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    for trade in trades_to_close:
        try:
            symbol, side = trade['symbol'], trade['side'].upper()
            close_side = 'SELL' if side == 'LONG' else 'BUY'
            
            pos_risk = bn.position_risk(symbol)
            for p in pos_risk:
                pos_amt = float(p.get('positionAmt', 0))
                if p.get('positionSide') == side and pos_amt != 0:
                    bn.order_market(symbol, close_side, abs(pos_amt), position_side=side)
                    closed_count += 1
                    break
        except Exception as e:
            print(f"Could not close trade for {trade.get('symbol')}: {e}")

    time.sleep(1)
    # FIX for Bug 1: Restart the ROI worker to update the list of active symbols.
    _update_positions_and_worker(account_id, wait_time=1)
    
    return jsonify({'ok': True, 'message': f"Attempted to close {len(trades_to_close)} trades. {closed_count} confirmed."})
#</editor-fold>

#<editor-fold desc="WebSocket for Real-time ROI">
def roi_websocket_worker(account_id):
    global worker_thread, worker_stop_event
    print(f"Starting ROI worker for account {account_id}...")
    acc = get_account(account_id)
    if not acc:
        print("Worker stopping: Account not found.")
        return

    try:
        bn = safe_get_client(acc)
        positions = bn.position_risk()
        active_symbols = [p['symbol'].lower() for p in positions if float(p.get('positionAmt', 0)) != 0]
    except Exception as e:
        print(f"Worker stopping: Could not fetch initial positions. Error: {e}")
        socketio.emit('worker_error', {'message': str(e)}, namespace='/trades')
        return

    if not active_symbols:
        print("Worker stopping: No active positions found.")
        socketio.emit('positions_update', {'account_id': account_id, 'trades': []}, namespace='/trades')
        return
    
    streams = '/'.join([f"{symbol}@markPrice@1s" for symbol in active_symbols])
    ws_url = f"{bn.ws_base}/stream?streams={streams}"
    
    while not worker_stop_event.is_set():
        ws = websocket.WebSocketApp(ws_url,
                                  on_message=lambda ws, msg: on_ws_message(msg),
                                  on_error=lambda ws, err: print(f"WS Error (Acc {account_id}): {err}"),
                                  on_close=lambda ws, code, msg: print(f"WS Closed (Acc {account_id})"))
        ws.run_forever()
        if not worker_stop_event.is_set():
            print("WebSocket disconnected. Reconnecting in 5 seconds...")
            time.sleep(5)
    print(f"ROI worker for account {account_id} has stopped.")

def on_ws_message(message):
    try:
        payload = json.loads(message)
        if 'data' in payload and isinstance(payload['data'], dict):
            data = payload['data']
            if data.get('e') == 'markPriceUpdate':
                symbol = data['s']
                mark_price = float(data['p'])
                socketio.emit('roi_update', {'symbol': symbol, 'mark_price': mark_price}, namespace='/trades')
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

@socketio.on('start_roi_updates', namespace='/trades')
def handle_start_roi_updates(data):
    global worker_thread, worker_stop_event
    account_id = data.get('account_id')

    if worker_thread and worker_thread.is_alive():
        worker_stop_event.set()
        worker_thread.join()
        print("Stopped previous ROI worker.")

    worker_stop_event.clear()
    worker_thread = threading.Thread(target=roi_websocket_worker, args=(account_id,))
    worker_thread.daemon = True
    worker_thread.start()

@socketio.on('stop_roi_updates', namespace='/trades')
def handle_stop_roi_updates():
    global worker_stop_event
    worker_stop_event.set()
    print("Stop signal sent to ROI worker.")

@socketio.on('request_initial_positions', namespace='/trades')
def handle_initial_positions(data):
    account_id = data.get('account_id')
    if not account_id: return
    
    try:
        acc = get_account(account_id)
        if not acc:
            emit('positions_update', {'trades': [], 'error': 'Account not found.'}, namespace='/trades')
            return
            
        bn = safe_get_client(acc)
        positions = bn.position_risk()
        initial_trades = []

        for p in positions:
            pos_amt = float(p.get('positionAmt', 0))
            if pos_amt != 0:
                initial_trades.append({
                    'symbol': p['symbol'],
                    'entry_price': float(p.get('entryPrice', 0)),
                    'side': p.get('positionSide'),
                    'leverage': int(p.get('leverage', 1))
                })
        emit('positions_update', {'trades': initial_trades}, namespace='/trades')
    except Exception as e:
        emit('positions_update', {'trades': [], 'error': str(e)}, namespace='/trades')

#</editor-fold>

if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '5003'))
    socketio.run(app, host=host, port=port)