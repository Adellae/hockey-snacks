"""Stažení exportu přihlášek z Týmuj.cz.

Týmuj nemá veřejné API, ale webová aplikace jede nad vlastním a dá se použít
obyčejným HTTP requestem — headless prohlížeč není potřeba. Endpointy jsou
odkoukané z app.tymuj.cz (září 2026):

1) přihlášení  POST https://api2.tymuj.cz/graphql
   GraphQL mutace userLogin(data: UserLoginInput) -> tokens { jwt }
   UserLoginInput má pole `username` (e-mail) a `password`.

2) export      POST https://api2.tymuj.cz/event/attendance/export
   hlavička Authorization: Bearer <jwt>
   tělo {"past": false, "teamId": "...", "upcoming": true}
   vrací rovnou .xlsx (stejný soubor jako tlačítko "Download export")

Není to oficiální rozhraní, takže se může bez varování změnit. Když se to
stane, importér to nahlásí a v administraci zbývá ruční nahrání souboru.
"""

import logging
import os
import time
from dataclasses import dataclass

import httpx

from . import config  # noqa: F401  -- načte .env do prostředí

log = logging.getLogger(__name__)

API_URL = "https://api2.tymuj.cz"
GRAPHQL_PATH = "/graphql"
EXPORT_PATH = "/event/attendance/export"

LOGIN_MUTATION = """
mutation SignIn($data: UserLoginInput!) {
  userLogin(data: $data) {
    tokens { jwt }
  }
}
"""

TIMEOUT = httpx.Timeout(60.0, connect=15.0)

# Týmuj občas vrátí 502/504 (viděno při prvním ostrém běhu). Kvůli jednomu
# výpadku nemá denní import padat — zkusí se to ještě dvakrát.
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 5

XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class TymujError(RuntimeError):
    pass


class TymujNotConfigured(TymujError):
    pass


@dataclass
class TymujCredentials:
    username: str
    password: str
    team_id: str

    @classmethod
    def from_env(cls) -> "TymujCredentials":
        missing = [
            k
            for k in ("TYMUJ_EMAIL", "TYMUJ_PASSWORD", "TYMUJ_TEAM_ID")
            if not os.environ.get(k)
        ]
        if missing:
            raise TymujNotConfigured(f"Chybí proměnné prostředí: {', '.join(missing)}")
        return cls(
            username=os.environ["TYMUJ_EMAIL"],
            password=os.environ["TYMUJ_PASSWORD"],
            team_id=os.environ["TYMUJ_TEAM_ID"],
        )


def _post_with_retry(client: httpx.Client, path: str, **kwargs) -> httpx.Response:
    """POST, který přežije krátkodobý výpadek Týmuj.

    Opakuje se jen u dočasných chyb (5xx, 429, výpadek sítě). Chyby jako
    401 nebo 403 se neopakují — špatné heslo opakováním nespravíme.
    """
    last_exc: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = client.post(path, **kwargs)
            if resp.status_code not in RETRY_STATUSES:
                resp.raise_for_status()
                return resp
            last_exc = httpx.HTTPStatusError(
                f"HTTP {resp.status_code}", request=resp.request, response=resp
            )
        except httpx.TransportError as exc:  # timeout, DNS, spadlé spojení
            last_exc = exc

        if attempt < MAX_ATTEMPTS:
            wait = RETRY_BACKOFF_SECONDS * attempt
            log.warning(
                "Týmuj neodpověděl (%s), pokus %d/%d, čekám %ds…",
                last_exc, attempt, MAX_ATTEMPTS, wait,
            )
            time.sleep(wait)

    raise TymujError(
        f"Týmuj neodpověděl ani po {MAX_ATTEMPTS} pokusech: {last_exc}"
    ) from last_exc


def login(client: httpx.Client, creds: TymujCredentials) -> str:
    resp = _post_with_retry(
        client,
        GRAPHQL_PATH,
        json={
            "query": LOGIN_MUTATION,
            "variables": {
                "data": {"username": creds.username, "password": creds.password}
            },
        },
    )
    payload = resp.json()

    # GraphQL vrací chyby s kódem 200, takže nestačí raise_for_status.
    if payload.get("errors"):
        msg = payload["errors"][0].get("message", "neznámá chyba")
        raise TymujError(f"Přihlášení do Týmuj selhalo: {msg}")

    jwt = (payload.get("data") or {}).get("userLogin", {}).get("tokens", {}).get("jwt")
    if not jwt:
        raise TymujError(
            "V odpovědi na přihlášení chybí token. Týmuj nejspíš změnil API — "
            "zkontroluj LOGIN_MUTATION v tymuj.py."
        )
    return jwt


def fetch_export(creds: TymujCredentials | None = None) -> bytes:
    """Vrátí .xlsx export přihlášek jako bytes."""
    creds = creds or TymujCredentials.from_env()

    with httpx.Client(base_url=API_URL, timeout=TIMEOUT, follow_redirects=True) as client:
        jwt = login(client, creds)

        resp = _post_with_retry(
            client,
            EXPORT_PATH,
            headers={"Authorization": f"Bearer {jwt}"},
            json={"past": False, "teamId": str(creds.team_id), "upcoming": True},
        )

        ctype = resp.headers.get("content-type", "")
        if XLSX_CONTENT_TYPE not in ctype:
            raise TymujError(
                f"Čekal se .xlsx, přišlo '{ctype}'. Export se nejspíš nepovedl."
            )
        # .xlsx je ZIP, takže musí začínat 'PK'. Chytí to i prázdnou odpověď.
        if not resp.content.startswith(b"PK"):
            raise TymujError("Odpověď není platný .xlsx soubor.")
        return resp.content


if __name__ == "__main__":
    data = fetch_export()
    print(f"Staženo {len(data)} bajtů.")
