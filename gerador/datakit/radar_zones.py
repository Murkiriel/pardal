"""Trechos aptos ao radar portátil da PRF (sources/prf_portable_radar) com a geometria da BR, pelo SNV.

A PRF dá só UF, BR, km inicial e km final. A linha da BR naquele estado (SnvRoutes, os eixos principais com o km em cada
vértice) é cortada entre os dois km (MeasuredLine.slice). Um trecho que atravessa duas linhas do SNV (a rota tem
quebras) vira um pedaço por linha, cada um com o seu km; o trecho de uma BR sem linha no SNV do estado fica de fora e é
contado. O km do SNV cai a 226 m da coordenada na mediana (lrs.py): para trechos de ~10 km, basta.

A geometria é simplificada (Douglas-Peucker, SIMPLIFY_M): as curvas ficam, os vértices de reta saem.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

from datakit.common import RadarStretch, RadarZone

Point = Tuple[float, float]

SIMPLIFY_M = 15.0


def simplify(points: Sequence[Point], tol_m: float) -> List[Point]:
    """Douglas-Peucker em metros (plano local na latitude do primeiro ponto): as pontas sempre ficam."""
    pts = list(points)
    n = len(pts)
    if n <= 2:
        return pts
    kx = 111_320.0 * math.cos(math.radians(pts[0][0]))
    ky = 111_132.0
    xy = [((p[1] - pts[0][1]) * kx, (p[0] - pts[0][0]) * ky) for p in pts]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        far, far_d = -1, tol_m
        for i in range(a + 1, b):
            px, py = xy[i][0] - ax, xy[i][1] - ay
            t = 0.0 if seg2 <= 1e-12 else max(0.0, min(1.0, (px * dx + py * dy) / seg2))
            d = math.hypot(px - dx * t, py - dy * t)
            if d > far_d:
                far, far_d = i, d
        if far >= 0:
            keep[far] = True
            stack += [(a, far), (far, b)]
    return [p for p, k in zip(pts, keep) if k]


def place(uf: str, valid_from: str, stretches: Sequence[RadarStretch], routes) -> Tuple[List[RadarZone], int]:
    """(pedaços com geometria, trechos da UF sem linha no SNV) dos trechos de `uf`; `routes` é SnvRoutes ou None."""
    zones: List[RadarZone] = []
    unplaced = 0
    for s in stretches:
        if s.uf != uf:
            continue
        lines = routes.lines.get((s.br, uf), []) if routes is not None else []
        pieces: List[Tuple[float, float, List[Point]]] = []
        for line in sorted(lines, key=lambda ln: ln.m_min):
            lo, hi = max(s.km_from, line.m_min), min(s.km_to, line.m_max)
            if hi <= lo or any(a <= lo and hi <= b for a, b, _ in pieces):
                continue
            pts = line.slice(lo, hi)
            if len(pts) >= 2:
                pieces.append((lo, hi, pts))
        if not pieces:
            unplaced += 1
            continue
        for lo, hi, pts in pieces:
            zones.append(RadarZone(f"BR-{s.br:03d}", round(lo, 3), round(hi, 3), valid_from,
                                   tuple(simplify(pts, SIMPLIFY_M))))
    return zones, unplaced
