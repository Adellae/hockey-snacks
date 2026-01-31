import sqlite3
from pathlib import Path

DB_PATH = Path("snacks.db")

SCHEMA = '''
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    group_name TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    match_datetime TEXT NOT NULL,
    opponent TEXT NOT NULL,
    raw_header TEXT NOT NULL,
    UNIQUE (match_datetime, opponent)
);

CREATE TABLE IF NOT EXISTS registrations (
    match_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    PRIMARY KEY (match_id, player_id)
);

CREATE TABLE IF NOT EXISTS snack_assignments (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    is_volunteer INTEGER NOT NULL DEFAULT 0,
    assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_snack_per_match
ON snack_assignments(match_id, player_id);
'''

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("DB initialized")
