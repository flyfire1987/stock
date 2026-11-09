"""
数据库 - SQLite 单文件
表:
  kline      日级行情
  account    账户余额/初始资金
  positions  持仓
  trades     交易记录
  daily_pnl  每日收益快照(用于画收益曲线)
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, date

DB_PATH = Path(__file__).parent / "data" / "stock.db"
DB_PATH.parent.mkdir(exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS kline (
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, close REAL, high REAL, low REAL,
    volume REAL, amount REAL,
    market_cap REAL, turnover REAL,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY,
    cash REAL NOT NULL,
    initial_cash REAL NOT NULL,
    total_fee REAL DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    code TEXT PRIMARY KEY,
    name TEXT,
    asset_type TEXT,
    shares REAL NOT NULL,
    cost_price REAL NOT NULL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    asset_type TEXT,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    shares REAL NOT NULL,
    amount REAL NOT NULL,
    fee REAL NOT NULL,
    cash_after REAL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    date TEXT PRIMARY KEY,
    total_assets REAL,
    cash REAL,
    market_value REAL,
    profit REAL,
    profit_rate REAL,
    base_index REAL
);

CREATE TABLE IF NOT EXISTS cash_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT,
    amount REAL,
    note TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_kline_code_date ON kline(code, date);
CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
"""


@contextmanager
def conn():
    """每次调用产生新连接。SQLite WAL 模式本身支持多读单写并发，无需 Python 层加锁。"""
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
        # 初始化账户（只初始化一次）
        row = c.execute("SELECT COUNT(*) c FROM account").fetchone()
        if row["c"] == 0:
            from config_loader import config as cfg
            initial = cfg.get("account.initial_cash", 10000)
            c.execute(
                "INSERT INTO account (id, cash, initial_cash, total_fee, updated_at) VALUES (1, ?, ?, 0, ?)",
                (initial, initial, datetime.now().isoformat()),
            )


def dict_rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


if __name__ == "__main__":
    init_db()
    print(f"✅ DB initialized at {DB_PATH}")