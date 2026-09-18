# 🏒 Svačinkový plánovač – HC Kobra Praha ženy

Férové a transparentní plánování svačinek na zápasy.

Aplikace stahuje přihlášky z Týmuj.cz, drží historii, kdo kolikrát nosil,
a navrhuje, kdo je příště na řadě.

---

## 🏗️ Jak to je poskládané

| Část | Kde běží | Co dělá |
|---|---|---|
| Webová appka | Streamlit Community Cloud | UI pro hráčky i správce |
| Databáze | Supabase Postgres (eu-west-1) | jediné trvalé úložiště |
| Denní import | GitHub Actions (cron) | stáhne export z Týmuj a nahraje ho |
| Ruční import | tlačítko v appce | stejný import na kliknutí |

Data jsou **jen v Supabase**. Žádná lokální databáze ani uložené exporty —
import jde rovnou z Týmuj do Postgresu.

```
app.py                  Streamlit UI (vstupní bod, musí zůstat v kořeni)
snacks/
  config.py             sdílené konstanty + načtení .env
  db.py                 připojení k Postgresu + schéma
  logic.py              automatické přiřazování + ruční zásahy
  importer.py           parsování Excel exportu
  tymuj.py              stažení exportu z Týmuj (api2.tymuj.cz)
  ingest.py             orchestrace: stáhni -> naimportuj -> zaloguj
tests/test_headers.py   testy rozpoznávání zápasů a vyloučených skupin
scripts/                jednorázové skripty (migrace ze SQLite)
.github/workflows/      denní cron
```

Testy se spouští i bez pytestu:

```bash
python tests/test_headers.py
```

---

## ⚙️ Lokální spuštění

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # a vyplnit
streamlit run app.py
```

`.env` musí obsahovat `DATABASE_URL` (Supabase **session pooler**, port 5432 —
ne direct connection, ta je jen přes IPv6), `ADMIN_PASSWORD` a `VIEWER_PASSWORD`.

---

## 👥 Role

* **Hráčka** — přehled férovosti a kdo je přiřazený na nadcházející zápasy
* **Správce** — navíc návrh dvojice, ruční přiřazení, odebrání, spuštění importu

Trenéři a neaktivní jsou ze svačinek vyloučení.

Pravidlo je v `EXCLUDED_GROUP_PATTERNS` v `snacks/config.py` a porovnává se
**vzorem, ne přesným jménem**. Důvod: podskupina „Trenéři" se v Týmuj
přejmenovala na „Trenéři a realizační tým" a přesná shoda tiše přestala
platit — trenéři pak spadli mezi kandidátky na svačinky.

---

## 📥 Import dat

Import je **idempotentní** — dá se pouštět opakovaně, nevytváří duplicity.

Tři cesty, všechny dělají přesně totéž:

1. **automaticky** — GitHub Action každý den v 6:30
2. **tlačítkem** — „Stáhnout z Týmuj teď" v administraci
3. **ručně** — nahrání staženého `.xlsx` v administraci (záloha, když Týmuj nejede)

Hráčka je přihlášená, pokud má v exportu hodnotu začínající na `JDE`.

### Přesunuté a zrušené zápasy

Zápasy se identifikují dvojicí (datum+čas, soupeř). Když se zápas přesune,
Týmuj ho v exportu vrátí s jiným časem — což by bez ošetření vytvořilo druhý
záznam a ten původní by zůstal viset jako duch.

Import proto zápasy, které v exportu chybí, označí jako `cancelled`. Pojistky:

* ruší se **jen zápasy v budoucnu** — odehraný zápas je fakt a historie svačinek
  se nikdy nepřepíše,
* **jen uvnitř časového okna exportu** — zápas se stejným soupeřem o pár měsíců
  dřív nebo později zůstane netknutý,
* zápas se **nemaže**, jen označí (můžou na něm viset svačinky),
* když se v dalším exportu zas objeví, vrátí se do `active` — výpadek Týmuj se
  spraví sám.

Zrušené zápasy se nepočítají do férovosti a nezobrazují se jako nadcházející.

---

## 🔌 Napojení na Týmuj

Týmuj nemá veřejné API, ale web jede nad vlastním a dá se použít přímo.
Endpointy odkoukané z app.tymuj.cz (září 2026):

**Přihlášení** — `POST https://api2.tymuj.cz/graphql`

```graphql
mutation SignIn($data: UserLoginInput!) {
  userLogin(data: $data) { tokens { jwt } }
}
```

`UserLoginInput` má `username` (e-mail) a `password`.

**Export** — `POST https://api2.tymuj.cz/event/attendance/export`

```
Authorization: Bearer <jwt>
{"past": false, "teamId": "8773", "upcoming": true}
```

Vrací rovnou `.xlsx`. `teamId` 8773 = HC Kobra Praha ženy.
`upcoming: true, past: false` znamená, že export obsahuje jen nadcházející
události — proto se zrušené zápasy hledají jen v okně exportu.

Není to oficiální rozhraní a může se bez varování změnit. Když se to stane,
import to nahlásí v administraci a zbývá ruční nahrání souboru.

### Pozor na názvy událostí

Názvy si v Týmuj píše každý po svém — v jedné sezóně se objevilo
`HC Kobra Praha ženy vs. X`, `Mamby Tábor vs. HC Kobra Praha ženy`
i `HC KOBRA ŽENY x Karlovy Vary`. Proto se porovnává bez diakritiky a bez
ohledu na velikost písmen, jako oddělovač se bere `vs.`, `vs` i `x`, a soupeř
se určí podle toho, která strana se míň podobá našemu názvu (kvůli zápasům
typu `Kobra B - Fejky vs. HC Kobra Praha ženy`, kde je „Kobra" na obou
stranách).

Sloupce, které import vyhodnotí jako „není zápas", se vypisují
v administraci — kdyby se pojmenování zas změnilo, ať to není vidět až
podle chybějící svačinky.

---

## 🔐 Secrets

| Kde | Co |
|---|---|
| `.env` (lokálně) | vše, negitované |
| Streamlit Cloud → Settings → Secrets | `DATABASE_URL`, `ADMIN_PASSWORD`, `VIEWER_PASSWORD` |
| GitHub → Settings → Secrets → Actions | `DATABASE_URL`, `TYMUJ_EMAIL`, `TYMUJ_PASSWORD`, `TYMUJ_TEAM_ID` |

---

## 🥨 Přiřazování svačinek

**Pořadí férovosti:** méně svačinek → delší doba od poslední → jméno (české
řazení). Hráčky, které nikdy nenosily, jsou nahoře. Pořadí je jedno jediné
(`FAIRNESS_ORDER` v `snacks/config.py`) a používá ho jak algoritmus, tak
tabulka v UI — takže co appka ukazuje nahoře, to algoritmus přiřadí.

**Přiřazuje se automaticky** po každém importu (`autofill_upcoming`
v `snacks/logic.py`). Pro každý nadcházející zápas, chronologicky:

1. odebere přiřazené hráčky, které na zápas nejedou,
2. doplní zápas na `TARGET_SNACK_COUNT` (2) podle pořadí.

Zápasy se procházejí od nejbližšího a pořadí se po každém přepočítá, takže
hráčka přiřazená na bližší zápas se u dalšího rovnou posune dozadu.

### Dvě pravidla, na kterých to stojí

**Zápas, kde už jsou aspoň dvě přiřazené, algoritmus nechává být.** I kdyby
podle pořadí měl jít někdo jiný. Kdo se přihlásí dobrovolně, nesmí být zpětně
přepsaný jen proto, že „ještě není na řadě".

**Kdo na zápas nejede, na něm nemá svačinku.** Platí i pro dobrovolnice —
odeberou se stejně jako automaticky přiřazené a doplní se další v pořadí.
Příznak `is_volunteer` tedy říká jen to, že hráčku přidal člověk, ne
algoritmus; nechrání ji před odebráním.

> `registrations` obsahuje jen hráčky s odpovědí „JDE". Kdo neodpověděl, je
> z pohledu algoritmu stejně „nejede" jako ten, kdo dal „NEJDE". V praxi to
> nevadí: přiřadit se dá jen přihlášená hráčka, takže z přiřazení vypadne
> jedině ta, která si to změnila na „NEJDE".

### Ruční zásahy správce

Slouží jen k tomu, aby se někdo mohl přihlásit dobrovolně místo přiřazených:

* **Přidat hráčku** — nabízí jen hráčky přihlášené na daný zápas.
* **Odebrat přiřazení** — uvolněné místo doplní algoritmus při dalším importu.

Minulé zápasy se nikdy nemění, ani automaticky, ani omylem.

---

## 🚀 Co ještě chybí

* nasadit na Streamlit Community Cloud a nastavit secrets (viz tabulka výš)
* notifikace týmu — plán je tlačítko, které vyrobí zprávu a odkaz
  `wa.me`, kterým se jedním klikem pošle do týmové skupiny
  (oficiální WhatsApp API do skupin posílat neumí)

---

## 🛠️ Poznámky k provozu

* Supabase free tier **uspí projekt po ~týdnu nečinnosti**. Denní Action ho
  přes sezónu drží vzhůru; přes léto počítej s jedním kliknutím na „Restore".
* GitHub vypíná naplánované workflow po 60 dnech bez commitu do repa.
* Streamlit Cloud appku uspává při nečinnosti, první načtení pak chvíli trvá.
