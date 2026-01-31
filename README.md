# 🏒 Svačinkový plánovač – HC Kobra Praha ženy

Tento projekt slouží k **férovému a transparentnímu plánování svačinek na zápasy** týmu HC Kobra Praha ženy.

Aplikace:

* importuje přihlášky na zápasy z Excelu (oficiální export z Týmuj.cz)
* udržuje historii, kolikrát kdo nosil svačinky
* navrhuje, kdo má být na řadě příště
* umožňuje ruční zásahy (odebrání / nahrazení osoby)
* rozlišuje **role uživatelů** (Hráčka vs. Správce)

---

## 👥 Role uživatelů

### 👀 Hráčka

Má **pouze náhled**:

* přehled férovosti svačinek (kdo je „na řadě“)
* výběr nadcházejícího zápasu a zobrazení, kdo je na něj přiřazen

### 🛠️ Správce

Má plná práva:

* vše, co vidí Hráčka
* návrh dvojice na svačinky
* ruční přiřazení jedné osoby
* odebrání přiřazení (pokud někdo nakonec nemůže)

> Trenéři a neaktivní hráči jsou **automaticky vyloučeni** ze svačinek.

---

## 📦 Struktura projektu

```
hockey-snacks/
├─ app.py                  # Streamlit aplikace (UI)
├─ db.py                   # SQLite schéma + připojení
├─ logic.py                # Logika výběru a správy svačinek
├─ import_excel_kobra.py   # Import Excel exportu
├─ requirements.txt
├─ snacks.db               # SQLite databáze (lokálně, negitovaná)
├─ .gitignore
└─ .streamlit/
   └─ secrets.toml         # Hesla (negitovaná)
```

---

## ⚙️ Instalace (lokálně)

```bash
python -m venv venv
source venv\Scripts\activate    #Windows
pip install -r requirements.txt
```

Inicializace databáze:

```bash
python db.py
```

---

## 🔐 Nastavení přístupu (role)

Vytvoř soubor:

```
.streamlit/secrets.toml
```

A vlož do něj:

```toml
ADMIN_PASSWORD = "nejake-admin-heslo"
VIEWER_PASSWORD = "nejake-viewer-heslo"
```

Do `.gitignore` přidej:

```
.streamlit/secrets.toml
```

---

## 📥 Import dat z Excelu

Používá se **oficiální export přihlášek z Týmuj.cz**

Import:

```bash
python import_excel_kobra.py attendance_export.xlsx
```

Co import dělá:

* detekuje pouze sloupce se zápasy HC Kobra Praha ženy
* hráč je přihlášen, pokud má hodnotu začínající na `JDE`
* **aktualizuje skupinu hráče (Podskupina)**, pokud se změní
* nevytváří duplicity hráčů, ani zápasů
* přihlášky se přepisují, aby byly vždy aktuální

Import můžeš **bezpečně spustit opakovaně** (např. každý týden).

---

## ▶️ Spuštění aplikace

```bash
streamlit run app.py
```

Aplikace se otevře v prohlížeči.

---

## 🥨 Přehled svačinek

Tabulka ukazuje:

* jméno hráčky
* kolikrát už nosila svačinky
* kdy naposledy

Řazení:

1. méně svačinek
2. delší doba od poslední svačinky
3. jméno

Hráčky, které **nikdy nenosily**, jsou nahoře.

---

## 📅 Zápasy

* v UI se zobrazují **pouze nadcházející zápasy**
* minulé zápasy se používají jen pro výpočet historie

---

## 🔄 Ruční zásahy (Správce)

Správce může:

* odebrat přiřazení (když někdo nakonec nemůže)
* ručně přidat náhradníka
* označit dobrovolníka

---

## 🧠 Technické poznámky

* Databáze: SQLite (`snacks.db`)
* Jazyk: Python
* UI: Streamlit
* Projekt je vhodný pro:

  * lokální použití
  * Streamlit Cloud

---

## 🚀 Možná rozšíření do budoucna

* historie svačinek po zápasech
* simulace budoucích zápasů
* export přehledu do PDF / CSV
* jemnější role (např. více správců)