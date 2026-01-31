import streamlit as st
import pandas as pd
from datetime import datetime

from db import get_connection, init_db
from logic import (
    preview_snack_pair,
    assign_snack_pair,
    get_assigned_for_match,
    unassign_snack,
    assign_single_person,
)

EXCLUDED_GROUPS = ("Trenéři", "Neaktivní")

# -----------------------------
# Přihlášení / role
# -----------------------------
def require_role() -> str:
    """
    Jednoduché role přes Streamlit secrets.
    Vytvoř .streamlit/secrets.toml s:
      ADMIN_PASSWORD = "..."
      VIEWER_PASSWORD = "..."
    """
    if "role" not in st.session_state:
        st.session_state.role = None

    st.sidebar.header("🔐 Přístup")

    # Už přihlášen(a)
    if st.session_state.role in ("admin", "viewer"):
        role_label = "Správce" if st.session_state.role == "admin" else "Hráčka"
        st.sidebar.success(f"Přihlášen/a jako: {role_label}")
        if st.sidebar.button("Odhlásit se"):
            st.session_state.role = None
            st.rerun()
        return st.session_state.role

    role_choice_ui = st.sidebar.selectbox("Role", ["Hráčka", "Správce"])
    password = st.sidebar.text_input("Heslo", type="password")

    if st.sidebar.button("Přihlásit se"):
        viewer_pw = st.secrets.get("VIEWER_PASSWORD", "")
        admin_pw = st.secrets.get("ADMIN_PASSWORD", "")

        role_choice = "viewer" if role_choice_ui == "Hráčka" else "admin"

        if role_choice == "viewer" and password == viewer_pw:
            st.session_state.role = "viewer"
            st.rerun()
        elif role_choice == "admin" and password == admin_pw:
            st.session_state.role = "admin"
            st.rerun()
        else:
            st.sidebar.error("Špatné heslo.")

    st.stop()


# -----------------------------
# Načítání dat
# -----------------------------
def load_snack_overview() -> pd.DataFrame:
    """
    Přehled svačinek (bez trenérů/neaktivních).
    Řazení: snacks_done ASC, last_snack_datetime ASC (nejstarší nahoře), jméno ASC.
    Hráčky, které nikdy nenosily, jsou nahoře.
    """
    conn = get_connection()
    df = pd.read_sql_query(
        f"""
        SELECT
            p.name AS player_name,
            COUNT(sa.id) AS snacks_done,
            MAX(m.match_datetime) AS last_snack_datetime
        FROM players p
        LEFT JOIN snack_assignments sa ON sa.player_id = p.id
        LEFT JOIN matches m ON m.id = sa.match_id
        WHERE COALESCE(p.group_name, '') NOT IN ({",".join(["?"] * len(EXCLUDED_GROUPS))})
        GROUP BY p.id
        """,
        conn,
        params=list(EXCLUDED_GROUPS),
    )
    conn.close()

    df["last_snack_datetime"] = pd.to_datetime(df["last_snack_datetime"])
    df = df.sort_values(
        by=["snacks_done", "last_snack_datetime", "player_name"],
        ascending=[True, True, True],
        na_position="first",
    )
    df["last_snack_date"] = df["last_snack_datetime"].dt.date
    df = df[["player_name", "snacks_done", "last_snack_date"]]
    df = df.rename(
        columns={
            "player_name": "Jméno",
            "snacks_done": "Počet svačinek",
            "last_snack_date": "Datum poslední svačinky",
        }
    )
    return df


def load_upcoming_matches():
    """Jen nadcházející zápasy (match_datetime >= teď)."""
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = get_connection()
    matches = conn.execute(
        """
        SELECT id, match_datetime, opponent
        FROM matches
        WHERE match_datetime >= ?
        ORDER BY match_datetime
        """,
        (now_iso,),
    ).fetchall()
    conn.close()
    return matches


def load_registrations(match_id: int):
    """Přihlášené hráčky na zápas (bez trenérů/neaktivních)."""
    conn = get_connection()
    regs = conn.execute(
        f"""
        SELECT p.id, p.name
        FROM registrations r
        JOIN players p ON p.id = r.player_id
        WHERE r.match_id = ?
          AND COALESCE(p.group_name, '') NOT IN ({",".join(["?"] * len(EXCLUDED_GROUPS))})
        ORDER BY p.name
        """,
        (match_id, *EXCLUDED_GROUPS),
    ).fetchall()
    conn.close()
    return regs


# -----------------------------
# App
# -----------------------------
def main():
    st.set_page_config(page_title="Svačinkový plánovač", layout="wide")
    st.title("🏒 Svačinkový plánovač — HC Kobra ženy 🐍")

    init_db()
    role = require_role()

    # -----------------------------
    # Hráčka/Správce: přehled svačinek
    # -----------------------------
    st.header("🥨 Přehled svačinek")
    st.caption("Nahoře jsou hráčky, které jsou nejvíc „na řadě“ (nikdy nenosily nebo nosily dávno).")
    st.caption("Trenéři a neaktivní jsou vyloučení.")
    overview = load_snack_overview()
    st.dataframe(overview, hide_index=True)

    st.divider()

    # -----------------------------
    # Hráčka/Správce: zobrazení přiřazení pro nadcházející zápasy
    # -----------------------------
    st.header("📅 Svačinky na nadcházející zápasy (zobrazení)")

    matches = load_upcoming_matches()
    if not matches:
        st.info("Nebyly nalezeny žádné nadcházející zápasy. Nejdřív naimportuj export.")
        return

    match_display = [f"{m['match_datetime']} — {m['opponent']}" for m in matches]
    idx = st.selectbox("Vyber zápas", range(len(matches)), format_func=lambda i: match_display[i])
    match = matches[idx]

    assigned = get_assigned_for_match(match["id"])

    if not assigned:
        st.info("Na tento zápas zatím nejsou přiřazené svačinky.")
    else:
        assigned_df = pd.DataFrame(
            [
                {
                    "Hráčka": a["player_name"],
                    "Dobrovolník": bool(a["is_volunteer"]),
                    "Přiřazeno": a["assigned_at"],
                }
                for a in assigned
            ]
        )
        st.dataframe(assigned_df, hide_index=True)

    # Hráčka končí tady
    if role != "admin":
        return

    # -----------------------------
    # Správce: správa přiřazení
    # -----------------------------
    st.divider()
    st.header("🛠️ Správce: správa svačinek")

    regs = load_registrations(match["id"])
    if not regs:
        st.warning("Žádné způsobilé přihlášené hráčky (po vyloučení trenérů/neaktivních).")
        return

    name_to_id = {r["name"]: r["id"] for r in regs}
    assigned_player_ids = {a["player_id"] for a in assigned}

    # --- Odebrání přiřazení ---
    st.subheader("🗑️ Odebrat přiřazení")
    if not assigned:
        st.info("Není co odebrat.")
    else:
        delete_options = {
            f"{a['player_name']} (přiřazeno {a['assigned_at']})": a["assignment_id"]
            for a in assigned
        }
        to_delete_label = st.selectbox("Vyber přiřazení k odebrání", list(delete_options.keys()))
        if st.button("Odebrat vybranou hráčku"):
            unassign_snack(delete_options[to_delete_label])
            st.success("Přiřazení bylo odebráno.")
            st.rerun()

    st.divider()

    # --- Návrh dvojice ---
    st.subheader("🤝 Navrhnout dvojici na svačinky")

    volunteer_names = st.multiselect(
        "Dobrovolníci (volitelné)",
        options=[r["name"] for r in regs if r["id"] not in assigned_player_ids],
    )
    volunteer_ids = [name_to_id[n] for n in volunteer_names]

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Náhled návrhu dvojice"):
            chosen = preview_snack_pair(match["id"], volunteer_ids=volunteer_ids)
            if len(chosen) < 2:
                st.warning("Je k dispozici méně než 2 způsobilé hráčky.")
            st.write("Navržená dvojice:")
            for r in chosen:
                st.write(f"- {r['name']}")

    with col2:
        if st.button("Přiřadit navrženou dvojici (uložit)"):
            chosen = assign_snack_pair(match["id"], volunteer_ids=volunteer_ids)
            st.success("Přiřazení svačinek uloženo ✅")
            for r in chosen:
                st.write(f"- {r['name']}")
            st.rerun()

    st.divider()

    # --- Ruční přiřazení jedné hráčky ---
    st.subheader("➕ Ruční přiřazení (jedna hráčka)")

    eligible = [r for r in regs if r["id"] not in assigned_player_ids]
    eligible_names = [r["name"] for r in eligible]

    if not eligible_names:
        st.info("Nezbývá žádná způsobilá přihlášená hráčka k přiřazení.")
    else:
        single_name = st.selectbox("Vyber hráčku", eligible_names)
        single_is_volunteer = st.checkbox("Označit jako dobrovolníka", value=False)

        if st.button("Přidat tuto hráčku na svačinky"):
            ok = assign_single_person(
                match_id=match["id"],
                player_id=name_to_id[single_name],
                is_volunteer=single_is_volunteer,
            )
            if ok:
                st.success(f"Přiřazeno: {single_name}.")
            else:
                st.warning(f"{single_name} už je přiřazen(a).")
            st.rerun()


if __name__ == "__main__":
    main()
