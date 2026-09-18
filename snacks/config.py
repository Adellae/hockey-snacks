"""Sdílená konfigurace projektu.

Načítá taky .env. Je to tady schválně: config importují všechny ostatní
moduly, takže proměnné prostředí jsou k dispozici bez ohledu na to, čím se
program spustí (Streamlit, ingest.py, tymuj.py samotný).
"""

from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    # .env leží v kořeni repozitáře, tedy o úroveň výš než tenhle balíček.
    # Cesta se odvozuje od souboru, ne od aktuálního adresáře, aby šlo
    # skripty spouštět odkudkoli. Ve Streamlit Cloud a v GitHub Actions
    # .env není a proměnné přijdou z prostředí.
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

# Tymuj uvádí časy v místním čase, ne v UTC.
LOCAL_TZ = ZoneInfo("Europe/Prague")

TEAM_NAME = "HC Kobra Praha ženy"
TOURNAMENT = "Turnaj"

# Názvy událostí si v Týmuj píše každý po svém — viděli jsme "HC Kobra Praha
# ženy", "HC KOBRA ŽENY" i oddělovač "x" místo "vs.". Proto se porovnává
# bez diakritiky a bez ohledu na velikost písmen a soupeř se hledá podle toho,
# která strana se víc podobá našemu jménu.
TEAM_KEYWORD = "kobra"

# Oddělovače domácí/hosté. Pomlčka schválně chybí — je i uvnitř názvů
# ("Kobra B - Fejky") a rozsekala by je.
VS_SEPARATORS = (" vs. ", " vs ", " x ", " X ")

# Události, které nejsou zápas. Porovnává se bez diakritiky, malými písmeny.
NON_MATCH_KEYWORDS = ("trenink", "schuze", "brigada", "porada", "soustredeni")

# Kolik hráček nosí svačinky na jeden zápas. Algoritmus zápas doplní na tenhle
# počet a když už ho zápas má, nesahá na něj.
TARGET_SNACK_COUNT = 2

# Skupiny vyloučené ze svačinek. Jediné místo, kde se definují.
#
# Schválně vzory pro ILIKE, ne přesná jména: podskupina "Trenéři" se v Týmuj
# přejmenovala na "Trenéři a realizační tým" a přesná shoda tiše přestala
# platit — trenéři pak spadli mezi kandidátky na svačinky. Vzor přežije
# i další přejmenování.
# Pozor na "trené" bez koncovky: množné číslo je "Trenéři" (s ř), takže
# vzor "%trenér%" by neodpovídal. "%trené%" chytí trenér, trenéři i trenérka.
EXCLUDED_GROUP_PATTERNS = (
    "%trené%",
    "%trene%",       # kdyby to někdo napsal bez diakritiky
    "%realizač%",    # kdyby ze skupiny zbylo jen "Realizační tým"
    "%realizac%",
    "%neaktiv%",
)

# Podmínka způsobilosti. Jedna definice pro appku i logiku výběru.
# COALESCE je tu schválně: hráčka bez vyplněné Podskupiny má group_name NULL
# a NULL v porovnání dává NULL, takže by tiše vypadla z nabídky.
ELIGIBLE_SQL = "COALESCE(p.group_name, '') NOT ILIKE ALL(%s)"
EXCLUDED_PARAM = list(EXCLUDED_GROUP_PATTERNS)

# České řazení jmen (S, Š jako samostatná písmena). Bez tohohle řadí Postgres
# podle en_US a Š se míchá mezi S.
NAME_COLLATION = 'cs-CZ-x-icu'

# Jediná definice férového pořadí. Používá ji návrh dvojice i tabulka v UI,
# aby návrh vždy odpovídal tomu, co je nahoře v tabulce.
# p.name, ne alias player_name: jakmile se na alias použije COLLATE, je z toho
# výraz a Postgres ho hledá mezi vstupními sloupci, kde alias neexistuje.
FAIRNESS_ORDER = (
    'snacks_done ASC, last_snack ASC NULLS FIRST, '
    f'p.name COLLATE "{NAME_COLLATION}" ASC'
)
