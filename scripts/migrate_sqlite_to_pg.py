"""Jednorázový převod snacks.db (SQLite) do Postgresu.

    python scripts/migrate_sqlite_to_pg.py snacks.db

ID se zachovávají, aby zůstaly vazby mezi tabulkami. Skript je idempotentní —
dá se pustit znovu, existující řádky přeskočí.
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from snacks.config import LOCAL_TZ  # noqa: E402
from snacks.db import get_conn, init_db  # noqa: E402


def parse_dt(value: str) -> datetime:
    """SQLite drží naivní 'YYYY-MM-DD HH:MM' v pražském čase."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=LOCAL_TZ)
        except ValueError:
            continue
    raise ValueError(f"Nerozpoznaný formát data: {value!r}")


def migrate(sqlite_path: str) -> None:
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    init_db()
    with get_conn() as pg:
        players = src.execute("SELECT id, name, group_name FROM players").fetchall()
        for p in players:
            pg.execute(
                "INSERT INTO players (id, name, group_name) VALUES (%s,%s,%s) "
                "ON CONFLICT (id) DO NOTHING",
                (p["id"], p["name"], p["group_name"]),
            )

        matches = src.execute(
            "SELECT id, match_datetime, opponent, raw_header FROM matches"
        ).fetchall()
        for m in matches:
            pg.execute(
                "INSERT INTO matches (id, match_datetime, opponent, raw_header, status) "
                "VALUES (%s,%s,%s,%s,'active') ON CONFLICT (id) DO NOTHING",
                (m["id"], parse_dt(m["match_datetime"]), m["opponent"], m["raw_header"]),
            )

        regs = src.execute("SELECT match_id, player_id FROM registrations").fetchall()
        for r in regs:
            pg.execute(
                "INSERT INTO registrations (match_id, player_id) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING",
                (r["match_id"], r["player_id"]),
            )

        snacks = src.execute(
            "SELECT id, match_id, player_id, is_volunteer, assigned_at FROM snack_assignments"
        ).fetchall()
        for s in snacks:
            pg.execute(
                "INSERT INTO snack_assignments (id, match_id, player_id, is_volunteer, assigned_at) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (
                    s["id"],
                    s["match_id"],
                    s["player_id"],
                    bool(s["is_volunteer"]),
                    parse_dt(s["assigned_at"]) if s["assigned_at"] else None,
                ),
            )

        # SERIAL o ručně vložených ID neví, takže by další INSERT spadl na duplicitu.
        for table in ("players", "matches", "snack_assignments"):
            pg.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
            )

        print(
            f"Převedeno: {len(players)} hráčů, {len(matches)} zápasů, "
            f"{len(regs)} přihlášek, {len(snacks)} svačinek."
        )

    src.close()


if __name__ == "__main__":
    migrate(sys.argv[1] if len(sys.argv) > 1 else "snacks.db")
