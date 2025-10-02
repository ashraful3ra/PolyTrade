# PolyTrade/app.py

import os
import json
import time
# REMOVING threading, flask_socketio, and websocket imports
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from functools import wraps
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from utils.db import init_db, connect, now, to_dict
from utils.crypto import enc_str, dec_str
from utils.binance import BinanceUM
from cryptography.fernet import InvalidToken
# import websocket # REMOVED

# --- Initialization ---
load_dotenv()
init_db()
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24))
# --- SocketIO and WebSocket worker logic is REMOVED to switch to Polling API ---


# --- Upgraded Password Protection with Hashing ---
APP_PASSWORD_HASH = generate_password_hash(os.environ.get('APP_PASSWORD', 'tradebot'))

# Helper function to compute ROI (copied logic from client/worker)
def _compute_roi(entry, mark, leverage, side):
    if not entry or entry <= 0: return 0.0
    roi = ((mark - entry) / entry) * leverage * 100
    return roi * (-1 if side == 'SHORT' else 1)

# Helper function to fetch live positions and calculate ROI (used for polling)
def _fetch_live_positions_and_roi(account_id):
    acc = get_account(account_id)
    if not acc:
        raise RuntimeError("Account not found.")
        
    bn = safe_get_client(acc)
    positions = bn.position_risk()
    trades = []

    for p in positions:
        pos_amt = float(p.get('positionAmt', 0))
        if pos_amt != 0:
            # We fetch the mark price here to calculate ROI immediately (Polling)
            mark_price_data = bn.price(p['symbol'])
            mark_price = float(mark_price_data.get('price', 0))

            entry_price = float(p.get('entryPrice', 0))
            side = p.get('positionSide')
            leverage = int(p.get('leverage', 1))
            
            roi = _compute_roi(entry_price, mark_price, leverage, side)

            trades.append({
                'symbol': p['symbol'],
                'entry_price': entry_price,
                'side': side,
                'leverage': leverage,
                'roi': roi,
                'mark_price': mark_price # Also return mark price for client reference
            })
    return trades

# Helper function to update all active accounts' balances
def _update_account_balances():
    accounts = list_accounts()
    updated_list = []
    for acc in accounts:
        if acc['active'] and acc['id']:
            try:
                bn = safe_get_client(acc)
                latest_balance = bn.futures_balance()
                with connect() as con:
                    # UPDATED: Use %s placeholder for MySQL compatibility
                    con.cursor().execute('UPDATE accounts SET futures_balance=%s, updated_at=%s WHERE id=%s', 
                                         (latest_balance, now(), acc['id']))
                    con.commit()
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
        # FIX for MySQL fetchall error: Separate cursor execution and fetchone
        cur = con.cursor()
        cur.execute('SELECT * FROM accounts WHERE id=%s', (acc_id,))
        return to_dict(cur.fetchone())

def safe_get_client(acc):
    try:
        api_key = dec_str(acc['api_key_enc'])
        api_secret = dec_str(acc['api_secret_enc'])
    except InvalidToken:
        raise RuntimeError("Encryption key mismatch.")
    return BinanceUM(api_key, api_secret, bool(acc['testnet']))

def list_accounts():
    with connect() as con:
        # FIX for MySQL fetchall error: Separate cursor execution and fetchall
        cur = con.cursor()
        cur.execute('SELECT * FROM accounts ORDER BY id DESC')
        return [to_dict(r) for r in cur.fetchall()]
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
@login_required
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
        # UPDATED: Use %s placeholder for MySQL compatibility
        cur.execute('INSERT INTO accounts (name,exchange,api_key_enc,api_secret_enc,testnet,active,futures_balance,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (name, 'BINANCE_UM', enc_str(api_key), enc_str(api_secret), testnet, 1, balance, now(), now()))
        con.commit()
    return jsonify({'ok': True, 'accounts': list_accounts()})

@app.route('/accounts/delete/<int:acc_id>', methods=['POST'])
@login_required
def accounts_delete(acc_id):
    with connect() as con:
        # UPDATED: Use %s placeholder for MySQL compatibility
        con.cursor().execute('DELETE FROM accounts WHERE id=%s', (acc_id,))
        con.commit()
    return jsonify({'ok': True, 'accounts': list_accounts()})

@app.route('/accounts/toggle/<int:acc_id>', methods=['POST'])
@login_required
def accounts_toggle(acc_id):
    with connect() as con:
        cur = con.cursor()
        # UPDATED: Use %s placeholder for MySQL compatibility (SELECT)
        cur.execute('SELECT active FROM accounts WHERE id=%s', (acc_id,))
        r = cur.fetchone()
        if r:
            new_status = 1 if r['active'] == 0 else 0
            # UPDATED: Use %s placeholder for MySQL compatibility (UPDATE)
            cur.execute('UPDATE accounts SET active=%s, updated_at=%s WHERE id=%s', (new_status, now(), acc_id))
            con.commit()
            return jsonify({'ok': True, 'status': new_status})
        return jsonify({'error': 'Account not found'}), 404

@app.route('/accounts/update_balances', methods=['POST'])
@login_required
def accounts_update_balances():
    try:
        updated_accounts = _update_account_balances()
        return jsonify({'ok': True, 'accounts': updated_accounts})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/symbol-info')
@login_required
def symbol_info():
    symbol = (request.args.get('symbol') or '').upper().strip()
    if not symbol: return jsonify({'error': 'symbol required'}), 400
    try:
        bn = BinanceUM('', '', False)
        lot, min_notional = bn.symbol_filters(symbol)
        return jsonify({'symbol': symbol, 'min_notional': min_notional or 0, 'lot': lot or {}})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/futures/symbols')
@login_required
def futures_symbols():
    try:
        bn = BinanceUM('', '', False)
        info = bn.exchange_info()
        symbols = [s['symbol'] for s in info.get('symbols', []) if s.get('quoteAsset') == 'USDT' and s.get('status') == 'TRADING']
        return jsonify({'symbols': symbols})
    except Exception as e: return jsonify({'symbols': [], 'error': str(e)}), 500

@app.route('/api/price')
@login_required
def get_price():
    symbol = (request.args.get('symbol') or '').upper().strip()
    if not symbol: return jsonify({'error': 'symbol required'}), 400
    try:
        bn = BinanceUM('', '', False)
        return jsonify(bn.price(symbol))
    except Exception as e: return jsonify({'error': str(e)}), 500

# Template APIs
@app.route('/api/templates/save', methods=['POST'])
@login_required
def tpl_save():
    data = request.get_json(force=True)
    name, settings = data.get('name', '').strip(), data.get('settings', {})
    if not name or not settings: return jsonify({'error': 'Template name and settings are required'}), 400
    with connect() as con:
        # UPDATED: Use %s placeholder for MySQL compatibility
        con.cursor().execute('INSERT INTO templates (name, settings_json, created_at) VALUES (%s,%s,%s)', (name, json.dumps(settings), now()))
        con.commit()
    return jsonify({'ok': True})

@app.route('/api/templates/list')
@login_required
def tpl_list():
    with connect() as con:
        cur = con.cursor()
        cur.execute('SELECT id, name, created_at FROM templates ORDER BY created_at DESC')
        templates = [to_dict(r) for r in cur.fetchall()]
    return jsonify({'items': templates})

@app.route('/api/templates/get/<int:tpl_id>')
@login_required
def tpl_get(tpl_id):
    with connect() as con:
        cur = con.cursor()
        # UPDATED: Use %s placeholder for MySQL compatibility
        cur.execute('SELECT settings_json FROM templates WHERE id=%s', (tpl_id,))
        r = cur.fetchone()
    if not r: return jsonify({'error': 'Template not found'}), 404
    return jsonify(json.loads(r['settings_json']))

@app.route('/api/templates/delete/<int:tpl_id>', methods=['POST'])
@login_required
def tpl_delete(tpl_id):
    with connect() as con:
        # UPDATED: Use %s placeholder for MySQL compatibility
        con.cursor().execute('DELETE FROM templates WHERE id=%s', (tpl_id,))
        con.commit()
    return jsonify({'ok': True})

# NEW ROUTE for polling live ROI/Positions
@app.route('/api/trades/fetch_roi/<int:account_id>', methods=['GET'])
@login_required
def trades_fetch_roi(account_id):
    try:
        trades = _fetch_live_positions_and_roi(account_id)
        return jsonify({'ok': True, 'trades': trades})
    except Exception as e:
        # Return 500 status on failure, prevents the HTML/JSON error loop
        return jsonify({'error': str(e)}), 500


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
    
    # [FIX for Prob 1 & 2] Wait a consistent time for Binance to process and update position
    time.sleep(1.5) 

    # Polling will handle the UI update on the client-side. We just return success.
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

    # [FIX for Prob 1 & 2] Wait a consistent time for Binance to process and update position
    time.sleep(1.5)
    
    # Polling will handle the UI update on the client-side. We just return success.
    return jsonify({'ok': True, 'message': f"Attempted to close {len(trades_to_close)} trades. {closed_count} confirmed."})
#</editor-fold>

if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '5003'))
    # Use Flask's native run method now, as SocketIO setup is removed.
    app.run(host=host, port=port)