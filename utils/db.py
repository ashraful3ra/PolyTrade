# PolyTrade/utils/db.py - FINAL CORRECTED VERSION

import pymysql.cursors
import os 
import time
from decimal import Decimal # <-- Eita dorkar Decimal type check korar jonne

# Use environment variables for connection details
HOST = os.environ.get('DB_HOST', 'utradebot.com')
USER = os.environ.get('DB_USER', 'polytradebot')
PASSWORD = os.environ.get('DB_PASSWORD', 'V3E~9mk=4VKZ')
DATABASE = os.environ.get('DB_NAME', 'polytradebot')
# NEW: DB_PORT environment variable theke load kora holo, default 3306
PORT = int(os.environ.get('DB_PORT', 3306)) 
SCHEMA_VERSION = 6

def now(): return int(time.time())

def connect(dict_cursor=True):
    """Establishes a connection to the MySQL database."""
    con = pymysql.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        db=DATABASE,
        port=PORT,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor if dict_cursor else pymysql.cursors.Cursor
    )
    return con

def to_dict(row):
    """
    FIX: Convert MySQL Decimal objects to standard Python float for JSON serialization.
    """
    if not row: return None
    new_dict = {}
    for key, value in dict(row).items():
        if isinstance(value, Decimal):
            new_dict[key] = float(value) # Decimal ke float e convert kora holo
        else:
            new_dict[key] = value
    return new_dict

def init_db():
    con = connect(dict_cursor=False)
    cur = con.cursor()
    
    # 1. Ensure the schema_version table exists (MySQL compatible syntax)
    cur.execute("""CREATE TABLE IF NOT EXISTS schema_version (
        version INT NOT NULL PRIMARY KEY
    ) ENGINE=InnoDB;""")
    con.commit()

    # 2. Check current version
    cur.execute("SELECT version FROM schema_version;")
    r = cur.fetchone()
    current_version = r[0] if r and r[0] is not None else 0

    if current_version < 6:
        print("Migrating schema to version 6...")
        # --- BOT TABLE SIMPLIFICATION (TEXT/DEFAULT fix shoho) ---
        cur.execute("DROP TABLE IF EXISTS bots;")
        cur.execute("""CREATE TABLE bots (
            id INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
            name TEXT NOT NULL,
            account_id INT NOT NULL,
            symbols_str TEXT NOT NULL,
            side TEXT NOT NULL,
            leverage INT NOT NULL,
            margin_amount DECIMAL(18, 8) NOT NULL,
            margin_type TEXT NOT NULL,
            status VARCHAR(50) DEFAULT 'Running',
            created_at INT,
            closed_at INT
        ) ENGINE=InnoDB;""")

        # --- INDIVIDUAL TRADES TABLE (TEXT/DEFAULT fix shoho) ---
        cur.execute("DROP TABLE IF EXISTS trades;")
        cur.execute("""CREATE TABLE trades (
            id INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
            bot_id INT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            leverage INT NOT NULL,
            margin_amount DECIMAL(18, 8) NOT NULL,
            entry_price DECIMAL(18, 8),
            mark_price DECIMAL(18, 8),
            status VARCHAR(50) DEFAULT 'Running',
            roi DECIMAL(18, 8) DEFAULT 0.0,
            pnl DECIMAL(18, 8) DEFAULT 0.0,
            FOREIGN KEY (bot_id) REFERENCES bots (id)
        ) ENGINE=InnoDB;""")

        # --- TEMPLATE TABLE (JSON type support) ---
        cur.execute("DROP TABLE IF EXISTS templates;")
        cur.execute("""CREATE TABLE templates (
            id INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
            name TEXT NOT NULL,
            settings_json JSON NOT NULL,
            created_at INT
        ) ENGINE=InnoDB;""")

        # --- ACCOUNTS TABLE ---
        cur.execute("DROP TABLE IF EXISTS accounts;")
        cur.execute("""CREATE TABLE accounts (
            id INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
            name TEXT NOT NULL,
            exchange TEXT NOT NULL,
            api_key_enc TEXT NOT NULL,
            api_secret_enc TEXT NOT NULL,
            testnet TINYINT DEFAULT 1,
            active TINYINT DEFAULT 1,
            futures_balance DECIMAL(18, 8),
            created_at INT,
            updated_at INT
        ) ENGINE=InnoDB;""")

    # 5. Update schema version
    if current_version == 0:
        cur.execute("INSERT INTO schema_version (version) VALUES (%s);", (SCHEMA_VERSION,))
    else:
        cur.execute("UPDATE schema_version SET version=%s;", (SCHEMA_VERSION,))

    con.commit()
    con.close()
    print(f'DB init OK. Schema version is now {SCHEMA_VERSION} (MySQL).')