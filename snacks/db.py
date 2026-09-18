"""Připojení k Postgresu a schéma databáze.

Connection string se bere z proměnné prostředí DATABASE_URL, takže stejný kód
běží ve Streamlitu, v GitHub Actions i lokálně. Streamlit si secrets do env
promítá v app.py.
"""

import os
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config  # noqa: F401  -- načte .env do prostředí

_pool: ConnectionPool | None = None


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL není nastavená. Lokálně ji dej do .env, "
            "ve Streamlit Cloud do secrets, v GitHub Actions do secrets."
        )
    return url


def get_pool() -> ConnectionPool:
    """Líně vytvořený sdílený pool.

    prepare_threshold=None vypíná automatické prepared statements. Supabase
    jede přes Supavisor a v transaction mode (port 6543) se spojení mezi
    dotazy přepínají, takže prepared statement z jednoho dotazu v dalším
    neexistuje a psycopg spadne na "prepared statement does not exist".
    Při téhle velikosti dat je to výkonnostně jedno.
    """
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_database_url(),
            min_size=0,
            max_size=4,
            max_idle=120,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=True,
        )
    return _pool


@contextmanager
def get_conn():
    """Spojení z poolu. Commit na konci bloku, rollback při výjimce."""
    with get_pool().connection() as conn:
        yield conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    group_name  TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    id              SERIAL PRIMARY KEY,
    match_datetime  TIMESTAMPTZ NOT NULL,
    opponent        TEXT NOT NULL,
    raw_header      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    UNIQUE (match_datetime, opponent)
);

CREATE TABLE IF NOT EXISTS registrations (
    match_id   INTEGER NOT NULL REFERENCES matches(id)  ON DELETE CASCADE,
    player_id  INTEGER NOT NULL REFERENCES players(id)  ON DELETE CASCADE,
    PRIMARY KEY (match_id, player_id)
);

CREATE TABLE IF NOT EXISTS snack_assignments (
    id            SERIAL PRIMARY KEY,
    match_id      INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id     INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    is_volunteer  BOOLEAN NOT NULL DEFAULT FALSE,
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (match_id, player_id)
);

-- Log importů, aby bylo v UI vidět, kdy naposledy doběhl a jestli prošel.
CREATE TABLE IF NOT EXISTS ingest_runs (
    id            SERIAL PRIMARY KEY,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    source        TEXT NOT NULL,
    status        TEXT NOT NULL,
    matches_seen  INTEGER,
    players_seen  INTEGER,
    registrations_written INTEGER,
    message       TEXT
);

-- Idempotentní pro databáze založené dřív, než status přibyl.
ALTER TABLE matches ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

CREATE INDEX IF NOT EXISTS idx_matches_datetime   ON matches(match_datetime);
CREATE INDEX IF NOT EXISTS idx_snack_player       ON snack_assignments(player_id);
CREATE INDEX IF NOT EXISTS idx_ingest_started     ON ingest_runs(started_at DESC);
"""


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(SCHEMA)


if __name__ == "__main__":
    init_db()
    print("DB initialized")
