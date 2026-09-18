"""Logika výběru a správy svačinek.

Přiřazování je automatické. Po každém importu se pro každý nadcházející zápas
spustí `autofill_match`, která:

  1. odebere automaticky přiřazené hráčky, které se ze zápasu odhlásily,
  2. doplní zápas na TARGET_SNACK_COUNT podle férového pořadí.

Dvě pravidla, která to celé drží pohromadě:

  * **Zápas, kde už jsou aspoň dvě přiřazené, se nechává být.** I kdyby podle
    pořadí měl jít někdo jiný. Když se někdo přihlásí dobrovolně, nesmí ho
    algoritmus zpětně přepsat.
  * **Kdo na zápas nejede, na něm nemá svačinku.** Platí i pro dobrovolnice —
    odebere se stejně jako automaticky přiřazená a doplní se další v pořadí.

Příznak `is_volunteer` tedy jen říká, že hráčku přidal člověk, ne algoritmus.
Nechrání ji před odebráním; vidí se v administraci a v logu.

Pozor: `registrations` obsahuje jen hráčky s odpovědí „JDE". Kdo ještě
neodpověděl, je z pohledu téhle logiky stejně „nejede" jako ten, kdo dal
„NEJDE".
"""

from .config import (
    ELIGIBLE_SQL,
    EXCLUDED_PARAM,
    FAIRNESS_ORDER,
    LOCAL_TZ,
    TARGET_SNACK_COUNT,
)
from .db import get_conn

EXCLUDED = EXCLUDED_PARAM

# Pořadí se počítá v SQL, ne v Pythonu — aby bylo seřazené úplně stejně jako
# tabulka v UI (Python řadí jména podle kódů znaků, Postgres podle češtiny,
# což by při shodě počtu svačinek dalo jiné pořadí).
RANKED_CANDIDATES_SQL = f"""
    SELECT p.id, p.name AS player_name,
           COUNT(m.id) AS snacks_done,
           MAX(m.match_datetime) AS last_snack
    FROM registrations r
    JOIN players p ON p.id = r.player_id
    LEFT JOIN snack_assignments sa ON sa.player_id = p.id
    LEFT JOIN matches m ON m.id = sa.match_id AND m.status = 'active'
    WHERE r.match_id = %s
      AND {ELIGIBLE_SQL}
      AND NOT EXISTS (
          SELECT 1 FROM snack_assignments x
          WHERE x.match_id = %s AND x.player_id = p.id
      )
    GROUP BY p.id, p.name
    ORDER BY {FAIRNESS_ORDER}
"""


def next_in_line(conn, match_id: int, limit: int):
    """Hráčky, které jsou na daný zápas nejvíc na řadě a ještě nejsou přiřazené."""
    if limit <= 0:
        return []
    rows = conn.execute(
        RANKED_CANDIDATES_SQL, (match_id, EXCLUDED, match_id)
    ).fetchall()
    return rows[:limit]


def autofill_match(conn, match_id: int) -> dict:
    """Srovná přiřazení u jednoho zápasu. Vrací co odebrala a co přidala."""
    removed: list[str] = []

    # 1) Kdo na zápas nejede, nemůže na něj nést svačinku — a to ani
    #    dobrovolnice. Ta se odebere stejně jako automaticky přiřazená
    #    a místo ní nastoupí další v pořadí.
    for row in conn.execute(
        """
        DELETE FROM snack_assignments sa
        USING players p
        WHERE sa.player_id = p.id
          AND sa.match_id = %s
          AND NOT EXISTS (
              SELECT 1 FROM registrations r
              WHERE r.match_id = sa.match_id AND r.player_id = sa.player_id
          )
        RETURNING p.name
        """,
        (match_id,),
    ).fetchall():
        removed.append(row["name"])

    # 2) Doplnit na cílový počet. Když už jich tam je dost, nesahat na to.
    current = conn.execute(
        "SELECT COUNT(*) AS n FROM snack_assignments WHERE match_id = %s",
        (match_id,),
    ).fetchone()["n"]

    added: list[str] = []
    for candidate in next_in_line(conn, match_id, TARGET_SNACK_COUNT - current):
        conn.execute(
            """
            INSERT INTO snack_assignments (match_id, player_id, is_volunteer)
            VALUES (%s, %s, FALSE)
            ON CONFLICT (match_id, player_id) DO NOTHING
            """,
            (match_id, candidate["id"]),
        )
        added.append(candidate["player_name"])

    return {"removed": removed, "added": added}


def autofill_upcoming(conn) -> list[dict]:
    """Projde všechny nadcházející zápasy a srovná u nich přiřazení.

    Jde se chronologicky a po každém zápase se pořadí počítá znovu, aby se
    hráčka přiřazená na bližší zápas rovnou posunula dozadu u toho dalšího.
    Minulé zápasy se nikdy nemění — jsou to fakta.
    """
    matches = conn.execute(
        """
        SELECT id, opponent, match_datetime
        FROM matches
        WHERE match_datetime >= now() AND status = 'active'
        ORDER BY match_datetime
        """
    ).fetchall()

    changes = []
    for match in matches:
        result = autofill_match(conn, match["id"])
        if result["removed"] or result["added"]:
            # Do místního času — Postgres vrací timestamptz v UTC a popisek
            # by pak neseděl s časem u výběru zápasu.
            when = match["match_datetime"].astimezone(LOCAL_TZ)
            changes.append(
                {
                    "match": f"{when:%d.%m.%Y %H:%M} — {match['opponent']}",
                    **result,
                }
            )
    return changes


def get_assigned_for_match(match_id: int):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT sa.id AS assignment_id, p.id AS player_id, p.name AS player_name,
                   sa.is_volunteer, sa.assigned_at
            FROM snack_assignments sa
            JOIN players p ON p.id = sa.player_id
            WHERE sa.match_id = %s
            ORDER BY sa.assigned_at ASC
            """,
            (match_id,),
        ).fetchall()


def unassign_snack(assignment_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM snack_assignments WHERE id = %s", (assignment_id,))


def assign_single_person(match_id: int, player_id: int, is_volunteer: bool = True):
    """Ruční přiřazení. Vrací False, pokud už hráčka přiřazená je."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO snack_assignments (match_id, player_id, is_volunteer)
            VALUES (%s, %s, %s)
            ON CONFLICT (match_id, player_id) DO NOTHING
            """,
            (match_id, player_id, is_volunteer),
        )
        return cur.rowcount > 0
