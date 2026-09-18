import os

import streamlit as st


def _bootstrap_env() -> None:
    """Secrets ze Streamlitu do env, aby db.py nemusel znát Streamlit.

    Musí proběhnout dřív, než se sáhne na connection pool.
    """
    for key in (
        "DATABASE_URL",
        "TYMUJ_EMAIL",
        "TYMUJ_PASSWORD",
        "TYMUJ_TEAM_ID",
        "ADMIN_PASSWORD",
        "VIEWER_PASSWORD",
    ):
        try:
            if key in st.secrets:
                os.environ.setdefault(key, str(st.secrets[key]))
        except FileNotFoundError:
            pass  # lokální běh bez secrets.toml — env je nastavené z .env


_bootstrap_env()

import hmac  # noqa: E402
from datetime import datetime  # noqa: E402

import pandas as pd  # noqa: E402

from snacks.config import (  # noqa: E402
    ELIGIBLE_SQL,
    EXCLUDED_PARAM,
    FAIRNESS_ORDER,
    LOCAL_TZ,
    NAME_COLLATION,
    TARGET_SNACK_COUNT,
)
from snacks.db import get_conn, init_db  # noqa: E402
from snacks.ingest import last_run, run_ingest  # noqa: E402
from snacks.logic import (  # noqa: E402
    assign_single_person,
    get_assigned_for_match,
    unassign_snack,
)
from snacks.tymuj import TymujNotConfigured  # noqa: E402

EXCLUDED = EXCLUDED_PARAM


def fmt(dt: datetime | None, with_time: bool = True) -> str:
    if dt is None:
        return "—"
    local = dt.astimezone(LOCAL_TZ)
    return local.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


# -----------------------------
# Přihlášení / role
# -----------------------------
def require_role() -> str:
    if "role" not in st.session_state:
        st.session_state.role = None

    st.sidebar.header("🔐 Přístup")

    if st.session_state.role in ("admin", "viewer"):
        label = "Správce" if st.session_state.role == "admin" else "Hráčka"
        st.sidebar.success(f"Přihlášen/a jako: {label}")
        if st.sidebar.button("Odhlásit se"):
            st.session_state.role = None
            st.rerun()
        return st.session_state.role

    role_ui = st.sidebar.selectbox("Role", ["Hráčka", "Správce"])
    password = st.sidebar.text_input("Heslo", type="password")

    if st.sidebar.button("Přihlásit se"):
        role = "viewer" if role_ui == "Hráčka" else "admin"
        expected = os.environ.get(
            "VIEWER_PASSWORD" if role == "viewer" else "ADMIN_PASSWORD", ""
        )

        # Fail closed: bez nastaveného hesla se nedá přihlásit vůbec.
        # Dřív tu byl default "", takže prázdné heslo prošlo.
        if not expected:
            st.sidebar.error("Heslo pro tuhle roli není na serveru nastavené.")
        elif hmac.compare_digest(password, expected):
            st.session_state.role = role
            st.rerun()
        else:
            st.sidebar.error("Špatné heslo.")

    st.stop()


# -----------------------------
# Načítání dat
# -----------------------------
def load_snack_overview() -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT p.name AS player_name,
                   COUNT(m.id) AS snacks_done,
                   MAX(m.match_datetime) AS last_snack
            FROM players p
            LEFT JOIN snack_assignments sa ON sa.player_id = p.id
            LEFT JOIN matches m ON m.id = sa.match_id AND m.status = 'active'
            WHERE {ELIGIBLE_SQL}
            GROUP BY p.id, p.name
            ORDER BY {FAIRNESS_ORDER}
            """,
            (EXCLUDED,),
        ).fetchall()

    return pd.DataFrame(
        [
            {
                "Jméno": r["player_name"],
                "Počet svačinek": r["snacks_done"],
                "Datum poslední svačinky": fmt(r["last_snack"], with_time=False),
            }
            for r in rows
        ]
    )


def load_upcoming_matches():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, match_datetime, opponent
            FROM matches
            WHERE match_datetime >= now() AND status = 'active'
            ORDER BY match_datetime
            """
        ).fetchall()


def load_registrations(match_id: int):
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT p.id, p.name
            FROM registrations r
            JOIN players p ON p.id = r.player_id
            WHERE r.match_id = %s AND {ELIGIBLE_SQL}
            ORDER BY p.name COLLATE "{NAME_COLLATION}"
            """,
            (match_id, EXCLUDED),
        ).fetchall()


def load_match_queue(match_id: int) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT p.name AS player_name,
                   COUNT(m_all.id) AS snacks_done,
                   MAX(m_all.match_datetime) AS last_snack,
                   bool_or(sa_match.id IS NOT NULL) AS is_assigned
            FROM registrations r
            JOIN players p ON p.id = r.player_id
            LEFT JOIN snack_assignments sa_all ON sa_all.player_id = p.id
            LEFT JOIN matches m_all
                   ON m_all.id = sa_all.match_id AND m_all.status = 'active'
            LEFT JOIN snack_assignments sa_match
                   ON sa_match.player_id = p.id AND sa_match.match_id = %s
            WHERE r.match_id = %s AND {ELIGIBLE_SQL}
            GROUP BY p.id, p.name
            ORDER BY is_assigned ASC, {FAIRNESS_ORDER}
            """,
            (match_id, match_id, EXCLUDED),
        ).fetchall()

    return pd.DataFrame(
        [
            {
                "Pořadí": i + 1,
                "Jméno": r["player_name"],
                "Počet svačinek": r["snacks_done"],
                "Datum poslední svačinky": fmt(r["last_snack"], with_time=False),
                "Už přiřazena": "Ano" if r["is_assigned"] else "Ne",
            }
            for i, r in enumerate(rows)
        ]
    )


# -----------------------------
# Administrace importu
# -----------------------------
def _show_import_result(stats) -> None:
    st.success(f"Hotovo — {stats.summary()}")
    for c in stats.cancelled:
        st.warning(f"Zrušeno/přesunuto: {c}")

    for change in getattr(stats, "autofill", []):
        lines = [f"**{change['match']}**"]
        if change["removed"]:
            lines.append(f"odhlásily se: {', '.join(change['removed'])}")
        if change["added"]:
            lines.append(f"nově na svačinky: {', '.join(change['added'])}")
        st.info("  \n".join(lines))

    ignored = getattr(stats, "ignored_headers", None)
    if ignored:
        # Kontrola pro případ, že se v Týmuj zas změní pojmenování zápasů.
        with st.expander(f"Sloupce vyhodnocené jako „není zápas“ ({len(ignored)})"):
            for h in ignored:
                st.write(f"- {h}")


def render_import_admin() -> None:
    st.subheader("🔄 Import dat z Týmuj")

    with get_conn() as conn:
        run = last_run(conn)

    if run is None:
        st.info("Zatím neproběhl žádný import.")
    else:
        when = fmt(run["started_at"])
        if run["status"] == "ok":
            st.success(f"Poslední import: {when} ({run['source']}) — {run['message']}")
        elif run["status"] == "running":
            st.warning(f"Import běží od {when} ({run['source']}).")
        else:
            st.error(
                f"Poslední import selhal: {when} ({run['source']}) — {run['message']}"
            )

    col1, col2 = st.columns(2)

    with col1:
        st.caption("Stejný import, jaký běží každý den automaticky.")
        if st.button("▶️ Stáhnout z Týmuj teď", use_container_width=True):
            with st.spinner("Stahuju z Týmuj a importuju…"):
                try:
                    stats = run_ingest(source="manual")
                except TymujNotConfigured as exc:
                    st.warning(str(exc))
                except Exception as exc:
                    st.error(f"Import selhal: {exc}")
                else:
                    _show_import_result(stats)

    with col2:
        st.caption("Záloha, když je Týmuj nedostupný.")
        uploaded = st.file_uploader("Nahraj export ručně", type=["xlsx", "xls"])
        if uploaded is not None and st.button(
            "Naimportovat soubor", use_container_width=True
        ):
            with st.spinner("Importuju…"):
                try:
                    stats = run_ingest(source="upload", data=uploaded.getvalue())
                except Exception as exc:
                    st.error(f"Import selhal: {exc}")
                else:
                    _show_import_result(stats)


# -----------------------------
# App
# -----------------------------
def main():
    st.set_page_config(page_title="Svačinkový plánovač", layout="wide")
    st.title("🏒 Svačinkový plánovač — HC Kobra ženy 🐍")

    init_db()
    role = require_role()

    st.header("🥨 Přehled svačinek")
    st.caption(
        "Nahoře jsou hráčky, které jsou nejvíc na řadě "
        "(nikdy nepřinesly nebo přinesly dávno)."
    )
    st.caption("Trenéři a neaktivní jsou vyloučení. Zrušené zápasy se nepočítají.")
    st.dataframe(load_snack_overview(), hide_index=True)

    st.divider()
    st.header("📅 Svačinky na nadcházející zápasy")

    matches = load_upcoming_matches()
    if not matches:
        st.info("Žádné nadcházející zápasy. Naimportuj aktuální export z Týmuj.")
        if role == "admin":
            st.divider()
            st.header("🛠️ Správce")
            render_import_admin()
        return

    display = [f"{fmt(m['match_datetime'])} — {m['opponent']}" for m in matches]
    idx = st.selectbox(
        "Vyber zápas", range(len(matches)), format_func=lambda i: display[i]
    )
    match = matches[idx]

    assigned = get_assigned_for_match(match["id"])

    st.subheader("Aktuálně přiřazené svačinky")
    if not assigned:
        st.info("Na tento zápas zatím nejsou přiřazené svačinky.")
    else:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Hráčka": a["player_name"],
                        "Dobrovolník": bool(a["is_volunteer"]),
                        "Přiřazeno": fmt(a["assigned_at"]),
                    }
                    for a in assigned
                ]
            ),
            hide_index=True,
        )

    st.divider()
    st.subheader("📋 Pořadí pro vybraný zápas")
    st.caption(
        "Jen přihlášené hráčky na vybraný zápas, řazené stejnou logikou jako návrh. "
        "Když někdo odpadne, další nepřiřazená hráčka nahoře je na řadě."
    )

    queue = load_match_queue(match["id"])
    if queue.empty:
        st.info("Na tento zápas nejsou žádné způsobilé přihlášené hráčky.")
    else:
        st.dataframe(queue, hide_index=True)

    if role != "admin":
        return

    st.divider()
    st.header("🛠️ Správce")
    render_assignment_admin(match, assigned)

    # Import je až úplně dole — používá se zřídka a nemá odtlačovat
    # přiřazování svačinek, kvůli kterému sem správce chodí.
    st.divider()
    render_import_admin()


def render_assignment_admin(match, assigned) -> None:
    st.caption(
        f"Svačinky přiřazuje algoritmus sám při každém importu — doplní zápas "
        f"na {TARGET_SNACK_COUNT} hráčky podle pořadí a kdo se ze zápasu "
        f"odhlásí, toho nahradí další v pořadí. Ruční přidání a odebrání níž "
        f"je pro případ, že se někdo chce na svačinky přihlásit dobrovolně."
    )
    if len(assigned) >= TARGET_SNACK_COUNT:
        st.info(
            f"Na tenhle zápas už jsou přiřazené {len(assigned)} hráčky, "
            "takže do něj algoritmus nezasahuje."
        )

    regs = load_registrations(match["id"])
    if not regs:
        st.warning("Žádné způsobilé přihlášené hráčky.")
        return

    name_to_id = {r["name"]: r["id"] for r in regs}
    assigned_ids = {a["player_id"] for a in assigned}

    st.subheader("➕ Přidat hráčku (dobrovolnice)")

    eligible = [r["name"] for r in regs if r["id"] not in assigned_ids]
    if not eligible:
        st.info("Nezbývá žádná způsobilá přihlášená hráčka.")
    else:
        st.caption(
            "Nabízí se jen hráčky přihlášené na tenhle zápas. Kdo na zápas "
            "nejede, se na svačinky přiřadit nedá — a kdyby se odhlásila "
            "později, algoritmus ji odebere a doplní další v pořadí."
        )
        single = st.selectbox("Vyber hráčku", eligible)
        is_vol = st.checkbox("Označit jako dobrovolnici", value=True)
        if st.button("Přidat na svačinky"):
            if assign_single_person(match["id"], name_to_id[single], is_vol):
                st.success(f"Přiřazeno: {single}.")
            else:
                st.warning(f"{single} už je přiřazen(a).")
            st.rerun()

    st.divider()
    st.subheader("🗑️ Odebrat přiřazení")

    if not assigned:
        st.info("Není co odebrat.")
    else:
        options = {
            f"{a['player_name']}"
            f"{' — dobrovolnice' if a['is_volunteer'] else ''}"
            f" (přiřazeno {fmt(a['assigned_at'])})": a["assignment_id"]
            for a in assigned
        }
        label = st.selectbox("Vyber přiřazení k odebrání", list(options.keys()))
        st.caption(
            "Uvolněné místo doplní algoritmus při dalším importu — "
            "nebo rovnou přidej náhradu ručně výš."
        )
        if st.button("Odebrat vybranou hráčku"):
            unassign_snack(options[label])
            st.success("Přiřazení bylo odebráno.")
            st.rerun()


if __name__ == "__main__":
    main()
