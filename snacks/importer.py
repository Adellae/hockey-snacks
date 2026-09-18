"""Import Excel exportu přihlášek z Týmuj.cz do Postgresu."""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from .config import (
    LOCAL_TZ,
    NON_MATCH_KEYWORDS,
    TEAM_KEYWORD,
    TEAM_NAME,
    TOURNAMENT,
    VS_SEPARATORS,
)


@dataclass
class ImportStats:
    players_seen: int = 0
    matches_seen: int = 0
    registrations_written: int = 0
    cancelled: list[str] = field(default_factory=list)
    skipped_headers: list[str] = field(default_factory=list)
    ignored_headers: list[str] = field(default_factory=list)
    autofill: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{self.matches_seen} zápasů",
            f"{self.players_seen} hráčů",
            f"{self.registrations_written} přihlášek",
        ]
        if self.cancelled:
            parts.append(f"{len(self.cancelled)} zrušeno")
        return ", ".join(parts)


def _norm(text: str) -> str:
    """Malá písmena bez diakritiky — 'HC KOBRA ŽENY' i 'HC Kobra ženy' dopadnou stejně."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


_TEAM_TOKENS = set(_norm(TEAM_NAME).split())


def _team_score(side: str) -> int:
    """Kolik slov z názvu našeho týmu je v téhle straně zápasu.

    Rozhoduje to, která strana jsme my. Pouhé hledání slova 'kobra' nestačí:
    u 'Kobra B - Fejky vs. HC Kobra Praha ženy' je kobra na obou stranách,
    ale pravá se shoduje ve víc slovech.
    """
    return len(_TEAM_TOKENS & set(_norm(side).split()))


def is_match_column(col_name: str) -> bool:
    if not isinstance(col_name, str):
        return False

    norm = _norm(col_name)

    # Trénink ani schůze není zápas, i kdyby v názvu bylo jméno týmu.
    if any(k in norm for k in NON_MATCH_KEYWORDS):
        return False
    if _norm(TOURNAMENT) in norm:
        return True
    # Buď je v názvu náš tým, nebo to aspoň vypadá jako "A vs. B".
    if TEAM_KEYWORD in norm:
        return True
    return any(_norm(sep) in norm for sep in VS_SEPARATORS)


def _split_sides(main_part: str) -> Optional[tuple[str, str]]:
    for sep in VS_SEPARATORS:
        if sep in main_part:
            left, _, right = main_part.partition(sep)
            return left.strip(), right.strip()
    return None


def parse_match_header(header: str) -> tuple[datetime, str, str]:
    """'Tým A vs. Tým B (31.1.2026 11:00)' -> (aware datetime, soupeř, hlavička)."""
    m = re.search(r"\(([^)]+)\)", header)
    if not m:
        raise ValueError(f"V hlavičce chybí datum: {header}")

    dt = datetime.strptime(m.group(1).strip(), "%d.%m.%Y %H:%M")
    # Týmuj píše místní čas; bez tohohle se v UTC kontejneru posune "nadcházející".
    dt = dt.replace(tzinfo=LOCAL_TZ)

    main_part = header[: m.start()].strip()
    opponent = main_part

    sides = _split_sides(main_part)
    if sides:
        home, away = sides
        # Soupeř je ta strana, která se míň podobá našemu názvu.
        if _team_score(home) != _team_score(away):
            opponent = away if _team_score(home) > _team_score(away) else home

    return dt, opponent, header


def upsert_player(conn, name: str, group_name: Optional[str]) -> int:
    if group_name is not None:
        group_name = str(group_name).strip()
        if group_name == "" or group_name.lower() == "nan":
            group_name = None

    return conn.execute(
        """
        INSERT INTO players (name, group_name)
        VALUES (%s, %s)
        ON CONFLICT (name) DO UPDATE SET
            group_name = COALESCE(EXCLUDED.group_name, players.group_name)
        RETURNING id
        """,
        (name, group_name),
    ).fetchone()["id"]


def get_or_create_match(conn, dt: datetime, opponent: str, raw_header: str) -> int:
    return conn.execute(
        """
        INSERT INTO matches (match_datetime, opponent, raw_header, status)
        VALUES (%s, %s, %s, 'active')
        ON CONFLICT (match_datetime, opponent) DO UPDATE SET
            raw_header = EXCLUDED.raw_header,
            status = 'active'
        RETURNING id
        """,
        (dt, opponent, raw_header),
    ).fetchone()["id"]


def import_export(source, conn) -> ImportStats:
    """Naimportuje export. `source` je cesta, bytes nebo file-like objekt.

    Import je idempotentní — dá se pouštět opakovaně.
    """
    df = pd.read_excel(source)
    stats = ImportStats()

    if df.shape[1] < 3:
        raise ValueError("Export musí mít aspoň 3 sloupce: jméno, podskupina, události.")

    name_col, group_col = df.columns[0], df.columns[1]

    # 1) Zápasy
    match_map: dict[object, int] = {}
    seen_dts: list[datetime] = []

    for col in df.columns[2:]:
        col_str = str(col)
        if not is_match_column(col_str):
            # Zaznamenat, ne jen přeskočit — kdyby se změnilo pojmenování
            # v Týmuj, ať je v administraci vidět, co se nezapočítalo.
            stats.ignored_headers.append(col_str)
            continue
        try:
            dt, opponent, raw_header = parse_match_header(col_str)
        except ValueError:
            stats.skipped_headers.append(col_str)
            continue
        match_map[col] = get_or_create_match(conn, dt, opponent, raw_header)
        seen_dts.append(dt)

    if not match_map:
        return stats

    stats.matches_seen = len(match_map)

    # 2) Hráči
    player_id_by_name: dict[str, int] = {}
    for _, row in df.iterrows():
        name = str(row[name_col]).strip()
        if not name or name.lower() == "nan":
            continue
        group_val = None if pd.isna(row[group_col]) else str(row[group_col]).strip()
        player_id_by_name[name] = upsert_player(conn, name, group_val)

    stats.players_seen = len(player_id_by_name)

    # 3) Přihlášky — pro každý zápas se přepíšou podle aktuálního exportu
    for col, match_id in match_map.items():
        going = []
        for _, row in df.iterrows():
            name = str(row[name_col]).strip()
            if not name or name.lower() == "nan":
                continue
            val = row[col]
            if pd.isna(val):
                continue
            if str(val).strip().upper().startswith("JDE"):
                pid = player_id_by_name.get(name)
                if pid is not None:
                    going.append((match_id, pid))

        conn.execute("DELETE FROM registrations WHERE match_id = %s", (match_id,))
        if going:
            conn.cursor().executemany(
                "INSERT INTO registrations (match_id, player_id) VALUES (%s, %s)", going
            )
        stats.registrations_written += len(going)

    # 4) Přesunuté/zrušené zápasy.
    #    Export je zdroj pravdy jen pro svoje vlastní časové okno. Co v okně je
    #    v DB, ale v exportu chybí, se přesunulo jinam nebo se zrušilo.
    #
    #    Dvě pojistky, aby se nesmazala historie:
    #      a) ruší se jen zápasy v budoucnu — odehraný zápas je fakt, ať už je
    #         v exportu, nebo ne (Týmuj starší události z exportu vyhazuje),
    #      b) jen uvnitř okna exportu, takže zápas se stejným soupeřem o pár
    #         měsíců dřív nebo později zůstane netknutý.
    #    Nemaže se, jen označuje — na zápase můžou viset svačinky.
    #    Když se zápas v dalším exportu zas objeví, get_or_create_match ho
    #    vrátí do 'active', takže výpadek na straně Týmuj se sám spraví.
    ghosts = conn.execute(
        """
        UPDATE matches m
        SET status = 'cancelled'
        WHERE m.match_datetime > now()
          AND m.match_datetime BETWEEN %s AND %s
          AND m.id <> ALL(%s)
          AND m.status = 'active'
        RETURNING
            m.match_datetime,
            m.opponent,
            (SELECT COUNT(*) FROM snack_assignments sa WHERE sa.match_id = m.id) AS snacks
        """,
        (min(seen_dts), max(seen_dts), list(match_map.values())),
    ).fetchall()

    stats.cancelled = [
        f"{g['match_datetime']:%d.%m.%Y %H:%M} — {g['opponent']}"
        + (f"  (pozor: {g['snacks']} přiřazených svačinek)" if g["snacks"] else "")
        for g in ghosts
    ]
    return stats
