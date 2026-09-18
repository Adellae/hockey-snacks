"""Orchestrace importu: stáhni z Týmuj -> naimportuj -> zaloguj běh.

Používá to jak denní GitHub Action, tak tlačítko v administraci, aby obě cesty
dělaly přesně totéž.
"""

import io
import logging

from .db import get_conn
from .importer import ImportStats, import_export
from .logic import autofill_upcoming
from .tymuj import TymujNotConfigured, fetch_export

log = logging.getLogger(__name__)


def _start_run(conn, source: str) -> int:
    return conn.execute(
        "INSERT INTO ingest_runs (source, status) VALUES (%s, 'running') RETURNING id",
        (source,),
    ).fetchone()["id"]


def _finish_run(conn, run_id: int, status: str, stats: ImportStats | None, message: str):
    conn.execute(
        """
        UPDATE ingest_runs
        SET finished_at = now(), status = %s, message = %s,
            matches_seen = %s, players_seen = %s, registrations_written = %s
        WHERE id = %s
        """,
        (
            status,
            message[:2000],
            stats.matches_seen if stats else None,
            stats.players_seen if stats else None,
            stats.registrations_written if stats else None,
            run_id,
        ),
    )


def run_ingest(source: str = "cron", data: bytes | None = None) -> ImportStats:
    """Naimportuje data. Když `data` nejsou, stáhnou se z Týmuj.

    `source` je jen štítek do logu: 'cron', 'manual' nebo 'upload'.
    Běh se loguje i když spadne — jinak by tichý výpadek nebyl poznat.
    """
    with get_conn() as conn:
        run_id = _start_run(conn, source)

    stats: ImportStats | None = None
    try:
        if data is None:
            data = fetch_export()

        with get_conn() as conn:
            stats = import_export(io.BytesIO(data), conn)
            # Přiřazení se srovnává hned po importu, ve stejné transakci:
            # právě teď se mohlo změnit, kdo na zápas jede.
            stats.autofill = autofill_upcoming(conn)
            _finish_run(conn, run_id, "ok", stats, stats.summary())

        log.info("Import (%s) hotov: %s", source, stats.summary())
        for change in stats.autofill:
            log.info(
                "  %s | odebráno: %s | přiřazeno: %s",
                change["match"],
                ", ".join(change["removed"]) or "—",
                ", ".join(change["added"]) or "—",
            )
        return stats

    except Exception as exc:
        # Chyba se zaloguje zvlášť, aby ji nesmazal rollback hlavní transakce.
        with get_conn() as conn:
            _finish_run(conn, run_id, "error", None, f"{type(exc).__name__}: {exc}")
        log.exception("Import (%s) selhal", source)
        raise


def last_run(conn):
    return conn.execute(
        "SELECT * FROM ingest_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        stats = run_ingest(source="cron")
        print(f"OK: {stats.summary()}")
        for c in stats.cancelled:
            print(f"  ZRUŠENO: {c}")
        for ch in stats.autofill:
            print(f"  {ch['match']}")
            if ch["removed"]:
                print(f"     odhlásily se: {', '.join(ch['removed'])}")
            if ch["added"]:
                print(f"     nově na svačinky: {', '.join(ch['added'])}")
    except TymujNotConfigured as exc:
        # Nedokončená konfigurace není důvod shodit celý workflow červeně.
        print(f"PŘESKOČENO: {exc}")
        raise SystemExit(0)
