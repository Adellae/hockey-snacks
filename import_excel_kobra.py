import re
import sys
from datetime import datetime
from typing import Dict, Tuple, Optional

import pandas as pd

from db import get_connection

TEAM_NAME = "HC Kobra Praha ženy"
EXCLUDE_TRAININGS = True  # ignore columns containing "Trénink"


def is_match_column(col_name: str) -> bool:
    """Return True if the column header represents a match involving TEAM_NAME."""
    if not isinstance(col_name, str):
        return False
    if TEAM_NAME not in col_name:
        return False
    if EXCLUDE_TRAININGS and "Trénink" in col_name:
        return False
    return True


def parse_match_header(header: str) -> Tuple[str, str, str]:
    """
    Parse a match header like:
      'HC Kobra Praha ženy vs. Střely Jindřichův Hradec (31.1.2026 11:00)'

    Returns:
      match_datetime_iso: 'YYYY-MM-DD HH:MM'
      opponent: opponent team name
      raw_header: original header string
    """
    raw_header = header

    m = re.search(r"\(([^)]+)\)", header)
    if not m:
        raise ValueError(f"Cannot find date/time in header: {header}")

    date_str = m.group(1).strip()  # e.g. '31.1.2026 11:00'
    dt = datetime.strptime(date_str, "%d.%m.%Y %H:%M")
    match_datetime_iso = dt.strftime("%Y-%m-%d %H:%M")

    main_part = header[: m.start()].strip()
    parts = [p.strip() for p in main_part.split("vs.")]
    opponent = main_part  # fallback

    if len(parts) == 2:
        home, away = parts
        if TEAM_NAME in home:
            opponent = away
        elif TEAM_NAME in away:
            opponent = home

    return match_datetime_iso, opponent, raw_header


def upsert_player(conn, name: str, group_name: Optional[str]) -> int:
    """
    Insert a new player or update group_name if the player already exists.

    Uses COALESCE to avoid overwriting an existing group_name with NULL/empty.
    """
    if group_name is not None:
        group_name = str(group_name).strip()
        if group_name == "" or group_name.lower() == "nan":
            group_name = None

    conn.execute(
        """
        INSERT INTO players (name, group_name)
        VALUES (?, ?)
        ON CONFLICT(name) DO UPDATE SET
            group_name = COALESCE(excluded.group_name, players.group_name)
        """,
        (name, group_name),
    )

    row = conn.execute(
        "SELECT id FROM players WHERE name = ?", (name,)
    ).fetchone()
    return row["id"]


def get_or_create_match(
    conn, match_datetime_iso: str, opponent: str, raw_header: str
) -> int:
    """
    Insert a match if it does not exist yet.
    Matches are unique by (match_datetime, opponent).

    On repeated imports, raw_header is updated.
    """
    conn.execute(
        """
        INSERT INTO matches (match_datetime, opponent, raw_header)
        VALUES (?, ?, ?)
        ON CONFLICT(match_datetime, opponent)
        DO UPDATE SET raw_header = excluded.raw_header
        """,
        (match_datetime_iso, opponent, raw_header),
    )

    row = conn.execute(
        "SELECT id FROM matches WHERE match_datetime = ? AND opponent = ?",
        (match_datetime_iso, opponent),
    ).fetchone()
    return row["id"]


def import_kobra_export(path: str) -> None:
    """
    Import Excel attendance export.

    Behavior:
    - detects match columns
    - upserts players and updates group_name if it changes
    - for each match, registrations are fully replaced based on current export
      (only cells starting with 'JDE' are considered)
    """
    df = pd.read_excel(path)

    if df.shape[1] < 3:
        raise ValueError(
            "Excel file must contain at least 3 columns: name, group, and events."
        )

    name_col = df.columns[0]
    group_col = df.columns[1]
    event_cols = df.columns[2:]

    conn = get_connection()

    # 1) Detect match columns and create/update matches
    match_map: Dict[object, int] = {}  # excel column -> match_id

    for col in event_cols:
        col_str = str(col)
        if not is_match_column(col_str):
            continue

        try:
            dt_iso, opponent, raw_header = parse_match_header(col_str)
        except Exception:
            # Skip unparseable headers
            continue

        match_id = get_or_create_match(conn, dt_iso, opponent, raw_header)
        match_map[col] = match_id

    if not match_map:
        conn.commit()
        conn.close()
        print("No match columns found for HC Kobra Praha ženy.")
        return

    # 2) Upsert players and build name -> player_id map
    player_id_by_name: Dict[str, int] = {}

    for _, row in df.iterrows():
        name = str(row[name_col]).strip()
        if not name or name.lower() == "nan":
            continue

        group_val = None if pd.isna(row[group_col]) else str(row[group_col]).strip()
        player_id = upsert_player(conn, name, group_val)
        player_id_by_name[name] = player_id

    # 3) For each match, recompute registrations based on current export
    for col, match_id in match_map.items():
        going_player_ids = []

        for _, row in df.iterrows():
            name = str(row[name_col]).strip()
            if not name or name.lower() == "nan":
                continue

            val = row[col]
            if pd.isna(val):
                continue

            status = str(val).strip().upper()
            if status.startswith("JDE"):
                pid = player_id_by_name.get(name)
                if pid is not None:
                    going_player_ids.append(pid)

        # Replace registrations for this match with current state
        conn.execute("DELETE FROM registrations WHERE match_id = ?", (match_id,))
        conn.executemany(
            "INSERT INTO registrations (match_id, player_id) VALUES (?, ?)",
            [(match_id, pid) for pid in going_player_ids],
        )

    conn.commit()
    conn.close()
    print("Import completed successfully.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_excel_kobra.py attendance_export.xlsx")
        raise SystemExit(2)

    import_kobra_export(sys.argv[1])
