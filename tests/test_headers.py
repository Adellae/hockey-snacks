"""Testy rozpoznávání zápasů z názvů sloupců v exportu.

Tohle je místo, kde už dvakrát tiše zmizel zápas:
  * "HC KOBRA ŽENY x Karlovy Vary" — jiná velikost písmen, bez "Praha"
    a oddělovač "x" místo "vs."
  * podskupina "Trenéři" se přejmenovala na "Trenéři a realizační tým"

Spouští se i bez pytestu:  python tests/test_headers.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from snacks.config import EXCLUDED_GROUP_PATTERNS  # noqa: E402
from snacks.importer import is_match_column, parse_match_header  # noqa: E402

# (název sloupce, očekávaný soupeř nebo None, když to není zápas)
HEADERS = [
    # Tréninky se nikdy nepočítají.
    ("Trénink Kobra (24.9.2026 19:15)", None),
    ("Trénink Kobra (1.10.2026 19:15)", None),
    # Běžný tvar, my doma i venku.
    ("HC Kobra Praha ženy vs. Bílí Tygři Liberec (8.3.2026 16:45)", "Bílí Tygři Liberec"),
    ("Střely Jindřichův Hradec vs. HC Kobra Praha ženy (8.3.2026 12:00)",
     "Střely Jindřichův Hradec"),
    ("FOX Vysočina B vs. HC Kobra Praha ženy (21.3.2026 15:45)", "FOX Vysočina B"),
    ("Mamby Tábor vs. HC Kobra Praha ženy (27.9.2026 14:45)", "Mamby Tábor"),
    # Verzálky, chybí "Praha", oddělovač "x".
    ("HC KOBRA ŽENY x Karlovy Vary (26.9.2026 17:15)", "Karlovy Vary"),
    # "Kobra" je na obou stranách — soupeř je ta méně podobná.
    ("Kobra B - Fejky vs. HC Kobra Praha ženy (1.3.2026 16:45)", "Kobra B - Fejky"),
    # Turnaj nemá soupeře, bere se celý název.
    ("Turnaj v Bílině (4.4.2026 9:00)", "Turnaj v Bílině"),
]

GROUPS = [
    ("Trenéři", False),
    ("Trenéři a realizační tým", False),
    ("Trenérky", False),
    ("Treneri", False),
    ("Neaktivní", False),
    ("Útočníci", True),
    ("Brankářky", True),
    ("Nováčci", True),
    ("Obránci", True),
    ("AHL", True),
]


def _group_eligible(group: str) -> bool:
    """Stejné pravidlo jako ILIKE v SQL, jen v Pythonu."""
    low = group.lower()
    return not any(p.strip("%") in low for p in EXCLUDED_GROUP_PATTERNS)


def test_match_headers():
    for header, expected in HEADERS:
        if expected is None:
            assert not is_match_column(header), f"{header!r} neměl být zápas"
        else:
            assert is_match_column(header), f"{header!r} měl být zápas"
            _, opponent, _ = parse_match_header(header)
            assert opponent == expected, f"{header!r}: {opponent!r} != {expected!r}"


def test_match_datetime_is_timezone_aware():
    dt, _, _ = parse_match_header("Mamby Tábor vs. HC Kobra Praha ženy (27.9.2026 14:45)")
    assert dt.tzinfo is not None, "čas musí mít časovou zónu, jinak se v UTC posune"
    assert (dt.hour, dt.minute) == (14, 45)


def test_header_without_date_is_rejected():
    try:
        parse_match_header("Zápas bez data")
    except ValueError:
        return
    raise AssertionError("název bez data měl vyhodit ValueError")


def test_excluded_groups():
    for group, eligible in GROUPS:
        assert _group_eligible(group) == eligible, f"{group!r} zařazena špatně"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            print(f"  OK    {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  CHYBA {name}: {exc}")
    print(f"\n{'Vše prošlo.' if not failures else f'Selhalo testů: {failures}'}")
    sys.exit(1 if failures else 0)
