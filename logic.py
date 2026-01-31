from db import get_connection

SNACK_EXCLUDED_GROUPS = {"Trenéři", "Neaktivní"}

def _snack_stats(conn):
    rows = conn.execute(
        """
        SELECT p.id, COUNT(sa.id) AS snacks_done, MAX(m.match_datetime) AS last_snack
        FROM players p
        LEFT JOIN snack_assignments sa ON sa.player_id = p.id
        LEFT JOIN matches m ON m.id = sa.match_id
        GROUP BY p.id
        """
    ).fetchall()
    return {r["id"]: (r["snacks_done"], r["last_snack"]) for r in rows}

def preview_snack_pair(match_id: int, volunteer_ids=None):
    volunteer_ids = set(volunteer_ids or [])
    conn = get_connection()

    # Who is registered for this match?
    regs = conn.execute(
        """
        SELECT p.id, p.name
        FROM registrations r
        JOIN players p ON p.id=r.player_id
        WHERE r.match_id=?
        AND p.group_name NOT IN ('Trenéři', 'Neaktivní')
        """,
        (match_id,),
    ).fetchall()

    # Exclude those already assigned for this match
    already = {
        r["player_id"]
        for r in conn.execute("SELECT player_id FROM snack_assignments WHERE match_id=?", (match_id,))
    }
    regs = [r for r in regs if r["id"] not in already]

    stats = _snack_stats(conn)

    # Sort helper (fairness)
    def key(r):
        snacks_done, last_snack = stats.get(r["id"], (0, None))
        sortable_last = last_snack or "0000-00-00 00:00"
        return (snacks_done, sortable_last, r["name"])

    # Volunteers first (still fairness among them)
    volunteers = sorted([r for r in regs if r["id"] in volunteer_ids], key=key)
    non_vols   = sorted([r for r in regs if r["id"] not in volunteer_ids], key=key)

    chosen = volunteers[:2]
    if len(chosen) < 2:
        chosen += non_vols[: (2 - len(chosen))]

    conn.close()
    return chosen  # list of rows with ["id","name"]

def assign_snack_pair(match_id: int, volunteer_ids=None):
    chosen = preview_snack_pair(match_id, volunteer_ids=volunteer_ids)

    volunteer_set = set(volunteer_ids or [])

    conn = get_connection()
    for r in chosen:
        is_volunteer = r["id"] in volunteer_set
        conn.execute(
            "INSERT INTO snack_assignments (match_id, player_id, is_volunteer) VALUES (?, ?, ?)",
            (match_id, r["id"], int(is_volunteer)),
        )
    conn.commit()
    conn.close()
    return chosen

def get_assigned_for_match(match_id: int):
    """Return list of currently assigned snack bringers for a match."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT sa.id AS assignment_id, p.id AS player_id, p.name AS player_name,
               sa.is_volunteer, sa.assigned_at
        FROM snack_assignments sa
        JOIN players p ON p.id = sa.player_id
        WHERE sa.match_id = ?
        ORDER BY sa.assigned_at ASC
        """,
        (match_id,),
    ).fetchall()
    conn.close()
    return rows


def unassign_snack(assignment_id: int):
    """Delete a snack assignment by assignment row id."""
    conn = get_connection()
    conn.execute("DELETE FROM snack_assignments WHERE id = ?", (assignment_id,))
    conn.commit()
    conn.close()


def assign_single_person(match_id: int, player_id: int, is_volunteer: bool = False):
    """Assign snacks for a match to one specific person (if not already assigned)."""
    conn = get_connection()

    # Prevent duplicates for same match+player (soft check)
    existing = conn.execute(
        "SELECT 1 FROM snack_assignments WHERE match_id = ? AND player_id = ?",
        (match_id, player_id),
    ).fetchone()

    if existing:
        conn.close()
        return False

    conn.execute(
        "INSERT INTO snack_assignments (match_id, player_id, is_volunteer) VALUES (?, ?, ?)",
        (match_id, player_id, int(is_volunteer)),
    )
    conn.commit()
    conn.close()
    return True