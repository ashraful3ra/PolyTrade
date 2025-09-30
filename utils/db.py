# PolyTrade/utils/db.py

import sqlite3, os, time

DB_FILE = os.path.join('data', 'app.db')
SCHEMA_VERSION = 6 # Incremented schema version

def now(): return int(time.time())

def connect():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con

def to_dict(row):
    if not row: return None
    return dict(row)

def init_db():
    if not os.path.exists('data'):
        os.makedirs('data')

    con = connect()
    cur = con.cursor()

    cur.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL PRIMARY KEY);")
    cur.execute("SELECT version FROM schema_version;")
    r = cur.fetchone()
    current_version = r['version'] if r else 0

    if current_version < 6:
        # --- BOT TABLE SIMPLIFICATION ---
        # Drop the old table if it exists to recreate it
        cur.execute("DROP TABLE IF EXISTS bots;")
        cur.execute("""CREATE TABLE IF NOT EXISTS bots (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            account_id INTEGER NOT NULL,
            symbols_str TEXT NOT NULL, -- Storing comma-separated symbols
            side TEXT NOT NULL,          -- 'LONG' or 'SHORT'
            leverage INTEGER NOT NULL,
            margin_amount REAL NOT NULL, -- Total margin for the entire bot/group of trades
            margin_type TEXT NOT NULL,
            status TEXT DEFAULT 'Running', -- Overall status: 'Running', 'Closed'
            created_at INTEGER,
            closed_at INTEGER
        );""")

        # --- NEW: INDIVIDUAL TRADES TABLE ---
        cur.execute("DROP TABLE IF EXISTS trades;")
        cur.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            leverage INTEGER NOT NULL,
            margin_amount REAL NOT NULL,
            entry_price REAL,
            mark_price REAL,
            status TEXT DEFAULT 'Running', -- 'Running', 'Closed'
            roi REAL DEFAULT 0.0,
            pnl REAL DEFAULT 0.0,
            FOREIGN KEY (bot_id) REFERENCES bots (id)
        );""")

        # --- TEMPLATE TABLE SIMPLIFICATION ---
        cur.execute("DROP TABLE IF EXISTS templates;")
        cur.execute("""CREATE TABLE IF NOT EXISTS templates (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            settings_json TEXT NOT NULL, -- Store all settings as a JSON string
            created_at INTEGER
        );""")

        # Update accounts table if needed (no changes for now)
        cur.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            exchange TEXT NOT NULL,
            api_key_enc TEXT NOT NULL,
            api_secret_enc TEXT NOT NULL,
            testnet INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            futures_balance REAL,
            created_at INTEGER,
            updated_at INTEGER
        );""")


    if current_version == 0:
        cur.execute("INSERT INTO schema_version (version) VALUES (?);", (SCHEMA_VERSION,))
    else:
        cur.execute("UPDATE schema_version SET version=?;", (SCHEMA_VERSION,))

    con.commit()
    con.close()
    print('DB init OK. Schema version is now 6.')