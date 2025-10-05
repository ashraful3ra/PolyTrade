import os
import json
import time
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from functools import wraps
from dotenv import load_dotenv
from utils.db import init_db, connect, now, to_dict
from utils.crypto import enc_str, dec_str
from utils.binance import BinanceUM
from cryptography.fernet import InvalidToken

# --- NEW: JWT/SSO Imports ---
from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity
from urllib.parse import quote_plus
from werkzeug.wrappers import Response

# --- Initialization ---
load_dotenv()
init_db()
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24))

# --- START: JWT/SSO CONFIGURATION ---
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY')
app.config['JWT_TOKEN_LOCATION'] = ['cookies']
app.config['JWT_COOKIE_CSRF_PROTECT'] = False
app.config['JWT_COOKIE_DOMAIN'] = os.environ.get('JWT_COOKIE_DOMAIN')
app.config['JWT_COOKIE_SECURE'] = os.environ.get('JWT_COOKIE_SECURE', 'True').lower() == 'true'
jwt = JWTManager(app)
AUTH_SERVICE_URL = os.environ.get('AUTH_SERVICE_URL', 'https://utradebot.com')
# --- END: JWT/SSO CONFIGURATION ---


# --- START: SSO/JWT INTEGRATION ---
def sso_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            jwt_required()(lambda: Response())()
            current_user_id = get_jwt_identity()
            kwargs['user_id'] = current_user_id
            return f(*args, **kwargs)
        except Exception:
            redirect_to = request.url
            sso_login_url = f"{AUTH_SERVICE_URL}/login?redirect_url={quote_plus(redirect_to)}"
            return redirect(sso_login_url)
    return decorated_function

@app.route('/logout')
def logout():
    redirect_to = request.url_root
    sso_logout_url = f"{AUTH_SERVICE_URL}/logout?redirect_url={quote_plus(redirect_to)}"
    return redirect(sso_logout_url)
# --- END: SSO/JWT INTEGRATION ---


#<editor-fold desc="Helper Functions (Updated for User-Specific Data)">
def get_account(acc_id, user_id):
    with connect() as con:
        cur = con.cursor()
        cur.execute('SELECT * FROM accounts WHERE id=%s AND user_id=%s', (acc_id, user_id))
        return to_dict(cur.fetchone())

def safe_get_client(acc):
    try:
        api_key = dec_str(acc['api_key_enc'])
        api_secret = dec_str(acc['api_secret_enc'])
    except InvalidToken:
        raise RuntimeError("Encryption key mismatch.")
    return BinanceUM(api_key, api_secret, bool(acc['testnet']))

def list_accounts(user_id):
    with connect() as con:
        cur = con.cursor()
        cur.execute('SELECT * FROM accounts WHERE user_id=%s ORDER BY id DESC', (user_id,))
        return [to_dict(r) for r in cur.fetchall()]

def _compute_roi(entry, mark, leverage, side):
    if not entry or entry <= 0: return 0.0
    roi = ((mark - entry) / entry) * leverage * 100
    return roi * (-1 if side == 'SHORT' else 1)

def _fetch_live_positions_and_roi(account_id, user_id):
    acc = get_account(account_id, user_id)
    if not acc:
        raise RuntimeError("Account not found or you do not have permission.")
    
    bn = safe_get_client(acc)
    positions = bn.position_risk()
    trades = []

    for p in positions:
        pos_amt = float(p.get('positionAmt', 0))
        if pos_amt != 0:
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
                'mark_price': mark_price
            })
    return trades

def _update_account_balances(user_id):
    accounts = list_accounts(user_id)
    updated_list = []
    for acc in accounts:
        if acc['active'] and acc['id']:
            try:
                bn = safe_get_client(acc)
                latest_balance = bn.futures_balance()
                with connect() as con:
                    con.cursor().execute('UPDATE accounts SET futures_balance=%s, updated_at=%s WHERE id=%s AND user_id=%s', 
                                         (latest_balance, now(), acc['id'], user_id))
                    con.commit()
                acc['futures_balance'] = latest_balance 
            except Exception as e:
                print(f"Error updating balance for account {acc.get('name')} ({acc['id']}): {e}")
        updated_list.append(acc)
    return updated_list
#</editor-fold>

#<editor-fold desc="UI Routes (Updated for SSO)">
@app.route('/')
@sso_required
def home(user_id): 
    return redirect(url_for('dashboard'))

@app.route('/account')
@sso_required
def account(user_id):
    return render_template('account.html', accounts_json=json.dumps(list_accounts(user_id)))

@app.route('/dashboard')
@sso_required
def dashboard(user_id):
    return render_template('dashboard.html', accounts=list_accounts(user_id))
#</editor-fold>

#<editor-fold desc="API Routes (Updated for SSO and User-Specific Data)">
@app.route('/accounts/add', methods=['POST'])
@sso_required
def accounts_add(user_id):
    data = request.get_json(force=True)
    name, api_key, api_secret = data.get('name','').strip(), data.get('api_key','').strip(), data.get('api_secret','').strip()
    testnet = 1 if data.get('testnet') else 0
    if not all([name, api_key, api_secret]): return jsonify({'error': 'Missing fields'}), 400
    try:
        bn = BinanceUM(api_key, api_secret, bool(testnet))
        balance = bn.futures_balance()
        bn.set_hedge_mode()
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    with connect() as con:
        cur = con.cursor()
        cur.execute('INSERT INTO accounts (name,exchange,api_key_enc,api_secret_enc,testnet,active,futures_balance,created_at,updated_at,user_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (name, 'BINANCE_UM', enc_str(api_key), enc_str(api_secret), testnet, 1, balance, now(), now(), user_id))
        con.commit()
    return jsonify({'ok': True, 'accounts': list_accounts(user_id)})

@app.route('/accounts/delete/<int:acc_id>', methods=['POST'])
@sso_required
def accounts_delete(acc_id, user_id):
    with connect() as con:
        con.cursor().execute('DELETE FROM accounts WHERE id=%s AND user_id=%s', (acc_id, user_id))
        con.commit()
    return jsonify({'ok': True, 'accounts': list_accounts(user_id)})

@app.route('/accounts/toggle/<int:acc_id>', methods=['POST'])
@sso_required
def accounts_toggle(acc_id, user_id):
    with connect() as con:
        cur = con.cursor()
        cur.execute('SELECT active FROM accounts WHERE id=%s AND user_id=%s', (acc_id, user_id))
        r = cur.fetchone()
        if r:
            new_status = 1 if r['active'] == 0 else 0
            cur.execute('UPDATE accounts SET active=%s, updated_at=%s WHERE id=%s AND user_id=%s', (new_status, now(), acc_id, user_id))
            con.commit()
            return jsonify({'ok': True, 'status': new_status})
        return jsonify({'error': 'Account not found'}), 404

@app.route('/accounts/update_balances', methods=['POST'])
@sso_required
def accounts_update_balances(user_id):
    try:
        updated_accounts = _update_account_balances(user_id)
        return jsonify({'ok': True, 'accounts': updated_accounts})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/templates/save', methods=['POST'])
@sso_required
def tpl_save(user_id):
    data = request.get_json(force=True)
    name, settings = data.get('name', '').strip(), data.get('settings', {})
    if not name or not settings: return jsonify({'error': 'Template name and settings are required'}), 400
    with connect() as con:
        con.cursor().execute('INSERT INTO templates (name, settings_json, created_at, user_id) VALUES (%s,%s,%s,%s)', (name, json.dumps(settings), now(), user_id))
        con.commit()
    return jsonify({'ok': True})

@app.route('/api/templates/list')
@sso_required
def tpl_list(user_id):
    with connect() as con:
        cur = con.cursor()
        cur.execute('SELECT id, name, created_at FROM templates WHERE user_id=%s ORDER BY created_at DESC', (user_id,))
        templates = [to_dict(r) for r in cur.fetchall()]
    return jsonify({'items': templates})

@app.route('/api/templates/get/<int:tpl_id>')
@sso_required
def tpl_get(tpl_id, user_id):
    with connect() as con:
        cur = con.cursor()
        cur.execute('SELECT settings_json FROM templates WHERE id=%s AND user_id=%s', (tpl_id, user_id))
        r = cur.fetchone()
    if not r: return jsonify({'error': 'Template not found'}), 404
    return jsonify(json.loads(r['settings_json']))

@app.route('/api/templates/delete/<int:tpl_id>', methods=['POST'])
@sso_required
def tpl_delete(tpl_id, user_id):
    with connect() as con:
        con.cursor().execute('DELETE FROM templates WHERE id=%s AND user_id=%s', (tpl_id, user_id))
        con.commit()
    return jsonify({'ok': True})

@app.route('/api/trades/fetch_roi/<int:account_id>', methods=['GET'])
@sso_required
def trades_fetch_roi(account_id, user_id):
    try:
        trades = _fetch_live_positions_and_roi(account_id, user_id)
        return jsonify({'ok': True, 'trades': trades})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/trades/submit', methods=['POST'])
@sso_required
def trades_submit(user_id):
    data = request.get_json(force=True)
    account_id, bot_name, coins = data.get('account_id'), data.get('bot_name', '').strip(), data.get('coins', [])
    if not all([account_id, bot_name, coins]): return jsonify({'error': 'Missing required fields.'}), 400
    
    acc = get_account(account_id, user_id)
    if not acc or not acc['active']: return jsonify({'error': 'Account is not active or not yours'}), 400
    
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
    
    time.sleep(1.5) 
    return jsonify({'ok': True, 'message': f"{len(successful_trades)} trades submitted."})

@app.route('/api/trades/close', methods=['POST'])
@sso_required
def trades_close(user_id):
    data, closed_count = request.get_json(force=True), 0
    account_id, trades_to_close = data.get('account_id'), data.get('trades', [])
    if not account_id or not trades_to_close: return jsonify({'error': 'Account and trades list required'}), 400

    acc = get_account(account_id, user_id)
    if not acc:
        return jsonify({'error': 'Account not found or does not belong to you'}), 403
        
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

    time.sleep(1.5)
    return jsonify({'ok': True, 'message': f"Attempted to close {len(trades_to_close)} trades. {closed_count} confirmed."})

@app.route('/api/symbol-info')
@sso_required
def symbol_info(user_id):
    symbol = (request.args.get('symbol') or '').upper().strip()
    if not symbol: return jsonify({'error': 'symbol required'}), 400
    try:
        bn = BinanceUM('', '', False)
        lot, min_notional = bn.symbol_filters(symbol)
        return jsonify({'symbol': symbol, 'min_notional': min_notional or 0, 'lot': lot or {}})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/futures/symbols')
@sso_required
def futures_symbols(user_id):
    try:
        bn = BinanceUM('', '', False)
        info = bn.exchange_info()
        symbols = [s['symbol'] for s in info.get('symbols', []) if s.get('quoteAsset') == 'USDT' and s.get('status') == 'TRADING']
        return jsonify({'symbols': symbols})
    except Exception as e: return jsonify({'symbols': [], 'error': str(e)}), 500

@app.route('/api/price')
@sso_required
def get_price(user_id):
    symbol = (request.args.get('symbol') or '').upper().strip()
    if not symbol: return jsonify({'error': 'symbol required'}), 400
    try:
        bn = BinanceUM('', '', False)
        return jsonify(bn.price(symbol))
    except Exception as e: return jsonify({'error': str(e)}), 500
#</editor-fold>

if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '5003'))
    app.run(host=host, port=port)