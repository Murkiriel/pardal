"""Trechos com buracos (sources/dnit_icm): os km com panela ruim ou péssima juntados em trechos e postos na rodovia.

Km seguidos (o fim de um é o começo do outro) na mesma BR, UF e sentido viram um trecho, com o pior nível. O trecho é
cortado na linha da BR pelo SNV (MeasuredLine.slice), como os trechos de radar portátil (radar_zones.py), e desenhado no
sentido do tráfego: o decrescente vai do km maior para o menor. O trecho de BR sem linha no SNV fica de fora e é
contado. A linha é simplificada (radar_zones.simplify).
"""
from __future__ import annotations

from itertools import groupby
from typing import List, Sequence, Tuple

from datakit.common import PotholeZone, RoughKm
from datakit.radar_zones import SIMPLIFY_M, simplify

_WORST = {"BAD": 1, "VERY_BAD": 2}


def merge(rows: Sequence[RoughKm]) -> List[RoughKm]:
    """Os km seguidos na mesma UF, BR e sentido num trecho só, com o pior nível; na ordem (UF, BR, sentido, km)."""
    out: List[RoughKm] = []
    ordered = sorted(rows, key=lambda r: (r.uf, r.br, r.decreasing, r.km_from))
    for _, group in groupby(ordered, key=lambda r: (r.uf, r.br, r.decreasing)):
        run: List[RoughKm] = []
        for r in group:
            if run and r.km_from <= run[-1].km_to + 1e-6:
                run.append(r)
                continue
            if run:
                out.append(_join(run))
            run = [r]
        if run:
            out.append(_join(run))
    return out


def _join(run: List[RoughKm]) -> RoughKm:
    level = max((r.level for r in run), key=lambda lv: _WORST.get(lv, 0))
    return RoughKm(run[0].uf, run[0].br, run[0].km_from, max(r.km_to for r in run), run[0].decreasing, level)


def place(uf: str, month: str, rows: Sequence[RoughKm], routes) -> Tuple[List[PotholeZone], int]:
    """(trechos de `uf` com a geometria, os que ficaram sem linha no SNV); `routes` é SnvRoutes ou None."""
    zones: List[PotholeZone] = []
    unplaced = 0
    for r in merge([x for x in rows if x.uf == uf]):
        lines = routes.lines.get((r.br, uf), []) if routes is not None else []
        pts: List[Tuple[float, float]] = []
        for line in sorted(lines, key=lambda ln: ln.m_min):
            piece = line.slice(r.km_from, r.km_to)
            if len(piece) >= 2:
                pts = piece
                break
        if not pts:
            unplaced += 1
            continue
        if r.decreasing:
            pts = pts[::-1]
        zones.append(PotholeZone(f"BR-{r.br:03d}", r.km_from, r.km_to, "DECREASING" if r.decreasing else "INCREASING",
                                 r.level, month, tuple(simplify(pts, SIMPLIFY_M))))
    return zones, unplaced
