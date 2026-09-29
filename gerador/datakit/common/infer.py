"""Limite estimado quando a via não tem `maxspeed` — regra do CTB por tipo de via.

Cada função devolve (típico, lado_baixo) — as duas tabelas publicadas nas colunas
`limit_kmh` e `limit_low_kmh`. Típico = limite legal usual (CTB art. 61); lado baixo = valor deliberadamente
menor, para quem prefere errar para o lado cauteloso. None quando não dá para estimar.
"""
from __future__ import annotations

from typing import Optional, Tuple

Pair = Optional[Tuple[int, int]]


def is_yes(v: Optional[str]) -> bool:
    """Qualquer valor preenchido que não seja "no" (lit=yes, lit=24/7…)."""
    return bool(v) and v != "no"


def zone_limit(zone: Optional[str]) -> Pair:
    """zone:maxspeed / maxspeed:type / source:maxspeed ('BR:urban', 'rural', '40'…)."""
    if not zone:
        return None
    z = zone.strip().lower()
    if z.startswith("br:"):
        z = z[3:]
    if z == "motorway":
        return 110, 110
    if z in ("trunk", "rural:trunk"):
        return 100, 100
    if z in ("rural", "nsl_single"):
        return 100, 80
    if z.startswith("urban") and ("trunk" in z or "primary" in z):
        return 60, 60
    if z.startswith("urban"):
        return 60, 50
    if z == "living_street":
        return 20, 20
    if z.isdigit() and 5 <= int(z) <= 200:
        return int(z), int(z)
    return None


_TYPICAL = {
    "motorway": (110, 110), "motorway_link": (60, 60),
    "trunk": (80, 100), "trunk_link": (40, 40),
    "primary": (60, 100), "primary_link": (40, 40),
    "secondary": (60, 80), "secondary_link": (40, 40),
    "tertiary": (40, 60), "tertiary_link": (40, 60),
    "unclassified": (40, 60),
    "residential": (30, 30), "living_street": (20, 20), "service": (30, 30),
}
_LOW = {
    "motorway": (110, 110), "motorway_link": (60, 60),
    "trunk": (60, 100), "trunk_link": (40, 40),
    "primary": (60, 80), "primary_link": (40, 40),
    "secondary": (50, 60), "secondary_link": (40, 40),
    "tertiary": (40, 40), "tertiary_link": (40, 40),
    "unclassified": (40, 40),
    "residential": (30, 30), "living_street": (20, 20), "service": (20, 20),
}


def class_limit(highway: Optional[str], lit: bool) -> Pair:
    """Pela classe da via; `lit` (iluminada) é o sinal de "é cidade". Valores como
    (com_luz, sem_luz) nas tabelas acima."""
    if highway not in _TYPICAL:
        return None
    i = 0 if lit else 1
    return _TYPICAL[highway][i], _LOW[highway][i]
