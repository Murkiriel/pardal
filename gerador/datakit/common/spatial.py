"""Busca de vizinhos por distância em metros, em qualquer latitude.

Uma grade de células quadradas em graus olhando só as 8 células em volta erra longe do Equador:
o grau de longitude encolhe com cos(latitude) (a 30° S mede ~96 km, não 111 km), e um par a
leste-oeste perto do raio caía duas células adiante — não era achado. Aqui a busca abre quantas
células forem precisas para cobrir o raio nos dois eixos, na latitude do ponto.
"""
from __future__ import annotations

import math
from typing import Dict, Generic, Iterable, Iterator, List, Optional, Tuple, TypeVar

from datakit.common.geo import _EARTH_R, haversine_m

T = TypeVar("T")

# Metros por grau de latitude na mesma esfera do haversine_m (~111.195 m).
M_PER_DEG = math.pi * _EARTH_R / 180.0
# Folga para arredondamento: a margem é sempre um pouco maior que o raio, nunca menor.
_FOLGA = 1.01


def lat_span_deg(m: float) -> float:
    """Graus de latitude que cobrem m metros."""
    return m / M_PER_DEG * _FOLGA


def lng_span_deg(m: float, lat: float) -> float:
    """Graus de longitude que cobrem m metros perto da latitude lat (vale também para o outro
    ponto, que pode estar até m metros mais perto do polo)."""
    worst = min(89.0, abs(lat) + lat_span_deg(m))
    return m / (M_PER_DEG * math.cos(math.radians(worst))) * _FOLGA


class Grid(Generic[T]):
    """Pontos indexados para achar os que estão a até radius_m de um ponto."""

    def __init__(self, radius_m: float, items: Iterable[Tuple[float, float, T]] = ()):
        self.radius_m = radius_m
        self._cell = lat_span_deg(radius_m)
        self._cells: Dict[Tuple[int, int], List[Tuple[float, float, T]]] = {}
        for lat, lng, item in items:
            self.add(lat, lng, item)

    def add(self, lat: float, lng: float, item: T) -> None:
        self._cells.setdefault((int(lat // self._cell), int(lng // self._cell)), []).append((lat, lng, item))

    def candidates(self, lat: float, lng: float) -> Iterator[Tuple[float, float, T]]:
        """(lat, lng, item) de todas as células que cobrem o raio em volta do ponto — inclui
        pontos mais longe que o raio; quem chama mede a distância."""
        c = self._cell
        dlat, dlng = lat_span_deg(self.radius_m), lng_span_deg(self.radius_m, lat)
        for cy in range(int((lat - dlat) // c), int((lat + dlat) // c) + 1):
            for cx in range(int((lng - dlng) // c), int((lng + dlng) // c) + 1):
                yield from self._cells.get((cy, cx), ())

    def within(self, lat: float, lng: float, radius_m: Optional[float] = None) -> Iterator[Tuple[float, T]]:
        """(distância em metros, item) de cada ponto a até radius_m (no máximo o raio do índice)."""
        r = self.radius_m if radius_m is None else min(radius_m, self.radius_m)
        for plat, plng, item in self.candidates(lat, lng):
            d = haversine_m((lat, lng), (plat, plng))
            if d <= r:
                yield d, item
