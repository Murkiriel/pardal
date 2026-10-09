"""Trechos com muitos acidentes com moto (sources/prf_accidents), postos na rodovia.

O km (inteiro, o da PRF) com [MIN_PER_KM] ou mais acidentes com moto nos 12 meses entra; km seguidos assim na mesma UF e
BR viram um trecho, com a soma dos acidentes e dos motociclistas mortos. O corte foi medido em 2026-10-09 (setembro de
2025 a agosto de 2026): 1.674 km no país, que juntam 54% dos acidentes com moto e 476 dos 2.208 motociclistas mortos
(Goiás: 68 km); com 10 seriam 660 km e só 34% dos acidentes. O trecho é cortado na linha da BR pelo SNV
(MeasuredLine.slice), como os de radar portátil (radar_zones.py); a BR sem linha no SNV da UF fica de fora e é contada.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import groupby
from typing import Iterable, List, Sequence, Tuple

from datakit.common import AccidentZone
from datakit.radar_zones import SIMPLIFY_M, simplify

MIN_PER_KM = 5


@dataclass(frozen=True)
class AccidentKm:
    """Um trecho de km seguidos (do km `km_from` até antes de `km_to`) com muitos acidentes com moto."""
    uf: str
    br: int
    km_from: int
    km_to: int
    accidents: int
    rider_deaths: int


def spots(accidents: Iterable, minimum: int = MIN_PER_KM) -> List[AccidentKm]:
    """Os trechos, na ordem (UF, BR, km), dos acidentes com moto (sources/prf_accidents.Accident)."""
    count: Counter = Counter()
    dead: Counter = Counter()
    for a in accidents:
        count[(a.uf, a.br, a.km)] += 1
        dead[(a.uf, a.br, a.km)] += a.rider_deaths
    hot = sorted(k for k, n in count.items() if n >= minimum)
    out: List[AccidentKm] = []
    for (uf, br), group in groupby(hot, key=lambda k: (k[0], k[1])):
        run: List[int] = []
        for _, _, km in group:
            if run and km != run[-1] + 1:
                out.append(_join(uf, br, run, count, dead))
                run = []
            run.append(km)
        out.append(_join(uf, br, run, count, dead))
    return out


def _join(uf: str, br: int, run: List[int], count: Counter, dead: Counter) -> AccidentKm:
    return AccidentKm(uf, br, run[0], run[-1] + 1, sum(count[(uf, br, k)] for k in run),
                      sum(dead[(uf, br, k)] for k in run))


def place(uf: str, period: Tuple[str, str], rows: Sequence[AccidentKm], routes) -> Tuple[List[AccidentZone], int]:
    """(trechos de `uf` com a geometria, os que ficaram sem linha no SNV); `routes` é SnvRoutes ou None."""
    zones: List[AccidentZone] = []
    unplaced = 0
    months = f"{period[0]}/{period[1]}"
    for r in rows:
        if r.uf != uf:
            continue
        lines = routes.lines.get((r.br, uf), []) if routes is not None else []
        pts: List[Tuple[float, float]] = []
        for line in sorted(lines, key=lambda ln: ln.m_min):
            piece = line.slice(float(r.km_from), float(r.km_to))
            if len(piece) >= 2:
                pts = piece
                break
        if not pts:
            unplaced += 1
            continue
        zones.append(AccidentZone(f"BR-{r.br:03d}", float(r.km_from), float(r.km_to), r.accidents, r.rider_deaths, months,
                                  tuple(simplify(pts, SIMPLIFY_M))))
    return zones, unplaced
